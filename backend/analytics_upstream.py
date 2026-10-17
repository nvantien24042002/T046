"""Fixed summary-only upstream boundary; no user-controlled URLs or proxies."""
import httpx
from pydantic import TypeAdapter

from backend.analytics_contracts import (
    AnalyticsError, ID, ORIGIN, UPSTREAM, METADATA_LIMIT, Summary, parse_json,
)


class LoopbackUpstream:
    def __init__(self, *, mock_transport=None):
        if mock_transport is not None and not isinstance(mock_transport, httpx.MockTransport):
            raise ValueError("Only MockTransport may be injected")
        self._transport = mock_transport

    def summary(self, dataset_id, capability, origin):
        if origin != ORIGIN:
            raise AnalyticsError(403, "LOCAL_ONLY")
        try:
            TypeAdapter(ID).validate_python(dataset_id)
        except ValueError:
            raise AnalyticsError(403, "ACCESS_SCOPE_DENIED") from None
        # The allowlist contains exactly this GET. No arbitrary request method/path API.
        url = httpx.URL(UPSTREAM + "/api/v1/datasets/" + dataset_id + "/summary")
        if (url.scheme, url.host, url.port) != ("http", "127.0.0.1", 8011):
            raise AnalyticsError(403, "LOCAL_ONLY")
        try:
            with httpx.Client(transport=self._transport, trust_env=False, follow_redirects=False,
                              timeout=httpx.Timeout(10, connect=3)) as client:
                with client.stream("GET", url, headers={
                    "Accept": "application/json", "Accept-Encoding": "identity",
                    "Upload-Capability": capability, "Origin": origin,
                }, follow_redirects=False) as response:
                    if 300 <= response.status_code < 400:
                        raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID")
                    raw = bytearray()
                    for block in response.iter_bytes(chunk_size=8192):
                        if len(raw) + len(block) > METADATA_LIMIT:
                            raise AnalyticsError(502, "UPSTREAM_RESPONSE_LIMIT")
                        raw.extend(block)
                    data = parse_json(raw)
                    if response.status_code != 200:
                        code = data.get("error", {}).get("code") if isinstance(data, dict) else None
                        if response.status_code == 404 and code == "DATASET_NOT_FOUND":
                            raise AnalyticsError(404, "DATASET_NOT_FOUND")
                        if response.status_code >= 500:
                            raise AnalyticsError(503, "BACKEND_UNAVAILABLE")
                        raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID")
                    summary = Summary.model_validate(data)
                    if summary.dataset_id != dataset_id:
                        raise ValueError("Dataset mismatch")
                    return summary.public()
        except httpx.RequestError:
            raise AnalyticsError(503, "BACKEND_UNAVAILABLE") from None
        except (ValueError, TypeError, AttributeError, RecursionError):
            raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID") from None

    def preview(self, dataset_id: str, capability: str, image_path: str, annotated: bool, origin: str) -> tuple[bytes, str]:
        if origin != ORIGIN:
            raise AnalyticsError(403, "LOCAL_ONLY")
        try:
            TypeAdapter(ID).validate_python(dataset_id)
        except ValueError:
            raise AnalyticsError(403, "ACCESS_SCOPE_DENIED") from None
        if not image_path or ".." in image_path or len(image_path) > 1000:
            raise AnalyticsError(422, "INVALID_PATH")
        url = httpx.URL(UPSTREAM + "/api/v1/datasets/" + dataset_id + "/preview")
        if (url.scheme, url.host, url.port) != ("http", "127.0.0.1", 8011):
            raise AnalyticsError(403, "LOCAL_ONLY")
        try:
            with httpx.Client(transport=self._transport, trust_env=False, follow_redirects=False,
                              timeout=httpx.Timeout(10, connect=3)) as client:
                with client.stream("GET", url, params={
                    "image_path": image_path,
                    "annotated": "true" if annotated else "false",
                }, headers={
                    "Accept": "*/*",
                    "Accept-Encoding": "identity",
                    "Upload-Capability": capability,
                    "Origin": origin,
                }, follow_redirects=False) as response:
                    if 300 <= response.status_code < 400:
                        raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID")
                    if response.status_code == 404:
                        raise AnalyticsError(404, "IMAGE_NOT_FOUND")
                    if response.status_code == 413:
                        raise AnalyticsError(413, "IMAGE_SIZE_LIMIT")
                    if response.status_code == 422:
                        raise AnalyticsError(422, "IMAGE_DECODE_FAILED")
                    if response.status_code >= 500:
                        raise AnalyticsError(503, "BACKEND_UNAVAILABLE")
                    if response.status_code != 200:
                        raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID")
                    media_type = response.headers.get("content-type", "image/png").split(";")[0].strip()
                    max_preview_bytes = 50 * 1024 * 1024
                    raw = bytearray()
                    for block in response.iter_bytes(chunk_size=65536):
                        if len(raw) + len(block) > max_preview_bytes:
                            raise AnalyticsError(413, "IMAGE_SIZE_LIMIT")
                        raw.extend(block)
                    return bytes(raw), media_type
        except httpx.RequestError:
            raise AnalyticsError(503, "BACKEND_UNAVAILABLE") from None

