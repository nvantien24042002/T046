# Stage 6A — Integration Test Results Report

## 1. Test Execution Summary

- **Date & Time**: 2026-10-16T20:40:38+07:00
- **Test Command**: `cmd /c "set CVAT_RUN_BROWSER_TESTS=1&& .venv\Scripts\python.exe -m pytest tests -q"`
- **Environment**: Windows 11, Python 3.12.14, pytest-9.1.1, Playwright Chromium (headless)
- **Exit Code**: `0`
- **Total Tests**: `105`
- **Passed**: `105`
- **Failed**: `0`
- **Skipped**: `0`
- **Warnings**: `2`
  - StarletteDeprecationWarning (`Using httpx with starlette.testclient is deprecated`)
  - UserWarning (`zipfile duplicate name test.txt` in archive validation member collision test)
- **Duration**: `111.86s (0:01:51)`

---

## 2. Test Breakdown by Test Module

| Test Module | Total Tests | Passed | Failed | Skipped | Scope / Description |
|:---|:---:|:---:|:---:|:---:|:---|
| `tests/test_integrated_api.py` | 11 | 11 | 0 | 0 | **Stage 6A Backend Integration Suite** (lifecycle, analysis, authorization, isolation, recovery) |
| `tests/test_core_pipeline.py` | 6 | 6 | 0 | 0 | **Stage 5 Pipeline Suite** (pure QA/QC parity, streaming reader, corrupt handling) |
| `tests/test_dataset_manager.py` | 7 | 7 | 0 | 0 | **Stage 4 Lifecycle Suite** (validation, reader, payload pinning, cancellation, crash recovery) |
| `tests/test_archive_validation.py` | 21 | 21 | 0 | 0 | **Stage 4 Archive Safety** (ZIP bombs, XML DTD/entity attacks, member collisions, size limits) |
| `tests/test_durable_api.py` | 6 | 6 | 0 | 0 | **Stage 3 API Protocol** (receipt retry, idempotency, chunk validation, rollback) |
| `tests/test_durable_network.py` | 1 | 1 | 0 | 0 | **Stage 3 Network Durability** (interrupted HTTP body, kill/restart after ACK) |
| `tests/test_durable_store.py` | 32 | 32 | 0 | 0 | **Stage 3 Storage Engine** (crash points, fsync boundaries, quota, orphan preservation, SQLite failure) |
| `tests/test_prototype_api.py` | 9 | 9 | 0 | 0 | **Stage 2 Prototype Baseline** (chunk bytes, size validation, host checks) |
| `tests/e2e/test_browser.py` | 3 | 3 | 0 | 0 | **Real Headless Chrome Tests** (chunks rerun, existing dashboard tabs, connection failure) |
| `tests/e2e/test_durable_browser.py` | 2 | 2 | 0 | 0 | **Real Headless Chrome Tests** (lost ACK replay, refresh/restart reselection) |
| `tests/test_baseline.py` | 4 | 4 | 0 | 0 | **Baseline Data Integrity** (fixture validation, distribution, coverage) |
| `tests/test_stage3_preservation.py` | 1 | 1 | 0 | 0 | **Preservation Suite** (verifies SHA-256 of Stage 2/3 protected sources) |
| `tests/test_stage4_preservation.py` | 1 | 1 | 0 | 0 | **Preservation Suite** (verifies SHA-256 of Stage 3 protected sources) |
| `tests/test_stage5_preservation.py` | 1 | 1 | 0 | 0 | **Preservation Suite** (verifies SHA-256 of Stage 4 protected sources) |
| **Total** | **105** | **105** | **0** | **0** | **100% Passing** |

---

## 3. Verification of Stage 6A Specific Requirements

| # | Requirement | Test Case | Status |
|:---:|:---|:---|:---:|
| 1 | Successful upload-to-validation-to-ready lifecycle | `test_full_upload_validation_analysis_lifecycle` | PASSED |
| 2 | Uploaded-but-not-yet-validated cannot be queried as ready | `test_unvalidated_upload_cannot_be_queried_as_ready` | PASSED |
| 3 | Invalid ZIP or XML never reaches ready | `test_invalid_zip_never_reaches_ready`, `test_xml_attack_never_reaches_ready` | PASSED |
| 4 | Pipeline/indexing failure produces truthful state & never publishes partial results | `test_pipeline_failure_does_not_publish_partial_results`, `test_invalid_zip_never_reaches_ready` | PASSED |
| 5 | Restart/recovery behavior uses existing Stage 3-4 mechanisms | `test_interrupted_worker_recovery` | PASSED |
| 6 | Duplicate processing requests are idempotent or safely rejected | `test_duplicate_validation_requests` | PASSED |
| 7 | Concurrent requests cannot publish conflicting state | `test_concurrent_validation_requests` | PASSED |
| 8 | Responses do not leak secrets, capabilities, filesystem paths, or raw exception traces | `test_api_responses_never_leak_paths_or_secrets`, `test_pipeline_failure_does_not_publish_partial_results` | PASSED |
| 9 | Stage 3-5 regression tests pass without modification | All 94 existing regression tests | PASSED |
| 10 | Existing baseline outputs remain equivalent on current fixtures | `test_pipeline_parity_with_baseline`, `test_baseline.py` | PASSED |
