"""Local-only durable byte upload API. No archive validation or dataset routes."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import sqlite3

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.requests import ClientDisconnect

from storage.config import UploadConfig, from_env, validate_owners
from storage.uploads import UploadError, UploadStore

PREFIX = "/api/v1/uploads"
CLIENT_DIR = Path(__file__).with_name("upload_client")


class CreateUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=5, max_length=200)
    size_bytes: int = Field(gt=0, strict=True)


class UploadLockManager:
    """Manages per-upload asyncio locks with ref-counting and safe lifecycle cleanup.

    Guarantees:
    - All concurrent requests for the same upload_id acquire the exact same lock.
    - An upload lock is only pruned from the registry when its reference count drops
      to 0 (i.e. no request is holding or waiting for the lock).
    - Even if a request fails, times out, is cancelled, or completes/cancels the upload,
      its reference is decremented in a finally block without removing the lock out from
      under other waiting or active requests.
    - Memory does not grow unbounded because when the last active/queued request finishes,
      the entry is automatically evicted.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._refs: dict[str, int] = {}

    @asynccontextmanager
    async def lock(self, upload_id: str):
        lock = self._locks.get(upload_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[upload_id] = lock
            self._refs[upload_id] = 0
        self._refs[upload_id] += 1
        try:
            async with lock:
                yield
        finally:
            new_refs = self._refs.get(upload_id, 1) - 1
            if new_refs <= 0:
                self._refs.pop(upload_id, None)
                self._locks.pop(upload_id, None)
            else:
                self._refs[upload_id] = new_refs

    def is_locked(self, upload_id: str) -> bool:
        lock = self._locks.get(upload_id)
        return lock.locked() if lock is not None else False

    def ref_count(self, upload_id: str) -> int:
        return self._refs.get(upload_id, 0)

    def has_lock(self, upload_id: str) -> bool:
        return upload_id in self._locks


def create_app(config: UploadConfig, owners: dict[str, str], *,
               store_factory=UploadStore, origins: tuple[str, ...] = (
                   "http://127.0.0.1:8011", "http://localhost:8011")) -> FastAPI:
    validate_owners(owners)
    keys = {owner: hashlib.sha256(key.encode()).digest() for owner, key in owners.items()}

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = await run_in_threadpool(store_factory, config)

        async def maintenance():
            while True:
                await asyncio.sleep(config.maintenance_seconds)
                try:
                    await run_in_threadpool(app.state.store.maintain)
                except (OSError, sqlite3.Error):
                    # Fail closed if the registry becomes unavailable; do not
                    # silently continue cleanup or serve new acknowledgements.
                    app.state.unhealthy = True
                    return
        app.state.unhealthy = False
        task = asyncio.create_task(maintenance())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await run_in_threadpool(app.state.store.close)

    app = FastAPI(title="CVAT durable upload bytes (Stage 3)", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=list(origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["X-Owner-Key", "Upload-Capability", "Upload-Offset", "X-Chunk-SHA256",
                       "Idempotency-Key", "Content-Type"])

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        try:
            local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        if (not local or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}
                or (request.headers.get("origin") and request.headers["origin"] not in origins)):
            return JSONResponse({"error": {"code": "LOCAL_ONLY"}}, status_code=403)
        return await call_next(request)

    @app.exception_handler(UploadError)
    async def upload_error(request, error):
        return JSONResponse({"error": {"code": error.code}}, status_code=error.status)

    @app.exception_handler(OSError)
    async def disk_error(request, error):
        return JSONResponse({"error": {"code": "STORAGE_IO_FAILED"}}, status_code=507)

    @app.exception_handler(sqlite3.Error)
    async def registry_error(request, error):
        app.state.unhealthy = True
        return JSONResponse({"error": {"code": "REGISTRY_UNAVAILABLE"}}, status_code=503)

    def owner(request: Request) -> str:
        if app.state.unhealthy:
            raise UploadError(503, "SERVICE_UNHEALTHY")
        offered = hashlib.sha256(request.headers.get("x-owner-key", "").encode()).digest()
        for name, digest in keys.items():
            if secrets.compare_digest(offered, digest):
                return name
        raise UploadError(403, "ACCESS_DENIED")

    def cap(request: Request) -> str:
        return request.headers.get("upload-capability", "")

    @app.get("/health")
    async def health() -> dict:
        if app.state.unhealthy:
            raise UploadError(503, "SERVICE_UNHEALTHY")
        return {"status": "ok", "stage": 3, "ready_datasets": False}

    @app.get("/client", include_in_schema=False)
    async def client() -> Response:
        return Response((CLIENT_DIR / "index.html").read_text(encoding="utf-8"), media_type="text/html",
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                 "Content-Security-Policy": "default-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"})

    @app.get("/client/uploader.js", include_in_schema=False)
    async def script() -> Response:
        return Response((CLIENT_DIR / "uploader.js").read_text(encoding="utf-8"), media_type="text/javascript",
                        headers={"Cache-Control": "no-store"})

    @app.post(PREFIX, status_code=201)
    async def create(request: Request) -> dict:
        identity = owner(request)
        body = bytearray()
        async for block in request.stream():
            if len(body) + len(block) > 4096:
                raise UploadError(413, "METADATA_TOO_LARGE")
            body.extend(block)
        try:
            metadata = CreateUpload.model_validate_json(body)
        except ValidationError:
            raise UploadError(422, "INVALID_METADATA") from None
        return await run_in_threadpool(app.state.store.create, identity, metadata.filename,
                                      metadata.size_bytes, request.headers.get("idempotency-key", ""))

    upload_locks = UploadLockManager()
    app.state.upload_locks = upload_locks

    @app.get(PREFIX + "/{upload_id}")
    async def status(upload_id: str, request: Request) -> dict:
        return await run_in_threadpool(app.state.store.status, owner(request), upload_id, cap(request))

    @app.get(PREFIX + "/{upload_id}/receipts")
    async def receipts(upload_id: str, request: Request, start: int = 0, limit: int = 128) -> dict:
        return await run_in_threadpool(app.state.store.receipts, owner(request), upload_id, cap(request), start, limit)

    @app.put(PREFIX + "/{upload_id}/chunks/{index}")
    async def chunk(upload_id: str, index: int, request: Request) -> dict:
        identity = owner(request)
        try:
            offset = int(request.headers.get("upload-offset", ""))
        except ValueError:
            raise UploadError(422, "INVALID_OFFSET") from None
        async with upload_locks.lock(upload_id):
            writer = await run_in_threadpool(app.state.store.begin, identity, upload_id, cap(request),
                                            index, offset, request.headers.get("x-chunk-sha256", ""))
            try:
                async with asyncio.timeout(config.request_seconds):
                    async for block in request.stream():
                        await run_in_threadpool(writer.write, block)
                    return await run_in_threadpool(writer.finish)
            except TimeoutError:
                raise UploadError(408, "CHUNK_TIMEOUT") from None
            except ClientDisconnect:
                raise UploadError(400, "CHUNK_INTERRUPTED") from None
            finally:
                # Shield rollback from request cancellation; storage cleanup uses
                # authoritative SQLite offset, including ambiguous COMMIT results.
                import anyio
                with anyio.CancelScope(shield=True):
                    await run_in_threadpool(writer.abort)

    @app.post(PREFIX + "/{upload_id}/complete")
    async def complete(upload_id: str, request: Request) -> dict:
        identity = owner(request)
        async with upload_locks.lock(upload_id):
            return await run_in_threadpool(app.state.store.complete, identity, upload_id, cap(request))

    @app.delete(PREFIX + "/{upload_id}")
    async def cancel(upload_id: str, request: Request) -> dict:
        identity = owner(request)
        async with upload_locks.lock(upload_id):
            return await run_in_threadpool(app.state.store.cancel, identity, upload_id, cap(request))

    return app


def create_from_env() -> FastAPI:
    config, owners = from_env()
    return create_app(config, owners)
