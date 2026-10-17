# Stage 5 executed test evidence

Date: 2026-10-16 (Asia/Saigon). Architecture, memory management, and module responsibilities: `CORE_PIPELINE_STAGE5.md`.

## Final test results

```powershell
$env:CVAT_RUN_BROWSER_TESTS='1'
.venv\Scripts\python.exe -m pytest tests -q
```

Actual output: **94 passed, 2 warnings in 103.95s**, exit code 0. All 60 Stage 3 regression tests, 29 Stage 4 tests, and 5 Stage 5 tests executed and passed. Browser tests were enabled and executed, not skipped.

Environment:
- OS: Windows 11 build 26200
- Python: 3.12.14 (isolated virtual environment `.venv`)
- SQLite runtime: 3.53.1
- Browser: Headless Chrome 155.0.8059.40
- Production dependencies: unchanged (`requirements.txt`, `requirements-prototype.txt`, `docs/PROTOTYPE_REQUIREMENTS_LOCK.txt`)

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
| Stage 4 archive & XML validation | 21 | ZIP/ZIP64 EOCD and locator inspection, traversal rejection, collision detection, entry/directory limits, compression ratio bombs, streaming decompress CRC32 verification, expat safe CVAT XML parsing, DTD/entity expansion denial, image decode validation, and manifest generation (`tests/test_archive_validation.py`). |
| Stage 4 DatasetManager & lifecycle | 7 | Initialization, format marker creation, validation state machine (`uploaded` -> `validating` -> `indexing` -> `publishing` -> `ready`), leased reader context manager, attempt cleanup, cancellation, payload pinning database triggers, and interrupted worker recovery (`tests/test_dataset_manager.py`). |
| Stage 5 preservation | 1 | Verifies bit-for-bit SHA-256 integrity of all 11 Stage 4 protected source files (`tests/test_stage5_preservation.py`). |
| Stage 5 Core Pipeline Adaptation | 4 | Complete baseline equivalence against `expected.json` (quality, label audit, cvat images, cvat boxes, distribution images, distribution boxes, classes, attributes, dynamic coverage), bounded memory without image_bytes retention, image-only dataset handling, single image preview and annotated box preview generation (`tests/test_core_pipeline.py`). |
| **Total** | **94** | **All tests passing, 0 failures, 0 skipped.** |
