# Large ZIP upload: repository audit and implementation plan

Audit date: 2026-10-16 (Asia/Saigon). Historical audit plan; accepted Stage 1/2 evidence is in `UPLOAD_PROTOTYPE_CHECKPOINT.md`. Current Stage 3 status is linked below; stop before Stage 4.

Stage 3 update: durable transport is now implemented and stops at uploaded/unvalidated. See `DURABLE_UPLOAD_STAGE3.md` and `DURABLE_UPLOAD_STAGE3_TEST_RESULTS.md`. Stage 4 remains unapproved and unimplemented; the original audit/design below is historical.

Requested destination: `D:\CVAT_Dashboard_Scene_Finder\docs\LARGE_ZIP_UPLOAD_PLAN.md`.

Historical delivery limitation: Windows initially denied creation of `docs`. The user updated permissions, and this plan was subsequently saved and hash-verified at the requested repository destination. The audit findings and proposed later stages below describe the original pre-implementation snapshot; current test evidence is recorded separately.

## 1. Scope and audit evidence

The immediate goal is uploading ZIPs of 20–50 GB through the existing Streamlit page, with progress, durable retry/resume, disk storage, safe validation and integration with existing analysis. A backend accepting large files without this browser integration does not satisfy the goal.

Read in full: `BACKEND_AGENT_TASK.md`, `BACKEND_TASK_01_LARGE_ZIP_UPLOAD.md`, both READMEs, `requirements.txt`, `app.py`, `ui_theme.py`, and all source in `core/`. The broader brief describes later FastAPI analysis APIs and a React consumer; those later migrations are outside this step. Its instruction to continue implementing is superseded by the user's explicit audit-only instruction. Only this document is produced; `docs/BACKEND_PLAN.md` and `docs/LARGE_UPLOAD_DESIGN.md` requested by those briefs are future implementation deliverables.

Observed source inventory: `app.py` (691 lines), `ui_theme.py`, empty `core/__init__.py`, `core/distribution.py`, `core/coverage_dynamic.py`, `core/coverage_ui.py`, `core/scene_finder.py`, two READMEs, two task briefs, requirements and generated `__pycache__` files. No backend, tests, applicable `AGENTS.md`, project `.streamlit/config.toml`, environment example, deployment/proxy files or upload storage implementation was found.

`git status --short` reported this directory is not a Git repository. Git history/ignored files/branch state cannot be established. No commit/push was attempted. SHA-256 hashes of existing source/documents were recorded for preservation checks.

Available `python` is `C:\Python314\python.exe`; Streamlit is absent in it. Streamlit skill discovery failed with Windows error 1920 while launching a detected interpreter. No packages were installed and no application tests/imports were run. Existing `cpython-311` cache filenames do not establish the deployed interpreter. Deployed Streamlit/Python versions, browser, infrastructure, authentication and hardware remain unknown. Source line references below refer to this snapshot. Proposed choices are not existing behavior or measured performance.

## 2. Current architecture and upload workflow

```text
Browser -> st.file_uploader -> uploaded.getvalue() -> zip_data bytes
  -> full SHA-256 on every rerun
  -> read_images_from_zip -> all accepted image bytes in list[ZipImage]
  -> load_cvat -> full XML tree -> image/box DataFrames
  -> session_state retains inputs and results
  -> analyze every image; audit all labels before rendering tabs
  -> overview / distribution + Scene Finder / quality / labels /
     image review / CSV export / coverage
```

There is no separate HTTP backend. `app.py` combines UI, archive parsing, decoding and QA/QC. Importing it executes Streamlit page/UI logic; a backend must not import it.

| Source / functions | Observed behavior and implication |
| --- | --- |
| `app.py:19–22`, `429–437` | Initializes UI/theme/sidebar, calls `st.file_uploader(..., type=["zip"])`, stops without a file. No application chunk/resume contract or upload component. |
| `app.py:439–442` | `uploaded.getvalue()` obtains whole ZIP bytes. `hashlib.sha256(zip_data)` runs outside cache guards on every rerun: O(compressed bytes) repeated cost. |
| `app.py:153–185`, `ZipImage`, `read_images_from_zip()` | Uses `ZipFile(io.BytesIO(zip_bytes))`, then `archive.read(info)` into retained image objects. Skips some macOS metadata, nonimages, directories and image entries over 50 MiB. Raises when sum of accepted image entry sizes exceeds 1 GiB. This is not an all-members expansion or compressed-ZIP limit. |
| `app.py:190–226`, `load_cvat()` | Reads complete XML and builds an ElementTree and full tables. Picks shallowest/shortest member with normalized basename `annotations.xml`. Matches images by normalized exact path, unique suffix, then unique basename. No XML size bound. |
| `app.py:444–480` | Session caches include image payload list, CVAT tables, image results and audit results. All image/label analysis happens automatically before tabs exist; threshold changes rerun corresponding analysis. |
| `app.py:55–151`, `analyze_image()`, `estimate_noise()` | OpenCV decoding and full-resolution gray/float/gradient/Laplacian arrays raise per-image peak RAM. Results retain `image_bytes` even on failure. References may share buffers rather than duplicate them, but prevent release. |
| `app.py:228–233`, `decode_image()` | Fully reads member and decodes it; has no shared 50 MiB/pixel guard. Label audit and annotated preview can read an image skipped by the initial loader. |
| `app.py:276–370`, `audit()`, `visual_metrics()` | Reopens byte-backed ZIP, decodes one image at a time, retains box result list, filters box table per image and compares boxes pairwise. Calls `st.progress` directly. Dense-image audit cost is independent of upload transport. |
| `core/distribution.py:6–33`, `parse_distribution()` | No Streamlit dependency, but accepts ZIP bytes and reads full XML into a tree/tables. Chooses first name ending in `annotations.xml`, unlike `load_cvat`; do not silently unify semantics. |
| `app.py:518`, `688–689` | Separately reparses distribution XML for distribution and coverage on reruns, outside image/label cache guards. |
| `core/distribution.py:35–52` | Pure pandas calculations. `attribute_distribution()` builds intermediate records across attributes and boxes; metadata can itself become large. |
| `core/coverage_dynamic.py`, `build_universe()` and related functions | Pure pandas coverage logic, including copied/merged metadata and user-defined targets; no upload handling. Preserve calculations and missing/unknown semantics. |
| `core/coverage_ui.py:7–102`, `render_coverage()` | Streamlit UI/session target editing and reset by dataset signature. Keep UI-only. |
| `core/scene_finder.py:17–30`, `63–88` | Streamlit renderer, matching resolver, metadata filters and preview pagination. Reopens byte-backed ZIP; previews read individual members after 50 MiB check. Pagination does not bound total metadata. |
| `app.py:631–673` | Original review depends on retained result image bytes; annotated review reopens byte-backed ZIP and calls unguarded `decode_image`. Both need lazy disk access. |
| `app.py:576–578`, `675–681`, coverage/Scene Finder exports | Builds complete CSV strings/encoded bytes; tables/plots receive full metadata/results. Separate memory/browser bottleneck. |
| `requirements.txt` | `streamlit>=1.35`, pandas, numpy, OpenCV headless, Pillow, Plotly. No backend/ASGI/safe XML/test dependency declarations; versions otherwise unpinned. |
| `ui_theme.py`, READMEs | Presentation and `streamlit run app.py` launch instructions; no transport/storage. |

### Limits and temporary storage

Streamlit documents `UploadedFile` as a `BytesIO` subclass and describes upload storage as RAM-backed. Current public documentation gives a default 200 MB per file; actual deployed overrides/effective limits are unknown. No override is set in source or project config. File extension filtering is not security validation. See [Streamlit file uploader](https://docs.streamlit.io/develop/api-reference/widgets/st.file_uploader) and [upload storage lifecycle](https://docs.streamlit.io/knowledge-base/using-streamlit/where-file-uploader-store-when-deleted).

No application disk spool, durable upload sessions, capacity checks, cancellation, TTL/cleanup, restart recovery or complete all-member integrity scan exists. Streamlit's widget lifecycle is not persistent dataset storage. Raising `server.maxUploadSize`, or copying the received widget value to disk afterward, leaves initial RAM use and resume unresolved. Regardless of internal transport segmentation in a Streamlit version, the application lacks a durable user-visible chunk/offset contract. A dedicated endpoint **and** embedded browser component are required.

## 3. Critical blockers and root causes

1. Whole-file widget upload and byte-oriented consumer interfaces; no durable state or resume after refresh/restart.
2. Retained bytes of every accepted image; increasing the 1 GiB guard would increase RAM risk.
3. Inconsistent safeguards: image limits are bypassed in other readers; XML, decoded pixels, entry count, directory size and all-member expansion are unbounded by application policy.
4. Synchronous analysis blocks UI; hashing/XML work repeats; audit is coupled to `st.progress` and dense box comparisons remain expensive.
5. Full metadata DataFrames, results, CSVs and plots can exhaust memory even after image payloads move to disk. Compressed size alone cannot define analysis capacity.

There is no extraction in current source; an existing `extractall` vulnerability is not claimed. New storage must nevertheless reject malicious member paths and avoid extraction.

## 4. Proposed architecture and choices

```text
Streamlit page + bidirectional upload component
  Browser File.slice + XHR -> FastAPI raw chunk endpoints
  -> bounded streamed writes -> private persistent filesystem
  -> SQLite session/chunk receipts/reservations/jobs
  -> bounded validation worker -> ready dataset ID/manifest
Streamlit adapter -> authorized shared dataset registry
  -> pure core path-backed readers -> existing QA/QC algorithms
  -> persisted result rows + bounded metadata -> current tabs/reports
```

Use a small custom Streamlit component with a maintained component bridge and plain JavaScript file input/progress/retry controls; no React application. File bytes remain between browser and API. Only IDs and small status events cross into Streamlit Python. Prototype bridge/build compatibility against the agreed Streamlit version: the repository's `>=1.35` cannot establish support for newer APIs. See [Streamlit custom components](https://docs.streamlit.io/develop/concepts/custom-components/components-v1/intro).

Browser `File`/`Blob.slice()` supplies one chunk; XHR upload events show tentative transfer progress. Server acknowledged bytes show durable progress. Never use whole-file `arrayBuffer()` or whole-file browser hashing. See [Blob slicing](https://developer.mozilla.org/en-US/docs/Web/API/Blob/slice) and [XHR upload progress](https://developer.mozilla.org/en-US/docs/Web/API/XMLHttpRequest/upload).

Use FastAPI/Pydantic and Uvicorn. Consume raw bodies via `Request.stream()`, count actual bytes, hash incrementally and write bounded blocks. Do not call `request.body()`, multipart aggregation or body-logging middleware for chunk routes. Offload blocking disk work with bounded concurrency. [Starlette streaming](https://starlette.dev/requests/) supports body iteration without whole-body accumulation.

Use `zipfile.ZipFile(path)` and ZIP64, not ZIP `BytesIO`. The completed ZIP must be seekable. Python supports archives over 4 GiB and warns about decompression resource hazards; see [ZIP documentation](https://docs.python.org/3/library/zipfile.html). Use reviewed `defusedxml` streaming XML parsing with explicit limits. Select/test/pin compatible versions during implementation; dependencies remain unchanged now.

Use stdlib SQLite for transactional sessions, receipts, capacity reservations, dataset ownership and durable job state; ZIPs stay on disk. This adds no database service and avoids JSON multi-file update races. MVP: one host, local persistent shared volume, one API worker process, one bounded validation/analysis worker process, initially one active upload and one analysis. Multiworker/replica/network-filesystem behavior is out of scope until separately tested.

Keep core independent of HTTP and Streamlit. A shared storage package resolves authorized dataset IDs; core receives a trusted path/reader, never client paths or backend imports. Cache using immutable dataset digest + configuration + algorithm version, not ZIP rehash per rerun.

### Integration with existing functionality

- Extract quality/label functions minimally; replace byte source and `st.progress` with reader interfaces/optional callbacks. Keep thresholds, score formulas, geometry/visual flags, priorities and all status names (`PASS`, `REVIEW`, `ERROR`, `NO_SIGNAL`, `A_ONLY`, `REVIEW_A_ONLY`).
- Decode one bounded image at a time and persist scalar results. OpenCV may need the full encoded **single image**, so a bounded per-image read is acceptable. Centralize byte/pixel guards for analysis and every preview. No all-image list or image bytes in session results.
- Stream and clear XML elements; preserve the distinct annotation-selection/parser projections until compatibility tests justify consolidation. Parse once per dataset. Admission limits must cap metadata before DataFrame construction; existing pandas operations remain supported within that envelope, not unlimited.
- Persist analysis rows; supply bounded pages, exact aggregates and streamed CSV. Preserve exact chart/statistical meanings within admitted limits; do not silently sample. If exact operations exceed budget, return a resource-limit message. Implement necessary disk queries rather than retaining image bytes or unbounded result tables.
- Preserve all seven tabs, Scene Finder filters/resolution/pagination/CSV, original/boxed previews, thresholds, coverage editing/reset and report schemas. Add explicit analysis start/progress/cancel for large data so pending analysis is visible and does not block upload completion requests. Retain small-file convenience where safe.
- Keep `streamlit run app.py` and small ordinary-widget uploads working. Feed small data through shared path-backed readers after bounded receipt. Add a visible “Large ZIP / resume” component within the existing page; large files must never enter the legacy `getvalue()` path.

## 5. Upload contract and states

All following contracts are proposed. IDs are server UUIDs; byte counts are integers; responses never expose local paths. `/docs` provides schemas and `/health` safe readiness.

| Endpoint | Success contract | Failure cases |
| --- | --- | --- |
| `POST /api/v1/uploads` | JSON filename, size_bytes, optional sha256; `201`: upload ID, capability, chunk size, committed bytes/offset/index, expiry, applied limits. Owner-scoped idempotency key prevents duplicate reservations after response loss. | `422` invalid metadata; `413` too large; `429` quota/concurrency; `507` insufficient capacity. |
| `PUT /api/v1/uploads/{id}/chunks/{index}` | Raw octet stream, `Upload-Offset`, `X-Chunk-SHA256`, exact length; `200` only after durable commit. Identical committed replay returns same receipt. | `409` offset/order/state/differing retry; `422` length/digest; `413` oversized; `410` expired; `507` disk full. |
| `GET /api/v1/uploads/{id}` | `200`: expected/committed bytes, percentage, next index/offset, expiry, state, dataset/job ID, validation progress and safe error. Optional bounded paginated receipt hashes for reselection verification. | Unknown `404`, expired `410`, authorization failure. |
| `POST /api/v1/uploads/{id}/complete` | Seal only after all bytes; `202` schedules validation with stable dataset/job ID. Repeats return same IDs/current state; ready repeat `200`. | Incomplete/invalid state `409`; asynchronous validation failures appear in status. |
| `DELETE /api/v1/uploads/{id}` | Idempotent `204` for owned temporary session; coordinate worker cancellation before deleting managed temporary files. | Ready dataset protected `409`; unknown `404`. |
| `GET /api/v1/datasets/{id}/summary` | `200`: state, ZIP bytes/digest, entry/image counts, annotation presence, warnings and capabilities. Pending is explicit; analysis requires ready. | Unknown `404`, authorization failure. |

Example create request/response (placeholders):

```json
{"filename":"dataset.zip","size_bytes":50000000000,"sha256":null}
```

```json
{"upload_id":"<uuid>","state":"created","size_bytes":50000000000,
 "chunk_size_bytes":8388608,"received_bytes":0,"next_offset":0,
 "next_chunk_index":0,"expires_at":"<UTC timestamp>",
 "upload_token":"<secret>","max_zip_bytes":53687091200}
```

```json
{"error":{"code":"OFFSET_MISMATCH","message":"Resume from the acknowledged offset.",
 "retryable":true,"next_offset":16777216,"request_id":"<opaque-id>"}}
```

Stable errors include UPLOAD_EXPIRED, CHECKSUM_MISMATCH, INSUFFICIENT_STORAGE, INVALID_ZIP, NO_IMAGES, RESOURCE_LIMIT and UNSUPPORTED_ZIP. Invalid/unsafe archive validation produces failed state and safe validation error (422 class), never ready. Serialize nonfinite analysis values as JSON null without changing numerical algorithms.

```text
created -> uploading -> uploaded -> validating -> ready
              |             |           |
              +-------------+-----------+-> failed
created/uploading/uploaded/validating -> cancelled (coordinated)
created/uploading/uploaded -> expired
```

Uploaded means all declared bytes are durable. Ready means hash policy, all-member integrity/resource validation and indexing passed, not that QA/QC completed. Analysis has separate queued/running/complete/failed/cancelled state.

## 6. Chunk commit, retry, resume and cleanup

Private one-volume layout:

```text
<root>/registry.sqlite3
<root>/uploads/<upload_id>/payload.part
<root>/uploads/<upload_id>/incoming.chunk
<root>/datasets/<dataset_id>/archive.zip
<root>/datasets/<dataset_id>/manifest.json
<root>/datasets/<dataset_id>/results.sqlite3
```

1. Creation validates metadata, reserves unreceived capacity transactionally and stores owner/token hash. Reservation is accounting, not allocated disk proof; check actual capacity throughout writes.
2. Serialize each session with a lock/lease. Sequential chunks only; expected length is min(chunk size, remaining bytes). Stream one scratch chunk, enforcing actual size and SHA-256. Partial/disconnected/mismatched chunks do not advance offset.
3. Append verified scratch to payload, flush/fsync payload, then transactionally persist receipt/index/length/digest/new offset before acknowledgement. Delete scratch after commit. No full-file assembly copy.
4. On restart truncate payload beyond committed offset and remove stale scratch under lease. If metadata claims bytes missing from disk, fail storage integrity rather than inventing progress. Hash replay body to verify identical receipt; differing replay conflicts. Test crashes before/after each durability boundary.
5. Completion seals writes and creates durable validation job/stable dataset ID. Hash entire ZIP in bounded blocks, compare optional client full SHA, and always store server SHA. After successful validation close handles, same-volume rename and register ready. Use promoting marker/recovery reconciliation for file-versus-DB crash windows. Cross-volume promotion is disallowed in MVP; Windows open-handle/rename behavior needs real tests.

Browser strategy:

- Start with 8 MiB sequential chunks and one in-flight buffer. Hash each chunk; optional whole-file client digest must be incremental, not one huge Web Crypto input.
- Retry network failures, 408/429 and transient 5xx with bounded exponential backoff/jitter, honoring Retry-After. After finite automatic attempts pause visibly with manual resume available. Query status after ambiguous acknowledgement; reconcile 409 rather than blindly resend. Permanent metadata/checksum/expired failures need user action.
- Persist nonsecret session descriptor and name/size/last-modified/sample hashes. Keep capability in protected same-origin session storage or a validated secure-cookie design, never URL/log. Same-tab reload may restore credentials; closed-tab recovery requires an existing user session or explicit saved recovery credential. Prototype and document exact supported lifecycle.
- Browser file inputs usually require reselection after refresh. Compare descriptor plus bounded samples and verify **every already committed chunk** against paginated receipt hashes before resuming a reselected file. This avoids combining files sharing superficial metadata; it reads local prefix but does not retransmit it. A server-only final digest cannot prove correspondence to the intended original file.
- Separate current-request transfer from committed progress and validation progress. At 100% bytes show validating, not ready. Stable component identity must survive Streamlit reruns without duplicate sessions. No st.* calls from workers.

Proposed TTL: 72 hours inactivity, renewed by chunk commits rather than polling, seven-day absolute incomplete lifetime. Never expire actively leased writes/validation solely by upload idle time. Persist worker leases/heartbeats; restart can rerun validation idempotently from sealed ZIP.

Cleanup at startup/periodically locks, tombstones and removes only expired/cancelled owned temporary files. Keep tombstones for 410 reporting; retain failed sealed uploads privately for a proposed 24-hour diagnostic window. Do not follow symlinks/reparse points or delete active jobs, ready datasets or original user files. Release reservations after cleanup succeeds. Provide operator inventory/dry-run cleanup and explicit dataset removal with active leases; ready datasets have no silent TTL deletion.

## 7. Capacity, memory, validation and security

### Proposed configurable limits (not measured capacity)

| Setting | Initial proposal |
| --- | --- |
| DATASET_STORAGE_DIR | Required private absolute directory on persistent local volume. |
| MAX_ZIP_BYTES | 50 GiB = 53,687,091,200 bytes, encompassing decimal 50 GB. |
| UPLOAD_CHUNK_BYTES / IO_BLOCK_BYTES | 8 MiB / 1 MiB application block; ASGI buffering also measured. |
| MAX_ACTIVE_UPLOADS / MAX_ACTIVE_ANALYSES | 1 / 1 initially. |
| MIN_FREE_DISK_BYTES | 10 GiB beyond outstanding reservations/working allowance. |
| MAX_UNCOMPRESSED_BYTES | 250 GiB across all members, declared and actual. |
| MAX_ZIP_ENTRIES / MAX_CENTRAL_DIRECTORY_BYTES | 100,000 / 32 MiB. |
| MAX_XML_BYTES | 32 MiB. |
| MAX_IMAGE_BYTES / MAX_IMAGE_PIXELS | Existing 50 MiB individual policy / initial 16 million pixel guard. |
| MAX_COMPRESSION_RATIO | Initial 200:1 per-entry and aggregate; explicit zero-size handling. |
| MAX_METADATA_IMAGES / MAX_METADATA_BOXES | 100,000 / 250,000, tuned downward if pandas memory requires. |
| UPLOAD_IDLE_TTL_SECONDS / UPLOAD_MAX_LIFETIME_SECONDS | 259,200 / 604,800. |
| FAILED_UPLOAD_TTL_SECONDS | 86,400. |
| ALLOWED_ORIGINS, public API URL, request idle and job deadlines | Deployment-specific; choose from representative throughput measurements. |

Add global/per-owner storage quota and results working budget before shared deployment. Keep legacy widget limits separate; do not raise Streamlit globally to 50 GiB. Dataset envelopes may legitimately reject files below compressed limit; expose these limits clearly.

Disk model: retained datasets + sum of active declared ZIP sizes + one scratch chunk per writer + bounded results/metadata allowance + safety reserve. Admission must include outstanding unreceived reservations in addition to current filesystem usage. Recheck physical space and handle ENOSPC/quota/write/fsync errors without committing bytes. Sparse logical size is not evidence of allocated disk capacity.

Illustration only: a 50 GiB ZIP, 5 GiB results allowance, 10 GiB reserve and 8 MiB scratch need about 65 GiB initially free with no retained datasets. The results allowance is not measured. No full extraction/second assembly copy is expected. Proxy buffering, backups, snapshots or cross-volume copies add space. FAT32 cannot hold these files.

Upload RAM should scale with request/block/chunk buffers, not ZIP size. Browser RAM should scale with one chunk. Validation still uses directory/XML metadata; analysis additionally uses encoded image bytes, decoded pixels, OpenCV float/float64 arrays, metadata copies, tables, caches and concurrency. A compressed-image byte cap alone is inadequate. Initial measurement targets: API RSS increase <=128 MiB over warmed idle for one upload, and combined analysis/worker/Streamlit peak <=2 GiB on the agreed representative workload. These are proposed gates to measure/revise, not achieved values. Measure OS private/RSS and browser memory; tracemalloc alone misses native allocations.

### Validation sequence

1. Before ordinary ZipFile construction, inspect bounded ZIP/ZIP64 tail/end records; validate offsets, central-directory bytes/count and reject split/multidisk formats. Checking infolist count only afterward is too late to cap allocation. Test malformed ZIP64 arithmetic and isolate resource-heavy parsing.
2. Open from disk; enforce entry counts, sizes, ratios, methods, encryption flags and overlapping/out-of-bounds structures. Start with stored/deflate and explicit rejection of unsupported/encrypted entries. ZIP64 support is mandatory.
3. Reject absolute/drive/UNC/traversal/NUL/control names, symlinks and normalized collisions/duplicate entries; do not sanitize malicious paths into acceptable ones. Existing lstrip-based helpers are name matching, not security validation. No extraction by original paths.
4. Stream every nondirectory member to EOF in bounded blocks, verifying CRC and **actual** per-member/total expansion, including nonimages. Directory sizes alone and unrestricted testzip are insufficient. Enforce time/CPU limits with progress/cancellation; validate without full extraction.
5. Preflight image headers/dimensions before guarded decode on all reader paths. Structural/resource/CRC failures fail upload. A CRC-intact but undecodable image remains an Image Quality ERROR/read warning within limits, preserving QC behavior.
6. Safe streaming CVAT XML denies DTD/entities/external access and bounds bytes/depth/records/attribute sizes. Image-only ZIPs become ready with annotation-dependent capabilities disabled. Empty/no-image ZIPs fail as current UI does. Safe but malformed annotations may yield image-only capability with explicit warning; forbidden XML/resource violations fail validation. Annotation-only dataset acceptance is deferred because current upload gate rejects it.
7. Persist counts, skips/capabilities, digest, validation coverage, versions and timings. Publish ready only after durable validation/registration. CRC integrity does not assert annotation correctness or successful image decoding.

### Security and deployment

Private storage outside static/public, least-privilege ACLs, server-generated names and ID-based lookup only. Sanitize display filenames/log values. Enforce ownership on chunk/status/summary/preview/report access; IDs alone are not authorization. Use owner-bound high-entropy upload capability with hashed storage and retain dataset access protection after completion. A local/private MVP needs no new identity platform; shared public use requires integration with trusted identity/gateway first.

Remote TLS, exact CORS allowlist, cookie CSRF protection where applicable, rate/session quotas, small JSON limits, no wildcard with credentials. Log IDs/state/byte counts and safe error codes, never image content/tokens/full paths/stack traces. Component assets may be public; uploaded datasets may not. Verify iframe origin/CSP/credentials/preflight in browser spike.

Prefer same-origin proxy routing for API and Streamlit; preserve Streamlit WebSockets. The API URL must work from the user's browser, not only server localhost. Set per-request body limits around chunks with overhead; avoid monolithic 50 GB requests. Disable proxy request buffering where supported or account for its disk, and align proxy/ASGI/browser idle timeouts for slow chunks. Verify prefix routing, TLS, persistent volume, restart recovery and disk permissions on actual deployment. Ephemeral-only hosting is unsuitable without explicit persistent storage.

## 8. File-by-file implementation plan (after approval)

Only this document is created now. Planned additions/modifications:

| File(s) | Change |
| --- | --- |
| app.py | Embed component; IDs/readers instead of large bytes; shared core calls, lazy previews, job controls, bounded tables/exports; retain tabs/thresholds/small upload. |
| ui/upload_component/__init__.py; ui/upload_component/frontend/index.html, src/uploader.js, package.json, build.mjs; generated frontend/dist/ | Maintained component bridge, plain JS slice/XHR/resume, reproducible static build included so running Streamlit does not require Node. Pin bridge/build versions after prototype. |
| ui/dataset_adapter.py | Authorized registry lookup, event validation and UI progress/orchestration; no large bridge payload. |
| backend/__init__.py, backend/main.py, backend/config.py | FastAPI lifecycle/health/OpenAPI/config/recovery/cleanup startup. |
| backend/api/uploads.py, backend/api/datasets.py, backend/schemas.py | Upload API and summary/job status/cancel/authorized preview/report operations needed by this Streamlit phase, stable schemas/errors. Full later analysis API deferred. |
| backend/services/uploads.py, backend/services/jobs.py | Protocol orchestration and bounded durable validation/analysis scheduling; no Streamlit imports. |
| storage/__init__.py, storage/registry.py, storage/files.py | Shared SQLite receipts/ownership/reservations/leases, file streaming/commit/promotion/recovery/cleanup; no UI/HTTP dependency. |
| core/archive.py, core/archive_validation.py | Path-backed guarded reader, ZIP64 preflight, entry/index/path/resource/CRC validation and manifest. |
| core/cvat_parser.py | Safe streaming projections preserving load_cvat/distribution behaviors. |
| core/image_quality.py, core/label_audit.py | Minimal extraction of existing algorithms/drawing with readers/callbacks and scalar results. |
| core/results.py | Disk row persistence, bounded pages, required exact queries/aggregates, streaming CSV. |
| core/distribution.py | Reader/parser adaptation; preserve distribution calculations. |
| core/scene_finder.py | Trusted reader-based guarded previews; retain UI/filter/resolver semantics. |
| core/coverage_ui.py | Minimal stable ID/signature/parsed metadata integration; bounded export if necessary. |
| core/coverage_dynamic.py, ui_theme.py, core/__init__.py | No algorithm/theme changes planned; necessary exports only if justified. |
| backend/requirements.txt, requirements-dev.txt; requirements.txt | Future compatible backend/safe XML/test dependencies, retain existing packages; document combined installation. No changes now. |
| .env.example, .gitignore | Nonsecret config; exclude storage, tokens, temporary/large test data. |
| scripts/run_upload_worker.py, scripts/storage_admin.py, scripts/benchmark_large_upload.py | Worker entry, operator inventory/dry-run cleanup, opt-in evidence-producing benchmarks. |
| tests/conftest.py; tests/unit/test_upload_protocol.py, test_storage_recovery.py, test_archive_validation.py, test_core_parity.py; tests/integration/test_upload_api.py, test_jobs.py; tests/e2e/test_streamlit_upload.py | Synthetic, protocol/security/recovery/parity/API/browser tests. |
| README.md, README_SCENE_FINDER.md; README_BACKEND.md, docs/BACKEND_PLAN.md, docs/LARGE_UPLOAD_DESIGN.md, docs/LARGE_UPLOAD_VALIDATION.md | Launch/config/build/API/proxy/retention/resume guidance and explicit tested/unverified evidence. |

Shared storage slightly adjusts the broader brief's suggested structure to avoid core/Streamlit depending on HTTP backend internals. Backend must not import app.py, coverage_ui, scene_finder or ui_theme. No React or unrelated business refactoring.

## 9. Implementation stages

1. Approval/environment/baseline: agree deployment/limits, establish supported Python/Streamlit, synthetic golden outputs and behavior inventory; align broader design docs.
2. Browser component spike: small-file selection/direct chunks/progress, stable reruns, refresh/reselection and origin/credential handling. Resolve frontend compatibility before backend completion.
3. Storage/protocol: durable offsets/receipts/reservations, streaming, recovery, cancellation/expiry; gate on crash-window/disk-failure/idempotency tests.
4. Validation/registration: ZIP64 preflight, bounded all-entry CRC, safe XML/capabilities and worker/recovery; gate on adversarial fixtures and no premature ready.
5. Pipeline adaptation: minimal extraction, readers, scalar disk results, bounded metadata/lazy previews; gate on algorithm/output parity and no large-byte path.
6. Integrated Streamlit/operations: all existing features, analysis progress, restart/browser/proxy smoke tests; gate on no duplicate sessions/jobs across reruns.
7. Controlled scale runs: opt-in allocated >4 GiB ZIP64 and real 20/50 GB browser/proxy uploads, integrity and representative analysis with measured resources. Production-size acceptance remains open until this evidence exists.

## 10. Unit, integration and end-to-end testing

No tests below were run in this audit. Use synthetic images/XML/ZIPs, no private data or Internet dependency; never commit giant fixtures.

Unit coverage:

- Metadata/size/quota creation limits, offsets/index/last-short-chunk, mismatched headers versus actual size, digest failures, identical/different replay, early/repeated complete, cancel/expiry/unknown IDs.
- Mid-request disconnect, disk write/flush/fsync/rename failure and crash injection at append/DB commit/ack/promotion boundaries; idempotent restart repair. Concurrent append/complete/cancel/cleanup cannot corrupt/delete active data.
- Reservation/capacity races, retained datasets, scratch/results budgets and failed cleanup; Windows containment/reparse/open-handle behavior.
- ZIP64/malformed directory/tail, CRC/truncation, traversal/UNC/drive/backslash names, collisions/symlinks, encryption/methods, split ZIP, entry/directory/XML/pixel/member/ratio limits and actual nonimage expansion; unsafe XML.
- Image-only/CVAT/no-image/missing/unsafe/malformed XML; annotation-selection differences, ambiguous names, missing referenced images, unreadable image, invalid geometry/nonfinite metrics and metadata missing versus unknown.
- Golden parity for quality score/status, label geometry/visual flags/priority, exact distribution/coverage and Scene Finder/filter/CSV behavior; explicit float tolerances. Baseline capture must not introduce backend import of app.py.
- Reader spies reject unbounded reads; inspect large-file paths for getvalue/ZIP BytesIO and retained payloads; check cleanup on exceptions. Static checks alone do not prove peak memory.

API integration:

- Real streamed HTTP bodies, not just buffered test-client input: init/chunks/status/restart/resume/complete/validate/summary, final exact length and independently computed hash.
- Cut connection mid-chunk and lose response after commit; replay, changed-file receipt mismatch, validation process kill/recovery; stable IDs and no duplicate jobs.
- Safe structured responses, nonfinite JSON sanitation, cross-owner denial, CORS/CSRF as applicable, request byte limits independent of headers.
- Status/health responsiveness during writes/CRC/analysis, quotas/leases/cancel/deadlines and no ready publication before integrity completion.

Browser/regression E2E:

- Launch actual Streamlit/API/worker; choose file through embedded component; observe committed versus tentative progress and validation; open every tab, previews and exports against expected fixture.
- Network interruption/reconnect, refresh/reselect and server restart at known offset; no acknowledged bytes resent. Refuse changed file even with same superficial metadata. Test credential recovery lifecycle explicitly.
- Streamlit reruns during upload, duplicate prevention, correct dataset/signature/coverage reset; intended proxy prefix/TLS/CSP/iframe/preflight behavior.
- Legacy small upload, thresholds, image-only/read-error data, Scene Finder unknown/missing/no-results, original/boxed review, coverage target editing and all CSV headers/data.
- Browser memory/responsiveness plus OS RSS/private memory. A CLI or buffered API upload is not browser end-to-end proof.

## 11. Acceptance and honest scale validation

Functional gates:

- ZIP selection/progress/retry/refresh-resume/cancel from Streamlit; bytes go directly to disk-backed API. Restart recovers durable state.
- Offset advances only after commit; replay safe, incomplete/corrupt payload never ready; ZIP64 and counts above 32-bit verified.
- All large consumers use path/bounded reader, no whole ZIP bytes or retained dataset image payloads; every access applies resource guards.
- All-member CRC and ZIP/XML/image/security limits enforced; image-only capability preserved; small-data algorithms/statuses/UI/report behavior compatible.
- RAM/disk/concurrency measured within supported image/metadata envelope; analysis is separate from upload completion request, with its own status/errors. No silent deletion.

| Evidence level | Demonstrates | Does not prove |
| --- | --- | --- |
| Small synthetic tests/tiny chunks | Protocol, validation, parity | 20–50 GB browser/network capacity. |
| Mock offsets >4 GiB/fault injection | Arithmetic/recovery/error branches | Real disk allocation/throughput/RSS. |
| Sparse file or very compressible ZIP | Selected logical-size/ZIP64 behavior | Tens of GB actually transferred or realistic resource pressure; ratio guard may reject correctly. |
| Real allocated >4 GiB ZIP64 upload | Large offsets/ZIP64 on that setup | 20/50 GB acceptance. |
| Real 20 and 50 GB browser uploads through target proxy plus validation/representative analysis | Recorded environment/files/workflow meet measured gates | Every possible dataset, resolution or entry count. |

Actual acceptance uses allocated compressed ZIPs of at least **20,000,000,000** and **50,000,000,000** bytes; record exact byte values and optionally additional GiB runs. Use representative CVAT images/XML and incompressible stored payloads where necessary for actual transfer size, avoiding compression bombs. Filler proves transport only; representative image/label workloads must separately prove pipeline behavior within declared limits.

Record hardware/OS/filesystem/free disk, Python/packages/browser/proxy/config, exact compressed/expanded bytes and entry/image/box/pixel counts, reference/final hashes, timestamps/throughput/retries/offsets, browser/API/worker/Streamlit memory, integrity/analysis/report results and failure logs. Interrupt mid-chunk and after acknowledgement, refresh and restart at known offset, verify only uncommitted data is retransmitted and hash equals local reference.

No speed SLA before measurement. If resources/data are unavailable, report “protocol/integration tested at X bytes; 20–50 GB end-to-end verification not performed.” Do not equate backend upload success with fast analysis. **This audit ran no upload tests, runtime smoke test, ZIP64 benchmark or 20–50 GB verification.**

## 12. Risks, assumptions and user decisions

Assumptions/risks: co-located persistent volume and browser-reachable API; version-compatible component bridge; SQLite/local locking only for one host/process topology; restart recovery does not cover disk loss; all-member CRC scan adds CPU/I/O before analysis; dense audit remains quadratic; XML/metadata/plot limits can reject or restrict a dataset below ZIP size cap. Resource caps must be tuned and disclosed. Parallel chunks, tus/object storage, distributed workers and external queues are alternatives if topology changes, not MVP dependencies.

Decisions needed before implementation:

1. **Deployment/storage:** target host/OS, persistent volume path/capacity, RAM/CPU, reverse proxy/public URL and expected concurrency. Recommend one host/shared volume/same-origin API, one upload and one analysis initially.
2. **Access:** trusted local operator or shared/remote users? Which existing identity/gateway can enforce ownership? Recommend private binding plus capability for local MVP; agree identity boundary before public exposure.
3. **Dataset envelope/retention:** approve or adjust proposed 50 GiB compressed/250 GiB expanded, entry/XML/image/pixel/metadata caps, 72-hour idle resume and explicit retention of ready datasets. Representative real dataset statistics are needed to tune safely.
4. **Artifact placement:** repository write permission is required to place this completed document at the requested docs path. No automatic approval review rejection occurred; the operating system denied filesystem writes, including the elevated retry. No permission changes were attempted.

Stop after this plan. Application code/dependencies/data remain unchanged; implementation requires explicit user approval.
