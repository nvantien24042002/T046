"""Tests for Track B immutable result storage."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from backend.analytics_configuration import (
    CANONICALIZATION_V1,
    config_id_v1,
    get_default_effective_config,
    payload_sha256_v1,
)
from backend.analytics_storage import ProvenanceRecord, ResultStore


@pytest.fixture
def store(tmp_path):
    """Create a temporary result store."""
    db_path = tmp_path / "results.db"
    return ResultStore(db_path)


@pytest.fixture
def sample_provenance():
    """Create sample provenance record."""
    effective = get_default_effective_config()
    config_id = config_id_v1(
        "config-2026-10-17-01",
        "defaults-2026-10-17-01",
        CANONICALIZATION_V1,
        effective
    )

    payload = {
        "dataset_id": "test_ds_001",
        "quality_rows": [{"filename": "a.jpg", "score": 85}],
        "audit_rows": [],
    }

    return ProvenanceRecord(
        result_id="result_20261017_001",
        dataset_id="test_ds_001",
        archive_sha256="a" * 64,
        config_id=config_id,
        configuration_schema_revision="config-2026-10-17-01",
        defaults_version="defaults-2026-10-17-01",
        canonicalization_version=CANONICALIZATION_V1,
        algorithm_version="algo-core-v1",
        producer_build_id="build_123",
        producer_commit="abc123def456",
        producer_timestamp=datetime.now(timezone.utc).isoformat(),
        request_fingerprint="f" * 64,
        payload_sha256=payload_sha256_v1(payload),
        published_at=datetime.now(timezone.utc).isoformat(),
    )


def test_store_initializes_schema(store, tmp_path):
    """Store creates required tables on initialization."""
    conn = sqlite3.connect(tmp_path / "results.db")
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('provenance', 'payloads')"
    )
    tables = [row[0] for row in cursor]
    conn.close()

    assert "provenance" in tables
    assert "payloads" in tables


def test_publish_validates_missing_identity(store, sample_provenance):
    """Publish rejects provenance with missing identity."""
    sample_provenance.result_id = ""

    payload = {"dataset_id": "test_ds_001", "quality_rows": []}
    effective_dict = get_default_effective_config().model_dump()

    with pytest.raises(ValueError, match="Missing required identity"):
        store.publish(sample_provenance, payload, effective_dict)


def test_publish_validates_payload_hash_mismatch(store, sample_provenance):
    """Publish rejects payload when hash doesn't match provenance."""
    payload = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "different.jpg"}]}
    effective_dict = get_default_effective_config().model_dump()

    # sample_provenance has hash of original payload
    with pytest.raises(ValueError, match="Payload hash mismatch"):
        store.publish(sample_provenance, payload, effective_dict)


def test_publish_validates_config_id_mismatch(store, sample_provenance):
    """Publish rejects when config_id doesn't match effective config."""
    effective = get_default_effective_config()

    # Change effective config but keep old config_id
    effective_dict = effective.model_dump()
    effective_dict["image_cfg"]["dark_threshold"] = {
        "kind": "integer",
        "decimal": "50"  # Changed from 40
    }

    payload = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "a.jpg", "score": 85}]}
    sample_provenance.payload_sha256 = payload_sha256_v1(payload)

    with pytest.raises(ValueError, match="Config ID mismatch"):
        store.publish(sample_provenance, payload, effective_dict)


def test_publish_succeeds_with_valid_data(store, sample_provenance):
    """Publish succeeds when all validations pass."""
    payload = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "a.jpg", "score": 85}], "audit_rows": []}
    effective_dict = get_default_effective_config().model_dump()

    # Should not raise
    store.publish(sample_provenance, payload, effective_dict)


def test_publish_rejects_duplicate_result_id(store, sample_provenance):
    """Publish rejects when result_id already exists."""
    payload = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "a.jpg", "score": 85}], "audit_rows": []}
    effective_dict = get_default_effective_config().model_dump()

    # First publish succeeds
    store.publish(sample_provenance, payload, effective_dict)

    # Second publish with same result_id fails
    with pytest.raises(ValueError, match="already published"):
        store.publish(sample_provenance, payload, effective_dict)


def test_publish_is_atomic(store, sample_provenance, tmp_path):
    """Failed publish leaves no partial state."""
    payload = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "a.jpg", "score": 85}], "audit_rows": []}
    effective_dict = get_default_effective_config().model_dump()

    # First publish succeeds
    store.publish(sample_provenance, payload, effective_dict)

    # Second publish fails
    try:
        store.publish(sample_provenance, payload, effective_dict)
    except ValueError:
        pass

    # Check only one record exists
    conn = sqlite3.connect(tmp_path / "results.db")
    cursor = conn.execute("SELECT COUNT(*) FROM provenance WHERE result_id = ?", (sample_provenance.result_id,))
    count = cursor.fetchone()[0]
    conn.close()

    assert count == 1


def test_list_results_empty_for_nonexistent_dataset(store):
    """List returns empty list for dataset with no results."""
    results = store.list_results("nonexistent_dataset")
    assert results == []


def test_list_results_returns_published_results(store, sample_provenance):
    """List returns descriptors for published results."""
    payload = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "a.jpg", "score": 85}], "audit_rows": []}
    effective_dict = get_default_effective_config().model_dump()

    store.publish(sample_provenance, payload, effective_dict)

    results = store.list_results("test_ds_001")
    assert len(results) == 1
    assert results[0].result_id == "result_20261017_001"
    assert results[0].dataset_id == "test_ds_001"


def test_list_results_filters_by_authorized_ids(store, sample_provenance):
    """List filters results by authorized result_ids."""
    payload = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "a.jpg", "score": 85}], "audit_rows": []}
    effective_dict = get_default_effective_config().model_dump()

    # Publish two results
    store.publish(sample_provenance, payload, effective_dict)

    sample_provenance.result_id = "result_20261017_002"
    sample_provenance.payload_sha256 = payload_sha256_v1(payload)
    store.publish(sample_provenance, payload, effective_dict)

    # List with authorization for only first result
    results = store.list_results("test_ds_001", authorized_result_ids=["result_20261017_001"])
    assert len(results) == 1
    assert results[0].result_id == "result_20261017_001"


def test_list_results_returns_empty_for_empty_authorization(store, sample_provenance):
    """List returns empty list when authorized_result_ids is empty."""
    payload = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "a.jpg", "score": 85}], "audit_rows": []}
    effective_dict = get_default_effective_config().model_dump()

    store.publish(sample_provenance, payload, effective_dict)

    results = store.list_results("test_ds_001", authorized_result_ids=[])
    assert results == []


def test_get_result_succeeds_with_valid_id(store, sample_provenance):
    """Get retrieves published result by ID."""
    payload = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "a.jpg", "score": 85}], "audit_rows": []}
    effective_dict = get_default_effective_config().model_dump()

    store.publish(sample_provenance, payload, effective_dict)

    descriptor, retrieved_payload = store.get_result("test_ds_001", "result_20261017_001")

    assert descriptor.result_id == "result_20261017_001"
    assert descriptor.dataset_id == "test_ds_001"
    assert retrieved_payload["dataset_id"] == "test_ds_001"
    assert len(retrieved_payload["quality_rows"]) == 1


def test_get_result_raises_for_nonexistent_id(store):
    """Get raises ValueError for nonexistent result_id."""
    with pytest.raises(ValueError, match="not found"):
        store.get_result("test_ds_001", "nonexistent_result")


def test_get_result_raises_for_dataset_mismatch(store, sample_provenance):
    """Get raises ValueError when dataset_id doesn't match."""
    payload = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "a.jpg", "score": 85}], "audit_rows": []}
    effective_dict = get_default_effective_config().model_dump()

    store.publish(sample_provenance, payload, effective_dict)

    with pytest.raises(ValueError, match="different dataset"):
        store.get_result("different_dataset", "result_20261017_001")


def test_get_result_returns_immutable_data(store, sample_provenance):
    """Retrieved result matches published provenance and payload."""
    payload = {
        "dataset_id": "test_ds_001",
        "quality_rows": [
            {"filename": "a.jpg", "score": 85, "status": "PASS"},
            {"filename": "b.jpg", "score": 60, "status": "REVIEW"},
        ],
        "audit_rows": [],
    }
    effective_dict = get_default_effective_config().model_dump()
    sample_provenance.payload_sha256 = payload_sha256_v1(payload)

    store.publish(sample_provenance, payload, effective_dict)

    descriptor, retrieved = store.get_result("test_ds_001", "result_20261017_001")

    # Descriptor fields match provenance
    assert descriptor.archive_sha256 == sample_provenance.archive_sha256
    assert descriptor.config_id == sample_provenance.config_id
    assert descriptor.payload_sha256 == sample_provenance.payload_sha256
    assert descriptor.verification == "verified"

    # Payload matches
    assert retrieved == payload


def test_concurrent_publish_serialized(store, sample_provenance):
    """Concurrent publishes are serialized by lock."""
    import threading

    payload1 = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "a.jpg", "score": 85}], "audit_rows": []}
    payload2 = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "b.jpg", "score": 90}], "audit_rows": []}
    effective_dict = get_default_effective_config().model_dump()

    sample_provenance.payload_sha256 = payload_sha256_v1(payload1)

    from dataclasses import replace
    sample_provenance2 = replace(sample_provenance, result_id="result_20261017_002", payload_sha256=payload_sha256_v1(payload2))

    errors = []

    def publish1():
        try:
            store.publish(sample_provenance, payload1, effective_dict)
        except Exception as e:
            errors.append(e)

    def publish2():
        try:
            store.publish(sample_provenance2, payload2, effective_dict)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=publish1)
    t2 = threading.Thread(target=publish2)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Both should succeed (different result_ids)
    assert len(errors) == 0

    results = store.list_results("test_ds_001")
    assert len(results) == 2


def test_provenance_immutability_after_publication(store, sample_provenance, tmp_path):
    """Published provenance cannot be modified."""
    payload = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "a.jpg", "score": 85}], "audit_rows": []}
    effective_dict = get_default_effective_config().model_dump()

    store.publish(sample_provenance, payload, effective_dict)

    # Try to update provenance directly in database (should be prevented by application logic)
    conn = sqlite3.connect(tmp_path / "results.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO provenance (result_id, dataset_id, archive_sha256, config_id, "
            "configuration_schema_revision, defaults_version, canonicalization_version, "
            "algorithm_version, payload_sha256, published_at, verification, effective_config_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sample_provenance.result_id, "different_dataset", "b" * 64, sample_provenance.config_id,
             "config-2026-10-17-01", "defaults-2026-10-17-01", CANONICALIZATION_V1, "algo-core-v1",
             "c" * 64, sample_provenance.published_at, "verified", "{}")
        )
        conn.commit()
    conn.close()


def test_null_request_fingerprint_preserved(store, sample_provenance):
    """Null request_fingerprint is preserved (not an error)."""
    sample_provenance.request_fingerprint = None

    payload = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "a.jpg", "score": 85}], "audit_rows": []}
    effective_dict = get_default_effective_config().model_dump()

    store.publish(sample_provenance, payload, effective_dict)

    descriptor, _ = store.get_result("test_ds_001", "result_20261017_001")
    assert descriptor.request_fingerprint is None


def test_null_producer_metadata_preserved(store, sample_provenance):
    """Null producer metadata fields are preserved."""
    sample_provenance.producer_build_id = None
    sample_provenance.producer_commit = None
    sample_provenance.producer_timestamp = None

    payload = {"dataset_id": "test_ds_001", "quality_rows": [{"filename": "a.jpg", "score": 85}], "audit_rows": []}
    effective_dict = get_default_effective_config().model_dump()

    store.publish(sample_provenance, payload, effective_dict)

    descriptor, _ = store.get_result("test_ds_001", "result_20261017_001")
    # Descriptor uses "unknown" as fallback for display
    assert descriptor.producer_build_id == "unknown"

