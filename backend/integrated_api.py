"""Integrated backend API combining durable uploads, dataset validation, and Stage 5 pipeline.

Local-only authenticated HTTP service for Stage 6A.
Preserves Stage 3 upload transport, coordinates Stage 4 dataset lifecycle,
and exposes Stage 5 pipeline execution on validated 'ready' datasets.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import hashlib
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator, ValidationError as PydanticValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, Response

from core.pipeline import execute_pipeline, read_single_image, get_annotated_preview_image
from backend.dashboard_projection import DashboardRequest, project_dashboard, canonical_image_path
from backend.durable_api import UploadLockManager
from core.image_quality import DEFAULT_IMAGE_CFG
from core.label_audit import DEFAULT_LABEL_CFG
from datasets.limits import Limits, ValidationError
from datasets.manager import DatasetManager
from storage.config import UploadConfig, from_env, validate_owners
from storage.uploads import UploadError, UploadStore

PREFIX = "/api/v1/uploads"
DATASET_PREFIX = "/api/v1/datasets"
CLIENT_DIR = Path(__file__).with_name("upload_client")


class CreateUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=5, max_length=200)
    size_bytes: int = Field(gt=0, strict=True)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_cfg: dict[str, Any] | None = None
    label_cfg: dict[str, Any] | None = None

    @field_validator("image_cfg", "label_cfg", mode="before")
    @classmethod
    def validate_thresholds(cls, value, info):
        if value is None:
            return value
        defaults = DEFAULT_IMAGE_CFG if info.field_name == "image_cfg" else DEFAULT_LABEL_CFG
        if not isinstance(value, dict) or value.keys() - defaults.keys():
            raise ValueError("Invalid analysis configuration")
        for key, number in value.items():
            try:
                valid = type(number) in (int, float) and math.isfinite(number) and number >= 0
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError("Expected finite nonnegative number")
            if key in {"min_width", "min_height"} and (type(number) is not int or number < 1):
                raise ValueError("Expected positive integer dimension")
            if key in {"dark_threshold", "bright_threshold", "black_threshold", "white_threshold"} and number > 255:
                raise ValueError("Intensity outside byte range")
            if key in {"duplicate_iou", "min_edge", "min_boundary"} and number > 1:
                raise ValueError("Fraction outside unit interval")
            if key in {"min_aspect", "max_aspect"} and number <= 0:
                raise ValueError("Aspect ratio must be positive")
            if (key == "min_area_pct" and number > 100) or (key == "max_area_pct" and number > 10000):
                raise ValueError("Area threshold outside supported range")
        effective = {**defaults, **value}
        pairs = (("dark_threshold", "bright_threshold"), ("black_threshold", "white_threshold")) if info.field_name == "image_cfg" else (("min_area_pct", "max_area_pct"), ("min_aspect", "max_aspect"))
        if any(effective[low] > effective[high] for low, high in pairs):
            raise ValueError("Threshold bounds are reversed")
        return value


DEFAULT_ORIGINS = (
    "http://127.0.0.1:8011",
    "http://localhost:8011",
    "http://127.0.0.1:8501",
    "http://localhost:8501",
    "http://127.0.0.1:8502",
    "http://localhost:8502",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
)


def make_dataset_summary(dataset_id: str, upload_id: str, manifest: dict) -> dict:
    capabilities = manifest.get("capabilities")
    if capabilities is None:
        has_images = bool(manifest.get("images", 0) > 0)
        has_annotations = bool(manifest.get("annotations", False))
        capabilities = {
            "image_quality": has_images,
            "label_audit": has_annotations,
            "distribution": has_annotations,
            "coverage": has_annotations,
            "scene_finder": has_annotations,
        }
    return {
        "dataset_id": dataset_id,
        "upload_id": upload_id,
        "state": "ready",
        "ready": True,
        "archive_bytes": manifest.get("archive_bytes"),
        "archive_sha256": manifest.get("archive_sha256"),
        "entries": manifest.get("entries"),
        "images": manifest.get("images"),
        "annotations": manifest.get("annotations"),
        "xml_records": manifest.get("xml_records", {}),
        "capabilities": capabilities,
    }


def create_integrated_app(
    config: UploadConfig,
    owners: dict[str, str],
    *,
    store_factory=UploadStore,
    limits: Limits | None = None,
    manager_factory=DatasetManager,
    origins: tuple[str, ...] | None = None,
    browser_sessions=None,
    react_dist: str | Path | None = None,
) -> FastAPI:
    validate_owners(owners)
    keys = {owner: hashlib.sha256(key.encode()).digest() for owner, key in owners.items()}
    analysis_lock = asyncio.Lock()
    validation_limits = limits or Limits()

    if origins is None:
        configured = os.environ.get("CVAT_ALLOWED_ORIGINS", "")
        extra_origins = tuple(o.strip() for o in configured.split(",") if o.strip())
        origins = DEFAULT_ORIGINS + extra_origins

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = await run_in_threadpool(store_factory, config)

        async def maintenance():
            while True:
                await asyncio.sleep(config.maintenance_seconds)
                try:
                    await run_in_threadpool(app.state.store.maintain)
                except (OSError, sqlite3.Error):
                    app.state.unhealthy = True
                    return

        task = None
        try:
            app.state.manager = await run_in_threadpool(manager_factory, app.state.store, validation_limits)
            app.state.cache_dir = app.state.store.root / ".analysis-cache-v1"
            app.state.cache_dir.mkdir(exist_ok=True)
            app.state.unhealthy = False
            worker = maintenance()
            try:
                task = asyncio.create_task(worker)
            except BaseException:
                worker.close()
                raise
            yield
        finally:
            try:
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
            finally:
                await run_in_threadpool(app.state.store.close)

    app = FastAPI(title="CVAT Integrated Backend (Stage 6A)", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "X-Owner-Key",
            "Upload-Capability",
            "Upload-Offset",
            "X-Chunk-SHA256",
            "Idempotency-Key",
            "Content-Type",
            "Authorization",
            "X-Prototype-Key",
        ],
    )

    enforce_local = os.environ.get("CVAT_ENFORCE_LOCAL", "1").lower() not in ("0", "false", "no")

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if not enforce_local:
            if request.headers.get("origin") and request.headers["origin"] not in origins:
                return JSONResponse({"error": {"code": "ORIGIN_DENIED"}}, status_code=403)
            return await call_next(request)
        try:
            local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        if not local or request.url.hostname not in {"127.0.0.1", "localhost", "::1"} or (
            request.headers.get("origin") and request.headers["origin"] not in origins
        ):
            return JSONResponse({"error": {"code": "LOCAL_ONLY"}}, status_code=403)
        return await call_next(request)

    @app.exception_handler(UploadError)
    async def upload_error(request: Request, error: UploadError):
        return JSONResponse({"error": {"code": error.code}}, status_code=error.status)

    @app.exception_handler(ValidationError)
    async def validation_error(request: Request, error: ValidationError):
        return JSONResponse({"error": {"code": str(error)}}, status_code=422)

    @app.exception_handler(OSError)
    async def disk_error(request: Request, error: OSError):
        return JSONResponse({"error": {"code": "STORAGE_IO_FAILED"}}, status_code=507)

    @app.exception_handler(sqlite3.Error)
    async def registry_error(request: Request, error: sqlite3.Error):
        if (
            isinstance(error, sqlite3.IntegrityError)
            and getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_TRIGGER
            and str(error) in {"DATASET_PAYLOAD_PINNED", "VALIDATION_WORKSPACE_RESERVED"}
        ):
            return JSONResponse({"error": {"code": str(error)}}, status_code=409)
        app.state.unhealthy = True
        return JSONResponse({"error": {"code": "REGISTRY_UNAVAILABLE"}}, status_code=503)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, error: Exception):
        return JSONResponse({"error": {"code": "INTERNAL_SERVER_ERROR"}}, status_code=500)

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

    def session_owner(upload_id: str, request: Request) -> str:
        """Resolve owner identity from X-Owner-Key or session Upload-Capability."""
        if app.state.unhealthy:
            raise UploadError(503, "SERVICE_UNHEALTHY")
        key = request.headers.get("x-owner-key", "")
        if key:
            return owner(request)
        capability = cap(request)
        if capability:
            row = app.state.store.db.execute(
                "SELECT owner_id, capability_hash FROM uploads WHERE id=?", (upload_id,)
            ).fetchone()
            if row and secrets.compare_digest(app.state.store._hash(capability), row["capability_hash"]):
                return row["owner_id"]
        raise UploadError(403, "ACCESS_DENIED")

    def resolve_dataset(dataset_id: str) -> tuple[str, str]:
        """Resolve dataset_id to upload_id; returns (upload_id, dataset_id)."""
        row = app.state.store.db.execute(
            "SELECT upload_id FROM validation_datasets WHERE dataset_id=?",
            (dataset_id,)
        ).fetchone()
        if not row:
            raise UploadError(404, "DATASET_NOT_FOUND")
        return row["upload_id"], dataset_id

    # ---------------------------------------------------------
    # Health and Client Routes
    # ---------------------------------------------------------

    @app.get("/health")
    async def health() -> dict:
        if app.state.unhealthy:
            raise UploadError(503, "SERVICE_UNHEALTHY")
        return {"status": "ok", "stage": "6A", "ready_datasets": True}

    @app.get("/client", include_in_schema=False)
    async def client() -> Response:
        if react_dist is not None:
            return Response(status_code=404)
        return Response(
            (CLIENT_DIR / "index.html").read_text(encoding="utf-8"),
            media_type="text/html",
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'",
            },
        )

    @app.get("/client/uploader.js", include_in_schema=False)
    async def script() -> Response:
        if react_dist is not None:
            return Response(status_code=404)
        return Response(
            (CLIENT_DIR / "uploader.js").read_text(encoding="utf-8"),
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    # ---------------------------------------------------------
    # Upload Routes (Stage 3 Protocol)
    # ---------------------------------------------------------

    @app.post(PREFIX, status_code=201)
    async def create_upload(request: Request) -> dict:
        identity = owner(request)
        body = bytearray()
        async for block in request.stream():
            if len(body) + len(block) > 4096:
                raise UploadError(413, "METADATA_TOO_LARGE")
            body.extend(block)
        try:
            metadata = CreateUpload.model_validate_json(body)
        except PydanticValidationError:
            raise UploadError(422, "INVALID_METADATA") from None
        return await run_in_threadpool(
            app.state.store.create,
            identity,
            metadata.filename,
            metadata.size_bytes,
            request.headers.get("idempotency-key", ""),
        )

    upload_locks = UploadLockManager()
    app.state.upload_locks = upload_locks

    @app.get(PREFIX + "/{upload_id}")
    async def upload_status(upload_id: str, request: Request) -> dict:
        identity = session_owner(upload_id, request)
        return await run_in_threadpool(app.state.store.status, identity, upload_id, cap(request))

    @app.get(PREFIX + "/{upload_id}/receipts")
    async def receipts(upload_id: str, request: Request, start: int = 0, limit: int = 128) -> dict:
        identity = session_owner(upload_id, request)
        return await run_in_threadpool(app.state.store.receipts, identity, upload_id, cap(request), start, limit)

    @app.put(PREFIX + "/{upload_id}/chunks/{index}")
    async def chunk(upload_id: str, index: int, request: Request) -> dict:
        identity = session_owner(upload_id, request)
        try:
            offset = int(request.headers.get("upload-offset", ""))
        except ValueError:
            raise UploadError(422, "INVALID_OFFSET") from None
        async with upload_locks.lock(upload_id):
            writer = await run_in_threadpool(
                app.state.store.begin,
                identity,
                upload_id,
                cap(request),
                index,
                offset,
                request.headers.get("x-chunk-sha256", ""),
            )
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
                import anyio
                with anyio.CancelScope(shield=True):
                    await run_in_threadpool(writer.abort)

    @app.post(PREFIX + "/{upload_id}/complete")
    async def complete_upload(upload_id: str, request: Request) -> dict:
        identity = session_owner(upload_id, request)
        async with upload_locks.lock(upload_id):
            return await run_in_threadpool(app.state.store.complete, identity, upload_id, cap(request))

    @app.delete(PREFIX + "/{upload_id}")
    async def cancel_upload(upload_id: str, request: Request) -> dict:
        identity = session_owner(upload_id, request)
        async with upload_locks.lock(upload_id):
            return await run_in_threadpool(app.state.store.cancel, identity, upload_id, cap(request))

    # ---------------------------------------------------------
    # Dataset Lifecycle Routes (Stage 4 Coordination)
    # ---------------------------------------------------------

    @app.post(PREFIX + "/{upload_id}/validate")
    async def start_validation(upload_id: str, request: Request, retry: bool = False) -> dict:
        identity = session_owner(upload_id, request)
        capability = cap(request)
        # Manager serializes admission/publication with uploads.guard, releasing
        # it while the worker runs. Do not block cancellation on the API lock.
        result = await run_in_threadpool(
            app.state.manager.validate,
            identity,
            upload_id,
            capability,
            retry=retry,
        )
        # Preserve synchronous POST semantics for duplicate callers without
        # holding a mutation lock or starting another attempt after cancellation.
        while result["state"] in {"validating", "indexing", "publishing"}:
            await asyncio.sleep(0.05)
            result = await run_in_threadpool(app.state.manager.status, identity, upload_id, capability)
        return result

    @app.get(PREFIX + "/{upload_id}/dataset")
    async def get_dataset_lifecycle(upload_id: str, request: Request) -> dict:
        identity = session_owner(upload_id, request)
        capability = cap(request)
        return await run_in_threadpool(app.state.manager.status, identity, upload_id, capability)

    @app.post(PREFIX + "/{upload_id}/dataset/cancel")
    async def cancel_dataset_validation(upload_id: str, request: Request) -> dict:
        identity = session_owner(upload_id, request)
        capability = cap(request)
        # Only persist intent under the manager's guard. Unrelated long-running
        # mutations must not prevent the runner from observing cancellation.
        return await run_in_threadpool(app.state.manager.cancel, identity, upload_id, capability)

    @app.delete(PREFIX + "/{upload_id}/dataset/attempts/{attempt_id}")
    async def cleanup_attempt(upload_id: str, attempt_id: str, request: Request) -> dict:
        identity = session_owner(upload_id, request)
        capability = cap(request)
        cleaned = await run_in_threadpool(
            app.state.manager.cleanup_attempt,
            identity,
            upload_id,
            capability,
            attempt_id,
        )
        return {"cleaned": cleaned}

    # ---------------------------------------------------------
    # Dataset Query and Pipeline Routes (Stage 5 Integration)
    # ---------------------------------------------------------

    @app.get(DATASET_PREFIX + "/{dataset_id}/summary")
    async def dataset_summary(dataset_id: str, request: Request) -> dict:
        upload_id, _ = resolve_dataset(dataset_id)
        identity = session_owner(upload_id, request)
        capability = cap(request)

        st = await run_in_threadpool(app.state.manager.status, identity, upload_id, capability)
        if st["state"] != "ready":
            return {
                "dataset_id": dataset_id,
                "upload_id": upload_id,
                "state": st["state"],
                "ready": False,
                "error_code": st.get("error_code"),
            }

        def get_summary():
            with app.state.manager.reader(identity, upload_id, capability) as reader:
                return make_dataset_summary(dataset_id, upload_id, reader["summary"])

        return await run_in_threadpool(get_summary)

    @app.post(DATASET_PREFIX + "/{dataset_id}/analyze")
    async def analyze_dataset(dataset_id: str, request: Request) -> dict:
        """Explicit analysis endpoint executing Stage 5 QA/QC pipeline."""
        upload_id, _ = resolve_dataset(dataset_id)
        identity = session_owner(upload_id, request)
        capability = cap(request)

        st = await run_in_threadpool(app.state.manager.status, identity, upload_id, capability)
        if st["state"] != "ready":
            raise UploadError(409, "DATASET_NOT_READY")

        body = bytearray()
        async for block in request.stream():
            if len(body) + len(block) > 8192:
                raise UploadError(413, "REQUEST_TOO_LARGE")
            body.extend(block)

        try:
            req_config = AnalyzeRequest.model_validate_json(body) if body else AnalyzeRequest()
        except PydanticValidationError:
            raise UploadError(422, "INVALID_ANALYSIS_REQUEST") from None

        request_fingerprint = hashlib.sha256(json.dumps(
            req_config.model_dump(), sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()

        def do_analysis():
            # analysis_lock serializes both the cache check and pipeline execution.
            # Identical concurrent requests and lost-response retries reuse results.
            cache_file = app.state.cache_dir / f"{dataset_id}.json"
            request_cache = app.state.cache_dir / f"{dataset_id}.{request_fingerprint}.json"
            for candidate in (request_cache, cache_file):
                if candidate.is_file():
                    try:
                        cached = json.loads(candidate.read_text(encoding="utf-8"))
                        if cached.get("request_fingerprint") == request_fingerprint:
                            cache_file.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
                            return cached
                    except (OSError, ValueError):
                        pass
            with app.state.manager.reader(identity, upload_id, capability) as reader:
                try:
                    results = execute_pipeline(
                        reader,
                        image_cfg=req_config.image_cfg,
                        label_cfg=req_config.label_cfg,
                    )
                except UploadError:
                    raise
                except Exception as err:
                    raise UploadError(500, "PIPELINE_EXECUTION_FAILED") from err
                cache_file = app.state.cache_dir / f"{dataset_id}.json"

                # Prepare JSON serializable bundle
                def to_records(df):
                    return json.loads(df.to_json(orient="records", double_precision=10))

                bundle = {
                    "dataset_id": dataset_id,
                    "request_fingerprint": request_fingerprint,
                    "effective_config": {"image_cfg": {**DEFAULT_IMAGE_CFG, **(req_config.image_cfg or {})}, "label_cfg": {**DEFAULT_LABEL_CFG, **(req_config.label_cfg or {})}},
                    "quality_rows": to_records(results["quality"]),
                    "audit_rows": to_records(results["audit"]),
                    "classes": to_records(results["classes"]),
                    "attribute_distribution": to_records(results["attribute_distribution"]),
                    "distribution_images": to_records(results["distribution_images"]),
                    "distribution_boxes": to_records(results["distribution_boxes"]),
                    "attributes": results["attributes"],
                }
                encoded = json.dumps(bundle, ensure_ascii=False)
                pending = request_cache.with_suffix(".tmp")
                pending.write_text(encoded, encoding="utf-8")
                pending.replace(request_cache)
                cache_file.write_text(encoded, encoding="utf-8")
                return bundle

        async with analysis_lock:
            return await run_in_threadpool(do_analysis)

    @app.get(DATASET_PREFIX + "/{dataset_id}/results")
    async def get_analysis_results(dataset_id: str, request: Request) -> dict:
        """Retrieve pre-computed Stage 5 analysis results.
        
        Never triggers implicit full processing on GET.
        """
        upload_id, _ = resolve_dataset(dataset_id)
        identity = session_owner(upload_id, request)
        capability = cap(request)

        st = await run_in_threadpool(app.state.manager.status, identity, upload_id, capability)
        if st["state"] != "ready":
            raise UploadError(409, "DATASET_NOT_READY")

        def load_cached():
            cache_file = app.state.cache_dir / f"{dataset_id}.json"
            if not cache_file.exists():
                raise UploadError(409, "ANALYSIS_NOT_RUN")
            return json.loads(cache_file.read_text(encoding="utf-8"))

        async with analysis_lock:
            return await run_in_threadpool(load_cached)

    @app.post(DATASET_PREFIX + "/{dataset_id}/dashboard")
    async def dashboard(dataset_id: str, request: Request):
        upload_id, _ = resolve_dataset(dataset_id)
        identity = session_owner(upload_id, request)
        capability = cap(request)
        body = bytearray()
        async for block in request.stream():
            if len(body) + len(block) > 65536:
                raise UploadError(413, "REQUEST_TOO_LARGE")
            body.extend(block)
        try:
            selection = DashboardRequest.model_validate_json(body)
        except PydanticValidationError:
            raise UploadError(422, "INVALID_DASHBOARD_REQUEST") from None
        if not selection.expected_fingerprint:
            raise UploadError(422, "ANALYSIS_IDENTITY_REQUIRED")

        def project():
            with app.state.manager.reader(identity, upload_id, capability) as reader:
                cache = app.state.cache_dir / f"{dataset_id}.json"
                if not cache.is_file():
                    raise UploadError(409, "ANALYSIS_NOT_RUN")
                payload = json.loads(cache.read_text(encoding="utf-8"))
                if payload.get("request_fingerprint") != selection.expected_fingerprint:
                    raise UploadError(409, "RESULT_CHANGED")
                summary = make_dataset_summary(dataset_id, upload_id, reader["summary"])
                try:
                    response = project_dashboard(selection, payload, summary)
                except ValueError:
                    raise UploadError(502, "INVALID_ANALYSIS_RESULT") from None
                response["source"] = {"request_fingerprint": selection.expected_fingerprint,
                    "result_id": None, "payload_sha256": None}
                return JSONResponse(response, headers={"Cache-Control": "no-store"})
        async with analysis_lock:
            return await run_in_threadpool(project)

    @app.get(DATASET_PREFIX + "/{dataset_id}/preview")
    async def preview_image(
        dataset_id: str,
        request: Request,
        image_path: str = Query(min_length=1, max_length=1000),
        annotated: bool = False,
    ) -> Response:
        """Stream a single image preview with size guard and optional annotation overlay."""
        upload_id, _ = resolve_dataset(dataset_id)
        identity = session_owner(upload_id, request)
        capability = cap(request)

        st = await run_in_threadpool(app.state.manager.status, identity, upload_id, capability)
        if st["state"] != "ready":
            raise UploadError(409, "DATASET_NOT_READY")

        def fetch_preview():
            with app.state.manager.reader(identity, upload_id, capability) as reader:
                paths = [r[0] for r in reader["index"].execute("SELECT name FROM entries WHERE kind='image'").fetchall()]
                canonical_path = canonical_image_path(image_path, paths)
                if canonical_path is None:
                    raise UploadError(404, "IMAGE_NOT_FOUND")
                try:
                    raw = read_single_image(reader["archive"], canonical_path)
                except KeyError:
                    raise UploadError(404, "IMAGE_NOT_FOUND") from None
                except ValueError:
                    raise UploadError(413, "IMAGE_SIZE_LIMIT") from None

                if not annotated:
                    return raw, ("image/jpeg" if raw.startswith(b"\xff\xd8") else "image/png" if raw.startswith(b"\x89PNG") else "image/webp" if raw[:4] == b"RIFF" else "image/bmp")

                # Draw annotations using indexed boxes for this image
                arr = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
                if arr is None:
                    raise UploadError(422, "IMAGE_DECODE_FAILED")

                # Retrieve boxes from index for this image path
                image_rows = reader["index"].execute(
                    "SELECT id, attributes FROM xml_nodes WHERE tag='image'"
                ).fetchall()
                target_node_id = None
                for node_id, attrs_json in image_rows:
                    attrs = json.loads(attrs_json)
                    name = attrs.get("name", "")
                    if canonical_image_path(name, paths) == canonical_path:
                        target_node_id = node_id
                        break

                if target_node_id is not None:
                    box_rows = reader["index"].execute(
                        "SELECT attributes FROM xml_nodes WHERE parent=? AND tag='box'",
                        (target_node_id,)
                    ).fetchall()
                    boxes = [json.loads(r[0]) for r in box_rows]
                    arr = get_annotated_preview_image(reader, canonical_path, boxes)

                ok, encoded = cv2.imencode(".png", arr)
                if not ok:
                    raise UploadError(500, "ENCODING_FAILED")
                return encoded.tobytes(), "image/png"

        image_bytes, media_type = await run_in_threadpool(fetch_preview)
        return Response(content=image_bytes, media_type=media_type)

    from backend.browser_api import add_browser_routes
    add_browser_routes(app, origins, {
        'status': upload_status, 'receipts': receipts, 'chunk': chunk,
        'complete': complete_upload, 'cancel': cancel_upload,
        'validate': start_validation, 'lifecycle': get_dataset_lifecycle,
        'cancel_validation': cancel_dataset_validation, 'summary': dataset_summary,
        'analyze': analyze_dataset, 'results': get_analysis_results,
        'dashboard': dashboard, 'preview': preview_image,
    }, CreateUpload, sessions=browser_sessions, react_dist=react_dist)
    return app


def create_integrated_from_env() -> FastAPI:
    config, owners = from_env()
    return create_integrated_app(config, owners, react_dist=os.environ.get('CVAT_REACT_DIST'))
