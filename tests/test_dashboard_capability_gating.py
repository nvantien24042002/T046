"""Disabled capability products stay absent despite available raw analysis rows."""
import pytest

from backend.dashboard_projection import DashboardRequest, project_dashboard
from tests.test_dashboard_projection import source


@pytest.mark.parametrize("page", ["overview", "review", "reports"])
@pytest.mark.parametrize("disabled", ["image_quality", "label_audit", "distribution", "coverage", "scene_finder"])
def test_individual_disabled_capability_never_supplies_its_products(source, page, disabled):
    payload, summary = source
    summary["capabilities"][disabled] = False
    view = project_dashboard(DashboardRequest(page=page), payload, summary)
    metrics = {item["key"] for item in view["metrics"]}
    tables = {item["key"] for item in view["tables"]}
    charts = {item["key"] for item in view["charts"]}
    if disabled == "image_quality":
        assert not metrics & {"analyzed_images", "review_images"}
        assert not tables & {"quality", "preview_images"}
        assert "statuses" not in charts
        if page == "review":
            assert all(row["image_path"] is None for table in view["tables"] for row in table["rows"])
    if disabled == "label_audit":
        assert "review_boxes" not in metrics and "priorities" not in charts
        assert "audit" not in tables
        if page == "reports": assert "classes" not in tables
    if disabled == "distribution":
        assert not metrics & {"boxes", "classes"}
        if page == "overview": assert "classes" not in tables
        if page == "reports": assert "boxes" not in tables


@pytest.mark.parametrize("page", ["overview", "review", "reports"])
@pytest.mark.parametrize("missing", [False, True])
def test_disabled_or_missing_flags_fail_closed_without_raw_fallback(source, page, missing):
    payload, summary = source
    summary["capabilities"] = {} if missing else {key: False for key in summary["capabilities"]}
    view = project_dashboard(DashboardRequest(page=page), payload, summary)
    assert view["charts"] == [] and view["tables"] == []
    if page == "overview":
        assert view["metrics"] == [{"key": "images", "label": "Ảnh trong archive", "value": summary["images"]}]
    else:
        assert view["state"] == "unsupported" and view["metrics"] == []


def test_report_distribution_is_independent_of_label_audit(source):
    payload, summary = source
    summary["capabilities"].update(image_quality=False, label_audit=False)
    view = project_dashboard(DashboardRequest(page="reports"), payload, summary)
    assert [table["key"] for table in view["tables"]] == ["boxes"]


from backend.integrated_api import make_dataset_summary


def test_make_dataset_summary_derivation():
    # 1. Full dataset: images > 0 and annotations == True
    m_full = {"archive_bytes": 1000, "archive_sha256": "a" * 64, "entries": 5, "images": 2, "annotations": True}
    s_full = make_dataset_summary("ds-1", "up-1", m_full)
    assert s_full["capabilities"] == {
        "image_quality": True,
        "label_audit": True,
        "distribution": True,
        "coverage": True,
        "scene_finder": True,
    }

    # 2. Image-only dataset: images > 0 and annotations == False
    m_img = {"archive_bytes": 500, "archive_sha256": "b" * 64, "entries": 3, "images": 2, "annotations": False}
    s_img = make_dataset_summary("ds-2", "up-2", m_img)
    assert s_img["capabilities"] == {
        "image_quality": True,
        "label_audit": False,
        "distribution": False,
        "coverage": False,
        "scene_finder": False,
    }

    # 3. No images, no annotations
    m_none = {"archive_bytes": 100, "archive_sha256": "c" * 64, "entries": 1, "images": 0, "annotations": False}
    s_none = make_dataset_summary("ds-3", "up-3", m_none)
    assert s_none["capabilities"] == {
        "image_quality": False,
        "label_audit": False,
        "distribution": False,
        "coverage": False,
        "scene_finder": False,
    }

    # 4. Explicit capabilities override preserved
    m_custom = {"images": 2, "annotations": True, "capabilities": {"image_quality": False, "label_audit": True}}
    s_custom = make_dataset_summary("ds-4", "up-4", m_custom)
    assert s_custom["capabilities"] == {"image_quality": False, "label_audit": True}


def test_projection_full_capabilities_ready_states(source):
    payload, summary = source
    payload["distribution_images"] = [{"image_id": "0", "image_name": "flat.png", "weather": "clear", "timeofday": "day"}]
    for page in ("overview", "quality", "labels", "distribution", "review", "reports", "scene-finder"):
        view = project_dashboard(DashboardRequest(page=page), payload, summary)
        assert view["state"] == "ready", f"Expected ready for page {page}, got {view['state']}"
    # Coverage without targets returns no_results (awaiting user targets), not unsupported
    cov = project_dashboard(DashboardRequest(page="coverage"), payload, summary)
    assert cov["state"] == "no_results"


def test_projection_image_only_gating(source):
    payload, summary = source
    summary["annotations"] = False
    summary["capabilities"] = {
        "image_quality": True,
        "label_audit": False,
        "distribution": False,
        "coverage": False,
        "scene_finder": False,
    }
    # Quality is supported
    q_view = project_dashboard(DashboardRequest(page="quality"), payload, summary)
    assert q_view["state"] == "ready"

    # Annotation-dependent pages are unsupported
    for page in ("labels", "distribution", "coverage", "scene-finder"):
        view = project_dashboard(DashboardRequest(page=page), payload, summary)
        assert view["state"] == "unsupported", f"Expected unsupported for page {page}"

    # Overview is ready with image-only notes and no box metrics
    ov_view = project_dashboard(DashboardRequest(page="overview"), payload, summary)
    assert ov_view["state"] == "ready"
    assert "boxes" not in {m["key"] for m in ov_view["metrics"]}


# Real in-process upload/parsing/pipeline API tests for capability consistency
from tests.test_integrated_api import integrated_api, auth, upload_fixture_via_api
from tests.fixtures import make_zip


def test_api_summary_and_dashboard_full_dataset_consistency(integrated_api, tmp_path):
    client, app, owners = integrated_api
    archive = tmp_path / "full_dataset.zip"
    make_zip(archive)
    session = upload_fixture_via_api(client, owners, archive.read_bytes())
    headers = auth(owners, session)
    lifecycle = client.post(f"/api/v1/uploads/{session['upload_id']}/validate", headers=headers).json()
    dataset = lifecycle["dataset_id"]

    # Verify summary reports all capabilities true
    summary = client.get(f"/api/v1/datasets/{dataset}/summary", headers=headers).json()
    assert summary["ready"] is True
    assert summary["capabilities"]["image_quality"] is True
    assert summary["capabilities"]["label_audit"] is True
    assert summary["capabilities"]["distribution"] is True
    assert summary["capabilities"]["coverage"] is True
    assert summary["capabilities"]["scene_finder"] is True

    # Run analysis
    analysis = client.post(f"/api/v1/datasets/{dataset}/analyze", json={}, headers=headers).json()
    fingerprint = analysis["request_fingerprint"]

    # Direct dashboard projection across pages must return ready (consistent with summary capabilities)
    for page in ("overview", "quality", "labels", "distribution", "review", "reports", "scene-finder"):
        res = client.post(f"/api/v1/datasets/{dataset}/dashboard", json={"page": page, "expected_fingerprint": fingerprint}, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["state"] == "ready", f"API projection for page {page} returned state {data.get('state')} with reason {data.get('reason')}"


def test_api_summary_and_dashboard_image_only_gating(integrated_api, tmp_path):
    client, app, owners = integrated_api
    archive = tmp_path / "image_only.zip"
    make_zip(archive, image_only=True)
    session = upload_fixture_via_api(client, owners, archive.read_bytes())
    headers = auth(owners, session)
    lifecycle = client.post(f"/api/v1/uploads/{session['upload_id']}/validate", headers=headers).json()
    dataset = lifecycle["dataset_id"]

    # Verify summary reports image_quality=True and annotation capabilities=False
    summary = client.get(f"/api/v1/datasets/{dataset}/summary", headers=headers).json()
    assert summary["ready"] is True
    assert summary["capabilities"]["image_quality"] is True
    assert summary["capabilities"]["label_audit"] is False
    assert summary["capabilities"]["distribution"] is False
    assert summary["capabilities"]["coverage"] is False
    assert summary["capabilities"]["scene_finder"] is False

    # Run analysis
    analysis = client.post(f"/api/v1/datasets/{dataset}/analyze", json={}, headers=headers).json()
    fingerprint = analysis["request_fingerprint"]

    # Quality is ready
    res_q = client.post(f"/api/v1/datasets/{dataset}/dashboard", json={"page": "quality", "expected_fingerprint": fingerprint}, headers=headers)
    assert res_q.status_code == 200
    assert res_q.json()["state"] == "ready"

    # Labels and Distribution are unsupported
    for page in ("labels", "distribution", "coverage", "scene-finder"):
        res = client.post(f"/api/v1/datasets/{dataset}/dashboard", json={"page": page, "expected_fingerprint": fingerprint}, headers=headers)
        assert res.status_code == 200
        assert res.json()["state"] == "unsupported"


import json


def test_api_dashboard_fingerprint_validation_and_fail_closed(integrated_api, tmp_path):
    client, app, owners = integrated_api
    archive = tmp_path / "fingerprint_test.zip"
    make_zip(archive)
    session = upload_fixture_via_api(client, owners, archive.read_bytes())
    headers = auth(owners, session)
    lifecycle = client.post(f"/api/v1/uploads/{session['upload_id']}/validate", headers=headers).json()
    dataset = lifecycle["dataset_id"]

    # 1. Dataset not analyzed yet: 409 ANALYSIS_NOT_RUN
    unrun = client.post(
        f"/api/v1/datasets/{dataset}/dashboard",
        json={"page": "overview", "expected_fingerprint": "a" * 64},
        headers=headers,
    )
    assert unrun.status_code == 409
    assert unrun.json()["error"]["code"] == "ANALYSIS_NOT_RUN"

    # Analyze
    analysis = client.post(f"/api/v1/datasets/{dataset}/analyze", json={}, headers=headers).json()
    valid_fingerprint = analysis["request_fingerprint"]
    assert valid_fingerprint is not None

    # 2. Valid fingerprint: 200 OK
    ok_res = client.post(
        f"/api/v1/datasets/{dataset}/dashboard",
        json={"page": "overview", "expected_fingerprint": valid_fingerprint},
        headers=headers,
    )
    assert ok_res.status_code == 200
    assert ok_res.json()["state"] == "ready"

    # 3. Missing fingerprint in request: 422 ANALYSIS_IDENTITY_REQUIRED
    missing_res = client.post(
        f"/api/v1/datasets/{dataset}/dashboard",
        json={"page": "overview"},
        headers=headers,
    )
    assert missing_res.status_code == 422
    assert missing_res.json()["error"]["code"] == "ANALYSIS_IDENTITY_REQUIRED"

    # 4. Fingerprint mismatch (tampered or wrong request): 409 RESULT_CHANGED
    mismatch_res = client.post(
        f"/api/v1/datasets/{dataset}/dashboard",
        json={"page": "overview", "expected_fingerprint": "f" * 64},
        headers=headers,
    )
    assert mismatch_res.status_code == 409
    assert mismatch_res.json()["error"]["code"] == "RESULT_CHANGED"

    # 5. Legacy cache missing fingerprint on disk: 409 RESULT_CHANGED (fail-closed)
    cache_file = app.state.cache_dir / f"{dataset}.json"
    cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
    del cache_data["request_fingerprint"]
    cache_file.write_text(json.dumps(cache_data), encoding="utf-8")

    legacy_res = client.post(
        f"/api/v1/datasets/{dataset}/dashboard",
        json={"page": "overview", "expected_fingerprint": valid_fingerprint},
        headers=headers,
    )
    assert legacy_res.status_code == 409
    assert legacy_res.json()["error"]["code"] == "RESULT_CHANGED"


def test_dashboard_projection_state_differentiation(source):
    payload, summary = source
    # 1. State 'ready': normal valid data
    ready_view = project_dashboard(DashboardRequest(page="quality"), payload, summary)
    assert ready_view["state"] == "ready"
    assert len(ready_view["metrics"]) > 0

    # 2. State 'empty': filter matches zero rows
    empty_req = DashboardRequest(page="quality", query={"quality_status": "ERROR"})
    empty_view = project_dashboard(empty_req, payload, summary)
    assert empty_view["state"] == "empty"
    assert "Không có ảnh khớp bộ lọc" in empty_view["reason"]

    # 3. State 'no_results': coverage with images but without user targets
    payload_with_images = {**payload, "distribution_images": [{"image_id": "0", "image_name": "flat.png", "weather": "clear", "timeofday": "day"}]}
    cov_view = project_dashboard(DashboardRequest(page="coverage"), payload_with_images, summary)
    assert cov_view["state"] == "no_results"
    assert "Chưa có targets do người dùng cung cấp" in cov_view["reason"]

    # 4. State 'empty' on coverage: when no image records exist in XML
    payload_no_images = {**payload, "distribution_images": []}
    cov_empty = project_dashboard(DashboardRequest(page="coverage"), payload_no_images, summary)
    assert cov_empty["state"] == "empty"
    assert "Không có image record" in cov_empty["reason"]

    # 5. State 'unsupported': page requires disabled capability
    summary_no_labels = {**summary, "capabilities": {**summary["capabilities"], "label_audit": False}}
    unsupported_view = project_dashboard(DashboardRequest(page="labels"), payload, summary_no_labels)
    assert unsupported_view["state"] == "unsupported"
    assert "Backend không hỗ trợ dữ liệu này" in unsupported_view["reason"]


