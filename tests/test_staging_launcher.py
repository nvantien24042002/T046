"""Tests for staging_launcher."""
import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from backend.staging_launcher import (
    SanitizedFormatter,
    build_staging_app,
    main,
    parse_result_id_map,
    resolve_issuer,
)


class DummyIssuer:
    def consume(self, code: str):
        return None


def test_sanitized_formatter():
    formatter = SanitizedFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Token: Bearer abcdef1234567890abcdef1234567890",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert "Bearer [REDACTED]" in formatted
    assert "abcdef1234567890abcdef1234567890" not in formatted


def test_resolve_issuer_invalid_format():
    with pytest.raises(ValueError, match="MODULE:CALLABLE format"):
        resolve_issuer("invalid_format_without_colon")


def test_resolve_issuer_rejects_test_module():
    with pytest.raises(ValueError, match="Test issuers are prohibited"):
        resolve_issuer("tests.analytics_support:FakeIssuer")


def test_resolve_issuer_success():
    mock_module = MagicMock()
    mock_module.create_my_issuer.return_value = DummyIssuer()
    with patch("importlib.import_module", return_value=mock_module):
        issuer = resolve_issuer("my_custom_package.issuer:create_my_issuer")
        assert isinstance(issuer, DummyIssuer)


def test_parse_result_id_map(tmp_path):
    assert parse_result_id_map(None) == {}
    assert parse_result_id_map("") == {}
    assert parse_result_id_map('{"ds1": "res1"}') == {"ds1": "res1"}

    map_file = tmp_path / "map.json"
    map_file.write_text('{"ds2": "res2"}', encoding="utf-8")
    assert parse_result_id_map(str(map_file)) == {"ds2": "res2"}

    with pytest.raises(ValueError, match="Failed to parse result_id_map"):
        parse_result_id_map("invalid-json")


def test_build_staging_app_without_store():
    with patch("backend.staging_launcher.resolve_issuer", return_value=DummyIssuer()):
        app = build_staging_app(issuer_factory="my_pkg.issuer:create")
        from backend.analytics_contracts import AUDIENCE, ORIGIN
        client = TestClient(app, base_url=AUDIENCE, client=("127.0.0.1", 1234), headers={"Origin": ORIGIN})

        # Routes check: verified results routes must not be present
        assert all("results" not in route.path for route in app.routes)


def test_build_staging_app_with_store(tmp_path):
    store_path = tmp_path / "results.db"
    with patch("backend.staging_launcher.resolve_issuer", return_value=DummyIssuer()):
        app = build_staging_app(
            issuer_factory="my_pkg.issuer:create",
            result_store_path=store_path,
        )
        route_paths = [route.path for route in app.routes]
        assert "/analytics/v1/datasets/{dataset_id}/results" in route_paths
        assert "/analytics/v1/datasets/{dataset_id}/results/{result_id}" in route_paths
        assert "/analytics/v1/datasets/{dataset_id}/preview" in route_paths


def test_build_staging_app_rejects_populate_without_result_id_map(tmp_path):
    store_path = tmp_path / "results.db"
    staging_root = tmp_path / "staging"
    staging_root.mkdir()

    with patch("backend.staging_launcher.resolve_issuer", return_value=DummyIssuer()):
        with pytest.raises(ValueError, match="requires an explicit --result-id-map"):
            build_staging_app(
                issuer_factory="my_pkg.issuer:create",
                result_store_path=store_path,
                populate_staging_root=staging_root,
                result_id_map=None,
            )


def test_main_fails_fast_on_invalid_issuer(capsys):
    ret = main(["--issuer-factory", "nonexistent.module:callable"])
    assert ret == 2
    captured = capsys.readouterr()
    assert "Staging launcher initialization failed" in captured.err


def test_main_invokes_uvicorn_with_safe_options():
    with patch("backend.staging_launcher.resolve_issuer", return_value=DummyIssuer()), \
         patch("uvicorn.run") as mock_uvicorn:
        ret = main(["--issuer-factory", "my_pkg.issuer:create"])
        assert ret == 0
        mock_uvicorn.assert_called_once()
        _, kwargs = mock_uvicorn.call_args
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 8012
        assert kwargs["workers"] == 1
        assert kwargs["reload"] is False
        assert kwargs["proxy_headers"] is False
        assert kwargs["access_log"] is False
