"""Local development authority only. No HTTP issuance, login or backend access."""
from contextlib import closing
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import time

from pydantic import TypeAdapter

from backend.analytics_contracts import AUDIENCE, ID, METADATA_LIMIT, ORIGIN
from backend.analytics_grants import IssuerRecord
from backend.local_issuer_paths import IssuerPaths, IssuerUnavailable, from_environment, private_acl
from storage.files import regular, sync_dir

APP_ID = 0x4E323035
MAX_LEDGER = 64 * 1024 * 1024
SCHEMA = """
PRAGMA application_id=1311911989;
CREATE TABLE issuer_meta(version INTEGER NOT NULL CHECK(version=1));
INSERT INTO issuer_meta VALUES(1);
CREATE TABLE codes(
 code_hash TEXT PRIMARY KEY CHECK(length(code_hash)=64),
 issued_at INTEGER NOT NULL, expires_at INTEGER NOT NULL CHECK(expires_at=issued_at+60),
 requester TEXT NOT NULL, audience TEXT NOT NULL, origin TEXT NOT NULL,
 dataset_id TEXT NOT NULL, upload_id TEXT NOT NULL, scopes TEXT NOT NULL,
 result_ids TEXT NOT NULL, credential_ref TEXT NOT NULL, credential_generation TEXT NOT NULL,
 consumed_at INTEGER, revoked_at INTEGER
);
CREATE TRIGGER no_delete BEFORE DELETE ON codes BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
CREATE TRIGGER immutable_authority BEFORE UPDATE OF code_hash,issued_at,expires_at,requester,audience,origin,
 dataset_id,upload_id,scopes,result_ids,credential_ref,credential_generation ON codes
 BEGIN SELECT RAISE(ABORT,'IMMUTABLE_AUTHORITY'); END;
CREATE TRIGGER consumed_once BEFORE UPDATE OF consumed_at ON codes
 WHEN OLD.consumed_at IS NOT NULL OR NEW.consumed_at IS NULL
 BEGIN SELECT RAISE(ABORT,'CONSUMED_ONCE'); END;
CREATE TRIGGER revoked_once BEFORE UPDATE OF revoked_at ON codes
 WHEN OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL
 BEGIN SELECT RAISE(ABORT,'REVOKED_ONCE'); END;
"""


def digest(code):
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def valid_id(value):
    return TypeAdapter(ID).validate_python(value)


def authorization(scopes, results):
    if (not isinstance(scopes, list) or not isinstance(results, list)
            or any(type(value) is not str for value in scopes+results)
            or len(set(scopes)) != len(scopes) or len(set(results)) != len(results)):
        raise IssuerUnavailable()
    if set(scopes) not in ({"summary:read"}, {"summary:read", "results:read"}):
        raise IssuerUnavailable()
    if "results:read" not in scopes and results:
        raise IssuerUnavailable()
    for value in results:
        valid_id(value)


class LocalDevelopmentIssuer:
    def __init__(self, paths: IssuerPaths, *, clock=time.time, connect=None):
        self.paths, self.clock = paths, clock
        self._connector = connect or sqlite3.connect
        paths.validate()
        with closing(self._open()) as db:
            self._credentials()

    @classmethod
    def initialize(cls, paths):
        """Explicit operator action; never called by the factory or consume."""
        paths.validate(initializing=True)
        if paths.ledger.exists() or paths.credentials.exists() or paths.claims.exists():
            raise IssuerUnavailable()
        try:
            paths.claims.mkdir(mode=0o700)
            fd = os.open(paths.ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(str(paths.ledger))) as db:
                db.executescript("BEGIN IMMEDIATE;"+SCHEMA+"COMMIT;")
            fd = os.open(paths.credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(b'{"version":1,"credentials":{}}')
                file.flush()
                os.fsync(file.fileno())
            sync_dir(paths.private_dir)
            paths.validate()
        except Exception:
            # Never delete/recreate partial state automatically.
            raise IssuerUnavailable() from None

    def _now(self):
        value = self.clock()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise IssuerUnavailable()
        return value

    def _open(self):
        self.paths.validate()
        db = self._connector(self.paths.ledger.as_uri()+"?mode=rw", uri=True,
                             timeout=5, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            if (db.execute("PRAGMA application_id").fetchone()[0] != APP_ID
                    or [row[0] for row in db.execute("SELECT version FROM issuer_meta")] != [1]):
                raise IssuerUnavailable()
            return db
        except Exception:
            db.close()
            raise IssuerUnavailable() from None

    def _credentials(self):
        if self.paths.credentials.stat().st_size > METADATA_LIMIT:
            raise IssuerUnavailable()
        from backend.analytics_contracts import parse_json
        data = parse_json(self.paths.credentials.read_bytes())
        if (type(data) is not dict or data.keys() != {"version", "credentials"}
                or type(data["version"]) is not int or data["version"] != 1
                or type(data["credentials"]) is not dict):
            raise IssuerUnavailable()
        return data

    def _write_credentials(self, data):
        raw = json.dumps(data, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > METADATA_LIMIT:
            raise IssuerUnavailable()
        temp = self.paths.private_dir / (".credential-write-"+secrets.token_hex(16))
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            private_acl(temp)
            os.replace(temp, self.paths.credentials)
            sync_dir(self.paths.private_dir)
        except Exception:
            # Partial private temp files are retained for operator review, not logged.
            raise IssuerUnavailable() from None

    @staticmethod
    def _profile(profile):
        required = {"dataset_id", "upload_id", "audience", "origin", "scopes", "result_ids",
                    "generation", "capability", "revoked"}
        if type(profile) is not dict or profile.keys() != required:
            raise IssuerUnavailable()
        valid_id(profile["dataset_id"])
        valid_id(profile["upload_id"])
        authorization(profile["scopes"], profile["result_ids"])
        if (profile["audience"] != AUDIENCE or profile["origin"] != ORIGIN
                or profile["revoked"] is not False
                or not isinstance(profile["generation"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", profile["generation"])
                or not isinstance(profile["capability"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", profile["capability"])):
            raise IssuerUnavailable()

    def add_credential(self, reference, *, dataset_id, upload_id, capability, scopes, result_ids):
        """Authorized operator binds pre-existing metadata; never queries the backend."""
        valid_id(reference)
        profile = dict(dataset_id=dataset_id, upload_id=upload_id, capability=capability,
                       audience=AUDIENCE, origin=ORIGIN, scopes=sorted(scopes), result_ids=sorted(result_ids),
                       generation=secrets.token_hex(16), revoked=False)
        self._profile(profile)
        try:
            with closing(self._open()) as db:
                db.execute("BEGIN IMMEDIATE")
                data = self._credentials()
                if reference in data["credentials"]:
                    raise IssuerUnavailable()  # no replacement/unrevocation/reference reuse
                data["credentials"][reference] = profile
                self._write_credentials(data)
                db.commit()
        except Exception:
            raise IssuerUnavailable() from None

    def issue(self, *, requester, credential_ref, dataset_id, scopes, result_ids):
        """Called only after explicit local operator authorization, never over HTTP."""
        try:
            if not isinstance(requester, str) or not 1 <= len(requester) <= 256 or any(ord(c) < 32 for c in requester):
                raise IssuerUnavailable()
            valid_id(credential_ref)
            valid_id(dataset_id)
            authorization(scopes, result_ids)
            with closing(self._open()) as db:
                db.execute("BEGIN IMMEDIATE")
                if self.paths.ledger.stat().st_size >= MAX_LEDGER:
                    raise IssuerUnavailable()
                profile = self._credentials()["credentials"][credential_ref]
                self._profile(profile)
                if (dataset_id != profile["dataset_id"] or not set(scopes) <= set(profile["scopes"])
                        or not set(result_ids) <= set(profile["result_ids"])):
                    raise IssuerUnavailable()
                stamp = int(self._now())
                code = secrets.token_urlsafe(32)
                key = digest(code)
                if (self.paths.claims/key).exists():
                    raise IssuerUnavailable()
                db.execute("INSERT INTO codes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)", (
                    key, stamp, stamp+60, requester, AUDIENCE, ORIGIN, dataset_id, profile["upload_id"],
                    json.dumps(sorted(scopes)), json.dumps(sorted(result_ids)), credential_ref, profile["generation"]))
                db.commit()
                return code
        except Exception:
            raise IssuerUnavailable() from None

    def _burn(self, key):
        path = self.paths.claims/key
        regular(path)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(b"spent\n")
            file.flush()
            os.fsync(file.fileno())
        sync_dir(self.paths.claims)

    def consume(self, code):
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", code):
            return None
        key = digest(code)
        try:
            with closing(self._open()) as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM codes WHERE code_hash=?", (key,)).fetchone()
                if row is None or row["consumed_at"] is not None or row["revoked_at"] is not None:
                    return None
                if (self.paths.claims/key).exists():
                    return None
                now = self._now()
                if not (type(row["issued_at"]) is int and type(row["expires_at"]) is int
                        and row["expires_at"] == row["issued_at"]+60 and row["issued_at"] <= now < row["expires_at"]):
                    return None
                profile = self._credentials()["credentials"][row["credential_ref"]]
                self._profile(profile)
                scopes, results = json.loads(row["scopes"]), json.loads(row["result_ids"])
                authorization(scopes, results)
                if (row["audience"] != AUDIENCE or row["origin"] != ORIGIN
                        or row["dataset_id"] != profile["dataset_id"] or row["upload_id"] != profile["upload_id"]
                        or row["credential_generation"] != profile["generation"]
                        or not set(scopes) <= set(profile["scopes"]) or not set(results) <= set(profile["result_ids"])):
                    return None
                authority = IssuerRecord(row["issued_at"], row["audience"], row["origin"],
                    row["dataset_id"], frozenset(scopes), frozenset(results), profile["capability"])
                self._burn(key)  # durable fail-closed evidence before attempting commit
                changed = db.execute("UPDATE codes SET consumed_at=? WHERE code_hash=? AND consumed_at IS NULL "
                                     "AND revoked_at IS NULL AND issued_at<=? AND expires_at>?",
                                     (int(now), key, now, now)).rowcount
                if changed != 1:
                    return None
                db.commit()  # no authority escapes before an unambiguous commit acknowledgement
                return authority
        except Exception:
            return None  # no secret/error bodies; spent marker survives ambiguous commit

    def revoke_code(self, code):
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", code):
            raise IssuerUnavailable()
        key = digest(code)
        try:
            with closing(self._open()) as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT code_hash FROM codes WHERE code_hash=?", (key,)).fetchone()
                if row is None:
                    return
                if not (self.paths.claims/key).exists():
                    self._burn(key)
                db.execute("UPDATE codes SET revoked_at=? WHERE code_hash=? AND revoked_at IS NULL", (int(self._now()), key))
                db.commit()
        except Exception:
            raise IssuerUnavailable() from None

    def revoke_credential(self, reference):
        try:
            valid_id(reference)
            with closing(self._open()) as db:
                db.execute("BEGIN IMMEDIATE")
                data = self._credentials()
                data["credentials"][reference]["revoked"] = True
                self._write_credentials(data)
                db.execute("UPDATE codes SET revoked_at=? WHERE credential_ref=? AND revoked_at IS NULL",
                           (int(self._now()), reference))
                db.commit()
        except Exception:
            raise IssuerUnavailable() from None


def create_issuer():
    """Factory for backend.analytics_dev; existing safe state required, no auto-init."""
    try:
        return LocalDevelopmentIssuer(from_environment())
    except Exception:
        raise IssuerUnavailable() from None
