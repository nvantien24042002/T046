import sys
from types import SimpleNamespace

import pytest

from backend.analytics_dev import build_app, main


def install(monkeypatch, factory):
    monkeypatch.setitem(sys.modules, "approved_private_test_module", SimpleNamespace(build_issuer=factory))


def test_launcher_has_only_normal_routes(monkeypatch):
    install(monkeypatch, lambda: SimpleNamespace(consume=lambda code: None))
    app = build_app("approved_private_test_module:build_issuer")
    assert not app.state.synthetic_results_only
    assert {route.path for route in app.routes} == {
        "/analytics/v1/grants/exchange", "/analytics/v1/datasets/{dataset_id}/summary"}


def test_launch_settings_without_starting_server(monkeypatch):
    install(monkeypatch, lambda: SimpleNamespace(consume=lambda code: None))
    observed = []
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: observed.append(kwargs))
    assert main(["--issuer-factory", "approved_private_test_module:build_issuer"]) == 0
    assert observed == [{"host": "127.0.0.1", "port": 8012, "workers": 1, "reload": False,
                         "proxy_headers": False, "access_log": False, "log_level": "warning"}]


@pytest.mark.parametrize("spec", ["tests.analytics_support:FakeIssuer", "bad", "x:y:z", "x:secret/value"])
def test_invalid_or_test_issuer_specs_are_rejected(spec):
    with pytest.raises(ValueError):
        build_app(spec)


@pytest.mark.parametrize("factory", [lambda: object(), "not-callable"])
def test_interface_is_required(monkeypatch, factory):
    install(monkeypatch, factory)
    with pytest.raises(ValueError):
        build_app("approved_private_test_module:build_issuer")


def test_startup_error_is_sanitized(monkeypatch, capsys):
    def broken():
        raise RuntimeError("FAKE-SECRET/private/path")
    install(monkeypatch, broken)
    assert main(["--issuer-factory", "approved_private_test_module:build_issuer"]) == 2
    text = capsys.readouterr().err
    assert "FAKE-SECRET" not in text and "private/path" not in text


def test_factory_argument_required():
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2
