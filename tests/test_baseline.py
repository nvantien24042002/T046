import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from tests.baseline.capture import capture, verify_sources
from tests.fixtures import ROOT, legacy_functions, make_zip


def test_original_sources_unchanged():
    verify_sources()


def test_captured_quality_label_distribution_coverage_and_reports(tmp_path):
    expected = json.loads((ROOT / "tests/baseline/expected.json").read_text(encoding="utf-8"))
    actual = capture(make_zip(tmp_path / "fixture.zip"))
    # Same pinned runtime; numeric values serialized to 10 decimal places.
    assert actual == expected
    assert actual["resolver"] == ["images/flat.png", None]
    assert actual["quality"][0]["status"] == "REVIEW"
    assert actual["quality"][0]["score"] == 50
    assert "POSSIBLE_DUPLICATE" in actual["audit"][0]["geometry_issues"]
    assert actual["audit"][-1]["status"] == "A_ONLY"
    assert actual["coverage"][0]["support"] == 1
    assert actual["distribution_images"][0]["weather"] == "unknown"
    assert actual["distribution_images"][1]["timeofday"] == "missing"


def test_image_only_and_corrupt_image_existing_behavior(tmp_path):
    legacy = legacy_functions()
    data = make_zip(tmp_path / "images.zip", image_only=True).read_bytes()
    images, skipped = legacy.read_images_from_zip(data)
    assert len(images) == 2 and skipped == []
    import pytest
    with pytest.raises(ValueError, match="annotations.xml"):
        legacy.load_cvat(data)
    from tests.baseline.capture import IMAGE_CFG
    result = legacy.analyze_image(legacy.ZipImage("broken.png", b"broken"), IMAGE_CFG)
    assert result["status"] == "ERROR" and result["issues"] == ["READ_ERROR"]


def test_existing_dashboard_starts_without_upload():
    page = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    assert not page.exception
    assert len(page.get("file_uploader")) == 1
