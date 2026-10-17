"""Unit and integration tests for analytics_cache_adapter."""
import json
from pathlib import Path
import sqlite3

import pytest

from backend.analytics_cache_adapter import (
    CacheAdapterError,
    ProvenanceInput,
    adapt_cache_to_result_store,
    load_staging_cache_directory,
    validate_cache_payload,
    validate_result_id,
)
from backend.analytics_configuration import (
    CANONICALIZATION_V1,
    PRODUCTION_REGISTRY,
    config_id_v1,
    get_default_effective_config,
    payload_sha256_v1,
)
from backend.analytics_storage import ResultStore


@pytest.fixture
def store(tmp_path):
    return ResultStore(tmp_path / "results.db")


@pytest.fixture
def sample_payload_data():
    return {
        "dataset_id": "test_ds_001",
        "quality_rows": [
            {
                "filename": "img1.jpg",
                "status": "PASS",
                "score": 95.0,
                "issues": [],
                "width": 640,
                "height": 480,
                "size_kb": 120.5,
                "blur_score": 1500.0,
                "brightness": 128.0,
                "contrast": 50.0,
                "noise_score": 0.5,
                "black_pct": 1.0,
                "white_pct": 0.0,
                "error": "",
            }
        ],
        "audit_rows": [],
        "classes": [
            {
                "class": "car",
                "boxes": 10,
                "images": 5,
                "share_pct": 100.0,
            }
        ],
        "attribute_distribution": [],
        "distribution_images": [
            {
                "image_id": "img1",
                "image_name": "img1.jpg",
                "weather": "sunny",
                "timeofday": "day",
            }
        ],
        "distribution_boxes": [],
        "attributes": [],
    }


@pytest.fixture
def sample_manifest_data():
    return {
        "dataset_id": "test_ds_001",
        "archive_sha256": "a" * 64,
        "archive_bytes": 1024,
        "images": 1,
        "annotations": True,
    }


def test_validate_result_id():
    assert validate_result_id("valid_id-123") == "valid_id-123"
    with pytest.raises(CacheAdapterError, match="Invalid result_id"):
        validate_result_id("")
    with pytest.raises(CacheAdapterError, match="Invalid result_id"):
        validate_result_id("invalid/chars")
    with pytest.raises(CacheAdapterError, match="Invalid result_id"):
        validate_result_id("has spaces")


def test_validate_cache_payload_success(sample_payload_data):
    validated = validate_cache_payload(sample_payload_data, "test_ds_001")
    assert validated["dataset_id"] == "test_ds_001"
    assert len(validated["quality_rows"]) == 1
    assert len(validated["classes"]) == 1


def test_validate_cache_payload_dataset_mismatch(sample_payload_data):
    with pytest.raises(CacheAdapterError, match="Dataset ID mismatch in cache"):
        validate_cache_payload(sample_payload_data, "other_ds")


def test_validate_cache_payload_malformed():
    with pytest.raises(CacheAdapterError, match="Payload schema validation failed"):
        validate_cache_payload({"dataset_id": "ds", "quality_rows": []}, "ds")


def test_adapt_cache_to_result_store_success(tmp_path, store, sample_payload_data, sample_manifest_data):
    cache_file = tmp_path / "test_ds_001.json"
    cache_file.write_text(json.dumps(sample_payload_data), encoding="utf-8")

    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(sample_manifest_data), encoding="utf-8")

    prov_in = ProvenanceInput(
        result_id="res_001",
        producer_build_id="build_v1",
        producer_commit="c0ffee",
        producer_timestamp="2026-10-16T12:00:00Z",
    )

    desc, payload = adapt_cache_to_result_store(
        cache_file,
        manifest_file,
        store,
        dataset_id="test_ds_001",
        result_id="res_001",
        expected_archive_sha256="a" * 64,
        provenance=prov_in,
        published_at="2026-10-17T10:00:00Z",
    )

    assert desc.result_id == "res_001"
    assert desc.dataset_id == "test_ds_001"
    assert desc.archive_sha256 == "a" * 64
    assert desc.verification == "verified"
    assert desc.producer_build_id == "build_v1"
    assert payload["dataset_id"] == "test_ds_001"

    # Verify descriptor conforms to PRODUCTION_REGISTRY
    validated = PRODUCTION_REGISTRY.validate(desc)
    assert validated.result_id == "res_001"

    # Deterministic idempotent re-run
    desc2, payload2 = adapt_cache_to_result_store(
        cache_file,
        manifest_file,
        store,
        dataset_id="test_ds_001",
        result_id="res_001",
        expected_archive_sha256="a" * 64,
    )
    assert desc2.result_id == "res_001"
    assert desc2.payload_sha256 == desc.payload_sha256


def test_adapt_cache_requires_explicit_result_id(tmp_path, store, sample_payload_data, sample_manifest_data):
    cache_file = tmp_path / "test_ds_001.json"
    cache_file.write_text(json.dumps(sample_payload_data), encoding="utf-8")
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(sample_manifest_data), encoding="utf-8")

    with pytest.raises(CacheAdapterError, match="Invalid result_id"):
        adapt_cache_to_result_store(
            cache_file,
            manifest_file,
            store,
            dataset_id="test_ds_001",
            result_id="",
        )


def test_adapt_cache_filename_mismatch(tmp_path, store, sample_payload_data, sample_manifest_data):
    cache_file = tmp_path / "other_filename.json"
    cache_file.write_text(json.dumps(sample_payload_data), encoding="utf-8")
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(sample_manifest_data), encoding="utf-8")

    with pytest.raises(CacheAdapterError, match="Cache filename 'other_filename.json' does not match"):
        adapt_cache_to_result_store(
            cache_file,
            manifest_file,
            store,
            dataset_id="test_ds_001",
            result_id="res_001",
        )


def test_adapt_cache_archive_sha_mismatch(tmp_path, store, sample_payload_data, sample_manifest_data):
    cache_file = tmp_path / "test_ds_001.json"
    cache_file.write_text(json.dumps(sample_payload_data), encoding="utf-8")
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(sample_manifest_data), encoding="utf-8")

    with pytest.raises(CacheAdapterError, match="Manifest archive_sha256 .* does not match registry"):
        adapt_cache_to_result_store(
            cache_file,
            manifest_file,
            store,
            dataset_id="test_ds_001",
            result_id="res_001",
            expected_archive_sha256="b" * 64,
        )


def test_adapt_cache_duplicate_conflict(tmp_path, store, sample_payload_data, sample_manifest_data):
    cache_file = tmp_path / "test_ds_001.json"
    cache_file.write_text(json.dumps(sample_payload_data), encoding="utf-8")
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(sample_manifest_data), encoding="utf-8")

    adapt_cache_to_result_store(
        cache_file,
        manifest_file,
        store,
        dataset_id="test_ds_001",
        result_id="res_001",
    )

    # Attempt to re-adapt with different payload content for same result_id
    tampered_payload = dict(sample_payload_data)
    tampered_payload["quality_rows"][0]["score"] = 50.0
    cache_file.write_text(json.dumps(tampered_payload), encoding="utf-8")

    with pytest.raises(CacheAdapterError, match="already published with different content"):
        adapt_cache_to_result_store(
            cache_file,
            manifest_file,
            store,
            dataset_id="test_ds_001",
            result_id="res_001",
        )


def test_load_staging_cache_directory_requires_result_id_map(tmp_path, store):
    with pytest.raises(CacheAdapterError, match="Explicit result_id_map is required"):
        load_staging_cache_directory(tmp_path, store, result_id_map={})


def test_load_staging_cache_directory_success(tmp_path, store, sample_payload_data, sample_manifest_data):
    staging_root = tmp_path / "staging_data"
    staging_root.mkdir()
    cache_dir = staging_root / ".analysis-cache-v1"
    cache_dir.mkdir()
    sealed_dir = staging_root / ".datasets-v1" / "sealed" / "att_001"
    sealed_dir.mkdir(parents=True)

    # Write cache and manifest
    (cache_dir / "test_ds_001.json").write_text(json.dumps(sample_payload_data), encoding="utf-8")
    (sealed_dir / "manifest.json").write_text(json.dumps(sample_manifest_data), encoding="utf-8")

    # Create mock registry.sqlite3
    conn = sqlite3.connect(staging_root / "registry.sqlite3")
    conn.execute("""
        CREATE TABLE validation_datasets (
            dataset_id TEXT PRIMARY KEY,
            attempt_id TEXT,
            state TEXT,
            payload_sha TEXT
        )
    """)
    conn.execute(
        "INSERT INTO validation_datasets VALUES (?, ?, ?, ?)",
        ("test_ds_001", "att_001", "ready", "a" * 64),
    )
    conn.commit()
    conn.close()

    results = load_staging_cache_directory(
        staging_root,
        store,
        result_id_map={"test_ds_001": "explicit_result_01"},
    )
    assert results == ["explicit_result_01"]

    desc, payload = store.get_result("test_ds_001", "explicit_result_01")
    assert desc.result_id == "explicit_result_01"
    assert desc.dataset_id == "test_ds_001"
    assert desc.archive_sha256 == "a" * 64


def test_staging_real_cache_compatibility_with_explicit_map(tmp_path, store):
    """Test reading staging upload root directly with explicit result_id_map."""
    staging_root = Path(r"D:\CVATPrivateUploads\stage3-data")
    if not (staging_root / "registry.sqlite3").is_file():
        pytest.skip("Staging root not available on this host")

    mapping = {
        "43396cbb-31ff-4afd-a6aa-1301c9797f29": "staging_result_43396cbb",
        "125d2cbe-6be1-4c3e-a3d7-5e541117a815": "staging_result_125d2cbe",
        "38eb472c-1faf-42af-bb84-879d8209890f": "staging_result_38eb472c",
    }

    results = load_staging_cache_directory(staging_root, store, result_id_map=mapping)
    assert len(results) == 3
    assert set(results) == set(mapping.values())

    for ds_id, res_id in mapping.items():
        desc, payload = store.get_result(ds_id, res_id)
        assert desc.dataset_id == ds_id
        assert desc.result_id == res_id
        assert desc.verification == "verified"
        assert payload["dataset_id"] == ds_id
        validated_desc = PRODUCTION_REGISTRY.validate(desc)
        assert validated_desc.result_id == res_id

    # Repeat load: verify deterministic idempotency and zero duplicate rows
    results_repeat = load_staging_cache_directory(staging_root, store, result_id_map=mapping)
    assert results_repeat == results

    conn = sqlite3.connect(store.db_path)
    prov_count = conn.execute("SELECT COUNT(*) FROM provenance").fetchone()[0]
    payload_count = conn.execute("SELECT COUNT(*) FROM payloads").fetchone()[0]
    conn.close()
    assert prov_count == 3
    assert payload_count == 3


def test_staging_tamper_rejection_in_temporary_storage(tmp_path, store):
    """Verify tamper rejection in isolated temporary storage without mutating source datasets."""
    staging_root = Path(r"D:\CVATPrivateUploads\stage3-data")
    if not (staging_root / "registry.sqlite3").is_file():
        pytest.skip("Staging root not available on this host")

    ds_id = "125d2cbe-6be1-4c3e-a3d7-5e541117a815"
    conn = sqlite3.connect(staging_root / "registry.sqlite3")
    attempt_id, payload_sha = conn.execute(
        "SELECT attempt_id, payload_sha FROM validation_datasets WHERE dataset_id=?", (ds_id,)
    ).fetchone()
    conn.close()

    src_cache = staging_root / ".analysis-cache-v1" / f"{ds_id}.json"
    src_manifest = staging_root / ".datasets-v1" / "sealed" / attempt_id / "manifest.json"

    # Make copies in tmp_path
    tmp_cache = tmp_path / f"{ds_id}.json"
    tmp_cache.write_text(src_cache.read_text(encoding="utf-8"), encoding="utf-8")
    tmp_manifest = tmp_path / "manifest.json"
    tmp_manifest.write_text(src_manifest.read_text(encoding="utf-8"), encoding="utf-8")

    # 1. Successful first ingestion
    desc1, _ = adapt_cache_to_result_store(
        tmp_cache,
        tmp_manifest,
        store,
        dataset_id=ds_id,
        result_id="test_tamper_res_01",
        expected_archive_sha256=payload_sha,
    )
    assert desc1.result_id == "test_tamper_res_01"

    # 2. Tampered payload: alter a score
    tampered_data = json.loads(tmp_cache.read_text(encoding="utf-8"))
    tampered_data["quality_rows"][0]["score"] = 0.0
    tmp_cache.write_text(json.dumps(tampered_data), encoding="utf-8")

    with pytest.raises(CacheAdapterError, match="already published with different content"):
        adapt_cache_to_result_store(
            tmp_cache,
            tmp_manifest,
            store,
            dataset_id=ds_id,
            result_id="test_tamper_res_01",
            expected_archive_sha256=payload_sha,
        )

    # 3. Tampered manifest archive_sha256: must fail closed against expected registry SHA
    tmp_manifest.write_text(json.dumps({
        "dataset_id": ds_id,
        "archive_sha256": "f" * 64,
    }), encoding="utf-8")

    with pytest.raises(CacheAdapterError, match="does not match registry payload_sha"):
        adapt_cache_to_result_store(
            tmp_cache,
            tmp_manifest,
            store,
            dataset_id=ds_id,
            result_id="test_tamper_res_02",
            expected_archive_sha256=payload_sha,
        )

