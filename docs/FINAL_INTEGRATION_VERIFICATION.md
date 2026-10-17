# Final integration verification — 2026-10-17

No application source was changed in this verification. Existing working-tree
changes were retained. No commit, push, secret rotation, migration or security
policy change occurred.

## Isolated regression results

Executed from D:\CVAT_Dashboard_Scene_Finder:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_upload_recovery.py tests/test_upload_session_adapter.py tests/test_streamlit_adapter.py tests/test_integrated_api.py tests/test_durable_api.py tests/test_stage3_preservation.py tests/test_stage4_preservation.py tests/test_stage5_preservation.py -q
```

Result: **54 passed, one existing Starlette/httpx deprecation warning, 24.83s**.
These are mocked transport, isolated TestClient, UI harness and preservation tests;
they are not a new-file upload against the live registry. The port-exclusive
network restart test was excluded to preserve the live service.

## Process inspection and controlled restart

Before: only 127.0.0.1:8011 listened, owned by backend PID 15424, launcher 1296.
No Streamlit process or 8501 listener existed. Both old command lines identified
.diagnostics/backend_runner.py. The private configuration was read from
D:\CVATPrivateUploads\secret_config.json without printing its values; it still
points to stage3-data, not legacy stage3-demo.

Commands used for process inspection:

```powershell
Get-NetTCPConnection -State Listen | Where-Object {$_.LocalPort -in 8011,8501} | Select-Object LocalAddress,LocalPort,OwningProcess
Get-CimInstance Win32_Process | Where-Object {$_.Name -match 'python|streamlit|uvicorn'}
```

Command-line output was filtered to withhold lines containing sensitive keywords.

Before stopping anything:

```powershell
.venv\Scripts\python.exe .diagnostics/final_preservation.py before
.venv\Scripts\python.exe .diagnostics/final_preservation.py after
```

The helper uses read-only SQLite and checks all registry-table fingerprints,
protected dataset/payload/legacy file metadata, no pending cleanup or active
validation, no leases, the empty active payload, and sufficient time to expiry.
All checks passed. Existing schema version remained 1; no migration was requested.

Only the listener with expected PID, loopback address and matching runner command
was eligible for stopping. The executed scoped stop logic was:

```powershell
foreach($targetId in @(15424,1296)){
 $targetProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$targetId"
 if($null -ne $targetProcess){
  if($targetProcess.CommandLine -notlike '*\.diagnostics\backend_runner.py*'){throw 'Process command mismatch; stop cancelled'}
  Stop-Process -Id $targetId -ErrorAction Stop
 }
}
```

PID 15424 was stopped. Launcher 1296 exited with it and needed no separate stop.
This was a targeted Windows process stop, not a claim of graceful HTTP draining;
preflight established no upload writer/validation was running.

The following starts ran only after confirming both ports were free:

```powershell
$backendLaunch=Start-Process -FilePath 'D:\CVAT_Dashboard_Scene_Finder\.venv\Scripts\python.exe' -ArgumentList '-u','D:\CVAT_Dashboard_Scene_Finder\.diagnostics\backend_runner.py' -WorkingDirectory 'D:\CVAT_Dashboard_Scene_Finder' -WindowStyle Hidden -RedirectStandardOutput 'D:\CVAT_Dashboard_Scene_Finder\.diagnostics\backend.final.stdout.log' -RedirectStandardError 'D:\CVAT_Dashboard_Scene_Finder\.diagnostics\backend.final.stderr.log' -PassThru
$dashboardLaunch=Start-Process -FilePath 'D:\CVAT_Dashboard_Scene_Finder\.venv\Scripts\python.exe' -ArgumentList '-u','D:\CVAT_Dashboard_Scene_Finder\.diagnostics\streamlit_runner.py' -WorkingDirectory 'D:\CVAT_Dashboard_Scene_Finder' -WindowStyle Hidden -RedirectStandardOutput 'D:\CVAT_Dashboard_Scene_Finder\.diagnostics\streamlit.final.stdout.log' -RedirectStandardError 'D:\CVAT_Dashboard_Scene_Finder\.diagnostics\streamlit.final.stderr.log' -PassThru
```

Backend runner invokes backend.integrated_api:create_integrated_from_env with
factory=True, host=127.0.0.1, port=8011, proxy_headers=False, access_log=False.
Streamlit runner loads only the existing UI configuration fields and invokes:

```text
streamlit run D:\CVAT_Dashboard_Scene_Finder\app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

Both runners use python -u and redact configured credentials in output. The four
captured stdout/stderr logs were scanned for existing owner keys and upload secret;
none were present. Do not rerun the starts while these services are listening.

After: backend launcher 25400 / server 23748; Streamlit launcher 6160 / server 6316.
Listeners are exactly 127.0.0.1:8011 and 127.0.0.1:8501. Backend startup log confirms
Application startup complete. Both processes were left running with updated code.

## Actual live verification

Host HTTP requests used httpx with trust_env=False and bounded timeouts; sandbox
loopback checks are not used as evidence of host availability.

| Endpoint | Result |
| --- | --- |
| GET http://127.0.0.1:8011/health | HTTP 200, status ok |
| GET http://127.0.0.1:8501/_stcore/health | HTTP 200 |
| GET http://127.0.0.1:8501/ | HTTP 200 |
| GET /api/v1/uploads/{existing-ready-upload-id}/dataset | HTTP 200 for both ready datasets |
| GET /api/v1/datasets/{existing-dataset-id}/summary | HTTP 200 for both |
| GET /api/v1/datasets/{existing-dataset-id}/results | HTTP 200 for both |

Existing identifiers and request credentials were kept in memory and are not
included in this report. Existing capability verification was used; authentication
was not bypassed. Cached analysis output had the expected schema: first dataset
42 quality / 47 audit rows; second dataset 548 quality / 38,759 audit rows.
No analysis was recomputed.

The running dashboard was opened in the in-app browser. Its actual rendered title,
sidebar configuration controls, ZIP uploader and initial guidance were verified.
Screenshot: .diagnostics/dashboard-final.png. No file was selected in that browser.

To test the ready-data rendering branch without changing any ready dataset:

```powershell
.venv\Scripts\python.exe .diagnostics/verify_ready_dashboard.py
```

This helper uses live authenticated GET cached results and an existing sealed
source, seeds the matching Streamlit AppTest session, and disables every upload,
validation and analysis mutation method. Result: **7 tabs, 20 metrics, 10 dataframes,
zero application exceptions**. Existing Streamlit width deprecation and bare-mode
harness warnings were emitted. This is a read-only live-data rendering integration
check, not an end-to-end browser selection/recovery/upload test. The dashboard has
no ready-dataset selector, so normal browser navigation to an existing dataset was
not claimed or added as a new feature.

## Upload blocker and preservation

A read-only preflight confirmed the one existing active session, still created,
86,336,100 declared bytes, zero received bytes and zero chunk receipts. The global
ONE_ACTIVE_UPLOAD guard therefore blocks any disposable new upload. A small-file
E2E was skipped: no live POST, PUT or DELETE was sent, and HTTP 429 was not newly
reproduced in this verification. No upload completion, cancellation or analysis
was attempted. The original session was not reused for test data.

After restart, live GETs and rendering, the preservation helper passed again:
all registry-table row fingerprints, ready-dataset/payload file metadata and
legacy stage3-demo metadata exactly matched the pre-restart snapshot. Both original
datasets remain ready. stage3-demo was never written. Metadata snapshots are not
a claim of full content hashing of every protected file.

New-upload E2E remains blocked until the owner explicitly approves resolving the
existing active session, or it reaches its ordinary expiry. No approval to mutate
that session is inferred from this verification task. Historical transport failure
root cause remains unconfirmed.

Artifacts added this turn: final_preservation.py, streamlit_runner.py,
verify_ready_dashboard.py, final-preservation-baseline.json, backend/streamlit
final stdout/stderr logs, final launcher PID files and dashboard-final.png under
.diagnostics/. Application sources and the 54-test suite were unchanged this turn.
