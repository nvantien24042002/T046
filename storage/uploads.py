"""SQLite receipts + fsynced payloads. No ZIP validation or ready datasets.

All mutations are serialized; one API process owns the root lock. Unknown files
are never adopted or recursively deleted. Test fault hooks are Python callbacks,
not remotely configurable switches.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import threading
import time
from typing import Callable
from uuid import UUID, uuid4

from storage.config import UploadConfig
from storage.files import ProcessLock, no_links, regular, sync_dir, sync_file

ACTIVE = {"created", "uploading"}


class UploadError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)


class UploadStore:
    def __init__(self, config: UploadConfig, *, clock: Callable[[], float] = time.time,
                 free_bytes: Callable[[], int] | None = None,
                 fault: Callable[[str], None] = lambda point: None):
        self.config, self.clock, self.fault = config, clock, fault
        self.root = config.root
        self.guard = threading.RLock()
        self.active_writers: dict[str, ChunkWriter] = {}
        self.free_bytes = free_bytes or (lambda: shutil.disk_usage(self.root).free)
        self.db = None
        self.process_lock = None
        try:
            no_links(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            no_links(self.root)
            if not (self.root / ".cvat-upload-root.json").exists() and (
                    set(p.name for p in self.root.iterdir()) - {".service.lock"}):
                raise ValueError("Storage initialization requires a new empty private root.")
            self.process_lock = ProcessLock(self.root / ".service.lock")
            self._initialize()
            self.recover()
        except BaseException:
            self.close()
            raise

    @property
    def active(self) -> ChunkWriter | None:
        with self.guard:
            return next(iter(self.active_writers.values()), None) if self.active_writers else None

    @active.setter
    def active(self, value: ChunkWriter | None) -> None:
        with self.guard:
            if value is None:
                self.active_writers.clear()
            else:
                self.active_writers[value.row["id"]] = value

    def is_active_writer(self, upload_id: str, writer: ChunkWriter) -> bool:
        with self.guard:
            return self.active_writers.get(upload_id) is writer

    def _unregister_writer(self, upload_id: str, writer: ChunkWriter) -> None:
        with self.guard:
            if self.active_writers.get(upload_id) is writer:
                del self.active_writers[upload_id]

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.process_lock is not None:
            self.process_lock.close()
            self.process_lock = None

    def _initialize(self) -> None:
        marker = self.root / ".cvat-upload-root.json"
        database = self.root / "registry.sqlite3"
        for path in (marker, database, self.root / "registry.sqlite3-journal"):
            regular(path)
        fresh = not marker.exists()
        if fresh:
            if set(p.name for p in self.root.iterdir()) != {".service.lock"}:
                raise ValueError("Storage initialization requires a new empty private root.")
            root_id = str(uuid4())
            with marker.open("xb") as output:
                output.write(json.dumps({"format": 1, "root_id": root_id}).encode())
                sync_file(output)
            sync_dir(self.root)
        else:
            with marker.open("rb") as source:
                root_id = json.loads(source.read(4096))["root_id"]
            if not database.is_file():
                raise ValueError("Registry missing; refusing to adopt or delete existing files.")
        self.db = sqlite3.connect(database, isolation_level=None, check_same_thread=False, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=EXTRA")
        self.db.execute("PRAGMA foreign_keys=ON")
        if fresh:
            self.db.executescript((Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8"))
            with self.transaction():
                self.db.executemany("INSERT INTO meta VALUES (?,?)", [
                    ("root_id", root_id), ("secret_tag", self._hash(self.config.secret))])
            sync_dir(self.root)
        if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Registry integrity check failed; no file recovery attempted.")
        metadata = dict(self.db.execute("SELECT key,value FROM meta"))
        if (self.db.execute("PRAGMA user_version").fetchone()[0] != 1
                or metadata.get("root_id") != root_id
                or metadata.get("secret_tag") != self._hash(self.config.secret)):
            raise ValueError("Registry version/root/secret mismatch; refusing startup.")
        self.uploads_dir = self.root / "uploads"
        no_links(self.uploads_dir)
        self.uploads_dir.mkdir(exist_ok=True)
        sync_dir(self.root)

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def capability(self, owner: str, upload_id: str) -> str:
        return hmac.new(self.config.secret.encode(), f"{owner}:{upload_id}".encode(), hashlib.sha256).hexdigest()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _folder(self, upload_id: str) -> Path:
        try:
            if str(UUID(upload_id)) != upload_id:
                raise ValueError()
        except ValueError:
            raise UploadError(404, "UNKNOWN_UPLOAD") from None
        folder = self.uploads_dir / upload_id
        no_links(folder)
        return folder

    def _file(self, upload_id: str, name: str) -> Path:
        assert name in {"payload.part", "incoming.chunk"}
        path = self._folder(upload_id) / name
        regular(path)
        return path

    def _row(self, upload_id: str):
        self._folder(upload_id)
        row = self.db.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
        if row is None:
            raise UploadError(404, "UNKNOWN_UPLOAD")
        return row

    def _owned(self, owner: str, upload_id: str, capability: str):
        row = self._row(upload_id)
        if row["owner_id"] != owner or not secrets.compare_digest(
                self._hash(capability), row["capability_hash"]):
            raise UploadError(403, "ACCESS_DENIED")
        return row

    @staticmethod
    def _view(row) -> dict:
        intact = row["state"] in ACTIVE | {"uploaded"}
        return dict(upload_id=row["id"], filename=row["filename"], size_bytes=row["size_bytes"],
                    chunk_size_bytes=row["chunk_bytes"], state=row["state"], ready=False, validated=False,
                    received_bytes=row["committed_bytes"] if intact else 0,
                    recorded_bytes=row["committed_bytes"], next_offset=row["committed_bytes"] if intact else None,
                    durable=intact, persistence="fsync-and-sqlite-process-restart",
                    expires_at=row["expires_at"], cleanup_pending=bool(row["cleanup_pending"]),
                    error_code=row["error_code"])

    def status(self, owner: str, upload_id: str, capability: str) -> dict:
        with self.guard:
            return self._view(self._owned(owner, upload_id, capability))

    def receipts(self, owner: str, upload_id: str, capability: str, start: int = 0, limit: int = 128) -> dict:
        with self.guard:
            row = self._owned(owner, upload_id, capability)
            if row["state"] not in ACTIVE | {"uploaded"}:
                raise UploadError(409, "UPLOAD_NOT_RESUMABLE")
            if not 0 <= start <= self.config.max_chunks or not 1 <= limit <= 256:
                raise UploadError(422, "INVALID_PAGE")
            rows = self.db.execute("SELECT chunk_index,offset_bytes,length_bytes,sha256 FROM receipts "
                                   "WHERE upload_id=? AND chunk_index>=? ORDER BY chunk_index LIMIT ?",
                                   (upload_id, start, limit)).fetchall()
            return {"receipts": [dict(r) for r in rows],
                    "next_index": rows[-1]["chunk_index"] + 1 if rows else start}

    def _idle(self, upload_id: str | None = None) -> None:
        if upload_id is not None:
            if upload_id in self.active_writers:
                raise UploadError(409, "WRITER_BUSY")
        elif self.active_writers:
            raise UploadError(409, "WRITER_BUSY")

    def _capacity(self, additional: int = 0) -> None:
        reserved = self.db.execute("SELECT COALESCE(SUM(reserved_bytes),0) FROM uploads").fetchone()[0]
        if self.free_bytes() < reserved + additional + self.config.min_free_bytes:
            raise UploadError(507, "INSUFFICIENT_STORAGE")

    def create(self, owner: str, filename: str, size: int, request_key: str) -> dict:
        with self.guard:
            self.maintain()
            if (type(size) is not int or not 0 < size <= self.config.max_bytes
                    or (size + self.config.chunk_bytes - 1) // self.config.chunk_bytes > self.config.max_chunks):
                raise UploadError(413, "SIZE_OR_CHUNK_LIMIT")
            if (not isinstance(filename, str) or not 5 <= len(filename) <= 200
                    or not filename.lower().endswith(".zip") or any(c in filename for c in "/\\:\x00")
                    or any(ord(c) < 32 for c in filename)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", request_key)):
                raise UploadError(422, "INVALID_METADATA")
            previous = self.db.execute("SELECT * FROM uploads WHERE owner_id=? AND request_key=?",
                                       (owner, request_key)).fetchone()
            if previous:
                if previous["size_bytes"] != size or previous["filename"] != filename:
                    raise UploadError(409, "IDEMPOTENCY_CONFLICT")
                return {**self._view(previous), "upload_capability": self.capability(owner, previous["id"])}
            stored = self.db.execute("SELECT COALESCE(SUM(size_bytes),0) FROM uploads WHERE cleaned_at IS NULL").fetchone()[0]
            if stored + size > self.config.quota_bytes:
                raise UploadError(507, "STORAGE_QUOTA")
            reservation = size + min(size, self.config.chunk_bytes)
            self._capacity(reservation)
            upload_id, now = str(uuid4()), self.clock()
            capability = self.capability(owner, upload_id)
            # Reserve/create metadata first. A crash here recovers an empty session.
            with self.transaction():
                self.db.execute("INSERT INTO uploads (id,owner_id,capability_hash,request_key,filename,size_bytes,"
                    "chunk_bytes,state,reserved_bytes,created_at,updated_at,expires_at,absolute_expires_at) "
                    "VALUES (?,?,?,?,?,?,?,'created',?,?,?,?,?)",
                    (upload_id, owner, self._hash(capability), request_key, filename, size,
                     self.config.chunk_bytes, reservation, now, now,
                     min(now + self.config.idle_seconds, now + self.config.lifetime_seconds),
                     now + self.config.lifetime_seconds))
            self.fault("session_commit")
            try:
                self._repair(upload_id)
            except OSError:
                self._fail(upload_id, "STORAGE_UNAVAILABLE")
                raise UploadError(507, "STORAGE_UNAVAILABLE") from None
            return {**self._view(self._row(upload_id)), "upload_capability": capability}

    def begin(self, owner: str, upload_id: str, capability: str, index: int, offset: int, digest: str):
        with self.guard:
            if upload_id in self.active_writers:
                raise UploadError(409, "WRITER_BUSY")
            row = self._owned(owner, upload_id, capability)
            if row["state"] in ACTIVE and self.clock() >= row["expires_at"]:
                self._expire(upload_id)
                raise UploadError(410, "UPLOAD_EXPIRED")
            if row["state"] not in ACTIVE | {"uploaded"}:
                raise UploadError(410 if row["state"] == "expired" else 409, "UPLOAD_NOT_RESUMABLE")
            if (not 0 <= index < self.config.max_chunks or not 0 <= offset <= row["size_bytes"]
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise UploadError(422, "INVALID_CHUNK_METADATA")
            receipt = self.db.execute("SELECT * FROM receipts WHERE upload_id=? AND chunk_index=?",
                                      (upload_id, index)).fetchone()
            if receipt:
                if receipt["offset_bytes"] != offset or receipt["sha256"] != digest:
                    raise UploadError(409, "REPLAY_CONFLICT")
                writer = ChunkWriter(self, row, index, offset, digest, receipt["length_bytes"], replay=True)
            else:
                if (row["state"] == "uploaded" or offset != row["committed_bytes"]
                        or index != offset // row["chunk_bytes"] or offset == row["size_bytes"]):
                    raise UploadError(409, "OFFSET_MISMATCH")
                self._capacity()
                try:
                    self._repair(upload_id)
                    writer = ChunkWriter(self, row, index, offset, digest,
                        min(row["chunk_bytes"], row["size_bytes"] - offset), replay=False)
                except OSError:
                    self._fail(upload_id, "STORAGE_UNAVAILABLE")
                    raise UploadError(507, "STORAGE_UNAVAILABLE") from None
            self.active_writers[upload_id] = writer
            return writer

    def complete(self, owner: str, upload_id: str, capability: str) -> dict:
        with self.guard:
            if upload_id in self.active_writers:
                raise UploadError(409, "WRITER_BUSY")
            row = self._owned(owner, upload_id, capability)
            if row["state"] in ACTIVE and self.clock() >= row["expires_at"]:
                self._expire(upload_id)
                raise UploadError(410, "UPLOAD_EXPIRED")
            if row["state"] == "uploaded":
                return self._view(row)
            if row["state"] not in ACTIVE or row["committed_bytes"] != row["size_bytes"]:
                raise UploadError(409, "INCOMPLETE_OR_INVALID_STATE")
            self._repair(upload_id)
            with self.transaction():
                self.db.execute("UPDATE uploads SET state='uploaded',reserved_bytes=0,updated_at=? WHERE id=?",
                                (self.clock(), upload_id))
            return self._view(self._row(upload_id))  # Byte receipt only, no validation/registration.

    def cancel(self, owner: str, upload_id: str, capability: str) -> dict:
        with self.guard:
            if upload_id in self.active_writers:
                raise UploadError(409, "WRITER_BUSY")
            row = self._owned(owner, upload_id, capability)
            if row["state"] != "expired":
                with self.transaction():
                    self.db.execute("UPDATE uploads SET state='cancelled',cleanup_pending=1 WHERE id=? AND cleaned_at IS NULL",
                                    (upload_id,))
            self.fault("cancel_commit")
            self._cleanup(upload_id)
            return self._view(self._row(upload_id))

    def _fail(self, upload_id: str, code: str) -> None:
        with self.transaction():
            self.db.execute("UPDATE uploads SET state='failed',error_code=? WHERE id=?", (code, upload_id))

    def _expire(self, upload_id: str) -> None:
        with self.transaction():
            self.db.execute("UPDATE uploads SET state='expired',cleanup_pending=1 WHERE id=?", (upload_id,))

    def _repair(self, upload_id: str) -> None:
        with self.guard:
            row = self._row(upload_id)
            folder = self._folder(upload_id)
            payload = self._file(upload_id, "payload.part")
            if not payload.exists():
                if row["committed_bytes"]:
                    self._fail(upload_id, "MISSING_ACKNOWLEDGED_BYTES")
                    raise UploadError(409, "MISSING_ACKNOWLEDGED_BYTES")
                folder.mkdir(exist_ok=True)
                with payload.open("xb") as file:
                    sync_file(file)
                sync_dir(folder)
                sync_dir(self.uploads_dir)
            size = payload.stat().st_size
            if size < row["committed_bytes"]:
                self._fail(upload_id, "MISSING_ACKNOWLEDGED_BYTES")
                raise UploadError(409, "MISSING_ACKNOWLEDGED_BYTES")
            if size > row["committed_bytes"]:
                with payload.open("r+b") as file:
                    file.truncate(row["committed_bytes"])
                    sync_file(file)
            scratch = self._file(upload_id, "incoming.chunk")
            if scratch.exists():
                scratch.unlink()
                sync_dir(folder)

    def _verify_receipts(self, row) -> None:
        receipts = self.db.execute("SELECT * FROM receipts WHERE upload_id=? ORDER BY chunk_index", (row["id"],)).fetchall()
        if len(receipts) > self.config.max_chunks:
            raise UploadError(409, "RECEIPT_INCONSISTENCY")
        payload = self._file(row["id"], "payload.part")
        if not payload.exists():
            if row["committed_bytes"] or receipts:
                raise UploadError(409, "MISSING_ACKNOWLEDGED_BYTES")
            return
        position = 0
        with payload.open("rb") as file:
            for index, receipt in enumerate(receipts):
                length = min(row["chunk_bytes"], row["size_bytes"] - position)
                if (receipt["chunk_index"] != index or receipt["offset_bytes"] != position
                        or receipt["length_bytes"] != length or length <= 0):
                    raise UploadError(409, "RECEIPT_INCONSISTENCY")
                digest, left = hashlib.sha256(), length
                while left:
                    block = file.read(min(left, self.config.block_bytes))
                    if not block:
                        raise UploadError(409, "MISSING_ACKNOWLEDGED_BYTES")
                    digest.update(block)
                    left -= len(block)
                if digest.hexdigest() != receipt["sha256"]:
                    raise UploadError(409, "RECEIPT_HASH_MISMATCH")
                position += length
        if position != row["committed_bytes"] or (row["state"] == "uploaded" and position != row["size_bytes"]):
            raise UploadError(409, "RECEIPT_INCONSISTENCY")

    def recover(self) -> None:
        with self.guard:
            if self.active_writers:
                raise UploadError(409, "WRITER_BUSY")
            for row in self.db.execute("SELECT * FROM uploads WHERE cleaned_at IS NULL").fetchall():
                if row["state"] in ACTIVE | {"uploaded"}:
                    try:
                        # Verify before truncation: inconsistent DB/receipts must not
                        # cause destructive repair of historically acknowledged data.
                        self._verify_receipts(row)
                        self._repair(row["id"])
                    except UploadError as error:
                        self._fail(row["id"], error.code)
                    except OSError:
                        self._fail(row["id"], "UNSAFE_OR_UNAVAILABLE_STORAGE")
            self.maintain()

    def _cleanup(self, upload_id: str) -> None:
        row = self._row(upload_id)
        if row["state"] not in {"cancelled", "expired"} or row["cleaned_at"] is not None:
            return
        try:
            folder = self._folder(upload_id)
            if folder.exists():
                if set(p.name for p in folder.iterdir()) - {"payload.part", "incoming.chunk"}:
                    return  # Unknown contents: preserve everything and reservation.
                paths = [self._file(upload_id, name) for name in ("payload.part", "incoming.chunk")]
                for path in paths:
                    if path.exists():
                        path.unlink()
                    sync_dir(folder)
                folder.rmdir()  # No recursive delete, and only this known empty directory.
                sync_dir(self.uploads_dir)
            self.fault("files_removed")
            with self.transaction():
                self.db.execute("UPDATE uploads SET cleaned_at=?,reserved_bytes=0,cleanup_pending=0 WHERE id=?",
                                (self.clock(), upload_id))
        except OSError:
            pass  # Retry later; reservation remains, no success claim about cleanup.

    def maintain(self) -> dict:
        with self.guard:
            for row in self.db.execute("SELECT id FROM uploads WHERE state IN ('created','uploading') AND expires_at<=?",
                                       (self.clock(),)).fetchall():
                if row["id"] not in self.active_writers:
                    self._expire(row["id"])
            for row in self.db.execute("SELECT id FROM uploads WHERE cleanup_pending=1 AND cleaned_at IS NULL").fetchall():
                if row["id"] not in self.active_writers:
                    self._cleanup(row["id"])
            if self.active_writers:
                return {"writer_active": True}
            known = {r[0] for r in self.db.execute("SELECT id FROM uploads")}
            orphans = sum(p.name not in known for p in self.uploads_dir.iterdir())
            return {"writer_active": False, "unknown_entries_preserved": orphans}


class ChunkWriter:
    def __init__(self, store: UploadStore, row, index: int, offset: int, digest: str, length: int, *, replay: bool):
        self.store, self.row, self.index, self.offset = store, row, index, offset
        self.expected_hash, self.length, self.replay = digest, length, replay
        self.count, self.hash = 0, hashlib.sha256()
        self.scratch = None if replay else store._file(row["id"], "incoming.chunk").open("xb")
        self.lock = threading.RLock()
        self.finished = False
        self.closed = False

    def write(self, block: bytes) -> None:
        with self.lock:
            if self.closed or not self.store.is_active_writer(self.row["id"], self):
                raise UploadError(409, "WRITER_CLOSED")
            if self.count + len(block) > self.length:
                raise UploadError(413, "CHUNK_TOO_LARGE")
            # ASGI fragments may exceed the application block budget; memoryview
            # avoids a second entire-fragment copy while bounding individual writes.
            view = memoryview(block)
            for start in range(0, len(view), self.store.config.block_bytes):
                piece = view[start:start + self.store.config.block_bytes]
                if self.scratch is not None:
                    if self.store.free_bytes() < len(piece) + self.store.config.min_free_bytes:
                        raise UploadError(507, "INSUFFICIENT_STORAGE")
                    if self.scratch.write(piece) != len(piece):
                        raise OSError("Short scratch write")
                self.hash.update(piece)
            self.count += len(block)

    def finish(self) -> dict:
        with self.lock:
            if self.closed or not self.store.is_active_writer(self.row["id"], self):
                raise UploadError(409, "WRITER_CLOSED")
            if self.count != self.length or self.hash.hexdigest() != self.expected_hash:
                raise UploadError(422, "CHUNK_LENGTH_OR_HASH_MISMATCH")
            if self.replay:
                self.finished = True
                self.closed = True
                self.store._unregister_writer(self.row["id"], self)
                with self.store.guard:
                    return {**self.store._view(self.store._row(self.row["id"])), "replayed": True}
            sync_file(self.scratch)
            self.scratch.close()
            self.scratch = None
            self.store.fault("scratch_fsync")
            payload = self.store._file(self.row["id"], "payload.part")
            if payload.stat().st_size != self.offset:
                raise UploadError(409, "PAYLOAD_LENGTH_CHANGED")
            with self.store._file(self.row["id"], "incoming.chunk").open("rb") as source, payload.open("r+b") as target:
                target.seek(self.offset)
                while block := source.read(self.store.config.block_bytes):
                    if self.store.free_bytes() < len(block) + self.store.config.min_free_bytes:
                        raise UploadError(507, "INSUFFICIENT_STORAGE")
                    if target.write(block) != len(block):
                        raise OSError("Short payload write")
                sync_file(target)
            self.store.fault("payload_fsync")
            now, committed = self.store.clock(), self.offset + self.length
            with self.store.guard:
                with self.store.transaction():
                    self.store.db.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?)",
                        (self.row["id"], self.index, self.offset, self.length, self.expected_hash, now))
                    self.store.db.execute("UPDATE uploads SET committed_bytes=?,state='uploading',reserved_bytes=?,"
                        "updated_at=?,expires_at=? WHERE id=?", (committed,
                        self.row["size_bytes"] - committed + min(self.row["size_bytes"], self.row["chunk_bytes"]),
                        now, min(now + self.store.config.idle_seconds, self.row["absolute_expires_at"]), self.row["id"]))
                    self.store.fault("before_receipt_commit")
                self.store.fault("receipt_commit")
                # After COMMIT a cleanup error must never truncate a committed chunk.
                self.store._repair(self.row["id"])
                self.finished = True
                self.closed = True
                self.store._unregister_writer(self.row["id"], self)
                return {**self.store._view(self.store._row(self.row["id"])), "replayed": False}

    def abort(self) -> None:
        with self.lock:
            if self.finished:
                return
            self.closed = True
            if self.scratch is not None:
                try:
                    self.scratch.close()
                except OSError:
                    pass
                self.scratch = None
            try:
                if self.store.is_active_writer(self.row["id"], self) and not self.replay:
                    # Read authoritative DB state after an ambiguous COMMIT. If DB
                    # cannot be read, leave files untouched for startup recovery.
                    self.store._repair(self.row["id"])
            except OSError:
                with self.store.guard:
                    self.store._fail(self.row["id"], "RECOVERY_IO_FAILED")
            finally:
                self.store._unregister_writer(self.row["id"], self)
