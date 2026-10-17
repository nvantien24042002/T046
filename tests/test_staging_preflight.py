"""Unit tests for staging_preflight."""
from unittest.mock import patch
import pytest

from backend.staging_preflight import CheckResult, check_port_free, print_report, run_preflight


def test_check_port_free():
    # Loopback port check
    is_free = check_port_free(49151)
    assert isinstance(is_free, bool)


def test_run_preflight_reports_blocked_without_env():
    with patch.dict("os.environ", {}, clear=True):
        results = run_preflight()
        blocked = [r for r in results if r.status == "BLOCKED"]
        assert len(blocked) >= 6
        assert any(r.name == "CVAT_ALLOWED_ORIGINS" for r in blocked)
        assert any(r.name == "N2_05B_LOCAL_DEV" for r in blocked)


def test_run_preflight_cors_check():
    with patch.dict("os.environ", {"CVAT_ALLOWED_ORIGINS": "http://127.0.0.1:5173"}):
        results = run_preflight()
        cors_result = next(r for r in results if r.name == "Frontend Origin Whitelist")
        assert cors_result.status == "READY"


def test_print_report_verdict(capsys):
    res_ready = [CheckResult(category="Test", name="Item1", status="READY", details="ok")]
    code = print_report(res_ready)
    assert code == 0
    captured = capsys.readouterr()
    assert "Verdict: READY" in captured.out

    res_blocked = [CheckResult(category="Test", name="Item2", status="BLOCKED", details="err", remediation="fix it")]
    code = print_report(res_blocked)
    assert code == 1
    captured = capsys.readouterr()
    assert "Verdict: BLOCKED" in captured.out
    assert "fix it" in captured.out
