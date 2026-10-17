# Authorized empty-session cancellation and live ZIP E2E — 2026-10-17

This supersedes the new-upload blocker in FINAL_INTEGRATION_VERIFICATION.md.
No application source was changed. Only verification scripts, evidence and this
report were added/updated; unrelated working-tree changes were retained.

## Authorization and safety gate

Owner authorization covered cancellation only after the exact session was verified.
.diagnostics/authorized_e2e.py performed read-only SQLite queries plus authenticated
GET status, receipts and dataset-lifecycle requests. The host preflight passed:
- Existing upload-table fingerprint matched the previously recorded baseline.
- Exactly one active session; created at the previously reported timestamp.
- State created, declared size 86,336,100 bytes, received bytes 0, receipts 0.
- Owner association matched the configured Streamlit owner; capability hash matched.
- No validation dataset, validation attempt or analysis association for this upload.
- No active validation, leases, pending cleanup or scratch file.
- No uploaded payload bytes: only its known zero-byte payload.part placeholder.
- Both original ready datasets, all unrelated registry rows, current schema and
  protected files were fingerprinted. Existing protected files received full
  SHA-256 content hashes as well as size and modification-time checks.

The first sandbox API probe returned ConnectError; it performed no mutation. The
read-only host-network preflight succeeded. Sandbox loopback failure was not used
as evidence about the host backend.

Cancellation code inspection confirmed DELETE /api/v1/uploads/{upload_id}:
- authenticates owner/capability through session_owner and store._owned;
- updates only the requested upload row;
- cleans only its known payload.part/incoming.chunk files, preserving unknown files;
- retains its cancelled audit row and releases only its reservation;
- is guarded against cancelling referenced dataset payloads by existing pin triggers.
No registry row was directly updated or deleted by the verifier.

## Commands actually run

From D:\CVAT_Dashboard_Scene_Finder:

```powershell
.venv\Scripts\python.exe .diagnostics/authorized_e2e.py preflight
.venv\Scripts\python.exe .diagnostics/authorized_e2e.py cancel
.venv\Scripts\python.exe .diagnostics/authorized_e2e.py e2e
```

Live API phases ran on the host network with approved tool execution; configuration
was loaded from the existing private JSON, never printed. These are guarded one-shot
verification modes, not commands to blindly rerun against a changed registry.

## Official cancellation result

DELETE returned HTTP 200 and state cancelled. The exact authorized audit row remains;
cleaned_at is set, reserved_bytes=0 and cleanup_pending=0. The empty placeholder
folder was removed by the official API. Active upload count became zero.
Every unrelated pre-existing registry row remained identical. The two original
ready datasets and their files/content hashes were unchanged. Schema and legacy
stage3-demo metadata also matched.

## Actual live upload, recovery, validation and analysis

A unique disposable-e2e-<random>.zip was generated locally with one harmless 64x64
PNG and a small valid annotations.xml. Compressed ZIP size: **501 bytes**. It had
no relation to the old test file or old session.

The live updated BackendClient.stream_upload used the same filename/size/SHA-256
identity key as the Streamlit recovery path. Its wrapper deliberately discarded
successful creation, chunk and completion acknowledgements once each. These were
synthetic client-side acknowledgement losses after genuine successful server
responses, not naturally occurring transport failures or a new root-cause finding.

Observed results:
- Creation acknowledgement loss translated safely to BACKEND_UNAVAILABLE.
- Chunk acknowledgement loss translated safely to NETWORK_ERROR.
- Completion acknowledgement loss translated safely to BACKEND_UNAVAILABLE.
- Five upload invocations (initial plus four retries/repeats) reused one session.
- Exactly one new session, one 501-byte chunk receipt and one completion request.
- Persisted payload SHA-256 matched the complete source ZIP.
- Upload persisted as uploaded; dataset validation persisted as ready.
- Repeated validation reused exactly one published validation attempt.
- Analysis returned **one quality row and one audit row** with the expected schema.
- Identical analysis POST returned identical results and reused the per-request
  cache without changing its content hash or modification time.
- GET results returned the same analysis; GET summary confirmed ready.
- No active upload, validation lease, test reservation or pending cleanup remained.

Endpoints actually exercised, with identifiers/credentials kept private:
- GET /health
- POST /api/v1/uploads (same stable idempotency key on recovery retries)
- GET /api/v1/uploads/{test-upload-id}
- GET /api/v1/uploads/{test-upload-id}/receipts
- PUT /api/v1/uploads/{test-upload-id}/chunks/0
- POST /api/v1/uploads/{test-upload-id}/complete
- POST /api/v1/uploads/{test-upload-id}/validate
- GET /api/v1/uploads/{test-upload-id}/dataset
- POST /api/v1/datasets/{test-dataset-id}/analyze
- GET /api/v1/datasets/{test-dataset-id}/results
- GET /api/v1/datasets/{test-dataset-id}/summary
- GET http://127.0.0.1:8501/_stcore/health

Both backend and Streamlit health returned HTTP 200 after E2E. This was a real HTTP
E2E using the updated recovery adapter; no browser file-selection E2E was claimed.

## Cleanup and preservation

The uniquely generated local ZIP was removed. The new tiny ready test dataset,
sealed upload, receipts and cached analysis remain intentionally. Existing supported
APIs have no deletion route for ready datasets; payload-pin triggers prevent upload
cancellation once a dataset references it. The supported attempt-cleanup endpoint
accepts failed/cleaned attempts, not published ready attempts. No unsupported deletion
or direct SQLite manipulation was attempted to remove the successful test result.

Evidence identifiers and aggregate counts, without credentials/capabilities, are in
.diagnostics/authorized-e2e-result.json. There are now the two original ready datasets
plus this one clearly identified disposable ready dataset. A future supported ready
retention/deletion feature would need a separate approved task; no feature was added.

Final preservation checks verified every unrelated existing row as a multiset subset
of the final registry; new rows belonged only to this authorized E2E. The old cancelled
row stayed unchanged throughout E2E. Protected original file size, timestamp and full
content hashes matched. All schema definitions matched. Legacy stage3-demo existence,
names, sizes and modification timestamps matched and it was never written. Legacy
metadata checks are not a claim of content hashing every legacy file.

Captured backend/Streamlit stdout/stderr logs passed a scan for configured secrets,
owner keys and the test capability. No credentials or raw exception text were logged.
Only a safe ReadError description was emitted for the intentional lost-create ack.
No authentication/concurrency policy, registry schema or storage root changed. No
commit or push occurred. The original historical transport failure remains unconfirmed.

## Isolated regression rerun

```powershell
.venv\Scripts\python.exe -m pytest tests/test_upload_recovery.py tests/test_upload_session_adapter.py tests/test_streamlit_adapter.py tests/test_integrated_api.py tests/test_durable_api.py tests/test_stage3_preservation.py tests/test_stage4_preservation.py tests/test_stage5_preservation.py -q
```

Result after the successful live E2E: **54 passed in 28.22s**, one existing
Starlette/httpx deprecation warning. These synthetic/isolated regressions are
separate from the real HTTP upload/analysis results above.
