# Stage 4 executed test evidence

Date: 2026-10-16 (Asia/Saigon). Design, architecture, schema, limits, and safety invariants: `DATASET_VALIDATION_STAGE4.md`.

## Final test results

```powershell
$env:CVAT_RUN_BROWSER_TESTS='1'
.venv\Scripts\python.exe -m pytest tests -q
```

Actual output: **89 passed, 2 warnings in 87.87s**, exit code 0. All 60 Stage 3 regression tests and all 29 Stage 4 tests executed and passed. Browser tests were enabled and executed, not skipped.

Environment:
- OS: Windows 11 build 26200
- Python: 3.12.14 (isolated virtual environment `.venv`)
- SQLite runtime: 3.53.1
- Browser: Headless Chrome 155.0.8059.40
- Production dependencies: unchanged (`requirements.txt`, `requirements-prototype.txt`, `docs/PROTOTYPE_REQUIREMENTS_LOCK.txt`)

Known warnings:
1. Starlette deprecation warning (`httpx` in testclient).
2. ZipFile warning regarding duplicate entry name in intentional adversarial collision test (`test_inspect_rejects_member_collision`).

## Test coverage breakdown

| Group | Tests | Description |
| --- | ---: | --- |
| Baseline regression | 16 | QA/QC scalar baselines, Streamlit dashboard startup, source hashes, original analysis parity. |
| Stage 3 durable store | 34 | Process-crash boundaries, ordered fsync/COMMIT, retry receipts, quota accounting, crash recovery. |
| Stage 3 durable API | 6 | Local-only auth, body streaming, replay/cancellation tombstones. |
| Stage 3 preservation | 1 | Stage 1/2 prototype and baseline preservation. |
| Stage 3 network / restart | 1 | Real socket cut, API kill/restart, resumed upload. |
| Stage 3 browser E2E | 2 | Real headless Chrome upload, interrupt, reselection, prefix verification, cancelled tombstones. |
| Stage 4 preservation | 1 | Verifies bit-for-bit SHA-256 integrity of all 13 Stage 3 protected source files (`tests/test_stage4_preservation.py`). |
| Stage 4 archive & XML validation | 21 | ZIP/ZIP64 EOCD and locator inspection, traversal rejection (`../`, absolute paths), collision detection, entry/directory limits, compression ratio bombs, streaming decompress CRC32 verification, expat safe CVAT XML parsing, DTD/entity expansion denial, image decode validation, and manifest generation (`tests/test_archive_validation.py`). |
| Stage 4 DatasetManager & lifecycle | 7 | Initialization, format marker creation, validation state machine (`uploaded` -> `validating` -> `indexing` -> `publishing` -> `ready`), leased reader context manager, attempt cleanup, cancellation, payload pinning database triggers, and interrupted worker recovery (`tests/test_dataset_manager.py`). |
| **Total** | **89** | **All tests passing, 0 failures, 0 skipped.** |

## Fixes applied during Stage 4 implementation

1. **Windows file handle lock during directory publication (`datasets/manager.py`)**:
   - Issue: In `DatasetManager._manifest()`, `sqlite3.connect()` was called as a context manager (`with sqlite3.connect(...) as db:`). In Python's standard library, exiting a sqlite3 context manager only commits or rolls back transactions; it does not close the database connection. On Windows, this held an open file handle on `index.sqlite3` inside the temporary `work` directory, causing subsequent `os.replace(work, sealed)` directory promotion to fail with `PermissionError: [WinError 32] The process cannot access the file because it is being used by another process` (or `[WinError 5] Access is denied`), transitioning the dataset to `failed` with code `PUBLICATION_IO_OR_REGISTRY_FAILED`.
   - Fix: Wrapped the SQLite connection in an explicit `try...finally: db.close()` block in `_manifest()` to ensure the connection is closed before `os.replace` executes.
