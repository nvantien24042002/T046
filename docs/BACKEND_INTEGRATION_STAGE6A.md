# Stage 6A — Backend Integration Architecture & Specification

## 1. Executive Summary

Stage 6A unifies the Stage 3 durable upload backend, Stage 4 dataset validation and indexing lifecycle, and Stage 5 index-backed core analysis pipeline into a single, cohesive, authenticated backend service (`backend/integrated_api.py`).

This integration strictly adheres to backend-only boundaries:
- **No Streamlit UI code was modified**: `app.py`, `ui_theme.py`, and existing dashboard modules remain intact and untouched.
- **No duplicate sources of truth**: The single authoritative state machine in `UploadStore` and `DatasetManager` governs all upload and dataset state.
- **Clean separation of concerns**:
  - `storage/`: Chunker transport, atomic append, receipt generation, SHA-256 verification.
  - `datasets/`: ZIP entry safety limits, expat XML parsing, SQLite index building, atomic sealing, reader leases, fault recovery.
  - `core/`: Pure algorithms for image quality (streaming bounded chunks), label audit, class/attribute distribution.
  - `backend/integrated_api.py`: FastAPI route coordination, authentication, request validation, serialized lifecycle state, and caching.

---

## 2. Architecture & Data Flow

```
                      +-----------------------------+
                      |       Client / Caller       |
                      +-----------------------------+
                        | (Owner Key, Capability,
                        |  Idempotency Key)
                        v
+---------------------------------------------------------------------+
|                  backend/integrated_api.py (FastAPI)                |
|                                                                     |
|  - Localhost-only & CORS enforcement                                |
|  - Constant-time Owner Key authentication                           |
|  - Capability token validation                                      |
|  - Mutex serialization for state mutations                          |
|  - Exception translation (Zero path/traceback leakage)              |
+---------------------------------------------------------------------+
        |                                       |
        v                                       v
+-------------------------------+   +---------------------------------+
|   storage/store.UploadStore   |   |   datasets/manager.DatasetManager
|                               |   |                                 |
| - Chunk transport & offset    |   | - ZIP inspection & XML parsing  |
| - Pre-allocation & quotas     |   | - SQLite index sealing          |
| - SHA-256 payload receipt     |   | - Atomic directory promotion    |
| - Tombstone & absolute expiry |   | - Leased immutable reader       |
+-------------------------------+   +---------------------------------+
                                                        |
                                                        | reader context
                                                        v
                                    +---------------------------------+
                                    |       core/pipeline.py          |
                                    |                                 |
                                    | - execute_pipeline()            |
                                    | - bounded streaming decode      |
                                    | - cvat_parser / image_quality   |
                                    | - label_audit / distribution    |
                                    +---------------------------------+
                                                        |
                                                        v (JSON bundle)
                                    +---------------------------------+
                                    | Isolated Analysis Cache Store   |
                                    | store.root / .analysis-cache-v1 |
                                    +---------------------------------+
```

### Invariant Rules
1. **Upload completion != Dataset readiness**: A completed upload payload (`state: uploaded`) is merely an unvalidated raw byte archive. It cannot be queried for summaries or analysis until validation succeeds.
2. **Atomic Sealing & Storage Cleanliness**: The dataset attempt folder strictly contains `{manifest.json, index.sqlite3}` to preserve the invariant of `DatasetManager._manifest()`. Analysis caches are stored in a dedicated sibling directory (`.analysis-cache-v1/`).
3. **No Implicit Heavy Work on GET**: `GET /api/v1/datasets/{id}/results` strictly queries pre-computed analysis cache. If analysis has not run, it returns HTTP 409 `ANALYSIS_NOT_RUN`. Heavy analysis is explicitly dispatched via `POST /api/v1/datasets/{id}/analyze`.
4. **Data Isolation**: Datasets are partitioned by owner. Cross-owner attempts return HTTP 403 `ACCESS_DENIED`.

---

## 3. API Endpoints Specification

### 3.1. Durable Upload Routes (`/api/v1/uploads`)
- `POST /api/v1/uploads`
  - Creates upload session with reservation and idempotency key.
  - Headers: `X-Owner-Key: <key>`, `Idempotency-Key: <uuid>`.
  - Body: `{"filename": "archive.zip", "size_bytes": 12345}`.
  - Returns `201 Created` with `upload_id` and `upload_capability`.
- `PUT /api/v1/uploads/{id}/chunks/{index}`
  - Streams chunk data with offset and SHA-256 validation.
  - Headers: `X-Owner-Key`, `Upload-Capability`, `Upload-Offset`, `X-Chunk-SHA256`.
  - Returns `200 OK` with chunk receipt.
- `POST /api/v1/uploads/{id}/complete`
  - Validates full payload SHA-256 and seals upload to `uploaded`.
  - Returns `200 OK` with `state: uploaded`, `ready: false`.
- `GET /api/v1/uploads/{id}`
  - Retrieves upload transport state.
- `POST /api/v1/uploads/{id}/cancel`
  - Cancels upload and frees reserved capacity.

### 3.2. Dataset Lifecycle Routes (`/api/v1/uploads/{id}/...`)
- `POST /api/v1/uploads/{id}/validate`
  - Dispatches validation and indexing worker on the sealed payload.
  - Query param: `retry=true` for re-running failed attempts.
  - Returns `200 OK` with current dataset state (`ready`, `validating`, `indexing`, `failed`).
- `GET /api/v1/uploads/{id}/dataset`
  - Queries dataset lifecycle status.
  - Response:
    ```json
    {
      "dataset_id": "89601f61-5ae0-4499-b468-91d1b1a90482",
      "upload_id": "0df62ff4-13bb-4017-91a5-3d0cf3b12ce6",
      "state": "ready",
      "ready": true,
      "error_code": null
    }
    ```
- `POST /api/v1/uploads/{id}/dataset/cancel`
  - Requests safe cancellation of in-flight validation. Returns 409 if no cancellable validation is running.
- `DELETE /api/v1/uploads/{id}/dataset/attempts/{attempt_id}`
  - Cleans scratch files for failed attempts.

### 3.3. Dataset Queries & Analysis Routes (`/api/v1/datasets/{dataset_id}`)
- `GET /api/v1/datasets/{dataset_id}/summary`
  - Retrieves dataset metadata from index without loading the archive.
  - Response:
    ```json
    {
      "dataset_id": "89601f61-5ae0-4499-b468-91d1b1a90482",
      "upload_id": "0df62ff4-13bb-4017-91a5-3d0cf3b12ce6",
      "state": "ready",
      "ready": true,
      "entries": 3,
      "images": 2,
      "annotations": true,
      "capabilities": {
        "image_quality": true,
        "label_audit": true,
        "distribution": true,
        "coverage": true,
        "scene_finder": true
      }
    }
    ```
- `POST /api/v1/datasets/{dataset_id}/analyze`
  - Executes Stage 5 pure core pipeline with reader lease.
  - Optional Body: `{"image_cfg": {...}, "label_cfg": {...}}`.
  - Serializes results and saves to private cache. Returns JSON analysis bundle.
- `GET /api/v1/datasets/{dataset_id}/results`
  - Retrieves pre-computed analysis results. Returns HTTP 409 `ANALYSIS_NOT_RUN` if not executed yet.
- `GET /api/v1/datasets/{dataset_id}/preview?image_path=...&annotated=true|false`
  - Streams bounded single image preview directly from ZIP reader with size guard (max 10MB).
  - When `annotated=true`, dynamically renders annotation bounding boxes from the indexed database.

---

## 4. Lifecycle Transitions & Fault Recovery

| Initial State | Event / Trigger | Target State | Notes |
|:---|:---|:---|:---|
| `created` | Chunk upload | `uploading` | Storage tracks byte offsets and chunk SHA-256 |
| `uploading` | `POST /complete` | `uploaded` | Sealed payload verified against full SHA-256 |
| `uploaded` | `POST /validate` | `validating` | Workspace reserved, runner starts entry scanning |
| `validating` | Entry scan complete | `indexing` | Expat XML parsing and SQLite index population |
| `indexing` | Validation pass | `publishing` | Manifest created, digest check, directory atomic rename |
| `publishing` | Commit & seal | `ready` | Final state; reader leases permitted |
| `validating`/`indexing` | Crash / Restart | `failed` | `_recover()` detects stale worker; marks `WORKER_INTERRUPTED` |
| `validating`/`indexing` | Invalid ZIP / XML | `failed` | Failure recorded truthfully with specific error code |
| Any non-ready | Analysis request | Rejected (409) | `DATASET_NOT_READY` prevents unsafe queries |

---

## 5. Security & Resource Protection

1. **Information Leak Prevention**:
   - Generic 500 handler suppresses Python tracebacks and internal file paths.
   - All errors formatted as `{"error": {"code": "..."}}`.
   - SQLite errors return `503 REGISTRY_UNAVAILABLE`.
   - File I/O errors return `507 STORAGE_IO_FAILED`.
2. **Access Control**:
   - Loopback enforcement: Requests restricted to 127.0.0.1 / localhost.
   - Constant-time secret comparison via `secrets.compare_digest`.
   - Upload capability required for all session interactions.
   - Cross-owner isolation verified by test suite.
3. **Bounded Memory & Streaming**:
   - ZIP members streamed in chunks through `StreamingMemberView`.
   - Images read on-demand without holding uncompressed bytearrays.
   - SQLite index queries executed as read-only immutable URIs.
