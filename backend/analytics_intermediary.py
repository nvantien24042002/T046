"""Additive Track A factory. No service startup or production issuer/provider wiring."""
from datetime import datetime, timezone
import ipaddress
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from backend.analytics_contracts import (
    AnalyticsError, AUDIENCE, ORIGIN, VERSION, Descriptor, ErrorEnvelope,
    ExchangeRequest, GrantResponse, METADATA_LIMIT, RESPONSE_LIMIT, RevisionRegistry, Summary,
    encoded, parse_json,
)
from backend.analytics_grants import GrantStore, TrustedIssuer
from backend.analytics_upstream import LoopbackUpstream


class SyntheticResults(Protocol):
    """Test orchestration only. Not a storage or integrity-verification provider."""
    def list_results(self, dataset_id: str) -> list[dict]: ...
    def read_result(self, dataset_id: str, result_id: str) -> dict | None: ...


def utc(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_analytics_app(*, issuer: TrustedIssuer, grants: GrantStore | None = None,
                         upstream=None, store=None, revisions: RevisionRegistry | None = None):
    """Normal factory: supports Track B verified routes when store is provided."""
    return _create(issuer, grants, upstream, None, revisions or RevisionRegistry(), store)


def create_synthetic_analytics_app(*, issuer: TrustedIssuer, grants: GrantStore,
                                  upstream, provider: SyntheticResults,
                                  revisions: RevisionRegistry):
    """Explicit test-only composition; never a deployment entry point."""
    return _create(issuer, grants, upstream, provider, revisions)


def _create(issuer, grants, upstream, provider, revisions, store=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    grants = grants if grants is not None else GrantStore()
    upstream = upstream if upstream is not None else LoopbackUpstream()
    app.state.grants = grants
    app.state.issuer = issuer
    app.state.upstream = upstream
    app.state.synthetic_results_only = provider is not None
    app.add_middleware(CORSMiddleware, allow_origins=[ORIGIN], allow_methods=["GET", "POST"],
                       allow_headers=["Authorization", "Content-Type"], allow_credentials=False)

    def failure(error):
        body = ErrorEnvelope.model_validate({"error": {"code": error.code}}).model_dump()
        return JSONResponse(body, status_code=error.status, headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def local_only(request, call_next):
        try:
            local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        if (not local or request.headers.getlist("origin") != [ORIGIN]
                or request.headers.getlist("host") != [AUDIENCE.removeprefix("http://")]
                or request.url.scheme != "http"):
            return failure(AnalyticsError(403, "LOCAL_ONLY"))
        try:
            response = await call_next(request)
        except Exception:
            # Do not serialize, log or chain upstream bodies/credentials.
            response = failure(AnalyticsError(500, "INTERNAL_SERVER_ERROR"))
            response.headers["Access-Control-Allow-Origin"] = ORIGIN
            response.headers["Vary"] = "Origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AnalyticsError)
    async def analytics_error(request, error):
        return failure(error)

    def authorized(request, dataset_id, *, results=False, result_id=None):
        headers = request.headers.getlist("authorization")
        if len(headers) != 1 or not headers[0].startswith("Bearer "):
            raise AnalyticsError(401, "ACCESS_GRANT_INVALID")
        token = headers[0][7:]
        if not token or len(token) > 512:
            raise AnalyticsError(401, "ACCESS_GRANT_INVALID")
        return token, grants.authorize(token, dataset_id, results=results, result_id=result_id)

    async def summary_for(request, dataset_id, *, results=False, result_id=None):
        token, grant = authorized(request, dataset_id, results=results, result_id=result_id)
        try:
            value = await run_in_threadpool(upstream.summary, dataset_id,
                                           grant.record.upstream_capability, ORIGIN)
            encoded(value, METADATA_LIMIT)
            summary = Summary.model_validate(value)
            if summary.dataset_id != dataset_id:
                raise ValueError("Wrong dataset")
        except (ValueError, TypeError):
            raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID") from None
        grants.authorize(token, dataset_id, results=results, result_id=result_id)
        return token, grant, summary

    @app.post("/analytics/v1/grants/exchange")
    async def exchange(request: Request):
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise AnalyticsError(422, "INVALID_ACCESS_REQUEST")
        raw = bytearray()
        async for block in request.stream():
            if len(raw) + len(block) > 8192:
                raise AnalyticsError(422, "INVALID_ACCESS_REQUEST")
            raw.extend(block)
        try:
            body = ExchangeRequest.model_validate(parse_json(raw))
        except (ValueError, TypeError, RecursionError):
            raise AnalyticsError(422, "INVALID_ACCESS_REQUEST") from None
        token, grant, stamp = await run_in_threadpool(grants.exchange, body.exchange_code, issuer)
        response = GrantResponse.model_validate({"schema_version": VERSION, "access_token": token, "token_type": "Bearer",
                "server_time": utc(stamp), "expires_at": utc(grant.expires_at),
                "dataset_id": grant.record.dataset_id, "result_ids": sorted(grant.record.result_ids),
                "scopes": sorted(grant.record.scopes)})
        return response.model_dump()

    @app.get("/analytics/v1/datasets/{dataset_id}/summary")
    async def summary(dataset_id: str, request: Request):
        _, _, value = await summary_for(request, dataset_id)
        return value.public()

    if provider is not None:
        # Only reachable via the explicitly synthetic factory, never normal construction.
        def descriptor(value, dataset_id, summary):
            encoded(value, METADATA_LIMIT)
            try:
                item = revisions.validate(Descriptor.model_validate(value))
                if item.dataset_id != dataset_id or item.archive_sha256 != summary.archive_sha256:
                    raise ValueError("Descriptor identity mismatch")
                return item
            except (ValueError, TypeError):
                raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID") from None

        @app.get("/analytics/v1/datasets/{dataset_id}/results")
        async def result_list(dataset_id: str, request: Request):
            token, grant, summary = await summary_for(request, dataset_id, results=True)
            if not summary.ready:
                raise AnalyticsError(409, "DATASET_NOT_READY")
            values = await run_in_threadpool(provider.list_results, dataset_id)
            encoded(values, RESPONSE_LIMIT)
            if not isinstance(values, list):
                raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID")
            results, seen = [], set()
            for value in values:
                # Filter before validation: an out-of-scope record discloses nothing.
                if not isinstance(value, dict):
                    raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID")
                if value.get("result_id") not in grant.record.result_ids:
                    continue
                item = descriptor(value, dataset_id, summary)
                if item.result_id in seen:
                    raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID")
                seen.add(item.result_id)
                results.append(item.model_dump(exclude_none=False))
            grants.authorize(token, dataset_id, results=True)
            result = {"schema_version": VERSION, "dataset_id": dataset_id, "results": results}
            encoded(result, RESPONSE_LIMIT)
            return result

        @app.get("/analytics/v1/datasets/{dataset_id}/results/{result_id}")
        async def result_detail(dataset_id: str, result_id: str, request: Request):
            token, _, summary = await summary_for(request, dataset_id, results=True, result_id=result_id)
            if not summary.ready:
                raise AnalyticsError(409, "DATASET_NOT_READY")
            value = await run_in_threadpool(provider.read_result, dataset_id, result_id)
            if value is None:
                raise AnalyticsError(404, "RESULT_NOT_FOUND")
            encoded(value, RESPONSE_LIMIT)
            try:
                if not isinstance(value, dict) or value.get("schema_version") != VERSION:
                    raise ValueError("Invalid envelope")
                encoded({"descriptor": value["descriptor"], "provenance": value["provenance"]}, METADATA_LIMIT)
                item = descriptor(value["descriptor"], dataset_id, summary)
                if item.result_id != result_id:
                    raise ValueError("Wrong result")
                # Only compare metadata. No Track B hashing or provenance verification.
                listed = await run_in_threadpool(provider.list_results, dataset_id)
                encoded(listed, RESPONSE_LIMIT)
                matches = [entry for entry in listed if entry.get("result_id") == result_id]
                if len(matches) != 1 or descriptor(matches[0], dataset_id, summary) != item:
                    raise ValueError("Descriptor changed")
                from backend.analytics_result_contracts import SyntheticEnvelope
                envelope = SyntheticEnvelope.model_validate(value)
                revisions.validate_submitted(item, envelope.provenance.submitted_config)
                if envelope.payload.dataset_id != dataset_id:
                    raise ValueError("Payload mismatch")
                grants.authorize(token, dataset_id, results=True, result_id=result_id)
                return envelope.model_dump(exclude_none=False, by_alias=True)
            except (ValueError, KeyError, TypeError, AttributeError):
                raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID") from None

    # Track B verified result routes (when ResultStore is provided)
    if store is not None:
        from backend.analytics_routes import add_verified_result_routes
        add_verified_result_routes(app, store, revisions, grants, summary_for)

    return app
