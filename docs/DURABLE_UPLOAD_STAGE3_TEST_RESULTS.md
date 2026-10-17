# Stage 3 executed test evidence

Date: 2026-10-16 (Asia/Saigon). Design, exact persistence ordering, schema, configuration and operating assumptions: `DURABLE_UPLOAD_STAGE3.md`.

## Final result

```powershell
$env:CVAT_RUN_BROWSER_TESTS='1'
.venv\Scripts\python.exe -m pytest tests -q --junitxml=test-results/stage3.xml
```

Actual output: **60 passed, 1 warning in 70.39s**, exit code 0. Browser tests were enabled and executed, not skipped. JUnit evidence is in ignored `test-results/stage3.xml` and browser facts in `test-results/stage3-browser-evidence.json`.

Environment: Windows 11 build 26200; isolated Python 3.12.14; SQLite runtime 3.53.1; existing pinned Stage 1/2 packages from `docs/PROTOTYPE_REQUIREMENTS_LOCK.txt`; real headless installed Chrome 155.0.8059.40. No production dependency files changed or new packages installed during Stage 3. `python -m pip --python .venv\Scripts\python.exe check`: **No broken requirements found**.

One known upstream warning: Starlette deprecates httpx-based TestClient in favor of httpx2. No warnings were suppressed or failures skipped to obtain this result. Real network/browser tests also exercise the service independently of TestClient.

## Coverage and counts

| Tests | Count | Actual checks |
| --- | ---: | --- |
| Accepted baseline + prototype API + prototype/dashboard browser regression | 16 | Unchanged source hashes, original scalar QA/QC/distribution/coverage/resolver/report baselines, ordinary dashboard startup, previous upload/rerun/hash and interrupted-browser behavior; seven tabs/Scene Finder/previews/CSV/coverage. |
| New storage unit/process tests | 34 | Receipts/replay, interrupted rollback, wrong offsets/length/hash, creation idempotency, owner/capability, reservations/quota, idle/absolute expiry, unknown orphan preservation, cleanup-blocked unknown content, trailing bytes/scratch recovery, missing/short/same-size corrupt files and inconsistent DB/receipts, partial-write/fsync/COMMIT failures, root lock/secret mismatch/nonempty adoption, real hardlink preservation, real process exits at creation/chunk/cancellation boundaries. |
| New durable API tests | 6 | Protocol/state/no ready registration, unauthorized session access, body/input/path rejection, idempotent create, replay/cancel tombstones, safe registry failure/unhealthy/restart. |
| Accepted prototype/dependency/test preservation manifest | 1 | Prototype source, dependencies/lock, prior regression tests and baseline expected output hashes remain unchanged. Original app/core/production dependencies are independently covered by accepted baseline tests. |
| Real socket + API kill/restart | 1 | Declared 65,536-byte request cut after 4,096 bytes, safe retry, durable acknowledgement, force-kill actual server, restart with same root/secrets, receipt replay, remaining bytes/seal/hash. |
| New real Chrome durable-client tests | 2 | Interrupt/refresh/reselection/API kill/restart, wrong prefix rejection with no writes, correct prefix resume without retransmission, final disk/reference hash, explicit cancellation; server commits but acknowledgement is deliberately dropped, then browser replays safely. |

Storage process-crash tests call `os._exit(73)` without unwinding Python cleanup at scratch fsync, payload fsync, before receipt commit, after receipt commit, creation reservation commit, cancellation commit and file-removal-before-reservation-release. These are process termination tests, not host power-cut tests. COMMIT proxies test both errors before a real commit and errors after the real commit succeeded; rollback consults persisted state instead of assuming failure means no commit.

## Durable browser evidence

| Field | Observed value |
| --- | --- |
| ZIP bytes | 341,460 |
| Chunks | 6 at negotiated maximum 65,536 bytes |
| Acknowledged before API kill/restart | 65,536 bytes |
| Refresh/reselect same source | Succeeded after verifying every acknowledged prefix receipt |
| Same-name/size source with altered prefix | Rejected as SOURCE_PREFIX_MISMATCH before new writes |
| Previously acknowledged prefix retransmitted | No |
| Disk hash equals source hash | Yes |
| SHA-256 (final combined run) | `bc846f71e062835a104cb91d0d206f514d5a282142d69820a35a3650b98f5f2f` |
| Completion | uploaded, ready=false, validated=false |
| Explicit cancellation after comparison | cancelled, cleanup_pending=false, temporary payload removed |

The separate lost-ack browser fixture was 144,852 bytes (three chunks). The route sent chunk 0 to the actual service, received success internally, then aborted the browser response. The client queried authoritative status and repeated the identical chunk; server returned replayed=true without duplicate append, and final payload hash matched the source.

ZIP header timestamps vary between fixture-generation runs, so archive hashes can differ across runs while image/XML contents and baseline metrics stay fixed. The hash above is from the final 60-test run; each run independently compares its actual stored bytes against its source file.

## Earlier runs and fixes

- Initial targeted storage tests: **26 passed**.
- Storage/API tests: **32 passed**, one upstream warning.
- Initial new network/browser run: **1 passed, 2 failed** because the Windows venv launcher had exited before its actual Python API child/port had stopped. The test helper was corrected to terminate only descendants of its owned Popen process and await exit/port release. No weakening of recovery assertions or service bypass was made.
- Repeated new network/browser run after that fix: **3 passed in 25.22s**.
- Expanded storage/API/preservation safety run: **41 passed**, one upstream warning.
- Final combined run: **60 passed** as above.

Additional safety improvement before the final run rejects multiply hardlinked managed files; the test demonstrated that an unrelated hardlink target was neither truncated nor deleted by recovery/cancellation.

## Evidence limits

Tests use synthetic data and private temporary directories on this machine. Config limits were reduced in fault tests to make protocol boundaries deterministic. The default configurable 50 GiB size is **not** actual large-upload evidence. No 20–50 GB transfer, ZIP64 validation, archive safety validation, full-file reference-digest policy, RAM/throughput capacity benchmark, production proxy, multi-host/local network filesystem comparison, ACL provisioning/audit or power-cut test was performed.

The service acknowledges transport bytes only after its file fsync + SQLite EXTRA transaction ordering. Windows process restart on the same healthy volume is exercised; universal power-loss/device persistence is not asserted. No validated/ready dataset is created. Stage 4 and dashboard/QA refactoring remain unimplemented pending approval.
