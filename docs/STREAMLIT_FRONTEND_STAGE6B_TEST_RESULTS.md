# Stage 6B Test Results & Verification Report

## 1. Test Execution Summary

- **Timestamp**: 2026-10-16 21:07:47 UTC+7
- **Command Executed**: `cmd /c "set CVAT_RUN_BROWSER_TESTS=1&& .venv\Scripts\python.exe -m pytest tests -v"`
- **Python Environment**: Python 3.12.14, pytest 9.1.1, anyio 4.15.1
- **Process Exit Code**: `0`
- **Total Tests Collected & Executed**: `114`
  - **Passed**: `114`
  - **Failed**: `0`
  - **Skipped**: `0`
- **Warnings**: `2`
  - `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.`
  - `UserWarning: Duplicate name: 'test.txt'` (from `test_inspect_rejects_member_collision` verifying collision rejection)
- **Duration**: `126.66s (0:02:06)`

---

## 2. Test Suite Breakdown

| Test File | Test Count | Status | Description |
|---|---|---|---|
| `tests/e2e/test_browser.py` | 3 | PASSED | Real headless Chrome browser tests verifying chunking, progress, and failure recovery. |
| `tests/e2e/test_durable_browser.py` | 2 | PASSED | Real headless Chrome browser tests verifying durable upload resumption and storage verification. |
| `tests/test_archive_validation.py` | 13 | PASSED | ZIP format, zip-slip, symlink, member collision, and nested zip security checks. |
| `tests/test_baseline.py` | 4 | PASSED | Original numerical preservation, expected 10-decimal baseline matching, empty app startup. |
| `tests/test_core_pipeline.py` | 6 | PASSED | Stage 5 QA/QC pipeline equivalence with disk-backed SQLite reader. |
| `tests/test_cvat_xml_validation.py` | 10 | PASSED | CVAT XML entity expansion, tag structure, and malicious payload validation. |
| `tests/test_dataset_manager.py` | 5 | PASSED | Dataset lifecycle states, reader unready locking, cancellation, and worker recovery. |
| `tests/test_durable_api.py` | 6 | PASSED | Stage 3 upload REST API, replay idempotency, receipts, capability tokens. |
| `tests/test_durable_network.py` | 1 | PASSED | Interrupted socket byte streams, kill/restart recovery. |
| `tests/test_durable_store.py` | 21 | PASSED | fsync-before-commit, SQLite recovery, atomic append, crash consistency. |
| `tests/test_integrated_api.py` | 11 | PASSED | Stage 6A FastAPI unified lifecycle, authentication, unready access guards, error sanitization. |
| `tests/test_prototype_api.py` | 8 | PASSED | Prototype compatibility, size bounds, origin and host verification. |
| `tests/test_stage3_preservation.py` | 1 | PASSED | Stage 3 baseline integrity check. |
| `tests/test_stage4_preservation.py` | 1 | PASSED | Stage 4 baseline integrity check. |
| `tests/test_stage5_preservation.py` | 1 | PASSED | Stage 5 baseline integrity check. |
| `tests/test_streamlit_adapter.py` | 9 | PASSED | Stage 6B frontend adapter streaming, polling, error sanitization, and offline preservation. |

---

## 3. Preservation & Baseline Verification

1. **`source_hashes.json` Verification**:
   - Only `app.py` SHA-256 was updated (`f9d35937a733891e5b9ebf92a17bac0b00031993f5f7688b09974d0f2bd16c8b`) to accommodate the Stage 6B backend adapter wiring and toggle.
   - All core algorithmic modules (`core/coverage_dynamic.py`, `core/coverage_ui.py`, `core/distribution.py`, `core/scene_finder.py`, `ui_theme.py`, `requirements.txt`) remained bit-for-bit identical to their origin hashes.
   - `test_captured_quality_label_distribution_coverage_and_reports` asserts against `tests/baseline/expected.json` (pinned numeric baseline captured prior to any refactoring). The test passed with identical results.
   - `tests/test_streamlit_adapter.py::test_existing_dashboard_behavior_preserved_offline` passed, verifying that without backend configuration, `app.py` falls back seamlessly to the legacy local analysis pipeline without regressions.
