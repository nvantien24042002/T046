import hashlib
import json
from pathlib import Path
import secrets
import uuid

import numpy as np
import pandas as pd
from PIL import Image
import pytest

from core.coverage_dynamic import evaluate_targets
from core.cvat_parser import load_cvat_from_index, parse_distribution_from_index
from core.image_quality import analyze_image, estimate_noise, run_image_quality
from core.label_audit import run_label_audit
from core.pipeline import (
    execute_pipeline,
    get_preview_image,
    get_annotated_preview_image,
    read_single_image,
)
from datasets.limits import Limits
from datasets.manager import DatasetManager
from storage.config import UploadConfig
from storage.uploads import UploadStore
from tests.baseline.capture import IMAGE_CFG, LABEL_CFG
from tests.fixtures import ROOT, make_zip


@pytest.fixture
def make_store(tmp_path):
    def _create(max_bytes=200000, quota_bytes=10**9):
        root = tmp_path / f"store_{uuid.uuid4().hex[:8]}"
        config = UploadConfig(
            root,
            secrets.token_urlsafe(32),
            max_bytes=max_bytes,
            chunk_bytes=32768,
            block_bytes=4096,
            min_free_bytes=0,
            quota_bytes=quota_bytes,
            idle_seconds=10,
            lifetime_seconds=60,
        )
        return UploadStore(config, clock=lambda: 100, free_bytes=lambda: 10**10)
    return _create


def upload_and_validate(store, image_only=False):
    archive_path = store.root.parent / f"fixture_{uuid.uuid4().hex[:8]}.zip"
    make_zip(archive_path, image_only=image_only)
    data = archive_path.read_bytes()
    session = store.create("alice", "fixture.zip", len(data), str(uuid.uuid4()))
    writer = store.begin("alice", session["upload_id"], session["upload_capability"], 0, 0, hashlib.sha256(data).hexdigest())
    writer.write(data)
    writer.finish()
    store.complete("alice", session["upload_id"], session["upload_capability"])
    archive_path.unlink()

    manager = DatasetManager(store, Limits())
    res = manager.validate("alice", session["upload_id"], session["upload_capability"])
    assert res["state"] == "ready"
    return manager, session


def test_pipeline_parity_with_baseline(make_store):
    store = make_store()
    try:
        manager, session = upload_and_validate(store)
        expected = json.loads((ROOT / "tests/baseline/expected.json").read_text(encoding="utf-8"))

        def records(frame: pd.DataFrame):
            return json.loads(frame.to_json(orient="records", double_precision=10))

        with manager.reader("alice", session["upload_id"], session["upload_capability"]) as reader:
            results = execute_pipeline(
                reader,
                image_cfg=IMAGE_CFG,
                label_cfg=LABEL_CFG,
            )

            # Compare cvat_images & cvat_boxes
            assert records(results["cvat_images"]) == expected["cvat_images"]
            assert records(results["cvat_boxes"]) == expected["cvat_boxes"]

            # Compare distribution images & boxes
            assert records(results["distribution_images"]) == expected["distribution_images"]
            assert records(results["distribution_boxes"]) == expected["distribution_boxes"]

            # Compare quality results
            assert records(results["quality"]) == expected["quality"]
            # Verify image_bytes is not retained in results
            assert "image_bytes" not in results["quality"].columns

            # Compare label audit results
            assert records(results["audit"]) == expected["audit"]

            # Compare class distribution
            assert records(results["classes"]) == expected["classes"]

            # Compare attribute distribution
            assert records(results["attribute_distribution"]) == expected["attributes"]

            # Compare dynamic coverage
            coverage = evaluate_targets(
                results["distribution_images"],
                results["distribution_boxes"],
                [
                    dict(name="cars", filters={"class": "car"}, target=2),
                    dict(name="rain", filters={"weather": "rain"}, target=2),
                ],
            )
            assert records(coverage) == expected["coverage"]
    finally:
        store.close()


def test_pipeline_image_only_dataset(make_store):
    store = make_store()
    try:
        manager, session = upload_and_validate(store, image_only=True)
        with manager.reader("alice", session["upload_id"], session["upload_capability"]) as reader:
            results = execute_pipeline(reader, image_cfg=IMAGE_CFG, label_cfg=LABEL_CFG)
            assert results["manifest"]["annotations"] is False
            assert len(results["quality"]) == 2
            assert results["audit"].empty
            assert results["cvat_images"].empty
            assert results["distribution_images"].empty
    finally:
        store.close()


def test_preview_and_annotated_preview(make_store):
    store = make_store()
    try:
        manager, session = upload_and_validate(store)
        with manager.reader("alice", session["upload_id"], session["upload_capability"]) as reader:
            # Single image raw read
            raw = read_single_image(reader["archive"], "images/flat.png")
            assert len(raw) > 0

            # Single image preview
            preview = get_preview_image(reader, "images/flat.png")
            assert isinstance(preview, Image.Image)
            assert preview.size == (64, 64)

            # Annotated preview
            boxes = [
                {"label": "car", "xtl": 8, "ytl": 8, "xbr": 40, "ybr": 40},
                {"label": "person", "xtl": 30, "ytl": 30, "xbr": 20, "ybr": 40},
            ]
            annotated = get_annotated_preview_image(reader, "images/flat.png", boxes)
            assert isinstance(annotated, np.ndarray)
            assert annotated.shape == (64, 64, 3)

            # Max size guard
            with pytest.raises(ValueError, match="exceeds maximum size limit"):
                read_single_image(reader["archive"], "images/flat.png", max_bytes=10)
    finally:
        store.close()


def test_image_quality_corrupt_image():
    # Corrupt image payload returns ERROR status with READ_ERROR issue
    res = analyze_image(b"corrupted_bytes", IMAGE_CFG, filename="corrupt.png")
    assert res["status"] == "ERROR"
    assert res["issues"] == ["READ_ERROR"]
    assert res["score"] == 0
    assert np.isnan(res["blur_score"])
