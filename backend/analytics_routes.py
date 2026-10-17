"""Track B verified result routes.

Implements immutable result retrieval with authorization, identity verification,
and provenance validation. Routes integrate with ResultStore for atomic storage.
"""
from fastapi import Query, Request, Response
from pydantic import ValidationError
from backend.dashboard_projection import DashboardRequest, project_dashboard
from starlette.concurrency import run_in_threadpool

from backend.analytics_contracts import (
    METADATA_LIMIT, ORIGIN, RESPONSE_LIMIT, VERSION, AnalyticsError, Descriptor,
    RevisionRegistry, encoded,
)
from backend.analytics_storage import ResultStore


def add_verified_result_routes(
    app,
    store: ResultStore,
    revisions: RevisionRegistry,
    grants,
    summary_for,
):
    """Add Track B verified result routes to the app.

    Args:
        app: FastAPI application
        store: Immutable result storage
        revisions: Configuration registry for validation
        grants: Grant store for authorization
        summary_for: Async function (request, dataset_id, *, results, result_id) -> (token, grant, summary)
    """

    def descriptor_from_row(desc: Descriptor, dataset_id: str, archive_sha256: str) -> Descriptor:
        """Validate descriptor identity matches expected dataset."""
        if desc.dataset_id != dataset_id or desc.archive_sha256 != archive_sha256:
            raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID")
        # Validate descriptor against registry (revision + version tuple support)
        return revisions.validate(desc)

    @app.get("/analytics/v1/datasets/{dataset_id}/results")
    async def verified_result_list(dataset_id: str, request: Request):
        """List authorized verified results for a dataset."""
        token, grant, summary = await summary_for(request, dataset_id, results=True)

        if not summary.ready:
            raise AnalyticsError(409, "DATASET_NOT_READY")

        # Retrieve only authorized results from immutable storage
        authorized_ids = list(grant.record.result_ids) if grant.record.result_ids is not None else []
        descriptors = await run_in_threadpool(
            store.list_results, dataset_id, authorized_result_ids=authorized_ids
        )

        # Validate each descriptor
        validated = []
        seen = set()
        for desc in descriptors:
            if desc.result_id in seen:
                raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID")
            seen.add(desc.result_id)

            validated_desc = descriptor_from_row(desc, dataset_id, summary.archive_sha256)
            validated.append(validated_desc.model_dump(exclude_none=False))

        # Re-authorize after successful retrieval
        grants.authorize(token, dataset_id, results=True)

        result = {
            "schema_version": VERSION,
            "dataset_id": dataset_id,
            "results": validated,
        }
        encoded(result, RESPONSE_LIMIT)
        return result

    @app.get("/analytics/v1/datasets/{dataset_id}/results/{result_id}")
    async def verified_result_detail(dataset_id: str, result_id: str, request: Request):
        """Retrieve one verified result with full provenance."""
        token, _, summary = await summary_for(request, dataset_id, results=True, result_id=result_id)

        if not summary.ready:
            raise AnalyticsError(409, "DATASET_NOT_READY")

        # Retrieve immutable result
        try:
            descriptor, payload = await run_in_threadpool(
                store.get_result, dataset_id, result_id
            )
        except ValueError as e:
            if "not found" in str(e):
                raise AnalyticsError(404, "RESULT_NOT_FOUND") from None
            raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID") from None

        # Validate descriptor
        validated_desc = descriptor_from_row(descriptor, dataset_id, summary.archive_sha256)

        if validated_desc.result_id != result_id:
            raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID")

        # Verify payload structure
        if not isinstance(payload, dict) or payload.get("dataset_id") != dataset_id:
            raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID")

        # Re-authorize after successful retrieval
        grants.authorize(token, dataset_id, results=True, result_id=result_id)

        # Construct verified envelope
        envelope = {
            "schema_version": VERSION,
            "descriptor": validated_desc.model_dump(exclude_none=False),
            "provenance": {
                "verification": "verified",
                "config_id": validated_desc.config_id,
                "payload_sha256": validated_desc.payload_sha256,
            },
            "payload": payload,
        }
        encoded(envelope, RESPONSE_LIMIT)
        return envelope

    @app.post("/analytics/v1/datasets/{dataset_id}/results/{result_id}/dashboard")
    async def result_dashboard(dataset_id: str, result_id: str, request: Request):
        token, _, summary = await summary_for(request, dataset_id, results=True, result_id=result_id)
        if not summary.ready:
            raise AnalyticsError(409, "DATASET_NOT_READY")
        body = bytearray()
        async for block in request.stream():
            if len(body) + len(block) > 65536:
                raise AnalyticsError(422, "INVALID_ACCESS_REQUEST")
            body.extend(block)
        try:
            selection = DashboardRequest.model_validate_json(body)
        except ValidationError:
            raise AnalyticsError(422, "INVALID_ACCESS_REQUEST") from None
        try:
            desc, payload = await run_in_threadpool(store.get_result, dataset_id, result_id)
            descriptor_from_row(desc, dataset_id, summary.archive_sha256)
            if selection.expected_payload_sha256 != desc.payload_sha256:
                raise AnalyticsError(409, "UPSTREAM_RESULT_INVALID")
            response = await run_in_threadpool(project_dashboard, selection, payload, summary.model_dump())
        except ValueError:
            raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID") from None
        grants.authorize(token, dataset_id, results=True, result_id=result_id)
        response["source"] = {"request_fingerprint": desc.request_fingerprint,
            "result_id": desc.result_id, "payload_sha256": desc.payload_sha256}
        encoded(response, RESPONSE_LIMIT)
        return response

    @app.get("/analytics/v1/datasets/{dataset_id}/preview")
    async def preview_image(
        dataset_id: str,
        request: Request,
        image_path: str = Query(min_length=1, max_length=1000),
        annotated: bool = False,
    ):
        """Stream a preview image from the upstream store with authorization and size checks."""
        token, grant, summary = await summary_for(request, dataset_id, results=True)
        if not summary.ready:
            raise AnalyticsError(409, "DATASET_NOT_READY")

        if not image_path or ".." in image_path:
            raise AnalyticsError(422, "INVALID_PATH")

        upstream_client = getattr(app.state, "upstream", None)
        if upstream_client is None:
            raise AnalyticsError(503, "BACKEND_UNAVAILABLE")

        image_bytes, media_type = await run_in_threadpool(
            upstream_client.preview,
            dataset_id,
            grant.record.upstream_capability,
            image_path,
            annotated,
            ORIGIN,
        )
        return Response(content=image_bytes, media_type=media_type)

