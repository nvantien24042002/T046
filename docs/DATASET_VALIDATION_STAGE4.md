# Stage 4: dataset validation, registration, and index construction

Date: 2026-10-16 (Asia/Saigon). This is the **archive validation and dataset registration checkpoint**, not UI integration or production large-file acceptance. Stop for review before Stage 5.

## Scope and files

The original Streamlit dashboard (`app.py`), all seven tabs, all `core/*.py`, `ui_theme.py`, production `requirements.txt`, Stage 1/2 prototype, and Stage 3 durable transport implementation (`storage/*`, `backend/durable_api.py`, `backend/upload_client/*`) remain strictly preserved and unchanged. Preservation is verified by `tests/baseline/source_hashes.json`, `tests/stage3/protected_sources.json`, and `tests/stage4/protected_sources.json`. No commits, pushes, or branch modifications were performed.

### Stage 4 deliverables

| File | Responsibility |
| --- | --- |
| `datasets/limits.py` | Centralized `Limits` dataclass (archive, expansion, entries, XML, images, worker memory, timeouts) and `ValidationError` with safe error codes. |
| `datasets/archive.py` | Low-level streaming ZIP and ZIP64 parser. Validates EOCD, locator, central directory records; enforces path security (no traversal, absolute paths, or link entries); enforces collision, entry, and ratio limits; streams decompress with CRC32 verification. |
| `datasets/xmlsafe.py` | Streaming CVAT XML parser using `xml.parsers.expat`. Forbids DTD declarations, entity expansion, external entity refs, and processing instructions. Enforces node, depth, attribute, and text limits. Populates `xml_nodes` index table. |
| `datasets/validate.py` | Worker-side archive validation orchestrator (`validate_archive`). Validates archive structure, verifies and decodes images under PIL decompression bomb and pixel limits, safely parses CVAT XML if present, indexes entries into SQLite `index.sqlite3`, and generates `manifest.json`. |
| `datasets/worker.py` | Isolated worker child process entry point. Communicates strictly over standard I/O (JSON line protocol). |
| `datasets/runner.py` | `WorkerRunner` managing worker subprocess execution. On Windows, attaches a parent-owned Windows Job Object (`WindowsJob`) to enforce per-process memory limits and ensure automatic worker termination on parent exit. |
| `datasets/schema.sql` | SQLite schema extensions: `validation_meta`, `validation_datasets`, `validation_attempts`, and `validation_leases`. Includes database triggers to pin upload payloads against premature cancellation or deletion while referenced by a dataset. |
| `datasets/manager.py` | `DatasetManager` wrapping `UploadStore`. Coordinates validation jobs, publishes sealed datasets, maintains state transitions, enforces concurrency bounds, manages reader leases, and recovers from worker interruptions. |
| `tests/stage4/protected_sources.json` | Manifest recording SHA-256 digests of all 13 Stage 3 protected source files. |
| `tests/test_stage4_preservation.py` | Automated test verifying all Stage 3 files remain bit-for-bit identical. |
| `tests/test_archive_validation.py` | Unit tests for ZIP/ZIP64 inspection, chunks decompression, adversarial path traversal, compression bombs, safe XML parser, and direct archive validation. |
| `tests/test_dataset_manager.py` | Integration tests for `DatasetManager` lifecycle, worker execution, reader leasing, payload pinning triggers, cancellation, and crash recovery. |

## SQLite schema and state transitions

Stage 4 extends the SQLite database (`registry.sqlite3`) with validation and dataset metadata tables:

| Table | Purpose |
| --- | --- |
| `validation_meta` | Version tracking (`version=1`). |
| `validation_datasets` | `upload_id` (PK, FK to `uploads`), `dataset_id` (unique UUID), `attempt_id`, `state`, `cancel_requested`, `error_code`, `manifest_sha`, `index_sha`, `payload_sha`. |
| `validation_attempts` | `id` (PK), `upload_id` (FK), `state` (`active`, `published`, `failed`, `cleaned`), `reserved_bytes`. |
| `validation_leases` | `id` (PK), `dataset_id` (FK). Tracks active reader leases. |

### Triggers and safety guards
- `validation_payload_pin`: Aborts (`DATASET_PAYLOAD_PINNED`) any update to `uploads` that attempts to cancel, expire, or truncate an upload row that is referenced by a dataset.
- `validation_no_payload_delete`: Aborts (`DATASET_PAYLOAD_PINNED`) any deletion of an upload row referenced by a dataset.
- `validation_admission_guard`: Rejects new uploads if validation workspace reservations are active.

### State lifecycle
```text
uploaded -> validating -> indexing -> publishing -> ready
    |           |            |           |
    |           +------------+-----------+-> failed
    +------------------------------------> cancelled
```

## Security and resource bounds

1. **ZIP / ZIP64 parser (`datasets/archive.py`)**:
   - Inspects EOCD and ZIP64 end-of-central-directory records before reading entries.
   - Rejects multidisk/split archives, encrypted entries, and unsupported compression algorithms.
   - Sanitizes member paths: strictly rejects path traversal (`../`), drive letters (`C:`), leading slashes, and control characters.
   - Enforces max compression ratio (200:1) both per-entry and aggregate.
   - Bounded block streaming with CRC32 verification prevents unmetered decompression.

2. **Safe XML parsing (`datasets/xmlsafe.py`)**:
   - Rejects DTD declarations, parameter entities, general entities, processing instructions, and external entity references to prevent XML entity expansion (Billion Laughs) and SSRF attacks.
   - Enforces depth limits, maximum attribute counts, attribute length bounds, and text buffer limits.

3. **Subprocess isolation & memory limits (`datasets/runner.py`)**:
   - Validation executes in a detached child process communicating via stdin/stdout JSON lines.
   - On Windows, a Win32 Job Object with `JOB_OBJECT_LIMIT_PROCESS_MEMORY` limits memory usage to `Limits.worker_memory_bytes` (1 GB default) and `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` ensures no orphaned worker processes survive.

4. **Windows file handle management**:
   - In `DatasetManager._manifest()`, SQLite connections opened to inspect `index.sqlite3` are explicitly closed in a `try...finally` block before `os.replace(work, sealed)` is invoked, preventing `WinError 32` / `WinError 5` file locks on Windows.

5. **Leased reader access (`DatasetManager.reader()`)**:
   - Context manager registers an ephemeral lease in `validation_leases`.
   - Manifest digest is verified against sealed metadata before granting access.
   - Returns open file handle to `payload.part` and read-only immutable SQLite connection to `index.sqlite3`.
   - Releases lease automatically on context exit. Attempting to clean up an attempt while a reader lease is active is rejected with `DATASET_LEASED`.
