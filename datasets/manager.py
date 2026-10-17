"""Opt-in coordinator over an already-open Stage 3 UploadStore.

No original archive copies/moves, retention deletion, HTTP routes or UI changes.
The original uploaded row remains retained/quota-charged even after publication.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from datasets.limits import Limits, ValidationError
from datasets.runner import WorkerRunner, WorkerUnconfirmed
from datasets.validate import digest_file
from storage.files import no_links, regular, sync_dir, sync_file
from storage.uploads import UploadError


class DatasetManager:
    def __init__(self, uploads, limits: Limits = Limits(), *, runner=None, fault=lambda point: None):
        self.uploads,self.limits,self.runner,self.fault = uploads,limits,runner or WorkerRunner(),fault
        self._registry_uncertain = False
        self._uncertain_workers = {}
        with uploads.guard:
            uploads._idle()
            if getattr(uploads,"_dataset_manager",None) is not None:
                raise ValidationError("MANAGER_ALREADY_OPEN")
            self.root = uploads.root / ".datasets-v1"
            no_links(self.root)
            marker = self.root / "format.json"
            regular(marker)
            if self.root.exists() and not marker.exists() and any(self.root.iterdir()):
                raise ValidationError("UNKNOWN_DATASET_DIRECTORY")
            self.root.mkdir(exist_ok=True)
            if not marker.exists():
                with marker.open("xb") as file:
                    file.write(b'{"format":1}')
                    sync_file(file)
            else:
                with marker.open("rb") as file:
                    if json.loads(file.read(4096)) != {"format":1}:
                        raise ValidationError("UNSUPPORTED_DATASET_FORMAT")
            for name in ("work","sealed"):
                path = self.root / name
                no_links(path)
                path.mkdir(exist_ok=True)
            sync_dir(self.root)
            sync_dir(uploads.root)
            script = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
            uploads.db.executescript("BEGIN IMMEDIATE;\n"+script+"\nCOMMIT;")
            if [r[0] for r in uploads.db.execute("SELECT version FROM validation_meta")] != [1]:
                raise ValidationError("UNSUPPORTED_DATASET_SCHEMA")
            uploads._dataset_manager = self
            # All leases belonged to a previous process: exclusive root ownership
            # and a single manager prevent resetting leases of live readers.
            with uploads.transaction():
                uploads.db.execute("DELETE FROM validation_leases")
            self._recover()

    def _path(self, kind, attempt):
        if kind not in {"work","sealed"} or str(UUID(attempt)) != attempt:
            raise ValidationError("INVALID_INTERNAL_ID")
        path = self.root / kind / attempt
        no_links(path)
        return path

    def _row(self, upload_id):
        if self._registry_uncertain:
            raise sqlite3.DatabaseError("REGISTRY_UNAVAILABLE")
        return self.uploads.db.execute("SELECT * FROM validation_datasets WHERE upload_id=?",(upload_id,)).fetchone()

    def _settle_registry(self):
        """Never reconcile using this connection's uncommitted writes."""
        try:
            if self.uploads.db.in_transaction:
                self.uploads.db.rollback()
        except sqlite3.Error:
            try:
                active = self.uploads.db.in_transaction
            except sqlite3.Error:
                active = True
            if active:
                self._registry_uncertain = True
            raise
        if self.uploads.db.in_transaction:
            self._registry_uncertain = True
            raise sqlite3.DatabaseError("REGISTRY_UNAVAILABLE")

    def _view(self,row):
        return {"dataset_id":row["dataset_id"],"upload_id":row["upload_id"],"state":row["state"],
                "ready":row["state"]=="ready","error_code":row["error_code"]}

    def status(self,owner,upload_id,capability):
        with self.uploads.guard:
            upload = self.uploads._owned(owner,upload_id,capability)
            row = self._row(upload_id)
            if row is None:
                return {"upload_id":upload_id,"state":"not_requested","ready":False}
            if row["state"] == "ready" and upload["state"] != "uploaded":
                self._failed(upload_id,"UPLOAD_INTEGRITY_FAILED")
                row = self._row(upload_id)
            return self._view(row)

    def _set(self,upload_id,state,code=None):
        with self.uploads.transaction():
            self.uploads.db.execute("UPDATE validation_datasets SET state=?,error_code=? WHERE upload_id=?",
                                    (state,code,upload_id))

    def _failed(self,upload_id,code):
        if upload_id in self._uncertain_workers:
            raise self._uncertain_workers[upload_id]
        row = self._row(upload_id)
        before = self.uploads.db.execute("SELECT * FROM validation_attempts WHERE id=?",(row["attempt_id"],)).fetchone()
        if before is None or before["upload_id"] != upload_id:
            raise sqlite3.DatabaseError("REGISTRY_UNAVAILABLE")
        terminal = "cancelled" if code=="VALIDATION_CANCELLED" else "failed"
        try:
            with self.uploads.transaction():
                self.uploads.db.execute("UPDATE validation_datasets SET state=?,error_code=? WHERE upload_id=?",
                    (terminal,code,upload_id))
                self.uploads.db.execute("UPDATE validation_attempts SET state='failed' WHERE id=?",(row["attempt_id"],))
        except sqlite3.Error:
            self._settle_registry()
            current = self._row(upload_id)
            attempt = self.uploads.db.execute("SELECT * FROM validation_attempts WHERE id=?",(row["attempt_id"],)).fetchone()
            if not (before and current and attempt and current["attempt_id"] == row["attempt_id"]
                    and current["state"] == terminal and current["error_code"] == code
                    and attempt["state"] == "failed" and attempt["reserved_bytes"] == before["reserved_bytes"]):
                raise

    def validate(self,owner,upload_id,capability,*,retry=False,cancelled=lambda: False):
        with self.uploads.guard:
            self.uploads._idle()
            upload = self.uploads._owned(owner,upload_id,capability)
            if upload["state"] != "uploaded":
                raise UploadError(409,"UPLOAD_NOT_SEALED")
            previous = self._row(upload_id)
            if previous and previous["state"] in {"ready","validating","indexing","publishing"}:
                return self._view(previous)
            if previous and not retry:
                raise UploadError(409,"EXPLICIT_RETRY_REQUIRED")
            if (self.uploads.db.execute("SELECT COUNT(*) FROM validation_datasets WHERE state IN ('validating','indexing','publishing')").fetchone()[0]
                    or self.uploads.db.execute("SELECT COUNT(*) FROM uploads WHERE state IN ('created','uploading')").fetchone()[0]):
                raise UploadError(409,"VALIDATION_OR_UPLOAD_BUSY")
            reservations = self.uploads.db.execute("SELECT COALESCE(SUM(reserved_bytes),0) FROM validation_attempts").fetchone()[0]
            self.uploads._capacity(reservations+self.limits.workspace_bytes)
            attempt = str(uuid4())
            dataset_id = previous["dataset_id"] if previous else str(uuid4())
            with self.uploads.transaction():
                if previous:
                    self.uploads.db.execute("UPDATE validation_datasets SET attempt_id=?,state='validating',cancel_requested=0,"
                        "error_code=NULL,manifest_sha=NULL,index_sha=NULL,payload_sha=NULL WHERE upload_id=?",(attempt,upload_id))
                else:
                    self.uploads.db.execute("INSERT INTO validation_datasets (upload_id,dataset_id,attempt_id,state) VALUES (?,?,?,'validating')",
                                            (upload_id,dataset_id,attempt))
                self.uploads.db.execute("INSERT INTO validation_attempts VALUES (?,?,'active',?)",(attempt,upload_id,self.limits.workspace_bytes))
            work = self._path("work",attempt)
        try:
            work.mkdir()
            sync_dir(work.parent)
            def is_cancelled():
                with self.uploads.guard:
                    return cancelled() or bool(self._row(upload_id)["cancel_requested"])
            def progress(phase):
                with self.uploads.guard:
                    if phase == "indexing":
                        self._set(upload_id,"indexing")
            self.runner.run(self.uploads._file(upload_id,"payload.part"),work,dataset_id,self.limits,
                cancelled=is_cancelled,progress=progress,
                space_ok=lambda: self.uploads.free_bytes() >= self.uploads.config.min_free_bytes+self.limits.block_bytes)
            with self.uploads.guard:
                if is_cancelled():
                    raise ValidationError("VALIDATION_CANCELLED")
                self.fault("validated")
                manifest = self._manifest(work,dataset_id)
                self.fault("index_sealed")
                with self.uploads.transaction():
                    self.uploads.db.execute("UPDATE validation_datasets SET state='publishing',manifest_sha=?,index_sha=?,payload_sha=? WHERE upload_id=?",
                        (digest_file(work/"manifest.json"),manifest["index_sha256"],manifest["archive_sha256"],upload_id))
                self.fault("publishing_commit")
                self._publish(self._row(upload_id))
                return self._view(self._row(upload_id))
        except BaseException as error:
            with self.uploads.guard:
                if isinstance(error,WorkerUnconfirmed):
                    self._uncertain_workers[upload_id] = error
                    raise
                if isinstance(error,sqlite3.Error):
                    self._settle_registry()
                row = self._row(upload_id)
                if row and row["state"] == "ready" and self._ready_valid(row):
                    if isinstance(error,Exception):
                        return self._view(row)
                    raise
                if isinstance(error,sqlite3.Error):
                    # Index SQLite failures are translated inside _manifest.
                    # Preserve original registry exception types for API policy handling.
                    raise
                if row and row["state"] != "ready":
                    code = str(error) if isinstance(error,ValidationError) else "PUBLICATION_IO_OR_REGISTRY_FAILED"
                    self._failed(upload_id,code)
                elif row:
                    self._failed(upload_id,"PUBLICATION_SEAL_MISMATCH")
            if not isinstance(error,Exception):
                raise
            return self.status(owner,upload_id,capability)

    def _manifest(self,folder,dataset_id):
        if set(p.name for p in folder.iterdir()) != {"manifest.json","index.sqlite3"}:
            raise ValidationError("PUBLICATION_CONTENT_MISMATCH")
        path,index = folder/"manifest.json",folder/"index.sqlite3"
        regular(path)
        regular(index)
        with path.open("rb") as file:
            raw = file.read(65537)
        if len(raw)>65536 or index.stat().st_size>self.limits.index_bytes:
            raise ValidationError("PUBLICATION_RESOURCE_LIMIT")
        manifest = json.loads(raw)
        if (manifest.get("version") != 1 or manifest.get("dataset_id") != dataset_id
                or manifest.get("validated") is not True or manifest.get("index_sha256") != digest_file(index)):
            raise ValidationError("PUBLICATION_SEAL_MISMATCH")
        db = None
        try:
            db = sqlite3.connect(index.as_uri()+"?mode=ro&immutable=1",uri=True)
            if (db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] != manifest["entries"]
                    or db.execute("SELECT COUNT(*) FROM xml_nodes").fetchone()[0] != manifest["xml_records"]["nodes"]):
                raise ValidationError("INDEX_INTEGRITY_FAILED")
        except sqlite3.Error as error:
            raise ValidationError("INDEX_INTEGRITY_FAILED") from error
        finally:
            if db is not None:
                try:
                    db.close()
                except sqlite3.Error as error:
                    raise ValidationError("INDEX_INTEGRITY_FAILED") from error
        return manifest

    def _ready_valid(self,row):
        """Reconcile committed publication, never merely a directory rename."""
        attempt = self.uploads.db.execute("SELECT * FROM validation_attempts WHERE id=?",(row["attempt_id"],)).fetchone()
        if (row["state"] != "ready" or not attempt or attempt["upload_id"] != row["upload_id"]
                or attempt["state"] != "published" or attempt["reserved_bytes"] != 0):
            return False
        upload = self.uploads._row(row["upload_id"])
        folder = self._path("sealed",row["attempt_id"])
        try:
            manifest = self._manifest(folder,row["dataset_id"])
            return (upload["state"] == "uploaded" and digest_file(folder/"manifest.json") == row["manifest_sha"]
                    and manifest["index_sha256"] == row["index_sha"]
                    and manifest["archive_sha256"] == row["payload_sha"]
                    and manifest["archive_bytes"] == upload["size_bytes"])
        except (ValidationError,OSError,ValueError,KeyError,TypeError,AttributeError):
            return False

    def _publish(self,row):
        if row["cancel_requested"]:
            raise ValidationError("VALIDATION_CANCELLED")
        upload = self.uploads._row(row["upload_id"])
        if upload["state"] != "uploaded":
            raise ValidationError("UPLOAD_INTEGRITY_FAILED")
        work,sealed = self._path("work",row["attempt_id"]),self._path("sealed",row["attempt_id"])
        if work.exists() and sealed.exists():
            raise ValidationError("AMBIGUOUS_PUBLICATION")
        folder = sealed if sealed.exists() else work
        manifest = self._manifest(folder,row["dataset_id"])
        if (digest_file(folder/"manifest.json") != row["manifest_sha"] or manifest["index_sha256"] != row["index_sha"]
                or manifest["archive_sha256"] != row["payload_sha"] or manifest["archive_bytes"] != upload["size_bytes"]):
            raise ValidationError("PUBLICATION_SEAL_MISMATCH")
        self.uploads._capacity(self.limits.block_bytes)
        if not sealed.exists():
            os.replace(work,sealed)
            sync_dir(work.parent)
            sync_dir(sealed.parent)
        self.fault("directory_rename")
        if self._row(row["upload_id"])["cancel_requested"]:
            raise ValidationError("VALIDATION_CANCELLED")
        with self.uploads.transaction():
            self.uploads.db.execute("UPDATE validation_datasets SET state='ready',error_code=NULL WHERE upload_id=?",(row["upload_id"],))
            self.uploads.db.execute("UPDATE validation_attempts SET state='published',reserved_bytes=0 WHERE id=?",(row["attempt_id"],))
        self.fault("ready_commit")

    def _recover(self):
        for row in self.uploads.db.execute("SELECT * FROM validation_datasets").fetchall():
            if row["state"] in {"validating","indexing"}:
                self._failed(row["upload_id"],"VALIDATION_CANCELLED" if row["cancel_requested"] else "WORKER_INTERRUPTED")
            elif row["state"] in {"publishing","ready"}:
                try:
                    if row["state"] == "publishing":
                        self._publish(row)
                    else:
                        if not self._ready_valid(row):
                            raise ValidationError("PUBLICATION_SEAL_MISMATCH")
                except Exception as error:
                    if isinstance(error,sqlite3.Error):
                        self._settle_registry()
                    current = self._row(row["upload_id"])
                    if current["state"] == "ready" and self._ready_valid(current):
                        continue
                    if isinstance(error,sqlite3.Error):
                        raise
                    code = "VALIDATION_CANCELLED" if isinstance(error,ValidationError) and str(error)=="VALIDATION_CANCELLED" else "PUBLICATION_RECOVERY_FAILED"
                    self._failed(row["upload_id"],code)

    def cancel(self,owner,upload_id,capability):
        with self.uploads.guard:
            self.uploads._owned(owner,upload_id,capability)
            row = self._row(upload_id)
            if row is None or row["state"] == "ready":
                raise UploadError(409,"NO_CANCELLABLE_VALIDATION")
            try:
                with self.uploads.transaction():
                    self.uploads.db.execute("UPDATE validation_datasets SET cancel_requested=1 WHERE upload_id=?",(upload_id,))
            except sqlite3.Error:
                self._settle_registry()
                if not self._row(upload_id)["cancel_requested"]:
                    raise
            return self._view(self._row(upload_id))

    def cleanup_attempt(self,owner,upload_id,capability,attempt_id):
        """Explicit scratch/index cleanup only; never original archives or ready data."""
        with self.uploads.guard:
            self.uploads._owned(owner,upload_id,capability)
            attempt = self.uploads.db.execute("SELECT * FROM validation_attempts WHERE id=? AND upload_id=?",(attempt_id,upload_id)).fetchone()
            if attempt is None or attempt["state"] not in {"failed","cleaned"}:
                raise UploadError(409,"ATTEMPT_PROTECTED")
            if self.uploads.db.execute("SELECT COUNT(*) FROM validation_leases WHERE dataset_id=?",(self._row(upload_id)["dataset_id"],)).fetchone()[0]:
                raise UploadError(409,"DATASET_LEASED")
            folders = [self._path(kind,attempt_id) for kind in ("work","sealed")]
            allowed = {"entry.tmp","index.sqlite3","index.sqlite3-journal","manifest.json"}
            for folder in folders:
                if folder.exists():
                    if set(p.name for p in folder.iterdir())-allowed:
                        return False
                    for path in folder.iterdir():
                        regular(path)
            for folder in folders:
                if folder.exists():
                    for path in folder.iterdir():
                        path.unlink()
                    sync_dir(folder)
                    folder.rmdir()
                    sync_dir(folder.parent)
            with self.uploads.transaction():
                self.uploads.db.execute("UPDATE validation_attempts SET state='cleaned',reserved_bytes=0 WHERE id=?",(attempt_id,))
            return True

    @contextmanager
    def reader(self,owner,upload_id,capability):
        lease, archive, index = str(uuid4()),None,None
        with self.uploads.guard:
            self.uploads._owned(owner,upload_id,capability)
            row = self._row(upload_id)
            if row is None or row["state"] != "ready" or self.uploads._row(upload_id)["state"] != "uploaded":
                raise UploadError(409,"DATASET_NOT_READY")
            folder = self._path("sealed",row["attempt_id"])
            manifest = self._manifest(folder,row["dataset_id"])
            if digest_file(folder/"manifest.json") != row["manifest_sha"]:
                raise UploadError(409,"PUBLICATION_SEAL_MISMATCH")
            with self.uploads.transaction():
                self.uploads.db.execute("INSERT INTO validation_leases VALUES (?,?)",(lease,row["dataset_id"]))
        try:
            archive = self.uploads._file(upload_id,"payload.part").open("rb")
            index = sqlite3.connect((folder/"index.sqlite3").as_uri()+"?mode=ro&immutable=1",uri=True)
            yield {"summary":manifest,"archive":archive,"index":index}
        finally:
            if archive:
                archive.close()
            if index:
                index.close()
            with self.uploads.guard, self.uploads.transaction():
                self.uploads.db.execute("DELETE FROM validation_leases WHERE id=?",(lease,))
