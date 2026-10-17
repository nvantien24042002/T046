"""Server projections from existing baseline rows; no live credentials or services."""
import json
from pathlib import Path
import pytest
from backend.dashboard_projection import DashboardRequest, project_dashboard

@pytest.fixture
def source():
    baseline = json.loads((Path(__file__).parent / "baseline" / "expected.json").read_text(encoding="utf-8"))
    payload = {"dataset_id": "dataset-test", "quality_rows": baseline["quality"],
        "audit_rows": baseline["audit"], "classes": baseline["classes"],
        "attribute_distribution": baseline["attributes"], "distribution_images": [],
        "distribution_boxes": [], "attributes": []}
    return payload, {"dataset_id": "dataset-test", "images": 2, "annotations": True,
        "capabilities": {"image_quality": True, "label_audit": True, "distribution": True, "coverage": True, "scene_finder": True}}

def test_overview_counts_are_server_computed(source):
    payload, summary = source
    view = project_dashboard(DashboardRequest(page="overview"), payload, summary)
    counts = {m["key"]: m["value"] for m in view["metrics"]}
    assert counts["images"] == 2
    assert counts["review_images"] == 2
    assert counts["classes"] == len(payload["classes"])
    assert view["dataset_id"] == "dataset-test"

def test_overview_images_only_does_not_invent_annotation_kpis(source):
    payload, summary = source
    summary["annotations"] = False
    payload.update(audit_rows=[], classes=[], distribution_boxes=[])
    view = project_dashboard(DashboardRequest(page="overview"), payload, summary)
    assert "boxes" not in {m["key"] for m in view["metrics"]}
    assert view["notes"]

def test_projection_rejects_cross_dataset_source(source):
    payload, summary = source
    summary["dataset_id"] = "another-dataset"
    with pytest.raises(ValueError):
        project_dashboard(DashboardRequest(page="overview"), payload, summary)

# Real in-process upload/parsing/pipeline; no listening HTTP server or production issuer.
from tests.test_integrated_api import integrated_api, auth, upload_fixture_via_api
from tests.fixtures import make_zip

def test_projection_api_pins_analysis_identity_and_never_analyzes_on_read(integrated_api, tmp_path):
    client, app, owners = integrated_api
    archive = tmp_path / "dashboard.zip"
    make_zip(archive)
    session = upload_fixture_via_api(client, owners, archive.read_bytes())
    headers = auth(owners, session)
    lifecycle = client.post(f"/api/v1/uploads/{session['upload_id']}/validate", headers=headers).json()
    dataset = lifecycle["dataset_id"]
    url = f"/api/v1/datasets/{dataset}/dashboard"
    selection = {"page": "overview", "expected_fingerprint": "a" * 64}
    assert client.post(url, json=selection, headers=headers).status_code == 409
    analysis = client.post(f"/api/v1/datasets/{dataset}/analyze", json={}, headers=headers).json()
    selection["expected_fingerprint"] = analysis["request_fingerprint"]
    response = client.post(url, json=selection, headers=headers)
    assert response.status_code == 200
    assert response.json()["source"]["request_fingerprint"] == selection["expected_fingerprint"]
    assert response.json()["source"]["result_id"] is None
    assert response.headers["cache-control"] == "no-store"
    before = client.get(f"/api/v1/datasets/{dataset}/results",headers=headers).json()
    for page in ("overview","quality","labels","distribution","review","reports","coverage","scene-finder"):
        response = client.post(url,json={**selection,"page":page},headers=headers)
        assert response.status_code == 200, (page,response.text)
        assert response.json()["dataset_id"] == dataset
        assert response.json()["page"] == page
    assert client.get(f"/api/v1/datasets/{dataset}/results",headers=headers).json() == before
    assert client.get(f"/api/v1/datasets/{dataset}/preview?image_path=flat.png",headers=headers).status_code == 200
    cors=client.options(url,headers={"Origin":"http://127.0.0.1:5173","Access-Control-Request-Method":"POST","Access-Control-Request-Headers":"Content-Type,Upload-Capability"})
    assert cors.status_code == 200
    assert client.post(url, json=selection).status_code == 403
    selection["expected_fingerprint"] = "b" * 64
    assert client.post(url, json=selection, headers=headers).json()["error"]["code"] == "RESULT_CHANGED"

@pytest.mark.parametrize("page", ["quality", "labels"])
def test_quality_and_labels_obey_capability(source, page):
    payload, summary = source
    summary["capabilities"].update(image_quality=False, label_audit=False)
    response = project_dashboard(DashboardRequest(page=page), payload, summary)
    assert response["state"] == "unsupported"
    assert response["metrics"] == []

def test_quality_server_aggregates_and_nullable_signals(source):
    payload, summary = source
    response = project_dashboard(DashboardRequest(page="quality"), payload, summary)
    metrics = {r["key"]: r["value"] for r in response["metrics"]}
    assert metrics["mean_score"] == 67.5
    assert metrics["REVIEW"] == 2
    assert response["tables"][0]["rows"] == payload["quality_rows"]
    assert response["notes"]  # No fabricated producer thresholds for old cache.

def test_quality_empty_filter_is_not_a_zero_mean(source):
    payload, summary = source
    response = project_dashboard(DashboardRequest(page="quality", query={"quality_status": "ERROR"}), payload, summary)
    assert response["state"] == "empty"
    assert next(r["value"] for r in response["metrics"] if r["key"] == "mean_score") is None

def test_label_filter_scopes_rows_aggregates_and_chart_together(source):
    payload, summary = source
    label = payload["audit_rows"][0]["label"]
    response = project_dashboard(DashboardRequest(page="labels", query={"class_label": label}), payload, summary)
    rows = response["tables"][0]["rows"]
    assert all(r["label"] == label for r in rows)
    assert next(r["value"] for r in response["metrics"] if r["key"] == "boxes") == len(rows)
    response = project_dashboard(DashboardRequest(page="labels", query={"class_label": "absent"}), payload, summary)
    assert response["state"] == "empty"
    assert response["tables"][0]["rows"] == []

def test_distribution_returns_original_classes_and_backend_thresholds(source):
    payload, summary = source
    response = project_dashboard(DashboardRequest(page="distribution"), payload, summary)
    assert response["tables"][0]["rows"] == payload["classes"]
    assert {p["key"]: p["value"] for p in response["parameters"]} == {"min_count":30,"min_share":1,"max_share":60}
    response = project_dashboard(DashboardRequest(page="distribution", query={"min_count":0,"min_share":0,"max_share":100}), payload, summary)
    assert next(t["rows"] for t in response["tables"] if t["key"] == "imbalance") == []

def test_reversed_distribution_thresholds_rejected():
    with pytest.raises(ValueError):
        DashboardRequest(page="distribution", query={"min_share":70,"max_share":60})

def test_review_paths_are_from_backend_image_inventory(source):
    from backend.dashboard_projection import canonical_image_path
    payload, summary = source
    response = project_dashboard(DashboardRequest(page="review"), payload, summary)
    assert response["tables"][0]["rows"][0]["image_path"] == payload["quality_rows"][0]["filename"]
    assert canonical_image_path("flat.png", ["images/flat.png"]) == "images/flat.png"
    assert canonical_image_path("flat.png", ["a/flat.png","b/flat.png"]) is None

def test_reports_class_review_summary_is_backend_owned(source):
    payload, summary = source
    response = project_dashboard(DashboardRequest(page="reports"), payload, summary)
    groups = next(t["rows"] for t in response["tables"] if t["key"] == "classes")
    for group in groups:
        rows = [r for r in payload["audit_rows"] if r["label"] == group["label"]]
        assert group["boxes"] == len(rows)
        assert group["reviews"] == sum(r["status"].startswith("REVIEW") for r in rows)
    assert all(t["export_filename"] for t in response["tables"])

def test_coverage_requires_explicit_targets_and_does_not_fill_missing_data(source):
    payload, summary = source
    payload["distribution_images"] = [{"image_id":"1","image_name":"x.jpg","weather":"unknown","timeofday":"day"}]
    payload["distribution_boxes"] = [{"image_id":"1","image_name":"x.jpg","box_id":"1:0","label":"car","occluded":"0","valid_geometry":True,"area_pct":1,"attributes":{}}]
    request = DashboardRequest(page="coverage")
    response = project_dashboard(request,payload,summary)
    assert response["state"] == "no_results"
    assert response["metrics"] == []
    assert not any(t["key"] == "recommendations" for t in response["tables"])
    request = DashboardRequest(page="coverage",query={"targets":[{"filters":{"class":"car"},"target":3,"unit":"images","weight":2}]})
    response = project_dashboard(request,payload,summary)
    evaluation = next(t["rows"] for t in response["tables"] if t["key"] == "evaluation")
    assert evaluation[0]["support"] == 1
    assert evaluation[0]["gap"] == 2
    assert evaluation[0]["weighted_gap"] == 1.3333

@pytest.mark.parametrize("target", [{"filters":{},"target":1},{"filters":{"bad":"x"},"target":1},{"filters":{"class":"car"},"target":0},{"filters":{"class":"car"},"target":1,"weight":0}])
def test_coverage_rejects_invalid_user_targets(target):
    with pytest.raises(ValueError): DashboardRequest(page="coverage", query={"targets":[target]})

def test_scene_counts_and_metadata_are_backend_owned(source):
    payload, summary = source
    payload["distribution_images"] = [{"image_id":"1","image_name":"night.jpg","weather":"unknown","timeofday":"night"},
        {"image_id":"2","image_name":"day.jpg","weather":"rain","timeofday":"day"}]
    response = project_dashboard(DashboardRequest(page="scene-finder",query={"weather":"rain"}),payload,summary)
    counts = {m["key"]:m["value"] for m in response["metrics"]}
    assert counts == {"matched":1,"total":2,"unresolved":1}
    assert response["tables"][0]["rows"][0]["image_name"] == "day.jpg"
    assert response["tables"][0]["rows"][0]["image_path"] is None

@pytest.mark.parametrize("page", ["coverage","scene-finder","distribution","labels"])
def test_annotation_capabilities_are_not_inferred_from_ui(source,page):
    payload,summary=source
    summary["annotations"]=False
    summary["capabilities"]={k:False for k in summary["capabilities"]}
    response=project_dashboard(DashboardRequest(page=page),payload,summary)
    assert response["state"] == "unsupported"
    assert response["tables"] == []

from tests.test_analytics_routes import store, app_with_store, client_with_grant, make_provenance
from backend.analytics_configuration import get_default_effective_config

def test_immutable_projection_uses_result_scope_and_payload_hash(client_with_grant, store, source):
    client, headers = client_with_grant
    payload, _ = source
    payload["dataset_id"] = "test_ds_001"
    provenance = make_provenance("result_20261017_001", "test_ds_001", payload)
    store.publish(provenance, payload, get_default_effective_config().model_dump())
    url = "/analytics/v1/datasets/test_ds_001/results/result_20261017_001/dashboard"
    request = {"page":"overview", "expected_payload_sha256":provenance.payload_sha256}
    response = client.post(url,json=request,headers=headers)
    assert response.status_code == 200
    assert response.json()["source"]["result_id"] == provenance.result_id
    assert response.json()["source"]["payload_sha256"] == provenance.payload_sha256
    assert client.post(url,json={**request,"expected_payload_sha256":"c"*64},headers=headers).status_code == 409
    assert client.post(url.replace("result_20261017_001","unauthorized-result"),json=request,headers=headers).status_code == 403
