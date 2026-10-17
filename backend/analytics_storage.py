"""Immutable result storage and provenance for Track B.

Implements atomic publication, provenance capture, and verified result retrieval.
Results are published only after integrity checks pass. A result_id never changes
meaning once visible.
"""
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backend.analytics_contracts import Descriptor
from backend.analytics_configuration import config_id_v1, payload_sha256_v1


@dataclass
class ProvenanceRecord:
    """Producer-time provenance binding all result identity."""
    result_id: str
    dataset_id: str
    archive_sha256: str

    # Configuration identity
    config_id: str
    configuration_schema_revision: str
    defaults_version: str
    canonicalization_version: str
    algorithm_version: str

    # Producer/build identity (must be truthful production-time evidence)
    producer_build_id: str | None
    producer_commit: str | None
    producer_timestamp: str | None

    # Request identity
    request_fingerprint: str | None

    # Payload integrity
    payload_sha256: str

    # Publication
    published_at: str
    verification: Literal["verified"] = "verified"


class ResultStore:
    """Immutable result storage with atomic publication."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        """Initialize storage schema."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")

            # Provenance table - immutable once published
            conn.execute("""
                CREATE TABLE IF NOT EXISTS provenance (
                    result_id TEXT PRIMARY KEY,
                    dataset_id TEXT NOT NULL,
                    archive_sha256 TEXT NOT NULL,
                    config_id TEXT NOT NULL,
                    configuration_schema_revision TEXT NOT NULL,
                    defaults_version TEXT NOT NULL,
                    canonicalization_version TEXT NOT NULL,
                    algorithm_version TEXT NOT NULL,
                    producer_build_id TEXT,
                    producer_commit TEXT,
                    producer_timestamp TEXT,
                    request_fingerprint TEXT,
                    payload_sha256 TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    verification TEXT NOT NULL CHECK (verification = 'verified'),
                    effective_config_json TEXT NOT NULL
                )
            """)

            # Payload table - stores result JSON blobs
            conn.execute("""
                CREATE TABLE IF NOT EXISTS payloads (
                    result_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (result_id) REFERENCES provenance(result_id)
                )
            """)

            # Indices for common queries
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dataset ON provenance(dataset_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_archive ON provenance(archive_sha256)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_config ON provenance(config_id)")

            conn.commit()
            conn.close()

    def publish(
        self,
        provenance: ProvenanceRecord,
        payload: dict,
        effective_config: dict,
    ) -> None:
        """Atomically publish a verified result.

        Validates integrity before publication. Once published, result_id is immutable.
        Concurrent publishers are serialized. Interrupted publication leaves no partial state.

        Raises:
            ValueError: If integrity checks fail or result_id already exists
        """
        # Pre-publication validation
        if not provenance.result_id or not provenance.dataset_id:
            raise ValueError("Missing required identity fields")

        # Verify payload hash matches provenance
        computed_hash = payload_sha256_v1(payload)
        if computed_hash != provenance.payload_sha256:
            raise ValueError(f"Payload hash mismatch: expected {provenance.payload_sha256}, got {computed_hash}")

        # Verify config_id matches provenance
        from backend.analytics_contracts import EffectiveConfiguration
        effective = EffectiveConfiguration.model_validate(effective_config)
        computed_config_id = config_id_v1(
            provenance.configuration_schema_revision,
            provenance.defaults_version,
            provenance.canonicalization_version,
            effective,
        )
        if computed_config_id != provenance.config_id:
            raise ValueError(f"Config ID mismatch: expected {provenance.config_id}, got {computed_config_id}")

        # Atomic publication with transaction
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")

                # Check if result_id already exists
                cursor = conn.execute(
                    "SELECT result_id FROM provenance WHERE result_id = ?",
                    (provenance.result_id,)
                )
                if cursor.fetchone() is not None:
                    raise ValueError(f"Result ID {provenance.result_id} already published")

                # Insert provenance
                effective_config_json = json.dumps(effective_config, ensure_ascii=False)
                conn.execute("""
                    INSERT INTO provenance (
                        result_id, dataset_id, archive_sha256, config_id,
                        configuration_schema_revision, defaults_version,
                        canonicalization_version, algorithm_version,
                        producer_build_id, producer_commit, producer_timestamp,
                        request_fingerprint, payload_sha256, published_at, verification,
                        effective_config_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    provenance.result_id,
                    provenance.dataset_id,
                    provenance.archive_sha256,
                    provenance.config_id,
                    provenance.configuration_schema_revision,
                    provenance.defaults_version,
                    provenance.canonicalization_version,
                    provenance.algorithm_version,
                    provenance.producer_build_id,
                    provenance.producer_commit,
                    provenance.producer_timestamp,
                    provenance.request_fingerprint,
                    provenance.payload_sha256,
                    provenance.published_at,
                    provenance.verification,
                    effective_config_json,
                ))

                # Insert payload
                payload_json = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                conn.execute(
                    "INSERT INTO payloads (result_id, payload_json) VALUES (?, ?)",
                    (provenance.result_id, payload_json)
                )

                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def list_results(self, dataset_id: str, authorized_result_ids: list[str] | None = None) -> list[Descriptor]:
        """List verified result descriptors for a dataset.

        Args:
            dataset_id: Dataset to query
            authorized_result_ids: If provided, only return results in this list

        Returns:
            List of descriptors, empty if none exist (not an error)
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            if authorized_result_ids is None:
                cursor = conn.execute(
                    "SELECT * FROM provenance WHERE dataset_id = ? ORDER BY published_at DESC",
                    (dataset_id,)
                )
            else:
                if not authorized_result_ids:
                    return []
                placeholders = ",".join("?" * len(authorized_result_ids))
                cursor = conn.execute(
                    f"SELECT * FROM provenance WHERE dataset_id = ? AND result_id IN ({placeholders}) ORDER BY published_at DESC",
                    (dataset_id, *authorized_result_ids)
                )

            descriptors = []
            for row in cursor:
                # Reconstruct effective_config from stored provenance
                # Note: We need the actual config, which should be stored or reconstructed
                # For now, this is a placeholder - real implementation would store effective_config
                descriptors.append(self._row_to_descriptor(row))

            conn.close()
            return descriptors

    def get_result(self, dataset_id: str, result_id: str) -> tuple[Descriptor, dict]:
        """Retrieve one immutable verified result.

        Args:
            dataset_id: Dataset ID for validation
            result_id: Result ID to retrieve

        Returns:
            (descriptor, payload) tuple

        Raises:
            ValueError: If result not found or dataset mismatch
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            # Get provenance
            cursor = conn.execute(
                "SELECT * FROM provenance WHERE result_id = ?",
                (result_id,)
            )
            row = cursor.fetchone()
            if row is None:
                conn.close()
                raise ValueError(f"Result {result_id} not found")

            if row["dataset_id"] != dataset_id:
                conn.close()
                raise ValueError(f"Result {result_id} belongs to different dataset")

            descriptor = self._row_to_descriptor(row)

            # Get payload
            cursor = conn.execute(
                "SELECT payload_json FROM payloads WHERE result_id = ?",
                (result_id,)
            )
            payload_row = cursor.fetchone()
            if payload_row is None:
                conn.close()
                raise ValueError(f"Payload for {result_id} not found")

            payload = json.loads(payload_row["payload_json"])

            # Verify persisted result integrity against silent mutation
            computed_payload_hash = payload_sha256_v1(payload)
            if computed_payload_hash != descriptor.payload_sha256:
                conn.close()
                raise ValueError(f"Payload hash mismatch: expected {descriptor.payload_sha256}, got {computed_payload_hash}")

            computed_config_id = config_id_v1(
                descriptor.configuration_schema_revision,
                descriptor.defaults_version,
                descriptor.canonicalization_version,
                descriptor.effective_config,
            )
            if computed_config_id != descriptor.config_id:
                conn.close()
                raise ValueError(f"Config ID mismatch: expected {descriptor.config_id}, got {computed_config_id}")

            conn.close()
            return descriptor, payload

    def _row_to_descriptor(self, row: sqlite3.Row) -> Descriptor:
        """Convert database row to Descriptor."""
        from backend.analytics_contracts import EffectiveConfiguration

        effective_config = EffectiveConfiguration.model_validate(
            json.loads(row["effective_config_json"])
        )

        return Descriptor(
            result_id=row["result_id"],
            dataset_id=row["dataset_id"],
            archive_sha256=row["archive_sha256"],
            config_id=row["config_id"],
            request_fingerprint=row["request_fingerprint"],
            configuration_schema_revision=row["configuration_schema_revision"],
            effective_config=effective_config,
            defaults_version=row["defaults_version"],
            canonicalization_version=row["canonicalization_version"],
            algorithm_version=row["algorithm_version"],
            producer_build_id=row["producer_build_id"] or "unknown",
            payload_sha256=row["payload_sha256"],
            verification="verified",
        )
