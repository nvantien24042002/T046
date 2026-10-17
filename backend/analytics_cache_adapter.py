"""Cache-to-ResultStore adapter for Track B verified results.

Loads pre-computed analysis cache and sealed dataset metadata into immutable ResultStore
without re-analysis or altering source data. Validates dataset identity, configuration
revision, canonical configuration identity, payload schema, provenance, and payload hash.

Provenance Integrity Policy:
- Original producer provenance (build ID, commit, analysis timestamp) is never fabricated.
- Ingestion metadata (publication timestamp, verification status) is recorded at publication.
- Result IDs must be explicitly authorized and supplied; synthetic IDs are never manufactured.
- Archive hashes must originate from authoritative sealed manifests and match the registry.
- Cache dataset IDs must match filenames, manifest entries, and registry records.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from backend.analytics_configuration import (
    CANONICALIZATION_V1,
    PRODUCTION_REGISTRY,
    config_id_v1,
    get_default_effective_config,
    payload_sha256_v1,
)
from backend.analytics_contracts import Descriptor
from backend.analytics_result_contracts import Payload
from backend.analytics_storage import ProvenanceRecord, ResultStore


DEFAULT_REVISION = "config-2026-10-17-01"
DEFAULT_DEFAULTS_VERSION = "defaults-2026-10-17-01"
DEFAULT_ALGORITHM_VERSION = "algo-core-v1"

RESULT_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,512}$")
SHA256_REGEX = re.compile(r"^[0-9a-f]{64}$")


class CacheAdapterError(ValueError):
    """Raised when analysis cache or dataset manifest is invalid or incompatible."""


@dataclass(frozen=True)
class ProvenanceInput:
    """Explicit provenance specification distinguishing original producer metadata from ingestion."""
    result_id: str
    producer_build_id: str | None = None
    producer_commit: str | None = None
    producer_timestamp: str | None = None
    request_fingerprint: str | None = None


def validate_result_id(result_id: str) -> str:
    """Validate result ID format."""
    if not isinstance(result_id, str) or not RESULT_ID_REGEX.match(result_id):
        raise CacheAdapterError(
            f"Invalid result_id '{result_id}': must be 1-512 characters matching ^[A-Za-z0-9_-]+$"
        )
    return result_id


def validate_cache_payload(raw_cache: dict[str, Any], expected_dataset_id: str) -> dict[str, Any]:
    """Validate that raw cache dictionary conforms to the verified Payload schema."""
    if not isinstance(raw_cache, dict):
        raise CacheAdapterError("Cache content must be a JSON object")

    dataset_id = raw_cache.get("dataset_id")
    if dataset_id != expected_dataset_id:
        raise CacheAdapterError(
            f"Dataset ID mismatch in cache: expected '{expected_dataset_id}', got '{dataset_id}'"
        )

    # Exclude request_fingerprint from payload schema validation
    payload_dict = {k: v for k, v in raw_cache.items() if k != "request_fingerprint"}

    try:
        validated = Payload.model_validate(payload_dict)
    except Exception as exc:
        raise CacheAdapterError(f"Payload schema validation failed: {exc}") from exc

    return validated.model_dump(by_alias=True)


def load_sealed_manifest(
    manifest_path: Path,
    expected_dataset_id: str,
    expected_archive_sha256: str | None = None,
) -> dict[str, Any]:
    """Load and validate sealed attempt manifest.json against authoritative hash."""
    if not manifest_path.is_file():
        raise CacheAdapterError(f"Manifest file not found: {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CacheAdapterError(f"Failed to parse manifest JSON: {exc}") from exc

    if not isinstance(manifest, dict):
        raise CacheAdapterError("Manifest must be a JSON object")

    if manifest.get("dataset_id") != expected_dataset_id:
        raise CacheAdapterError(
            f"Dataset ID mismatch in manifest: expected '{expected_dataset_id}', got '{manifest.get('dataset_id')}'"
        )

    archive_sha256 = manifest.get("archive_sha256")
    if not archive_sha256 or not SHA256_REGEX.match(archive_sha256):
        raise CacheAdapterError(f"Invalid archive_sha256 in manifest: {archive_sha256}")

    if expected_archive_sha256 is not None and archive_sha256 != expected_archive_sha256:
        raise CacheAdapterError(
            f"Manifest archive_sha256 '{archive_sha256}' does not match registry payload_sha '{expected_archive_sha256}'"
        )

    return manifest


def adapt_cache_to_result_store(
    cache_path: Path,
    manifest_path: Path,
    store: ResultStore,
    *,
    dataset_id: str,
    result_id: str,
    expected_archive_sha256: str | None = None,
    provenance: ProvenanceInput | None = None,
    published_at: str | None = None,
    revision: str = DEFAULT_REVISION,
) -> tuple[Descriptor, dict[str, Any]]:
    """Adapt a pre-computed cache file and sealed manifest into immutable ResultStore.

    Args:
        cache_path: Path to analysis cache JSON.
        manifest_path: Path to sealed attempt manifest.json.
        store: Target ResultStore instance.
        dataset_id: Expected dataset UUID.
        result_id: Explicitly authorized result ID (MUST NOT be empty or omitted).
        expected_archive_sha256: Authoritative archive SHA-256 from database registry.
        provenance: Optional producer-time provenance input.
        published_at: Ingestion/publication timestamp (defaults to current UTC ISO 8601).
        revision: Configuration schema revision identifier.

    Returns:
        (descriptor, payload) tuple of published result.

    Raises:
        CacheAdapterError: If inputs are invalid or schema validation fails.
    """
    valid_result_id = validate_result_id(result_id)

    if not cache_path.is_file():
        raise CacheAdapterError(f"Cache file not found: {cache_path}")

    # Validate filename matches dataset_id
    if not cache_path.name.startswith(dataset_id):
        raise CacheAdapterError(
            f"Cache filename '{cache_path.name}' does not match dataset ID '{dataset_id}'"
        )

    try:
        raw_cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CacheAdapterError(f"Failed to parse cache JSON: {exc}") from exc

    payload = validate_cache_payload(raw_cache, dataset_id)
    manifest = load_sealed_manifest(manifest_path, dataset_id, expected_archive_sha256)
    archive_sha256 = manifest["archive_sha256"]

    payload_hash = payload_sha256_v1(payload)

    # Check if result_id is already published in store
    try:
        existing_desc, existing_payload = store.get_result(dataset_id, valid_result_id)
        # Deterministic idempotency: if content and archive hashes match, return existing
        if existing_desc.archive_sha256 == archive_sha256 and existing_desc.payload_sha256 == payload_hash:
            return existing_desc, existing_payload
        raise CacheAdapterError(
            f"Result ID '{valid_result_id}' already published with different content or archive hash"
        )
    except ValueError as e:
        if "not found" not in str(e):
            raise CacheAdapterError(f"Result store error: {e}") from e

    # Build canonical configuration identity
    effective_config = get_default_effective_config(revision)
    effective_dict = effective_config.model_dump()

    config_id = config_id_v1(
        revision,
        DEFAULT_DEFAULTS_VERSION,
        CANONICALIZATION_V1,
        effective_config,
    )

    # Publication timestamp is the ingestion time; distinct from producer_timestamp
    ingestion_time = published_at or datetime.now(timezone.utc).isoformat()

    # Truthful provenance extraction: do not manufacture producer commit or build if absent
    prod_build_id = provenance.producer_build_id if provenance else None
    prod_commit = provenance.producer_commit if provenance else None
    prod_timestamp = provenance.producer_timestamp if provenance else None
    req_fp = (provenance.request_fingerprint if provenance and provenance.request_fingerprint
              else raw_cache.get("request_fingerprint"))

    record = ProvenanceRecord(
        result_id=valid_result_id,
        dataset_id=dataset_id,
        archive_sha256=archive_sha256,
        config_id=config_id,
        configuration_schema_revision=revision,
        defaults_version=DEFAULT_DEFAULTS_VERSION,
        canonicalization_version=CANONICALIZATION_V1,
        algorithm_version=DEFAULT_ALGORITHM_VERSION,
        producer_build_id=prod_build_id,
        producer_commit=prod_commit,
        producer_timestamp=prod_timestamp,
        request_fingerprint=req_fp,
        payload_sha256=payload_hash,
        published_at=ingestion_time,
        verification="verified",
    )

    try:
        store.publish(record, payload, effective_dict)
    except Exception as exc:
        raise CacheAdapterError(f"Failed to publish result to store: {exc}") from exc

    descriptor, published_payload = store.get_result(dataset_id, valid_result_id)
    PRODUCTION_REGISTRY.validate(descriptor)
    return descriptor, published_payload


def load_staging_cache_directory(
    staging_root: Path,
    store: ResultStore,
    result_id_map: dict[str, str],
) -> list[str]:
    """Scan staging root for ready datasets, load their analysis caches, and publish to ResultStore.

    Args:
        staging_root: Path to staging storage root (e.g. D:\\CVATPrivateUploads\\stage3-data).
        store: Target ResultStore.
        result_id_map: Explicit mapping of {dataset_id: result_id}. Required to prevent
                       unauthorized or synthetic result ID manufacture.

    Returns:
        List of published result IDs.

    Raises:
        CacheAdapterError: If mapping is missing, registry is invalid, or hashes mismatch.
    """
    if not result_id_map:
        raise CacheAdapterError(
            "Explicit result_id_map is required. Automatic manufacture of synthetic result IDs is prohibited."
        )

    staging_path = Path(staging_root).resolve()
    registry_file = staging_path / "registry.sqlite3"
    cache_dir = staging_path / ".analysis-cache-v1"
    sealed_dir = staging_path / ".datasets-v1" / "sealed"

    if not registry_file.is_file():
        raise CacheAdapterError(f"Registry database not found: {registry_file}")
    if not cache_dir.is_dir():
        raise CacheAdapterError(f"Analysis cache directory not found: {cache_dir}")
    if not sealed_dir.is_dir():
        raise CacheAdapterError(f"Sealed datasets directory not found: {sealed_dir}")

    conn = sqlite3.connect(registry_file)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT dataset_id, attempt_id, payload_sha FROM validation_datasets WHERE state='ready'"
        )
        ready_datasets = {r["dataset_id"]: dict(r) for r in cursor.fetchall()}
    finally:
        conn.close()

    published_result_ids: list[str] = []

    for dataset_id, target_result_id in result_id_map.items():
        if dataset_id not in ready_datasets:
            raise CacheAdapterError(
                f"Dataset '{dataset_id}' from result_id_map is not ready in registry"
            )

        entry = ready_datasets[dataset_id]
        attempt_id = entry["attempt_id"]
        authoritative_payload_sha = entry["payload_sha"]

        cache_file = cache_dir / f"{dataset_id}.json"
        manifest_file = sealed_dir / attempt_id / "manifest.json"

        if not cache_file.is_file():
            matches = list(cache_dir.glob(f"{dataset_id}.*.json"))
            if matches:
                cache_file = matches[0]
            else:
                raise CacheAdapterError(f"Cache file for dataset '{dataset_id}' not found")

        if not manifest_file.is_file():
            raise CacheAdapterError(f"Manifest for attempt '{attempt_id}' not found")

        desc, _ = adapt_cache_to_result_store(
            cache_file,
            manifest_file,
            store,
            dataset_id=dataset_id,
            result_id=target_result_id,
            expected_archive_sha256=authoritative_payload_sha,
        )
        published_result_ids.append(desc.result_id)

    return published_result_ids
