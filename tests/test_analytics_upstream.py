import httpcore
import httpx
import pytest

from backend.analytics_contracts import AnalyticsError, ORIGIN
from backend.analytics_upstream import LoopbackUpstream
from tests.analytics_support import ready


def test_direct_transport_ignores_environment_proxies(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8888")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:8888")
    monkeypatch.setenv("NO_PROXY", "")
    observed = []
    def handle(transport, request):
        assert not isinstance(transport._pool, httpcore.HTTPProxy)
        observed.append(request)
        return httpx.Response(200, json=ready())
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    result = LoopbackUpstream().summary("dataset-a", "FAKE-CAP", ORIGIN)
    assert result["ready"] is True
    assert len(observed) == 1
    request = observed[0]
    assert str(request.url) == "http://127.0.0.1:8011/api/v1/datasets/dataset-a/summary"
    assert request.method == "GET" and request.headers["Upload-Capability"] == "FAKE-CAP"
    assert request.headers["Origin"] == ORIGIN
    assert "authorization" not in request.headers and "x-owner-key" not in request.headers


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_never_forwards_credential(status, caplog):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://elsewhere.invalid/FAKE-SECRET"})
    with pytest.raises(AnalyticsError) as caught:
        LoopbackUpstream(mock_transport=httpx.MockTransport(handle)).summary("dataset-a", "FAKE-CAP", ORIGIN)
    assert caught.value.code == "UPSTREAM_RESULT_INVALID" and len(calls) == 1
    assert "FAKE-SECRET" not in str(caught.value) and "FAKE-CAP" not in caplog.text


@pytest.mark.parametrize("dataset,origin", [("../escape", ORIGIN), ("x?query", ORIGIN),
    ("https://elsewhere.invalid", ORIGIN), ("dataset-a", "http://localhost:5173")])
def test_invalid_destination_rejected_before_transport(dataset, origin):
    def forbidden(_):
        pytest.fail("request must not be sent")
    with pytest.raises(AnalyticsError):
        LoopbackUpstream(mock_transport=httpx.MockTransport(forbidden)).summary(dataset, "fake", origin)


@pytest.mark.parametrize("status,body,code", [
    (200, b"not-json", "UPSTREAM_RESULT_INVALID"),
    (200, b'{"ready":true}', "UPSTREAM_RESULT_INVALID"),
    (200, b'{"x":NaN}', "UPSTREAM_RESULT_INVALID"),
    (200, b'{"x":1,"x":2}', "UPSTREAM_RESULT_INVALID"),
    (403, b'{"error":{"code":"ACCESS_DENIED"}}', "UPSTREAM_RESULT_INVALID"),
    (404, b'{"error":{"code":"DATASET_NOT_FOUND"}}', "DATASET_NOT_FOUND"),
    (503, b'{"error":{"code":"REGISTRY_UNAVAILABLE"}}', "BACKEND_UNAVAILABLE"),
    (200, b"x"*65537, "UPSTREAM_RESPONSE_LIMIT"),
], ids=["malformed", "schema", "nan", "duplicate", "auth", "missing", "unhealthy", "oversized"])
def test_response_failures_are_sanitized(status, body, code):
    transport = httpx.MockTransport(lambda _: httpx.Response(status, content=body))
    with pytest.raises(AnalyticsError) as caught:
        LoopbackUpstream(mock_transport=transport).summary("dataset-a", "FAKE-CAP", ORIGIN)
    assert caught.value.code == code


def test_transport_failure_is_safe():
    def fail(request):
        raise httpx.ConnectError("FAKE-CAP private/path", request=request)
    with pytest.raises(AnalyticsError) as caught:
        LoopbackUpstream(mock_transport=httpx.MockTransport(fail)).summary("dataset-a", "FAKE-CAP", ORIGIN)
    assert caught.value.code == "BACKEND_UNAVAILABLE" and "FAKE-CAP" not in str(caught.value)


def test_injected_network_transport_rejected():
    with httpx.HTTPTransport() as transport:
        with pytest.raises(ValueError):
            LoopbackUpstream(mock_transport=transport)


def test_compressed_response_rejected_before_decompression():
    def handle(_):
        return httpx.Response(200, headers={"Content-Encoding": "custom"}, content=b"fake")
    with pytest.raises(AnalyticsError) as caught:
        LoopbackUpstream(mock_transport=httpx.MockTransport(handle)).summary("dataset-a", "fake", ORIGIN)
    assert caught.value.code == "UPSTREAM_RESULT_INVALID"
