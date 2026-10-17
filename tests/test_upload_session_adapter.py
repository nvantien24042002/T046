"""Upload-session adapter regressions using only in-memory transports."""
import logging

import httpx
import pytest

from ui.backend_adapter import BackendClient, BackendError


@pytest.mark.parametrize('status', [200, 201])
def test_create_success(status):
    payload = {'upload_id': 'disposable', 'upload_capability': 'short-token'}
    def handler(request):
        assert request.method == 'POST'
        assert request.url.path == '/api/v1/uploads'
        assert request.headers['X-Owner-Key'] == 'short-key'
        assert request.headers['Idempotency-Key'] == 'one-attempt'
        assert request.read() == b'{"filename":"sample.zip","size_bytes":12}'
        return httpx.Response(status, json=payload)
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        adapter = BackendClient(owner_key='short-key', http_client=transport)
        assert adapter.create_upload('folder/sample.zip', 12, 'one-attempt') == payload
        assert not transport.is_closed


@pytest.mark.parametrize('status,code', [(403, 'ACCESS_DENIED'), (413, 'METADATA_TOO_LARGE'), (422, 'INVALID_METADATA')])
def test_create_http_errors(status, code, caplog):
    def handler(request):
        return httpx.Response(status, json={'error': {'code': code, 'message': 'secret response'}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        with pytest.raises(BackendError) as caught:
            BackendClient(http_client=transport).create_upload('sample.zip', 12)
    assert caught.value.code == code
    assert caught.value.status_code == status
    assert caught.value.__cause__ is None
    assert 'secret response' not in str(caught.value)
    assert not caplog.records


@pytest.mark.parametrize('kind', [httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.RemoteProtocolError, httpx.RequestError])
def test_create_transport_failure_is_safe_and_chained(kind, caplog):
    # Include short secrets and paths which generic token regexes would miss.
    sensitive = 'X-Owner-Key: short-key Upload-Capability: short-token Authorization: Bearer password https://user:pass@localhost/?secret=hidden D:\\private\\config'
    original = None
    def handler(request):
        nonlocal original
        original = kind(sensitive, request=request)
        raise original
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        with caplog.at_level(logging.WARNING, logger='ui.backend_adapter'):
            with pytest.raises(BackendError) as caught:
                BackendClient(owner_key='short-key', http_client=transport).create_upload('sample.zip', 12)
    assert caught.value.code == 'BACKEND_UNAVAILABLE'
    assert caught.value.message == 'Không thể kết nối đến dịch vụ phân tích dữ liệu.'
    assert caught.value.__cause__ is original
    assert kind.__name__ in caplog.text
    for forbidden in ('short-key', 'short-token', 'Authorization', 'X-Owner-Key', 'Upload-Capability', 'password', 'hidden', 'private', 'user:pass'):
        assert forbidden not in caplog.text
        assert forbidden not in str(caught.value)
    assert all(record.exc_info is None for record in caplog.records)


def test_create_malformed_json(caplog):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(201, content=b'secret-token invalid JSON'))) as transport:
        with caplog.at_level(logging.WARNING, logger='ui.backend_adapter'):
            with pytest.raises(BackendError) as caught:
                BackendClient(http_client=transport).create_upload('sample.zip', 12)
    assert caught.value.code == 'INVALID_BACKEND_RESPONSE'
    assert isinstance(caught.value.__cause__, ValueError)
    assert 'secret-token' not in caplog.text
    assert 'secret-token' not in str(caught.value)


def test_owned_client_timeout_and_cleanup(monkeypatch):
    real_client = httpx.Client
    clients = []
    def factory(**kwargs):
        assert kwargs['timeout'] == 4.5
        client = real_client(transport=httpx.MockTransport(lambda request: httpx.Response(201, json={'upload_id': 'fake', 'upload_capability': 'fake-capability'})), **kwargs)
        clients.append(client)
        return client
    monkeypatch.setattr('ui.backend_adapter.httpx.Client', factory)
    BackendClient(timeout=4.5).create_upload('sample.zip', 12)
    assert len(clients) == 1
    assert clients[0].is_closed
