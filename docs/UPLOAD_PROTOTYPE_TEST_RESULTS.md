# Stage 1/2 verification results

Date: 2026-10-16 (Asia/Saigon). See `UPLOAD_PROTOTYPE_CHECKPOINT.md` for setup, scope, file changes, commands and limitations.

## Final executed checks

```powershell
$env:CVAT_RUN_BROWSER_TESTS='1'
.venv\Scripts\python.exe -m pytest tests -q --junitxml=test-results/checkpoint.xml
```

Actual result: **16 passed, 1 warning in 31.19s**, exit code 0.

- Four baseline tests: recorded original source hashes; all captured quality/label/distribution/coverage/resolver/report outputs; image-only/read-error behavior; original Streamlit startup without upload.
- Nine API tests (including four parameterized size cases): exact streamed file/hash, metadata size restrictions, key/capability/origin/host/preflight checks, incomplete/incorrect/oversized chunk rollback, one-active-upload policy, no ready/dataset route, required random secret.
- Three real headless Chrome tests: sequential upload/rerun/hash; original dashboard seven tabs, Scene Finder, original preview, downloaded quality CSV and coverage; interrupted network reports failed/ready=false.
- `python -m pip --python .venv\Scripts\python.exe check`: **No broken requirements found**, exit code 0.

Upstream warning: Starlette 1.8.0 deprecates httpx-based TestClient in favor of httpx2. No test failure was suppressed; the suite still uses pinned httpx 0.28.1. Real HTTP/browser checks do not depend on TestClient.

## Browser evidence

Generated from `test-results/browser-evidence.json`:

| Field | Actual value |
| --- | --- |
| Chrome | 155.0.8059.40 |
| Actual ZIP size | 538,068 bytes |
| Requests carrying chunks | 9 |
| Maximum permitted chunk | 65,536 bytes |
| Acknowledgements | Monotonic actual body counts, each increment <=65,536 bytes |
| ZIP SHA-256 | `0140b29c52232d25e58490c78e5031cad9ed73dc8096a77c2c0d14b4da6adec2` |
| Streamlit rerun during transfer | Completed without duplicate session/chunk requests |
| Completion | received_unvalidated, ready=false, durable=false |

Chrome upload was throttled to 128 KiB/s for the rerun check. This artificial test setting is not a throughput benchmark. The 13,780-byte legacy fixture was uploaded through the unchanged dashboard, with expected quality CSV values checked. The local API/Streamlit servers used only loopback ports 8010, 8502 and 8501; the test runner stopped its owned child processes afterward. Test payloads/logs/screenshots remain in pytest-managed private temporary directories, and the JUnit/nonsecret browser evidence are in ignored `test-results/`.

## Earlier runs and corrections

Baseline capture succeeded before refactoring. The first baseline/API run had 12 passes and one incorrect handwritten expected-score assertion (35 versus observed original 50); correcting only that assertion yielded 13 passes. No golden values or algorithm code were changed to mask a regression.

First browser run: 1 passed, 2 failed assertions. Upload and hash succeeded; Playwright's simplified request headers omitted Chrome's automatic Content-Length, so the bound check was changed to actual server acknowledgement increments. Original CSV download caused a rerun with transient stale/new tabs; the test now waits for the seven-tab count before continuing. Next browser run: **3 passed in 45.69s**. Final combined run above: **16 passed**.

## What remains unverified or unimplemented

No 20–50 GB upload, >4 GiB ZIP64, real reverse proxy, RSS/browser memory capacity benchmark, throughput SLA, multiuser exposure or long-running analysis benchmark was performed. The separate prototype is not integrated into the production dashboard. Original dashboard RAM-backed small-upload behavior has not yet been refactored.

No SQLite/durable acknowledgements, idempotent retries, refresh/reselection resume, server crash recovery, cancellation/TTL/cleanup service, disk reservations, safe ZIP/XML validation or ready dataset registration exists at this checkpoint. These are intentionally reserved for subsequent approved stages. The prototype must not be used as evidence of production large-file support.
