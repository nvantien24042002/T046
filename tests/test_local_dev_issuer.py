from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.analytics_contracts import AUDIENCE, ORIGIN
from backend.analytics_dev import build_app
from backend.analytics_grants import GrantStore
from backend.analytics_intermediary import create_analytics_app
from backend.local_dev_issuer import LocalDevelopmentIssuer, digest, create_issuer
from backend.local_issuer_paths import IssuerPaths, IssuerUnavailable, from_environment
from tests.analytics_support import Clock, FakeUpstream

CAP = "FAKE_PRIVATE_CAPABILITY_"+"x"*40


@pytest.fixture
def configured(tmp_path, monkeypatch):
    import backend.local_issuer_paths as paths_module
    import backend.local_dev_issuer as issuer_module
    # ACL policy is unit-injected; real filesystem/link/SQLite tests still run.
    # Windows ACL integration must be verified separately on the approved directory.
    monkeypatch.setattr(paths_module, "private_acl", lambda *a, **k: None)
    monkeypatch.setattr(issuer_module, "private_acl", lambda *a, **k: None)
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    paths = IssuerPaths(root, root/"codes.sqlite3", root/"credentials.json", tmp_path/"unopened-datasets")
    LocalDevelopmentIssuer.initialize(paths)
    clock = Clock()
    issuer = LocalDevelopmentIssuer(paths, clock=clock)
    issuer.add_credential("ref-a", dataset_id="dataset-a", upload_id="upload-a", capability=CAP,
                          scopes=["summary:read", "results:read"], result_ids=["result-a"])
    return paths, clock, issuer


def issue(issuer, **changes):
    fields = dict(requester="synthetic-operator", credential_ref="ref-a", dataset_id="dataset-a",
                  scopes=["summary:read"], result_ids=[])
    fields.update(changes)
    return issuer.issue(**fields)


def sql(paths, statement, params=()):
    with sqlite3.connect(paths.ledger) as db:
        return db.execute(statement, params).fetchall()


def test_valid_issuance_custody_and_restart(configured, caplog):
    paths, clock, issuer = configured
    code = issue(issuer)
    rows = sql(paths, "SELECT code_hash,expires_at-issued_at,consumed_at FROM codes")
    assert rows == [(digest(code), 60, None)]
    assert code.encode() not in paths.ledger.read_bytes() and CAP.encode() not in paths.ledger.read_bytes()
    assert CAP in paths.credentials.read_text()
    restarted = LocalDevelopmentIssuer(paths, clock=clock)
    record = restarted.consume(code)
    assert record.dataset_id == "dataset-a" and record.upstream_capability == CAP
    assert record.scopes == frozenset({"summary:read"}) and record.result_ids == frozenset()
    assert CAP not in repr(record)
    assert issuer.consume(code) is None
    assert LocalDevelopmentIssuer(paths, clock=clock).consume(code) is None
    assert [p.name for p in paths.claims.iterdir()] == [digest(code)]
    assert code not in caplog.text and CAP not in caplog.text


def test_concurrent_consumers_exactly_one(configured):
    paths, clock, issuer = configured
    code = issue(issuer)
    def consume(_):
        return LocalDevelopmentIssuer(paths, clock=clock).consume(code)
    with ThreadPoolExecutor(8) as pool:
        records = list(pool.map(consume, range(8)))
    assert sum(record is not None for record in records) == 1


@pytest.mark.parametrize("value", [None, "bad", "!"*43, "a"*513, "a"*43])
def test_unknown_and_malformed(configured, value):
    assert configured[2].consume(value) is None


@pytest.mark.parametrize("age,valid", [(59.9, True), (60, False), (61, False), (-1, False)])
def test_authoritative_expiry(configured, age, valid):
    _, clock, issuer = configured
    code = issue(issuer)
    clock.value += age
    assert (issuer.consume(code) is not None) == valid


@pytest.mark.parametrize("changes", [
    {"dataset_id":"other"}, {"credential_ref":"missing"},
    {"scopes":["results:read"]}, {"scopes":["summary:read"],"result_ids":["result-a"]},
    {"scopes":["summary:read","results:read"],"result_ids":["other"]},
    {"scopes":["admin"]}, {"scopes":["summary:read","summary:read"]},
])
def test_operator_scope_association_is_not_expanded(configured, changes):
    with pytest.raises(IssuerUnavailable):
        issue(configured[2], **changes)


@pytest.mark.parametrize("field,value", [
    ("audience", "wrong"), ("origin", "http://localhost:5173"), ("dataset_id", "other"),
    ("upload_id", "other"), ("scopes", '["results:read"]'), ("result_ids", '["other"]'),
    ("credential_ref", "missing"), ("credential_generation", "0"*32),
])
def test_corrupt_ledger_associations_fail_closed(configured, field, value):
    paths, _, issuer = configured
    code = issue(issuer)
    # Deliberately corrupt only this test-owned ledger, bypassing its immutable guard.
    sql(paths, "DROP TRIGGER immutable_authority")
    sql(paths, f"UPDATE codes SET {field}=?", (value,))
    assert issuer.consume(code) is None


@pytest.mark.parametrize("field,value", [
    ("audience","wrong"), ("origin","wrong"), ("dataset_id","other"), ("upload_id","other"),
    ("scopes",["summary:read"]), ("result_ids",[]), ("generation","0"*32), ("revoked",True),
])
def test_credential_binding_changes_block_existing_code(configured, field, value):
    paths, _, issuer = configured
    code = issue(issuer, scopes=["summary:read","results:read"], result_ids=["result-a"])
    data = json.loads(paths.credentials.read_text())
    data["credentials"]["ref-a"][field] = value
    paths.credentials.write_text(json.dumps(data))
    assert issuer.consume(code) is None


class FaultConnection:
    def __init__(self, db, fault):
        self.db, self.fault = db, fault
    def __getattr__(self, name):
        return getattr(self.db, name)
    @property
    def row_factory(self):
        return self.db.row_factory
    @row_factory.setter
    def row_factory(self, value):
        self.db.row_factory = value
    def execute(self, statement, *args):
        if self.fault == "update" and statement.startswith("UPDATE codes SET consumed_at"):
            raise sqlite3.OperationalError("injected update failure")
        if self.fault == "begin" and statement == "BEGIN IMMEDIATE":
            raise sqlite3.OperationalError("injected begin failure")
        return self.db.execute(statement, *args)
    def commit(self):
        if self.fault == "before":
            raise sqlite3.OperationalError("injected commit failure")
        self.db.commit()
        if self.fault in {"after", "close"}:
            if self.fault == "after":
                raise sqlite3.OperationalError("injected lost acknowledgement")
    def close(self):
        self.db.close()
        if self.fault == "close":
            raise sqlite3.OperationalError("injected close acknowledgement loss")


@pytest.mark.parametrize("fault", ["before", "after", "update", "close", "begin"])
def test_transaction_ambiguity_never_returns_authority(configured, fault):
    paths, clock, issuer = configured
    code = issue(issuer)
    issuer._connector = lambda *args, **kwargs: FaultConnection(sqlite3.connect(*args, **kwargs), fault)
    assert issuer.consume(code) is None
    fresh = LocalDevelopmentIssuer(paths, clock=clock)
    if fault == "begin":
        assert fresh.consume(code) is not None  # no consumption attempt/commit occurred
    else:
        assert (paths.claims/digest(code)).is_file()
        assert fresh.consume(code) is None
        consumed = sql(paths, "SELECT consumed_at FROM codes")[0][0]
        assert (consumed is not None) == (fault in {"after", "close"})


def test_append_only_retention_and_revocation(configured):
    paths, _, issuer = configured
    code = issue(issuer)
    assert issuer.consume(code)
    for statement in ("DELETE FROM codes", "UPDATE codes SET consumed_at=NULL",
                      "UPDATE codes SET issued_at=issued_at+1"):
        with pytest.raises(sqlite3.IntegrityError):
            sql(paths, statement)
    code2 = issue(issuer)
    issuer.revoke_code(code2)
    assert issuer.consume(code2) is None
    code3 = issue(issuer)
    issuer.revoke_credential("ref-a")
    assert issuer.consume(code3) is None
    with pytest.raises(IssuerUnavailable):
        issue(issuer)
    assert sql(paths, "SELECT COUNT(*) FROM codes")[0][0] == 3
    with pytest.raises(IssuerUnavailable):
        LocalDevelopmentIssuer.initialize(paths)


def test_factory_launcher_and_http_containment(configured, monkeypatch, caplog):
    paths, clock, issuer = configured
    configure_environment(monkeypatch, paths)
    app = build_app("backend.local_dev_issuer:create_issuer")
    assert {route.path for route in app.routes} == {
        "/analytics/v1/grants/exchange", "/analytics/v1/datasets/{dataset_id}/summary"}
    code = issue(issuer)
    # Use fake authoritative time and fake summary; no real backend or dataset.
    app = create_analytics_app(issuer=issuer, grants=GrantStore(clock=clock), upstream=FakeUpstream())
    with TestClient(app, base_url=AUDIENCE, client=("127.0.0.1",1234), headers={"Origin":ORIGIN}) as client:
        response = client.post("/analytics/v1/grants/exchange", json={"exchange_code":code})
        assert response.status_code == 200
        assert CAP not in response.text and code not in response.text
        headers = {"Authorization":"Bearer "+response.json()["access_token"]}
        assert client.get("/analytics/v1/datasets/dataset-a/summary", headers=headers).status_code == 200
        assert client.post("/analytics/v1/grants/exchange", json={"exchange_code":code}).status_code == 401
        assert client.post("/analytics/v1/issue").status_code == 404
    assert CAP not in caplog.text and code not in caplog.text


def configure_environment(monkeypatch, paths):
    monkeypatch.setenv("N2_05B_LOCAL_DEV", "1")
    for key, value in (("PRIVATE_DIR",paths.private_dir),("LEDGER",paths.ledger),
                       ("CREDENTIALS",paths.credentials),("DATASET_ROOT",paths.dataset_root)):
        monkeypatch.setenv("N2_05B_ISSUER_"+key, str(value))
    monkeypatch.delenv("CVAT_UPLOAD_DIR", raising=False)


@pytest.mark.parametrize("missing", ["N2_05B_LOCAL_DEV","N2_05B_ISSUER_PRIVATE_DIR",
    "N2_05B_ISSUER_LEDGER","N2_05B_ISSUER_CREDENTIALS","N2_05B_ISSUER_DATASET_ROOT"])
def test_missing_configuration_fails_closed(configured, monkeypatch, missing):
    configure_environment(monkeypatch, configured[0])
    monkeypatch.delenv(missing)
    with pytest.raises(IssuerUnavailable):
        create_issuer()


@pytest.mark.parametrize("change", ["relative", "same_file", "dataset", "repository", "missing_dir"])
def test_unsafe_locations(configured, tmp_path, change):
    paths = configured[0]
    if change == "relative": paths = replace(paths, ledger=Path("relative.sqlite3"))
    elif change == "same_file": paths = replace(paths, credentials=paths.ledger)
    elif change == "dataset": paths = replace(paths, dataset_root=paths.private_dir)
    elif change == "repository":
        root = Path(__file__).resolve().parents[1]
        paths = IssuerPaths(root, root/"never-created.sqlite3", root/"never-created.json", paths.dataset_root)
    else: paths = replace(paths, private_dir=tmp_path/"missing")
    with pytest.raises(IssuerUnavailable):
        paths.validate()


def test_hardlink_and_unverifiable_acl(configured, monkeypatch):
    paths = configured[0]
    extra = paths.private_dir/"hardlink"
    os.link(paths.credentials, extra)
    with pytest.raises(IssuerUnavailable):
        paths.validate()
    extra.unlink()  # isolated test-owned link only
    import backend.local_issuer_paths as module
    def denied(*args, **kwargs):
        raise OSError("unverifiable ACL")
    monkeypatch.setattr(module, "private_acl", denied)
    with pytest.raises(IssuerUnavailable):
        paths.validate()


def test_cli_refuses_redirect_and_requires_operator_confirmation(configured, monkeypatch, capsys):
    import backend.local_issuer_cli as cli
    configure_environment(monkeypatch, configured[0])
    # pytest's non-terminal streams cannot receive real exchange codes.
    assert cli.main(["issue"]) == 2
    monkeypatch.setattr(cli, "private_terminal", lambda: None)
    answers = iter(["requester", "ref-a", "dataset-a", "summary:read", "", "NO"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert cli.main(["issue"]) == 2
    assert sql(configured[0], "SELECT COUNT(*) FROM codes")[0][0] == 0
    assert CAP not in capsys.readouterr().err
