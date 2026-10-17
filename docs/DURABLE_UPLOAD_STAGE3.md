# Stage 3: durable upload storage and recovery

Date: 2026-10-16 (Asia/Saigon). This is a **byte-upload checkpoint**, not dataset validation or production large-file acceptance. Stop for review before Stage 4.

## Scope and files

The original dashboard, all seven tabs, `app.py`, every `core/*.py`, `ui_theme.py`, production `requirements.txt`, accepted prototype, prototype dependencies and prior baseline fixtures/tests remain unchanged. Preservation is checked by `tests/baseline/source_hashes.json` and `tests/stage3/protected_sources.json`. No commits, pushes or branch operations were performed.

New service: `backend/durable_api.py`, separate from `prototype/api.py`. New isolated browser protocol client: `backend/upload_client/index.html` and `uploader.js`, served at `/client`. It deliberately does not integrate with Streamlit yet. No React or new dependency installation is required beyond the existing isolated Stage 1/2 environment. Uploaded payloads have no serving/download route.

Added files:

| File | Responsibility |
| --- | --- |
| `storage/config.py` | Validated storage, byte/chunk/quota/time limits; required stable secret and owner credentials. |
| `storage/schema.sql` | SQLite version 1 schema below. |
| `storage/files.py` | Flush/fsync, POSIX directory sync, single-process OS lock, symlink/reparse/hardlink/regular-file guards. |
| `storage/uploads.py`, `storage/__init__.py` | Receipts/reservations, sequential bounded writes, replay, recovery, cancellation/expiry and conservative cleanup. No HTTP or Streamlit dependency. |
| `backend/durable_api.py`, `backend/__init__.py` | Local-only authenticated HTTP routes, bounded stream ingestion, thread offload, serialized mutations and periodic maintenance. |
| `backend/upload_client/{index.html,uploader.js}` | Same-origin small test client; credential entry, slicing/hashing, retry, refresh/reselection prefix proof, pause/cancel. No file bytes in browser storage. |
| `tests/test_durable_store.py` | Deterministic storage, actual process-crash boundaries, COMMIT/write/fsync faults, quotas, cleanup and corruption tests. |
| `tests/test_durable_api.py` | API, ownership/capability, input limits and failure responses. |
| `tests/durable_support.py`, `tests/test_durable_network.py` | Owned child-server lifecycle; real interrupted HTTP body, forced process termination and restart after acknowledgement. |
| `tests/e2e/test_durable_browser.py` | Real Chrome refresh/reselection/restart, changed prefix rejection, lost acknowledgement replay and cancellation. |
| `tests/test_stage3_preservation.py`, `tests/stage3/protected_sources.json` | Accepted source/test/dependency preservation checks. |
| `.env.upload.example` | Nonsecret configuration reference; not an automatically loaded dotenv file. |

Modified `.gitignore` excludes `upload-data/` and `.env.upload`. This document and `DURABLE_UPLOAD_STAGE3_TEST_RESULTS.md` record behavior/evidence; broader design status documents point here. No ZIP validation, extraction, image decode, dataset registry, analysis worker, QA/QC refactor or production dashboard integration was added.

## SQLite schema and state transitions

SQLite is in `registry.sqlite3` under the operator-selected root, using `journal_mode=DELETE`, `synchronous=EXTRA`, `foreign_keys=ON`, explicit `BEGIN IMMEDIATE` transactions and `user_version=1`. Database startup runs `quick_check`; root ID/version/stable-secret tag must match before recovery may alter files.

| Table | Key / fields |
| --- | --- |
| `meta` | Key/value pairs: root UUID and SHA-256 tag of the stable capability secret; no plaintext credentials. |
| `uploads` | UUID primary key; owner ID; capability hash; owner-scoped unique idempotency key; display filename; declared total bytes; fixed negotiated chunk bytes; state; committed bytes; reserved bytes; creation/update/inactivity/absolute deadlines; safe error; cleanup-pending flag and cleaned timestamp. |
| `receipts` | `(upload_id, chunk_index)` primary key and unique `(upload_id, offset_bytes)`; length, SHA-256 and commit timestamp; foreign key to upload. |

Reservations are transactionally persisted **in each upload row**, not a separate service/table. The root OS lock and in-process mutation serialization make admission checks plus reservation creation exclusive. Another API process/worker cannot open this root. SQLite is not used to store image or archive bytes.

```text
created -> uploading -> uploaded
created/uploading -> expired
created/uploading/uploaded -> failed (detected storage/receipt inconsistency)
created/uploading/uploaded/failed -> cancelled (explicit authorized deletion)
cancelled/expired + safe cleanup -> same tombstone state, cleaned_at set
```

All declared bytes can be acknowledged while state is still uploading; explicit `/complete` seals them as uploaded. Complete is idempotent and performs **no ZIP validation**. Every response has `ready=false`, `validated=false`. No dataset ID exists. Uploaded files never become ready in Stage 3 and do not automatically expire; they count against storage quota until explicitly cancelled. Failed sessions are retained for inspection until explicitly cancelled. Tombstones/receipts remain in SQLite, including after cleanup.

For intact created/uploading/uploaded sessions, received_bytes is the SQLite committed offset. For failed/cancelled/expired sessions, durable=false, received_bytes=0, next_offset=null; recorded_bytes retains the historical acknowledged counter for diagnosis. It must not be presented as verified bytes currently available after corruption/deletion.

## Exact persistence and recovery ordering

### Root and session creation

1. Require an absolute private local root, no symlink/reparse ancestors; initialize only an empty directory (or reopen a matching owned root). Reject nonempty unrelated directories without adopting/deleting them. A new root marker, registry and uploads directory establish service ownership.
2. Hold a process-lifetime kernel lock on `.service.lock` (Windows msvcrt / POSIX flock); root opening is exclusive. SQLite/files are rejected if symlink/reparse/nonregular/multiply hardlinked. The lock is released by OS process death.
3. Admission reserves declared size plus one scratch chunk before creating a session directory. Commit created metadata/reservation first, then create/fsync the empty payload and sync directory entries on POSIX before returning creation. A crash between reservation and filesystem creation is recoverable as a known empty session, using the same idempotency key. Interrupted root initialization is different: missing/broken registry fails closed for manual inspection.

### A new chunk

1. Require authenticated owner **and** matching session capability, resumable state, unexpired deadline, exact next index/offset, valid SHA-256 header and expected length (only final chunk can be short). Check physical free space against reservations/headroom.
2. Stream HTTP body to that session's `incoming.chunk`. Enforce actual byte length and incremental hash; bounded memoryview slices cap individual application writes at 1 MiB. No `request.body()`, ZIP BytesIO, whole-file byte array or multipart aggregation. OS writes/hash work run off the event loop. The default browser buffers/hashes one negotiated chunk, at most 8 MiB.
3. After full length/hash match: `scratch.flush()` then `os.fsync(scratch.fileno())`; close scratch.
4. Copy scratch in bounded blocks into `payload.part` **at the current committed offset**. Recheck headroom during writes; reject unexpected payload length. `payload.flush()` then `os.fsync(payload.fileno())`; close payload.
5. `BEGIN IMMEDIATE`; insert receipt and update offset/state/reservation/deadlines in the **same** transaction; `COMMIT` under synchronous EXTRA. Only after successful COMMIT may the handler return an acknowledgement. The receipt never precedes payload fsync.
6. Remove scratch and sync its directory on POSIX. If cleanup/response fails after COMMIT, no new acknowledgement is sent; the already-committed chunk remains authoritative. An identical retry consumes/hashes the repeated body and returns replayed=true with current durable offset, without appending again.

The code uses flush before fsync as prescribed by [Python's os.fsync documentation](https://docs.python.org/3/library/os.html#os.fsync). SQLite EXTRA adds rollback-journal directory synchronization to FULL in DELETE mode; see [SQLite synchronous documentation](https://www.sqlite.org/pragma.html#pragma_synchronous).

### Errors and restart

- Disconnect, short/oversized body, wrong hash, write/fsync failure or transaction rollback: close scratch; re-read **authoritative SQLite state**; truncate only unacknowledged payload tail to that offset, fsync truncation, remove known scratch. No receipt or byte advance for incomplete data.
- COMMIT can fail ambiguously. Never assume it rolled back and blindly truncate to an old in-memory offset. Abort reads current DB state: a committed receipt preserves bytes; an uncommitted transaction rolls back. If SQLite cannot be read, leave files untouched, return an error/unhealthy state and recover after service restart.
- Startup, under the root lock, verifies all ordered receipt offsets/indices/lengths and their stored chunk hashes by bounded reads. It verifies receipts **before any tail truncation**. Then it truncates only bytes beyond the verified committed prefix and removes known scratch.
- Missing/short acknowledged payload, same-size corruption, noncontiguous/inconsistent receipt or DB offset: mark failed and preserve files/reservations; no fabricated resume offset, downward counter reset or destructive repair. Root/database corruption/version/secret mismatches refuse startup rather than adopting data.
- Startup hashes all acknowledged prefixes, including uploaded payloads. This can take significant time for real large data; health is unavailable until initialization/recovery finishes. No recovery-time SLA is claimed.

The documented guarantee is **API process termination/restart on the same functioning local volume**, after file fsync and SQLite EXTRA COMMIT succeed. Windows Python provides no portable directory fsync here; directory sync runs on POSIX only. Hardware write caches, filesystem/SQLite VFS behavior, host power loss, volume loss, snapshots/restores and faulty devices remain external assumptions. No power-cut test or universal power-loss guarantee is claimed. Unsupported storage must not be described as durable simply because tests passed.

## API, authentication and client resume

All routes are loopback/localhost-only with exact origin checks; run Uvicorn with `--no-proxy-headers`, one worker, no public bind/proxy. Random owner keys map to operator-defined owner IDs; SHA-256 comparisons authenticate `X-Owner-Key`. Session actions also require `Upload-Capability`. Capabilities are deterministic HMACs of the stable server secret + owner + session UUID; only hashes are persisted. This permits lost-create-response replay without storing plaintext capability. Every session action rechecks both bindings; no client-supplied path is accepted.

| Route | Contract |
| --- | --- |
| `POST /api/v1/uploads` | <=4 KiB JSON filename/size_bytes, owner key and Idempotency-Key. 201 returns ID/capability, limits, durable status/deadline. Same owner/key/metadata yields same session/capability; changed metadata conflicts. |
| `PUT /api/v1/uploads/{id}/chunks/{index}` | Binary body with owner/capability, Upload-Offset and X-Chunk-SHA256. 200 only after ordered persistence; exact acknowledged replay does not append. A different replay conflicts. |
| `GET /api/v1/uploads/{id}` | Authoritative status/deadline/error/cleanup state. Owned terminal tombstones return 200 with terminal state, not a misleading resume offset. |
| `GET /api/v1/uploads/{id}/receipts?start=0&limit=128` | Owner/capability protected; at most 256 receipt hashes/page for reselection proof. Terminal/unresumable sessions conflict. |
| `POST /api/v1/uploads/{id}/complete` | 200 only after every declared byte is committed; uploaded, ready=false, validated=false. Repeats are safe. |
| `DELETE /api/v1/uploads/{id}` | Commit cancellation first, then attempt safe cleanup; 200 exposes cleanup_pending rather than claiming deletion when blocked. Repeats are safe. Only unvalidated upload data exists in Stage 3. |
| `GET /health` | Safe readiness; unhealthy registry returns 503. No paths/credentials. |
| `GET /client`, `/client/uploader.js` | Isolated protocol test page/assets; no user payload serving. |

Errors use `{"error":{"code":"..."}}`, without stack traces, content, secrets or full storage paths. 403 owner/capability/origin denial; 404 unknown ID; 409 offset/state/replay conflict; 410 expired writes; 413 configured size/actual chunk/JSON body limits; 422 metadata/hash/length/page errors; 429 one-active-upload; 507 capacity/quota/I/O; 503 registry unavailable/unhealthy. A registry error stops further acknowledgements until restart. Client disconnect is handled without logging content/credentials. Test-only fault hooks are constructor callbacks, not API/environment crash switches.

The test client stores owner credential, session capability, idempotency key and nonsecret source name/size in **same-tab sessionStorage**, clearly disclosed in its label; no file bytes are stored. Forget removes browser credentials without deleting server files. Close-tab/new-browser recovery is not guaranteed without retained credentials; this needs a production ownership/session UX before exposing a later Streamlit integration.

Refresh requires file reselection. Name/size must match, then **every acknowledged prefix chunk** is locally hashed against paginated server receipts before uploading more bytes. A changed prefix fails without new writes. This cannot prove the original never-uploaded suffix was unchanged without a trusted initial whole-file digest; no such claim is made. Whole-file Web Crypto buffering is prohibited.

Sequential XHR retries are bounded (four attempts with exponential backoff/jitter), query status after ambiguous responses, and replay the identical chunk safely. If the service remains unavailable or retries exhaust, the UI pauses for manual resume. Durable percent uses acknowledgements; tentative current-request bytes are explicitly not acknowledged. Pause aborts the browser request; cancellation waits for the current mutation/rollback. Network request deadline defaults to 120 seconds; a stalled kernel filesystem operation is not forcibly interruptible, so cancellation has no hard real-time guarantee.

## Capacity, expiry and cleanup

Default provisional settings: declared upload 50 GiB, chunk 8 MiB, I/O block <=1 MiB, 10 GiB free headroom, 100 GiB total logical upload quota, maximum 10,000 chunks/session, one active created/uploading session, idle TTL 72 hours, absolute TTL seven days; maintenance every 60 seconds. Compressed/expanded ZIP, XML/image/pixel/entry validation limits are **Stage 4**, not enforced/claimed here.

For each incomplete session reserve remaining declared payload bytes plus one scratch chunk (initially total bytes + min(total, chunk)). Physical free-space admission includes all outstanding reservations + new reservation + headroom. Already written payload occupies physical disk; logical quota also counts all retained uncleaned declared uploads, including failed/cancelled-pending data. Recheck capacity before a chunk and actual headroom during scratch and payload writes. External writers can still consume space after admission; failures are explicit, no false receipt. Metadata/tombstone growth is not independently quota-managed; headroom and operator monitoring remain necessary.

Only successful new chunk commits extend idle expiry; status polling and replay do not. Absolute deadline never extends. Maintenance is serialized with mutations and never deletes an active writer. Expiry commits expired/cleanup_pending first; cleanup releases reservation only after confirmed managed-file removal. A crash after deletion but before DB release safely retries cleanup on restart.

Known cancelled/expired session cleanup is nonrecursive: check directory/links/contents, unlink only payload.part/incoming.chunk, remove only the empty UUID directory, sync directories on POSIX, then transactionally release reservation/mark cleaned. Unknown contents or unsafe paths block deletion and retain cleanup_pending/reservation. Failed sessions require explicit authenticated cancellation. Unknown orphan names/directories without registry provenance are counted and **preserved**, never adopted or automatically removed. Operators must inspect such cases offline; no recursive administrator delete tool was added.

## Runtime and exact commands

Use the existing isolated Python 3.12 / pinned Stage 1/2 environment (`docs/PROTOTYPE_REQUIREMENTS_LOCK.txt`). Production dependency files are unchanged. New environment variables are documented in `.env.upload.example`; no dotenv loader is assumed.

Set a new empty private persistent directory and retrieve/generate stable secrets through an operator-controlled secret store. The following is an **initial local setup example**, not commands executed against a user data directory:

```powershell
$env:CVAT_UPLOAD_DIR = 'D:\CVATPrivateUploads\stage3'  # Operator chooses/verifies private persistent volume.
$env:CVAT_UPLOAD_SECRET = & .venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$stage3OwnerKey = & .venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$env:CVAT_UPLOAD_OWNER_KEYS_JSON = @{ local = $stage3OwnerKey } | ConvertTo-Json -Compress
# Retain the same server secret/owner configuration securely for subsequent restarts.
.venv\Scripts\python.exe -m uvicorn backend.durable_api:create_from_env --factory --host 127.0.0.1 --port 8011 --no-proxy-headers
```

Open `http://127.0.0.1:8011/client` and enter the owner's key using your local secret-management workflow. Do not regenerate the capability secret on restart of an existing root: mismatch refuses startup. Do not place secrets in source, URLs, logs or committed environment files. Ports/origins for this test client are fixed to 8011; any future proxy/origin/access changes require explicit design/testing. No deployment or real persistent user-data root has been provisioned by this task.

```powershell
.venv\Scripts\python.exe -m pytest tests/test_durable_store.py tests/test_durable_api.py tests/test_stage3_preservation.py -q
.venv\Scripts\python.exe -m pytest tests/test_durable_network.py -q
$env:CVAT_RUN_BROWSER_TESTS = '1'
.venv\Scripts\python.exe -m pytest tests -q --junitxml=test-results/stage3.xml
python -m pip --python .venv\Scripts\python.exe check
```

Browser tests require installed Chrome and free ports 8010, 8011, 8501, 8502. They only start/stop their own processes and private synthetic files. Windows virtual-environment launchers may spawn the actual Python server as a child; the new test lifecycle helper terminates only descendants of its recorded Popen process and waits for their exit. The test-host restricted token denied access to Python-private temporary directories as in Stage 1/2, so actual tests ran under approved elevated tool execution; no ACL/access-control changes were made to work around this.

## Storage and operating-system assumptions / limitations

- Single host, one API process, local persistent filesystem, functioning fsync and SQLite locking/VFS; no NFS/SMB, replicas, shared worker roots or object storage. Actual process-restart tests ran on Windows 11 build 26200, Python 3.12.14 and Chrome 155.0.8059.40. POSIX-specific directory sync has not been exercised here.
- Operator must enforce private ACLs: only service identity/necessary administrators may mutate the root. The implementation rejects link/reparse/hardlink paths but does not provision/audit ACLs or provide protection against an administrator/another authorized local writer racing pathname checks. No arbitrary client paths exist.
- Root layout: marker + kernel lock + SQLite DB/journal + uploads/UUID/payload.part and incoming.chunk. Original prototype storage is not adopted. Never configure static/public, filesystem root, an unrelated directory or an untrusted-writable path. Network/ephemeral volumes are unsupported.
- Real production path, disk capacity/reserve/quota, ACLs, backup/restore and stable secret custody are **not yet selected or verified**. Tests use private pytest directories, not user datasets. Stale uploaded files, failed sessions, unknown files and permanent tombstones can accumulate; monitor capacity and inspect safely.
- Source name/size/prefix proof does not authenticate unuploaded suffix or validate ZIP contents. Final full-file integrity/reference-digest policy and ZIP64/ZIP bomb/XML/image safety belong to Stage 4. An uploaded state is never a validated/ready dataset.
- No 20–50 GB run, ZIP64 validation, memory-capacity/throughput benchmark, power-cut test, production proxy or multiuser public deployment was performed. Configurable limits do not establish verified capacity. Public exposure is prohibited at this checkpoint.
- Status durable=true records the protocol's prior fsync/transaction guarantee, not continuous device-health monitoring. Receipt hashes are fully rechecked at startup; hardware corruption or privileged external modification after startup is not continuously detected. Such changes are outside the healthy-volume/private-ACL assumption and can invalidate stored data.

## Decisions needed before Stage 4

1. Confirm actual private persistent root, local filesystem/volume capacity, ACL/service identity, operational disk reserve/quota, monitoring and stable secret/backup custody. Keep current local-only boundary unless an approved ownership/authentication deployment is provided.
2. Confirm real dataset statistics and validation policies: expanded bytes, central-directory/entry count, XML size/record limits, encoded-image/pixel limits, allowed compression methods/encryption, malformed annotation capability behavior and validation deadlines.
3. Agree ready-dataset promotion and retention; uploaded/failed data currently requires explicit cancellation, unknown orphans are preserved, and namespace/power-loss guarantees depend on target OS/volume.

Actual test commands/results are in `DURABLE_UPLOAD_STAGE3_TEST_RESULTS.md`. Stage 4 has not started; wait for review and approval.
