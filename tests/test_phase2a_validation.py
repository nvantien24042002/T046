"""Request/response contract checks with temporary data and in-process transports."""
import copy
import io
import json

import httpx
import pytest
from pydantic import ValidationError

from backend.integrated_api import AnalyzeRequest
from core.image_quality import DEFAULT_IMAGE_CFG
from core.label_audit import DEFAULT_LABEL_CFG
from tests.fixtures import make_zip
from tests.test_integrated_api import integrated_api, upload_fixture_via_api, auth
from tests.test_adapter_security_boundary import OPERATIONS
from ui.backend_adapter import BackendClient, BackendError


def ready_dataset(fixture, tmp_path):
    client, app, owners = fixture
    data = make_zip(tmp_path / "isolated.zip").read_bytes()
    session = upload_fixture_via_api(client, owners, data)
    response = client.post("/api/v1/uploads/" + session["upload_id"] + "/validate", headers=auth(owners, session))
    assert response.status_code == 200
    assert response.json()["state"] == "ready"
    return client, app, owners, session, response.json()["dataset_id"]


def test_bad_analysis_requests_never_enter_pipeline(integrated_api, tmp_path, monkeypatch):
    client, app, owners, session, dataset = ready_dataset(integrated_api, tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("invalid input entered pipeline")
    monkeypatch.setattr("backend.integrated_api.execute_pipeline", forbidden)
    invalid = [b"{", b"null", b"[]", b"true", b'"secret-input"',
               b'{"image_cfg":{"blur_threshold":NaN}}',
               b'{"label_cfg":{"min_texture":Infinity}}',
               b'{"image_cfg":{"noise_threshold":-Infinity}}',
               b'{"image_cfg":{"noise_threshold":1e999}}']
    for payload in [
        {"extra": 1}, {"image_cfg": []}, {"label_cfg": "bad"},
        {"image_cfg": {"blur_threshold": "100"}},
        {"image_cfg": {"blur_threshold": True}},
        {"image_cfg": {"min_width": 1.5}}, {"image_cfg": {"min_height": 0}},
        {"image_cfg": {"dark_threshold": 256}},
        {"image_cfg": {"bright_threshold": -1}},
        {"image_cfg": {"black_threshold": 250, "white_threshold": 20}},
        {"image_cfg": {"dark_threshold": 240}},
        {"image_cfg": {"unknown": 1}},
        {"label_cfg": {"duplicate_iou": 1.01}},
        {"label_cfg": {"min_edge": -0.1}},
        {"label_cfg": {"min_boundary": 2}},
        {"label_cfg": {"min_aspect": 0}},
        {"label_cfg": {"min_area_pct": 101}},
        {"label_cfg": {"max_area_pct": 10001}},
        {"label_cfg": {"min_area_pct": 50, "max_area_pct": 40}},
        {"label_cfg": {"min_aspect": 8}},
        {"label_cfg": {"min_texture": None}},
    ]:
        invalid.append(json.dumps(payload).encode())
    headers = {**auth(owners, session), "Content-Type": "application/json"}
    for body in invalid:
        response = client.post(f"/api/v1/datasets/{dataset}/analyze", headers=headers, content=body)
        assert response.status_code == 422
        assert response.json() == {"error": {"code": "INVALID_ANALYSIS_REQUEST"}}
    assert list(app.state.cache_dir.iterdir()) == []
    assert client.get("/health").status_code == 200
    adapter = BackendClient(owner_key=owners["alice"], http_client=client)
    with pytest.raises(BackendError) as caught:
        adapter.trigger_analysis(dataset, session["upload_capability"], {"min_width": "bad"})
    assert caught.value.status_code == 422
    assert caught.value.code == "INVALID_ANALYSIS_REQUEST"


def test_valid_configs_preserve_values_defaults_and_results(integrated_api, tmp_path, monkeypatch):
    client, app, owners, session, dataset = ready_dataset(integrated_api, tmp_path)
    import backend.integrated_api as api
    original = api.execute_pipeline
    observed = []
    def pipeline(*args, **kwargs):
        observed.append(copy.deepcopy(kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(api, "execute_pipeline", pipeline)
    adapter = BackendClient(owner_key=owners["alice"], http_client=client)
    default = adapter.trigger_analysis(dataset, session["upload_capability"])
    assert observed == [{"image_cfg": None, "label_cfg": None}]
    full = {"image_cfg": DEFAULT_IMAGE_CFG.copy(), "label_cfg": DEFAULT_LABEL_CFG.copy()}
    response = client.post(f"/api/v1/datasets/{dataset}/analyze", headers=auth(owners, session), json=full)
    assert response.status_code == 200
    assert observed[-1] == full
    assert AnalyzeRequest.model_validate(full).model_dump() == full
    for key in default:
        if key != "request_fingerprint":
            assert response.json()[key] == default[key]
    assert adapter.get_analysis_results(dataset, session["upload_capability"]) == response.json()
    # Preserve partial configs, empty dictionaries, null and UI-supported extremes.
    for payload in [{}, {"image_cfg": {}}, {"label_cfg": None},
                    {"image_cfg": {"blur_threshold": 0, "min_width": 1}},
                    {"label_cfg": {"max_area_pct": 10000, "duplicate_iou": 0, "min_boundary": 1}}]:
        parsed = AnalyzeRequest.model_validate(payload).model_dump()
        assert parsed == {"image_cfg": None, "label_cfg": None, **payload}
        response = client.post(f"/api/v1/datasets/{dataset}/analyze", headers=auth(owners, session), json=payload)
        assert response.status_code == 200


@pytest.mark.parametrize("field,defaults", [("image_cfg", DEFAULT_IMAGE_CFG), ("label_cfg", DEFAULT_LABEL_CFG)])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), -1, True, "1", [], None])
def test_every_threshold_rejects_bad_types_and_nonfinite_values(field, defaults, invalid):
    for key in defaults:
        with pytest.raises(ValidationError):
            AnalyzeRequest.model_validate({field: {key: invalid}})


@pytest.mark.parametrize("mutation", [
    {"received_bytes": True}, {"received_bytes": 2}, {"size_bytes": "1"},
    {"chunk_size_bytes": 0}, {"state": []}, {"filename": None}, {"ready": 1},
])
def test_invalid_upload_status_fields(mutation):
    payload = {"upload_id": "fake-id", "filename": "test.zip", "size_bytes": 1,
               "received_bytes": 0, "chunk_size_bytes": 1, "state": "created", **mutation}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as client:
        with pytest.raises(BackendError) as caught:
            BackendClient(owner_key="fake-owner", http_client=client).get_upload_status("fake-id", "fake-capability")
    assert caught.value.code == "INVALID_BACKEND_RESPONSE"


@pytest.mark.parametrize("operation", OPERATIONS[:-1])
@pytest.mark.parametrize("body", [b"not JSON fake-secret", b"null", b"[]", b"42", b"{}", b'{"state":[]}'])
def test_malformed_success_responses_are_safe(operation, body, caplog):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))) as client:
        with pytest.raises(BackendError) as caught:
            operation(BackendClient(owner_key="fake-owner", http_client=client))
    assert caught.value.code == "INVALID_BACKEND_RESPONSE"
    assert "fake-secret" not in str(caught.value)
    assert "fake-secret" not in caplog.text


def bundle():
    result = {"dataset_id": "fake-dataset", **{key: [] for key in (
        "quality_rows", "audit_rows", "classes", "attribute_distribution",
        "distribution_images", "distribution_boxes", "attributes",
    )}}
    result["quality_rows"] = [{"filename": "test.png", "status": "PASS", "score": 100,
                               "issues": [], "blur_score": 100, "brightness": 100, "contrast": 25}]
    return result


def test_analysis_without_optional_fingerprint_and_extra_fields():
    payload = {**bundle(), "future_optional": {"value": 1}}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as client:
        adapter = BackendClient(owner_key="fake-owner", http_client=client)
        assert adapter.get_analysis_results("fake-dataset", "fake-capability") == payload


@pytest.mark.parametrize("mutation", [
    {"quality_rows": [1]}, {"quality_rows": []}, {"attributes": {}}, {"audit_rows": None},
    {"request_fingerprint": 123}, {"classes": [{"score": float("nan")}]},
])
def test_invalid_analysis_fields(mutation):
    body = json.dumps({**bundle(), **mutation}).encode()
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))) as client:
        with pytest.raises(BackendError) as caught:
            BackendClient(owner_key="fake-owner", http_client=client).get_analysis_results("fake-dataset", "fake-capability")
    assert caught.value.code == "INVALID_BACKEND_RESPONSE"


@pytest.mark.parametrize("receipt", [
    None, [], {}, {"chunk_index": "0", "offset_bytes": 0, "length_bytes": 1, "sha256": "a" * 64},
    {"chunk_index": 0, "offset_bytes": False, "length_bytes": 1, "sha256": "a" * 64},
    {"chunk_index": 0, "offset_bytes": 0, "length_bytes": 0, "sha256": "a" * 64},
    {"chunk_index": 0, "offset_bytes": 0, "length_bytes": 1, "sha256": []},
])
def test_bad_receipts_rejected_before_read_or_write(receipt):
    seen = []
    def handle(request):
        seen.append(request)
        if request.url.path.endswith("/receipts"):
            return httpx.Response(200, json={"receipts": [receipt], "next_index": 1})
        return httpx.Response(200, json={
            "upload_id": "fake-id", "filename": "test.zip", "size_bytes": 1,
            "received_bytes": 1, "chunk_size_bytes": 1, "state": "uploading",
        })
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(BackendError) as caught:
            BackendClient(owner_key="fake-owner", http_client=client).stream_upload(
                io.BytesIO(b"a"), "test.zip", 1,
                recovery_session={"upload_id": "fake-id", "upload_capability": "fake-capability"},
                confirmed_legacy_source=True,
            )
    assert caught.value.code == "INVALID_BACKEND_RESPONSE"
    assert len(seen) == 2 and all(r.method == "GET" for r in seen)


@pytest.mark.parametrize("body", [b"[]", b'{"error":null}', b'{"error":{"code":[]}}', b"broken JSON"])
def test_malformed_not_run_error_does_not_escape(body):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(409, content=body))) as client:
        with pytest.raises(BackendError) as caught:
            BackendClient(owner_key="fake-owner", http_client=client).get_analysis_results("fake-dataset", "fake-capability")
    assert caught.value.code == "HTTP_409"


def test_bad_recovery_record_never_creates_session():
    seen = []
    with httpx.Client(transport=httpx.MockTransport(lambda r: seen.append(r))) as client:
        with pytest.raises(BackendError) as caught:
            BackendClient(owner_key="fake-owner", http_client=client).stream_upload(
                io.BytesIO(b"a"), "test.zip", 1, recovery_session={}, confirmed_legacy_source=True,
            )
    assert caught.value.code == "INVALID_BACKEND_RESPONSE"
    assert seen == []


@pytest.mark.parametrize("value", [[], False, 0, "", {"blur_threshold": float("nan")},
                                  {"blur_threshold": float("inf")}, {"blur_threshold": object()}])
def test_adapter_invalid_request_serialization_is_safe(value):
    seen = []
    with httpx.Client(transport=httpx.MockTransport(lambda r: seen.append(r))) as client:
        with pytest.raises(BackendError) as caught:
            BackendClient(owner_key="fake-owner", http_client=client).trigger_analysis("fake-dataset", "fake-capability", value)
    assert caught.value.code == "INVALID_ANALYSIS_REQUEST"
    assert caught.value.status_code == 422
    assert seen == []


def test_adapter_empty_config_keeps_previous_wire_default():
    def handle(request):
        assert json.loads(request.content) == {}
        return httpx.Response(200, json=bundle())
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        BackendClient(owner_key="fake-owner", http_client=client).trigger_analysis("fake-dataset", "fake-capability", {}, {})


@pytest.mark.parametrize("kind", ["quality_rows", "audit_rows", "classes", "attribute_distribution", "distribution_images", "distribution_boxes"])
@pytest.mark.parametrize("row", [{}, {"unexpected": "fake-secret"}])
def test_rows_missing_fe_columns_are_rejected(kind, row):
    payload = {**bundle(), kind: [row]}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as client:
        with pytest.raises(BackendError) as caught:
            BackendClient(owner_key="fake-owner", http_client=client).get_analysis_results("fake-dataset", "fake-capability")
    assert caught.value.code == "INVALID_BACKEND_RESPONSE"
    assert "fake-secret" not in str(caught.value)


def test_unavailable_image_metrics_remain_nullable():
    payload = {**bundle(), "quality_rows": [{
        "filename": "broken.png", "status": "ERROR", "score": 0, "issues": ["READ_ERROR"],
        "blur_score": None, "brightness": None, "contrast": None,
    }]}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as client:
        result = BackendClient(owner_key="fake-owner", http_client=client).get_analysis_results("fake-dataset", "fake-capability")
    assert result == payload
