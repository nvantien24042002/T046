# Upload session diagnostics and verification

Changed adapter behavior: create_upload logs the exception class and a fixed,
allowlisted transport description. It never logs raw exception text, request
URLs, headers, response bodies, or traceback. The existing BACKEND_UNAVAILABLE
message is preserved and the original exception is chained as __cause__.
Invalid JSON now becomes INVALID_BACKEND_RESPONSE with a chained cause.

No connection pooling change: production clients close at the end of each
operation; injected clients remain caller-owned. Streamlit recreates the adapter
on reruns. Persistent pooling would require resource ownership and cleanup work;
no captured evidence justifies it as a fix for this failure.

Verification on 2026-10-17:
- MockTransport adapter tests: 12 passed; no upload storage used.
- Streamlit adapter, durable API, durable network, integrated API and stage 3
  preservation tests: 28 passed, one existing Starlette/httpx deprecation warning.
  Sandbox temporary-directory access failed; the approved unrestricted rerun passed.
- Current default http://127.0.0.1:8011/health: ConnectError. No owner credential
  available in the shell. Existing service authenticated creation was not tested.
- Isolated integrated backend on an ephemeral loopback port, with a generated
  owner and fresh temporary root: health passed; exactly one 22-byte declared
  disposable session created (no chunks); authenticated DELETE passed. Registry
  verified cancelled state, cleaned_at set, zero reserved bytes and cleanup_pending,
  no payload folders, and exactly one audit row. No active, duplicate or orphaned
  sessions. Server stopped and temporary root removed.
- Legacy D:\CVATPrivateUploads\stage3-demo: before/after metadata fingerprint
  unchanged (existence, relative names, sizes and modification timestamps).
  No writes performed there. This was metadata verification, not a content hash audit.

The original incident's root cause remains UNCONFIRMED. A current ConnectError
establishes transport unavailability during the check, not Windows socket churn
or a historical pooling defect. No retry was introduced: a lost POST response
can leave a created session and automatic retry needs a stable idempotency key.

## Safe manual check of the existing service

1. Start the backend using its documented factory and existing secure configuration.
   Do not change the storage root or regenerate its stable secret. Check /health
   returns HTTP 200 and status ok/healthy before an authenticated test.
2. In a private local Python session, supply the owner credential from your secure
   environment. Do not put credentials in URLs, shell history, printed headers or
   screenshots. Generate one unique Idempotency-Key and retain it in memory.
3. POST /api/v1/uploads with X-Owner-Key and Idempotency-Key headers, JSON
   {"filename":"disposable-verification.zip","size_bytes":22}. Send once; keep
   upload_id and upload_capability in memory. Do not send chunks or validation.
4. In a finally block, DELETE /api/v1/uploads/{upload_id} with X-Owner-Key and
   Upload-Capability headers. Confirm HTTP 200 and cancelled state. Check status
   using the same credential and capability. A cancelled audit row is expected.
5. Have the operator check only this session's registry row: cleaned_at is set,
   reserved_bytes=0, cleanup_pending=0, and its payload folder is absent. Do not
   delete registry rows or unrelated files. Compare a read-only before/after
   metadata snapshot of the legacy root if independently confirming preservation.
6. If POST fails ambiguously, retain the same idempotency key; do not submit with
   a new key. Once healthy, recover the original session using that key, then clean
   it via DELETE. Do not claim cleanup until its state and storage are verified.
7. For transport failures, inspect only the adapter warning class/description.
   The chained exception is available to a trusted debugger; do not display its
   raw text/traceback in the UI or attach it to shared logs.

## Recovery investigation: 2026-10-17

Host inspection (outside sandbox restrictions) found no listeners on 8011/8501
and no Python/Uvicorn/Streamlit processes. Required CVAT configuration was absent
from process, user and machine environment. No historical startup/runtime logs
were found in the repository or the private configuration directory. Therefore
absence of a running listener explains current host connection refusal; why the
previous service stopped and the original upload incident remain unconfirmed.

A private existing configuration was discovered at
D:\CVATPrivateUploads\secret_config.json. Values were never printed. Validation
confirmed its stage3-data storage root, matching existing registry secret and
matching frontend/backend owner credential. Existing dataset schema was version
1. Before restart: no expired active sessions, pending cleanup, validation leases,
oversized payloads or repair scratch files were found.

Restored exactly one backend using .diagnostics/backend_runner.py. It reads the
existing private JSON into the child process environment, invokes
backend.integrated_api:create_integrated_from_env, reserves 127.0.0.1:8011,
uses unbuffered output and sanitized logging, and disables access logs. Existing
root markers and registry are required. It never writes configuration or changes
secrets. Restart was authorized; no migration, reset or authentication change was
performed. The factory's standard startup/recovery ran against the existing root;
read-only comparisons verified every existing upload and validation dataset row
unchanged afterward.

Processes created: launcher PID 1296; actual backend PID 15424. Startup log confirms
application startup complete. Backend was left running; no existing process was
terminated. Streamlit has no listener on 8501 and was not started by this task.

Host GET http://127.0.0.1:8011/health: HTTP 200, status ok after startup and after
verification. Streamlit /_stcore/health: ConnectError. Sandbox loopback probes
continued to report ConnectError even when host probes returned 200: do not infer
host service failure from that sandbox-only result.

Regression invocation:
.venv\Scripts\python.exe -m pytest tests/test_upload_session_adapter.py tests/test_streamlit_adapter.py tests/test_durable_api.py tests/test_durable_network.py tests/test_integrated_api.py tests/test_stage3_preservation.py -q

Result: 39 passed, 1 failed, one Starlette/httpx deprecation warning. The network
test failed at its safety guard because 8011 was occupied by the restored backend;
its kill/restart scenario was not exercised. The restored service was preserved.

One authenticated metadata-only POST was attempted using the stored owner key.
It returned HTTP 429 / ONE_ACTIVE_UPLOAD because that owner already had an active
session. No new session was created, so there was no test session to DELETE.
No original upload was completed, cancelled, expired or otherwise changed by the
test. Existing registry rows matched their pre-restart fingerprints. No unknown
upload folders were found. Successful session creation remains blocked by the
existing active upload, not missing credentials or transport failure.

Next action: the owner should resume/complete that existing upload using their
normal UI, or explicitly authorize its cancellation after reviewing it. Once it
is no longer active, repeat the manual disposable-session procedure above and
DELETE only that new test session. Do not bypass the one-active-upload policy.
For the port-exclusive network regression, use an approved maintenance window
with 8011 free; do not kill the restored service merely to make the test pass.

Artifacts: .diagnostics/backend_runner.py, backend.stdout.log, backend.stderr.log,
and backend-launch.pid. Logs were checked for configured secrets and owner keys:
none found. The log formatter redacts known credentials and emits exception class
instead of raw traceback text. For a future restart, first confirm no existing
listener, then use the runner with python -u and redirected logs; never run two
instances or overwrite current logs while a process is writing them.

Legacy stage3-demo existence, names, sizes and modification timestamps matched
the before/after snapshot. No writes were performed there. Configuration, active
registry data and existing working-tree changes were preserved. No application
source change, connection pooling, commit or push was performed in this recovery.

## Recovery implementation: arbitrary ZIP sources (2026-10-17)

Files changed in this development step:
- app.py
- ui/backend_adapter.py
- backend/integrated_api.py
- tests/test_upload_recovery.py (new)
- docs/UPLOAD_SESSION_DIAGNOSTICS.md

No migration or authentication-policy change was needed. No connection pooling
was added. The live backend was not restarted, and no live mutation request was
sent. The existing registry, its two ready datasets, and stage3-demo were not
written by this task. All regression data used isolated temporary registries.

### Source identity and retry behavior

The Streamlit page hashes the full selected source in bounded reads and derives
a stable Idempotency-Key from filename, declared size and SHA-256. The key is
scoped to an owner by the existing API UNIQUE(owner_id, request_key) constraint.
The adapter computes the same key itself when callers do not supply one. There
is no special filename, path, ZIP size or original test archive in this logic;
the existing API filename, capacity and ZIP validation limits still apply.

Selecting a file no longer starts a POST or analysis automatically: the user must
click the upload/continue action. A lost creation response is retried with the
same key; no additional session is allocated. File identity changes produce a
different key and cannot silently adopt an existing owner's active upload.
Frontend cached results are also scoped to endpoint, owner and source identity.

Before resuming, the adapter checks exact filename and declared size, reads the
current acknowledged offset, and hashes every receipt-backed prefix chunk against
the selected source. Mismatches stop before any new chunk or completion request.
It resumes at the acknowledged chunk index and offset. Lost chunk acknowledgements
are recovered from registry receipts; a lost completion response reuses the sealed
upload. Sealed uploads are prefix-verified across all bytes before reuse.

For legacy sessions with a random original key, the optional recovery section
requires the original upload ID and capability plus explicit source confirmation.
The checkbox is bound to source identity, endpoint/owner and session ID. Ownership
alone is never treated as source identity. Full-file fingerprints were not stored
for those older sessions: matching filename/size and even matching partial receipts
cannot prove the unseen suffix. With zero receipts, source identity rests on the
operator's explicit confirmation. If the source is uncertain, do not proceed.
There is no owner-only discovery/takeover endpoint or capability exposure.

Existing handles must come from the original trusted session/operator workflow.
The UI does not retrieve lost legacy capabilities from the registry. The live
legacy session has not been recovered, resumed, completed or cancelled by this
implementation. Any action on it still needs the user's explicit approval.

Cancelled/expired matching sessions are rejected by default. An explicit option
allows a new attempt only after a prior matching session is already terminal;
its key is deterministically chained from the prior key and session ID so retries
still converge. The old audit record is preserved. This option cannot bypass an
active session, does not cancel/expire anything, and is not applied to a manual
legacy recovery handle. Failed sessions require operator review rather than an
automatic replacement. Chained terminal recovery is capped at 32 records.

Validation retries do not automatically set retry=True anymore. Existing active
or ready validation is reused by the manager; retrying a failed validation requires
an explicit checkbox. Identical analysis requests are cached by dataset and
normalized request-config fingerprint inside the existing mutations lock. Concurrent
requests, response-loss retries and revisiting a previously used configuration
reuse a completed result. New per-config cache files are atomically published;
the existing latest-result file is retained for /results compatibility. A changed
configuration intentionally computes a separate result. No analysis runs until
the upload/continue action is clicked (or existing UI analysis settings are changed).

### Global concurrency and abandonment

ONE_ACTIVE_UPLOAD remains global and returns HTTP 429 to every new upload attempt,
including another owner, while any created/uploading session exists. This matches
the storage layer's single-writer guard and the validation manager's global busy
checks. Tests verify this restriction and identical-request idempotency under
concurrent requests. No other owner's session metadata is disclosed in the UI.

Abandoned sessions continue to use the existing idle and absolute lifetime limits.
Normal backend maintenance expires eligible sessions and cleans their payloads;
this existing behavior was not changed or invoked manually against live data.
Before expiration, an abandoned active session blocks other owners as well as
new files from its own owner. Only completing the intended source, an explicitly
authorized cancellation through the existing API, or normal expiration resolves
that blocker. The UI never cancels an abandoned session automatically.

### Verification and limitations

Final regression command:
.venv\Scripts\python.exe -m pytest tests/test_upload_recovery.py tests/test_upload_session_adapter.py tests/test_streamlit_adapter.py tests/test_integrated_api.py tests/test_durable_api.py tests/test_stage3_preservation.py tests/test_stage4_preservation.py tests/test_stage5_preservation.py -q

The port-exclusive durable-network regression was not run against occupied 8011;
the restored service was preserved. No real ZIP upload or analysis was performed.
A host-side health check returned HTTP 200; read-only queries confirmed the existing
active session was still created, declared 86,336,100 bytes, with zero committed
bytes/receipts, and both pre-existing validation datasets remained ready.

Remaining limits:
- The running backend still has its pre-development code loaded. Activating the
  new analysis retry cache requires a controlled backend restart. Streamlit is
  stopped and must be started with the existing private configuration.
- Full source hashing costs one pass per Streamlit rerun; selected bytes still
  use Streamlit's existing upload handling. This does not remove its file-size
  limits or convert this UI into the separate direct-browser uploader.
- A server crash before an analysis result is cached can require recomputation.
  Exactly-once execution across crashes would require durable analysis-job design;
  no such database migration was introduced. Per-config result caches currently
  remain on disk without an eviction policy; automatic data cleanup was not added.
- The source fingerprint is encoded in the idempotency key rather than a new
  authoritative registry column. Legacy files cannot gain a retroactive fingerprint.
- A prefix mismatch never changes the existing session. Files with unknown identity
  need explicit confirmation; if that cannot be given, stop for operator review.
- The original historical transport failure remains unconfirmed.

Safe next action: review the recovery UI and decide whether the legacy test session
should be resumed with its intended source or cancelled. Neither action is authorized
by merely implementing this flow. No session mutation, destructive cleanup, migration,
secret rotation or security-policy change was performed. No commit or push was made.

Final result: 54 passed, one existing Starlette/httpx deprecation warning.
The 13 recovery tests cover lost creation/chunk/completion acknowledgements,
Unicode and different filenames, metadata/prefix/content mismatches, explicit
legacy consent, concurrent cross-owner creation, duplicate retries, validation
attempt reuse, analysis-config changes and returning to cached configurations,
UI action gating, and explicit terminal-attempt replacement preserving history.
Python compilation checks also passed.

## Final integration verification — 2026-10-17

See FINAL_INTEGRATION_VERIFICATION.md for exact commands and endpoints.
The 54 isolated regressions passed again (one existing deprecation warning).
Backend was restarted with existing private configuration; Streamlit was started.
Both listen only on 127.0.0.1 and both health endpoints return HTTP 200.
Actual browser rendering passed. Both ready datasets' live status, summary and
cached results GETs passed. A read-only live-data AppTest render passed with
7 tabs, 20 metrics and 10 dataframes; it seeded the existing cached session and
disabled all mutation methods, so it is not a new-upload browser E2E.
No disposable upload was attempted because the unchanged active session globally
blocks creation. No live POST/PUT/DELETE was sent. Final preservation fingerprints
matched all registry rows and protected dataset/payload/legacy file metadata.
The running services now load the updated code; no application features were
changed during this verification. Existing-session resolution still needs the
owner's explicit approval before new-upload live E2E can proceed.

## Authorized cancellation and successful live ZIP E2E — 2026-10-17

The owner explicitly authorized cancelling the exact empty test session after safety
checks. Host read-only queries and API GETs passed every gate. Official DELETE
returned HTTP 200, retained the cancelled audit row, removed only its empty payload
placeholder and cleared the active-session blocker. All unrelated original rows,
both original ready datasets and protected files/content hashes stayed unchanged.

A uniquely named 501-byte disposable ZIP then completed upload, validation and
analysis against the live backend using the updated recovery adapter. Creation,
chunk and completion acknowledgement losses were deliberately injected client-side;
retries produced exactly one new session, one chunk receipt and one completion.
Validation retry reused one published attempt. Identical analysis retry reused its
persisted request cache and returned the same one quality row and one audit row.
Both service health checks remained HTTP 200. No browser file-selection E2E was claimed.

The local generated ZIP was removed. The new ready test dataset remains because no
supported ready-dataset deletion API exists; published ready payloads are protected.
No unsupported deletion or direct registry modification was used. The two original
ready datasets remain untouched; legacy stage3-demo metadata is unchanged and no
writes were performed there. See AUTHORIZED_UPLOAD_E2E_RESULTS.md for commands,
endpoints, preservation evidence and remaining limitations.
