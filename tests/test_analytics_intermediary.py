import copy
import json

from fastapi.testclient import TestClient
import pytest

from backend.analytics_contracts import AUDIENCE, ORIGIN, AnalyticsError, RevisionRegistry
from backend.analytics_grants import GrantStore
from backend.analytics_intermediary import create_analytics_app, create_synthetic_analytics_app
from tests.analytics_support import (
    Clock, FakeIssuer, FakeResults, FakeUpstream, record, ready, registry,
)

ROOT = "/analytics/v1"
SUMMARY = ROOT+"/datasets/dataset-a/summary"
RESULTS = ROOT+"/datasets/dataset-a/results"


@pytest.fixture
def context():
    clock = Clock()
    issuer = FakeIssuer({"code": record(clock)})
    grants, upstream, provider = GrantStore(clock=clock), FakeUpstream(), FakeResults()
    return clock, issuer, grants, upstream, provider


def client_for(context, *, synthetic=False, revisions=None, remote="127.0.0.1"):
    _, issuer, grants, upstream, provider = context
    args = dict(issuer=issuer, grants=grants, upstream=upstream)
    app = (create_synthetic_analytics_app(**args, provider=provider,
             revisions=revisions if revisions is not None else registry()) if synthetic
           else create_analytics_app(**args))
    return TestClient(app, base_url=AUDIENCE, client=(remote, 1234), headers={"Origin": ORIGIN})


def exchange(client, code="code"):
    response = client.post(ROOT+"/grants/exchange", json={"exchange_code": code})
    assert response.status_code == 200, response.text
    return response.json(), {"Authorization": "Bearer "+response.json()["access_token"]}


def test_exchange_summary_and_no_deployed_results(context, caplog):
    with client_for(context) as client:
        response, headers = exchange(client)
        assert response["schema_version"] == "analytics.v1"
        assert response["token_type"] == "Bearer"
        assert response["server_time"].endswith("Z") and response["expires_at"].endswith("Z")
        result = client.get(SUMMARY, headers=headers)
        assert result.status_code == 200
        assert result.json()["schema_version"] == "analytics.v1"
        assert result.json()["error_code"] is None
        assert result.headers["Cache-Control"] == "no-store"
        assert result.headers["Access-Control-Allow-Origin"] == ORIGIN
        assert "FAKE-UPSTREAM-CAPABILITY" not in result.text+json.dumps(response)+caplog.text
        assert client.post(ROOT+"/grants/exchange", json={"exchange_code": "code"}).status_code == 401
        assert client.get(RESULTS, headers=headers).status_code == 404
        assert client.get(RESULTS+"/result-a", headers=headers).status_code == 404
        assert context[4].calls == []
        assert all("results" not in route.path for route in client.app.routes)
        for route in ("/issuer", "/analyze", "/cancel", "/upload"):
            assert client.post(ROOT+route).status_code == 404


@pytest.mark.parametrize("origin", [None, "null", "http://localhost:5173", "http://127.0.0.1:5174", "https://evil.invalid"])
def test_origin_denied_before_issuer(context, origin):
    with client_for(context) as client:
        headers = {} if origin is None else {"Origin": origin}
        request = client.build_request("POST", ROOT+"/grants/exchange", json={"exchange_code": "code"})
        request.headers.pop("origin", None)
        request.headers.update(headers)
        result = client.send(request)
        assert result.status_code == 403 and result.json()["error"]["code"] == "LOCAL_ONLY"
        assert "code" in context[1].records


@pytest.mark.parametrize("headers", [
    {"Host": "localhost:8012"}, {"Host": "127.0.0.1:8013"},
    [("Origin", ORIGIN), ("Origin", ORIGIN)],
])
def test_host_and_duplicate_origins_denied(context, headers):
    with client_for(context) as client:
        assert client.get(SUMMARY, headers=headers).status_code == 403


def test_remote_client_and_preflight(context):
    with client_for(context, remote="192.0.2.1") as client:
        assert client.get(SUMMARY).status_code == 403
    with client_for(context) as client:
        response = client.options(SUMMARY, headers={"Access-Control-Request-Method": "GET",
                                                     "Access-Control-Request-Headers": "Authorization"})
        assert response.status_code == 200
        assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
        assert "Access-Control-Allow-Credentials" not in response.headers
        assert client.options(SUMMARY, headers={"Access-Control-Request-Method": "DELETE"}).status_code == 400


@pytest.mark.parametrize("body", [b"{bad", b"[]", b'{"exchange_code":1}',
    b'{"exchange_code":"code","extra":1}', b'{"exchange_code":"code","exchange_code":"code"}',
    b"x"*8193])
def test_exchange_malformed_body_is_safe(context, body):
    with client_for(context) as client:
        result = client.post(ROOT+"/grants/exchange", content=body, headers={"Content-Type": "application/json"})
        assert result.status_code == 422 and result.json() == {"error": {"code": "INVALID_ACCESS_REQUEST"}}
        assert "code" in context[1].records


@pytest.mark.parametrize("state", ["validating", "indexing", "publishing", "failed", "cancelled"])
def test_nonready_states_preserved_and_never_load_results(context, state):
    context[3].value = {"dataset_id": "dataset-a", "upload_id": "upload-a", "state": state,
                        "ready": False, "error_code": "VALIDATION_FAILED" if state == "failed" else None}
    with client_for(context, synthetic=True) as client:
        _, headers = exchange(client)
        result = client.get(SUMMARY, headers=headers)
        assert result.status_code == 200 and result.json()["state"] == state
        assert client.get(RESULTS, headers=headers).json() == {"error": {"code": "DATASET_NOT_READY"}}
        assert context[4].calls == []


@pytest.mark.parametrize("value", [
    {"dataset_id": "wrong"}, {"images": 2**53}, {"ready": False},
    {"schema_version": "analytics.v99"}, {"archive_sha256": "not-a-hash"},
])
def test_malformed_summary_rejected(context, value):
    context[3].value.update(value)
    with client_for(context) as client:
        _, headers = exchange(client)
        result = client.get(SUMMARY, headers=headers)
        assert result.status_code == 502 and result.json()["error"]["code"] == "UPSTREAM_RESULT_INVALID"


def test_authorization_before_lookup_and_recheck_after_upstream(context):
    with client_for(context) as client:
        assert client.get(SUMMARY).status_code == 401
        assert context[3].calls == []
        _, headers = exchange(client)
        assert client.get(SUMMARY.replace("dataset-a", "other"), headers=headers).status_code == 403
        assert context[3].calls == []
        original = context[3].summary
        def slow(*args):
            value = original(*args)
            context[0].value += 600
            return value
        context[3].summary = slow
        assert client.get(SUMMARY, headers=headers).status_code == 401


def test_summary_only_denies_results_and_empty_allowlist_is_empty(context):
    context[1].records["code"] = record(context[0], scopes=frozenset({"summary:read"}), result_ids=frozenset())
    with client_for(context, synthetic=True) as client:
        _, headers = exchange(client)
        assert client.get(SUMMARY, headers=headers).status_code == 200
        assert client.get(RESULTS, headers=headers).status_code == 403
        assert context[4].calls == []
    context[1].records["second-code"] = record(context[0], result_ids=frozenset())
    with client_for(context, synthetic=True) as client:
        _, headers = exchange(client, "second-code")
        assert client.get(RESULTS, headers=headers).json()["results"] == []
        before = list(context[4].calls)
        assert client.get(RESULTS+"/other", headers=headers).status_code == 403
        assert context[4].calls == before


def test_synthetic_result_orchestration(context):
    with client_for(context, synthetic=True) as client:
        _, headers = exchange(client)
        listed = client.get(RESULTS, headers=headers)
        assert listed.status_code == 200 and len(listed.json()["results"]) == 1
        result = client.get(RESULTS+"/result-a", headers=headers)
        assert result.status_code == 200
        data = result.json()
        assert data["descriptor"] == listed.json()["results"][0]
        assert data["descriptor"]["request_fingerprint"] is None
        assert data["descriptor"]["effective_config"]["image_cfg"]["min_width"]["decimal"] == "9007199254740993"
        assert data["payload"]["quality_rows"][0]["noise_score"] is None
        context[4].detail = None
        assert client.get(RESULTS+"/result-a", headers=headers).status_code == 404


@pytest.mark.parametrize("mutation", ["config", "version", "archive", "payload", "provenance", "metadata_limit"])
def test_synthetic_identity_schema_and_resource_failures(context, mutation):
    item = context[4].detail
    if mutation == "config":
        item["descriptor"]["effective_config"]["image_cfg"]["min_width"]["decimal"] = "2"
    elif mutation == "version":
        item["descriptor"]["canonicalization_version"] = "unsupported"
    elif mutation == "archive":
        item["descriptor"]["archive_sha256"] = "c"*64
    elif mutation == "payload":
        item["payload"]["dataset_id"] = "other"
    elif mutation == "provenance":
        del item["provenance"]["dependency_digest"]
    else:
        item["descriptor"]["extra"] = "x"*65536
    with client_for(context, synthetic=True) as client:
        _, headers = exchange(client)
        result = client.get(RESULTS+"/result-a", headers=headers)
        assert result.status_code == 502


def test_no_default_configuration_revision(context):
    with client_for(context, synthetic=True, revisions=RevisionRegistry()) as client:
        _, headers = exchange(client)
        assert client.get(RESULTS, headers=headers).status_code == 502


def test_unknown_exception_has_no_sensitive_body(context, caplog):
    def fail(*args):
        raise RuntimeError("FAKE-UPSTREAM-CAPABILITY private/path")
    context[3].summary = fail
    with client_for(context) as client:
        _, headers = exchange(client)
        result = client.get(SUMMARY, headers=headers)
        assert result.status_code == 500
        assert result.json() == {"error": {"code": "INTERNAL_SERVER_ERROR"}}
        assert "FAKE-UPSTREAM-CAPABILITY" not in result.text+caplog.text


def test_revocation_during_read_discards_response(context):
    with client_for(context) as client:
        response, headers = exchange(client)
        original = context[3].summary
        def revoked(*args):
            value = original(*args)
            context[2].revoke(response["access_token"])
            return value
        context[3].summary = revoked
        assert client.get(SUMMARY, headers=headers).status_code == 401


@pytest.mark.parametrize("mutation", ["unsafe_integer", "nonfinite", "submitted", "provenance_limit", "duplicate"])
def test_synthetic_validation_gaps(context, mutation):
    provider = context[4]
    if mutation == "unsafe_integer":
        provider.detail["payload"]["quality_rows"][0]["size_kb"] = 2**53+1
    elif mutation == "nonfinite":
        provider.detail["payload"]["quality_rows"][0]["noise_score"] = float("nan")
    elif mutation == "submitted":
        provider.detail["provenance"]["submitted_config"]["image_cfg"] = {
            "min_width": {"kind": "integer", "decimal": "1"}}
    elif mutation == "provenance_limit":
        provider.detail["provenance"]["extra"] = "x"*65536
    else:
        provider.listed *= 2
    with client_for(context, synthetic=True) as client:
        _, headers = exchange(client)
        result = client.get(RESULTS+"/result-a", headers=headers)
        assert result.status_code == 502


def test_full_response_limit_explicit_not_truncated(context, monkeypatch):
    import backend.analytics_intermediary as boundary
    monkeypatch.setattr(boundary, "RESPONSE_LIMIT", 128)
    with client_for(context, synthetic=True) as client:
        _, headers = exchange(client)
        result = client.get(RESULTS, headers=headers)
        assert result.status_code == 502
        assert result.json() == {"error": {"code": "UPSTREAM_RESPONSE_LIMIT"}}


def test_descriptor_comparison_ignores_order_and_descriptive_additions(context):
    provider = context[4]
    provider.detail["descriptor"] = dict(reversed(list(provider.detail["descriptor"].items())))
    provider.detail["descriptor"]["display_metadata"] = "not identity"
    with client_for(context, synthetic=True) as client:
        _, headers = exchange(client)
        assert client.get(RESULTS+"/result-a", headers=headers).status_code == 200
