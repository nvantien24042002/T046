"""Local-only bounded transport spike. No durable sessions or ready datasets.

Run with one worker and --no-proxy-headers. Restart loses session metadata;
received_bytes is NOT a crash-durable acknowledgement. Stage 3 replaces this.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_FILE_BYTES = 16 * 1024 * 1024  # Hard ceiling: this is only a small-file spike.
CHUNK_BYTES = 64 * 1024
PREFIX = "/prototype/v1/uploads"


@dataclass(frozen=True)
class Settings:
    storage_dir: Path
    bootstrap_key: str
    allowed_origins: tuple[str, ...] = ("http://127.0.0.1:8502", "http://localhost:8502")

    def __post_init__(self) -> None:
        if len(self.bootstrap_key) < 32:
            raise ValueError("Generate CVAT_PROTOTYPE_KEY with at least 32 random characters.")
        if not self.storage_dir.is_absolute():
            raise ValueError("Prototype storage must be an absolute private directory.")


class CreateUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=5, max_length=200)
    size_bytes: int = Field(gt=0, le=MAX_FILE_BYTES, strict=True)


@dataclass
class Session:
    path: Path
    size: int
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    received: int = 0
    state: str = "uploading"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def reject(status: int, code: str) -> None:
    raise HTTPException(status_code=status, detail={"code": code})


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="CVAT small-file transport prototype", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.allowed_origins),
        allow_methods=["POST", "PUT", "GET"],
        allow_headers=["Content-Type", "Authorization", "X-Prototype-Key", "Upload-Offset"],
    )
    sessions: dict[str, Session] = {}
    app.state.sessions = sessions
    app.state.settings = settings

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        from starlette.responses import JSONResponse

        try:
            is_local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            is_local = False
        origin = request.headers.get("origin")
        if not is_local or (origin and origin not in settings.allowed_origins):
            return JSONResponse({"detail": {"code": "LOCAL_ONLY"}}, status_code=403)
        # Avoid DNS-rebinding through an attacker-controlled hostname.
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": {"code": "LOCAL_HOST_REQUIRED"}}, status_code=403)
        return await call_next(request)

    def owned(request: Request, upload_id: str) -> Session:
        session = sessions.get(upload_id)
        if session is None:
            reject(404, "UNKNOWN_UPLOAD")
        if not secrets.compare_digest(request.headers.get("authorization", "").encode("utf-8"),
                                      ("Bearer " + session.token).encode("utf-8")):
            reject(403, "ACCESS_DENIED")
        return session

    def status(upload_id: str, session: Session) -> dict:
        return {"upload_id": upload_id, "received_bytes": session.received,
                "size_bytes": session.size, "chunk_size_bytes": CHUNK_BYTES,
                "state": session.state, "ready": False, "durable": False}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "prototype": True, "durable": False}

    @app.post(PREFIX, status_code=201)
    async def create(request: Request) -> dict:
        if not secrets.compare_digest(request.headers.get("x-prototype-key", "").encode("utf-8"),
                                      settings.bootstrap_key.encode("utf-8")):
            reject(403, "ACCESS_DENIED")
        body = bytearray()
        async for block in request.stream():
            if len(body) + len(block) > 4096:
                reject(413, "METADATA_TOO_LARGE")
            body.extend(block)
        try:
            metadata = CreateUpload.model_validate_json(body)
        except ValidationError:
            reject(422, "INVALID_METADATA")
        if (not metadata.filename.lower().endswith(".zip")
                or any(c in metadata.filename for c in "/\\:\x00")
                or any(ord(c) < 32 for c in metadata.filename)):
            reject(422, "INVALID_FILENAME")
        if any(s.state == "uploading" for s in sessions.values()):
            reject(409, "ONE_ACTIVE_UPLOAD")
        if len(sessions) >= 8:
            reject(429, "PROTOTYPE_SESSION_LIMIT")
        upload_id = str(uuid4())
        try:
            settings.storage_dir.mkdir(parents=True, exist_ok=True)
            path = settings.storage_dir / f"{upload_id}.part"
            with path.open("xb"):
                pass
        except OSError:
            reject(507, "STORAGE_UNAVAILABLE")
        session = Session(path=path, size=metadata.size_bytes)
        sessions[upload_id] = session
        return {**status(upload_id, session), "upload_token": session.token}

    @app.get(PREFIX + "/{upload_id}")
    async def get_status(upload_id: str, request: Request) -> dict:
        return status(upload_id, owned(request, upload_id))

    @app.put(PREFIX + "/{upload_id}/chunks/{index}")
    async def chunk(upload_id: str, index: int, request: Request) -> dict:
        session = owned(request, upload_id)
        async with session.lock:
            if (session.state != "uploading" or index != session.received // CHUNK_BYTES
                    or request.headers.get("upload-offset") != str(session.received)):
                reject(409, "OFFSET_OR_STATE_MISMATCH")
            expected = min(CHUNK_BYTES, session.size - session.received)
            if expected <= 0:
                reject(409, "ALREADY_RECEIVED")
            count = 0
            try:
                with session.path.open("r+b") as payload:
                    payload.seek(session.received)
                    try:
                        async for block in request.stream():
                            if count + len(block) > expected:
                                reject(413, "CHUNK_TOO_LARGE")
                            # Each HTTP chunk is already capped at 64 KiB.
                            payload.write(block)
                            count += len(block)
                        if count != expected:
                            reject(422, "CHUNK_LENGTH_MISMATCH")
                    except BaseException:
                        payload.truncate(session.received)
                        raise
            except OSError:
                session.state = "failed"
                reject(507, "WRITE_FAILED")
            session.received += count
            return status(upload_id, session)

    @app.post(PREFIX + "/{upload_id}/complete")
    async def complete(upload_id: str, request: Request) -> dict:
        session = owned(request, upload_id)
        async with session.lock:
            if session.state != "uploading" or session.received != session.size:
                reject(409, "INCOMPLETE_OR_INVALID_STATE")
            digest = hashlib.sha256()
            try:
                with session.path.open("rb") as payload:
                    while block := payload.read(CHUNK_BYTES):
                        digest.update(block)
            except OSError:
                session.state = "failed"
                reject(507, "READ_FAILED")
            session.state = "received_unvalidated"
            return {**status(upload_id, session), "sha256": digest.hexdigest()}

    return app


def create_from_env() -> FastAPI:
    """Deliberately no public bind, default secret, or default storage location."""
    return create_app(Settings(
        storage_dir=Path(os.environ["CVAT_PROTOTYPE_DIR"]),
        bootstrap_key=os.environ["CVAT_PROTOTYPE_KEY"],
    ))
