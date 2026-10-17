"""Frontend adapter for communicating with the Stage 6A FastAPI backend.

Strictly bounded-memory, authenticated HTTP communication over loopback.
Never leaks local filesystem paths, secrets, or exception tracebacks.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import time
from typing import Callable, Generator, BinaryIO, Any

import httpx
from ui.backend_responses import validate_response

logger = logging.getLogger(__name__)

# Default loopback configuration
DEFAULT_BACKEND_URL = os.environ.get("CVAT_BACKEND_URL", "http://127.0.0.1:8011")
DEFAULT_OWNER_KEY = os.environ.get("CVAT_OWNER_KEY", "")

CHUNK_SIZE = 64 * 1024  # 64 KiB bounded stream chunk


class BackendError(Exception):
    """Safe user-facing exception from backend communication."""
    def __init__(self, code: str, message: str, status_code: int = 500):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def sanitize_message(text: str) -> str:
    """Strip any drive letters, paths, or secret tokens from error messages."""
    if not text:
        return ""
    # Strip Windows drive paths like D:\foo\bar or C:/foo/bar
    text = re.sub(r"[A-Za-z]:[\\/][^\s'\"]+", "[REDACTED_PATH]", text)
    # Strip UNIX style paths like /home/user/...
    text = re.sub(r"/(?:home|tmp|var|usr)/[^\s'\"]+", "[REDACTED_PATH]", text)
    # Strip long hex/base64 tokens/keys (32+ chars)
    text = re.sub(r"\b[A-Za-z0-9_-]{32,}\b", "[REDACTED_KEY]", text)
    return text


def upload_source_key(filename: str, size_bytes: int, sha256: str) -> str:
    """Stable, owner-scoped by the API, content-bound idempotency key."""
    return hashlib.sha256(json.dumps(
        [os.path.basename(filename), size_bytes, sha256], ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


class BackendClient:
    """HTTP Client communicating with the Stage 6A backend over loopback."""

    def __init__(
        self,
        base_url: str = DEFAULT_BACKEND_URL,
        owner_key: str = DEFAULT_OWNER_KEY,
        timeout: float = 30.0,
        http_client: Any | None = None,
    ):
        self.base_url = self._validated_base_url(base_url)
        self.owner_key = owner_key
        self.timeout = timeout
        self._shared_client = http_client

    @staticmethod
    def _validated_base_url(value: str) -> str:
        try:
            url = httpx.URL(value)
            allowed = (
                not any(ord(char) <= 32 or ord(char) == 127 for char in value)
                and url.scheme in {"http", "https"}
                and url.host in {"127.0.0.1", "localhost", "::1"}
                and (url.port is None or 1 <= url.port <= 65535)
                and not url.userinfo and not url.query and not url.fragment
            )
        except (httpx.InvalidURL, TypeError, ValueError):
            allowed = False
        if not allowed:
            raise BackendError("INVALID_BACKEND_URL", "Địa chỉ backend phải là địa chỉ loopback hợp lệ.") from None
        return str(url).rstrip("/")

    def _client(self, timeout: float | None = None):
        adapter = self
        shared = self._shared_client is not None
        client = self._shared_client if shared else httpx.Client(
            timeout=timeout or self.timeout, trust_env=False, follow_redirects=False,
        )

        class GuardedClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                if not shared:
                    client.close()

            def _request(self, method, value, **kwargs):
                base = httpx.URL(adapter._validated_base_url(adapter.base_url))
                url = httpx.URL(adapter._validated_base_url(str(value)))
                if (url.scheme, url.host, url.port) != (base.scheme, base.host, base.port):
                    raise BackendError("INVALID_BACKEND_URL", "Địa chỉ request không khớp backend đã cấu hình.")
                # Injected clients are for in-process transports only. Their network
                # transports may have explicit proxies which trust_env cannot override.
                if shared and (
                    not isinstance(client, httpx.Client)
                    or isinstance(client._transport_for_url(url), httpx.HTTPTransport)
                ):
                    raise BackendError("UNSAFE_BACKEND_CLIENT", "Client backend phải sử dụng transport kiểm thử nội bộ.")
                kwargs["follow_redirects"] = False
                response = getattr(client, method)(url, **kwargs)
                if 300 <= response.status_code < 400:
                    raise BackendError(
                        "BACKEND_REDIRECT_BLOCKED", "Backend trả về chuyển hướng không được phép.",
                        status_code=response.status_code,
                    )
                return response

            def get(self, url, **kwargs):
                return self._request("get", url, **kwargs)

            def post(self, url, **kwargs):
                return self._request("post", url, **kwargs)

            def put(self, url, **kwargs):
                return self._request("put", url, **kwargs)

        return GuardedClient()

    def _headers(self, capability: str | None = None, extra: dict | None = None) -> dict:
        self._validated_base_url(self.base_url)
        h = {
            "Accept": "application/json",
            "X-Owner-Key": self.owner_key,
        }
        if capability:
            h["Upload-Capability"] = capability
        if extra:
            h.update(extra)
        return h

    def check_health(self) -> bool:
        """Check if FastAPI backend is reachable on loopback."""
        try:
            with self._client(timeout=2.0) as client:
                res = client.get(f"{self.base_url}/health")
                return res.status_code == 200 and res.json().get("status") in ("ok", "healthy")
        except Exception:
            return False

    def create_upload(self, filename: str, size_bytes: int, idempotency_key: str | None = None) -> dict:
        """Start a durable upload session."""
        import secrets
        key = idempotency_key or secrets.token_urlsafe(24)
        headers = self._headers(extra={"Idempotency-Key": key})
        try:
            with self._client(timeout=self.timeout) as client:
                res = client.post(
                    f"{self.base_url}/api/v1/uploads",
                    headers=headers,
                    json={"filename": os.path.basename(filename), "size_bytes": size_bytes},
                )
                if res.status_code not in (200, 201):
                    self._handle_error_response(res)
                return self._response_json(res, "create")
        except httpx.RequestError as exc:
            # Raw exception text (and traceback) can contain URLs or credentials.
            # Use only fixed descriptions; retain the original via __cause__.
            description = "HTTP transport failure"
            for kind, safe_message in (
                (httpx.ConnectError, "Connection establishment failed"),
                (httpx.TimeoutException, "HTTP operation timed out"),
                (httpx.RemoteProtocolError, "Connection closed or invalid HTTP response"),
                (httpx.ReadError, "Response read failed"),
            ):
                if isinstance(exc, kind):
                    description = safe_message
                    break
            logger.warning("create_upload transport failure: %s: %s", type(exc).__name__, description)
            raise BackendError("BACKEND_UNAVAILABLE", "Không thể kết nối đến dịch vụ phân tích dữ liệu.") from exc
        except ValueError as exc:
            logger.warning("create_upload response failure: ValueError: Invalid JSON response")
            raise BackendError("INVALID_BACKEND_RESPONSE", "Phản hồi từ dịch vụ phân tích dữ liệu không hợp lệ.") from exc

    def upload_chunk(
        self,
        upload_id: str,
        capability: str,
        chunk_index: int,
        offset: int,
        chunk_data: bytes,
    ) -> dict:
        """Upload a single chunk with offset and SHA-256."""
        sha256 = hashlib.sha256(chunk_data).hexdigest()
        headers = self._headers(
            capability=capability,
            extra={
                "Upload-Offset": str(offset),
                "X-Chunk-SHA256": sha256,
                "Content-Type": "application/octet-stream",
            },
        )
        try:
            with self._client(timeout=self.timeout) as client:
                res = client.put(
                    f"{self.base_url}/api/v1/uploads/{upload_id}/chunks/{chunk_index}",
                    headers=headers,
                    content=chunk_data,
                )
                if res.status_code != 200:
                    self._handle_error_response(res)
                return self._response_json(res, "upload")
        except httpx.RequestError as exc:
            raise BackendError("NETWORK_ERROR", "Mất kết nối trong quá trình truyền dữ liệu.") from exc

    def complete_upload(self, upload_id: str, capability: str) -> dict:
        """Seal uploaded payload."""
        headers = self._headers(capability=capability)
        try:
            with self._client(timeout=self.timeout) as client:
                res = client.post(f"{self.base_url}/api/v1/uploads/{upload_id}/complete", headers=headers)
                if res.status_code != 200:
                    self._handle_error_response(res)
                return self._response_json(res, "upload")
        except httpx.RequestError as exc:
            raise BackendError("BACKEND_UNAVAILABLE", "Không thể hoàn tất upload.") from exc

    def get_upload_status(self, upload_id: str, capability: str) -> dict:
        """Query raw upload transport status."""
        headers = self._headers(capability=capability)
        try:
            with self._client(timeout=self.timeout) as client:
                res = client.get(f"{self.base_url}/api/v1/uploads/{upload_id}", headers=headers)
                if res.status_code != 200:
                    self._handle_error_response(res)
                return self._response_json(res, "upload")
        except httpx.RequestError as exc:
            raise BackendError("BACKEND_UNAVAILABLE", "Không thể kiểm tra trạng thái upload.") from exc

    def start_validation(self, upload_id: str, capability: str, retry: bool = False) -> dict:
        """Start or retry dataset validation/indexing."""
        headers = self._headers(capability=capability)
        try:
            with self._client(timeout=self.timeout) as client:
                res = client.post(
                    f"{self.base_url}/api/v1/uploads/{upload_id}/validate",
                    headers=headers,
                    params={"retry": "true"} if retry else {},
                )
                if res.status_code != 200:
                    self._handle_error_response(res)
                return self._response_json(res, "dataset")
        except httpx.RequestError as exc:
            raise BackendError("BACKEND_UNAVAILABLE", "Không thể bắt đầu kiểm tra dataset.") from exc

    def get_dataset_status(self, upload_id: str, capability: str) -> dict:
        """Query dataset validation lifecycle status."""
        headers = self._headers(capability=capability)
        try:
            with self._client(timeout=self.timeout) as client:
                res = client.get(f"{self.base_url}/api/v1/uploads/{upload_id}/dataset", headers=headers)
                if res.status_code != 200:
                    self._handle_error_response(res)
                return self._response_json(res, "dataset")
        except httpx.RequestError as exc:
            raise BackendError("BACKEND_UNAVAILABLE", "Không thể kiểm tra trạng thái dataset.") from exc

    def cancel_validation(self, upload_id: str, capability: str) -> dict:
        """Request validation cancellation."""
        headers = self._headers(capability=capability)
        try:
            with self._client(timeout=self.timeout) as client:
                res = client.post(f"{self.base_url}/api/v1/uploads/{upload_id}/dataset/cancel", headers=headers)
                if res.status_code != 200:
                    self._handle_error_response(res)
                return self._response_json(res, "dataset")
        except httpx.RequestError as exc:
            raise BackendError("BACKEND_UNAVAILABLE", "Không thể hủy thao tác.") from exc

    def get_dataset_summary(self, dataset_id: str, capability: str) -> dict:
        """Fetch summary of a ready dataset."""
        headers = self._headers(capability=capability)
        try:
            with self._client(timeout=self.timeout) as client:
                res = client.get(f"{self.base_url}/api/v1/datasets/{dataset_id}/summary", headers=headers)
                if res.status_code != 200:
                    self._handle_error_response(res)
                return self._response_json(res, "summary")
        except httpx.RequestError as exc:
            raise BackendError("BACKEND_UNAVAILABLE", "Không thể lấy thông tin tóm tắt dataset.") from exc

    def trigger_analysis(
        self,
        dataset_id: str,
        capability: str,
        image_cfg: dict | None = None,
        label_cfg: dict | None = None,
    ) -> dict:
        """Explicitly run analysis pipeline on a ready dataset."""
        headers = self._headers(capability=capability)
        if any(value is not None and not isinstance(value, dict) for value in (image_cfg, label_cfg)):
            raise BackendError("INVALID_ANALYSIS_REQUEST", self._translate_error("INVALID_ANALYSIS_REQUEST"), status_code=422)
        payload = {}
        if image_cfg:
            payload["image_cfg"] = image_cfg
        if label_cfg:
            payload["label_cfg"] = label_cfg
        try:
            json.dumps(payload, allow_nan=False)
        except (ValueError, TypeError, RecursionError, OverflowError):
            raise BackendError("INVALID_ANALYSIS_REQUEST", self._translate_error("INVALID_ANALYSIS_REQUEST"), status_code=422) from None
        try:
            with self._client(timeout=300.0) as client:  # Analysis may take longer
                res = client.post(f"{self.base_url}/api/v1/datasets/{dataset_id}/analyze", headers=headers, json=payload)
                if res.status_code != 200:
                    self._handle_error_response(res)
                return self._response_json(res, "analysis")
        except httpx.RequestError as exc:
            raise BackendError("BACKEND_UNAVAILABLE", "Không thể thực hiện phân tích dữ liệu.") from exc

    def get_analysis_results(self, dataset_id: str, capability: str) -> dict | None:
        """Retrieve pre-computed analysis results; returns None if ANALYSIS_NOT_RUN."""
        headers = self._headers(capability=capability)
        try:
            with self._client(timeout=self.timeout) as client:
                res = client.get(f"{self.base_url}/api/v1/datasets/{dataset_id}/results", headers=headers)
                if res.status_code == 409:
                    err_code = self._error_code(res)
                    if err_code == "ANALYSIS_NOT_RUN":
                        return None
                if res.status_code != 200:
                    self._handle_error_response(res)
                return self._response_json(res, "analysis")
        except httpx.RequestError as exc:
            raise BackendError("BACKEND_UNAVAILABLE", "Không thể lấy kết quả phân tích.") from exc

    def fetch_preview(self, dataset_id: str, capability: str, image_path: str, annotated: bool = False) -> bytes:
        """Fetch raw image preview bytes with optional bounding box overlay."""
        headers = self._headers(capability=capability)
        try:
            with self._client(timeout=self.timeout) as client:
                res = client.get(
                    f"{self.base_url}/api/v1/datasets/{dataset_id}/preview",
                    headers=headers,
                    params={"image_path": image_path, "annotated": "true" if annotated else "false"},
                )
                if res.status_code != 200:
                    self._handle_error_response(res)
                return res.content
        except httpx.RequestError as exc:
            raise BackendError("BACKEND_UNAVAILABLE", "Không thể tải preview ảnh.") from exc


    def stream_upload(
        self,
        file_obj: BinaryIO,
        filename: str,
        size_bytes: int,
        on_progress: Callable[[int, int], None] | None = None,
        chunk_bytes: int = CHUNK_SIZE,
        idempotency_key: str | None = None,
        recovery_session: dict | None = None,
        confirmed_legacy_source: bool = False,
        restart_terminal: bool = False,
    ) -> dict:
        """Bounded-memory upload reading chunks from a file-like object.
        
        Never loads the entire file into RAM.
        """
        if recovery_session is not None and not confirmed_legacy_source:
            raise BackendError("RECOVERY_CONFIRMATION_REQUIRED", "Cần xác nhận tệp nguồn cho phiên cũ chưa có dấu vân tay toàn tệp.")
        if recovery_session is not None:
            self._validate_data(recovery_session, "create")
        if idempotency_key is None and recovery_session is None:
            file_obj.seek(0)
            digest = hashlib.sha256()
            actual_size = 0
            while block := file_obj.read(CHUNK_SIZE):
                digest.update(block)
                actual_size += len(block)
            if actual_size != size_bytes:
                raise BackendError("SOURCE_SIZE_MISMATCH", "Kích thước tệp nguồn không khớp.")
            idempotency_key = upload_source_key(filename, size_bytes, digest.hexdigest())
        session = recovery_session or self.create_upload(filename, size_bytes, idempotency_key)
        self._validate_data(session, "create")
        upload_id = session["upload_id"]
        capability = session["upload_capability"]
        status = self.get_upload_status(upload_id, capability)
        # An explicit new attempt may follow only a terminal cancelled/expired one.
        # Chain keys remain reproducible across reruns and response loss.
        terminal_steps = 0
        while (restart_terminal and recovery_session is None
               and status.get("state") in ("cancelled", "expired")):
            terminal_steps += 1
            if terminal_steps > 32:
                raise BackendError("UPLOAD_NOT_RESUMABLE", "Cần người vận hành kiểm tra lịch sử upload.")
            idempotency_key = hashlib.sha256((idempotency_key + ":" + upload_id).encode()).hexdigest()
            session = self.create_upload(filename, size_bytes, idempotency_key)
            upload_id, capability = session["upload_id"], session["upload_capability"]
            status = self.get_upload_status(upload_id, capability)
        if status.get("filename") != os.path.basename(filename) or status.get("size_bytes") != size_bytes:
            raise BackendError("SOURCE_METADATA_MISMATCH", "Tên hoặc kích thước tệp không khớp phiên upload.")
        if status.get("state") not in ("created", "uploading", "uploaded"):
            raise BackendError("UPLOAD_NOT_RESUMABLE", "Phiên upload không thể tiếp tục.")
        offset = status["received_bytes"]
        effective_chunk_size = status["chunk_size_bytes"]
        if not 0 <= offset <= size_bytes or effective_chunk_size <= 0:
            raise BackendError("INVALID_BACKEND_RESPONSE", "Trạng thái upload không hợp lệ.")
        file_obj.seek(0)
        # Validate every acknowledged byte before writing anything to this session.
        position = cursor = 0
        while position < offset:
            try:
                with self._client() as client:
                    response = client.get(
                        f"{self.base_url}/api/v1/uploads/{upload_id}/receipts",
                        headers=self._headers(capability), params={"start": cursor, "limit": 128},
                    )
                    if response.status_code != 200:
                        self._handle_error_response(response)
                    receipts = self._response_json(response, "receipts")["receipts"]
            except httpx.RequestError as exc:
                raise BackendError("BACKEND_UNAVAILABLE", "Không thể kiểm tra dữ liệu đã nhận.") from exc
            except (ValueError, KeyError, TypeError) as exc:
                raise BackendError("INVALID_RECEIPTS", "Không thể xác minh dữ liệu đã nhận.") from exc
            if not receipts:
                raise BackendError("INVALID_RECEIPTS", "Không thể xác minh dữ liệu đã nhận.")
            for receipt in receipts:
                length = receipt["length_bytes"]
                if (receipt["chunk_index"] != cursor or receipt["offset_bytes"] != position
                        or length <= 0 or position + length > offset):
                    raise BackendError("INVALID_RECEIPTS", "Thông tin chunk không hợp lệ.")
                digest = hashlib.sha256()
                left = length
                while left:
                    block = file_obj.read(min(CHUNK_SIZE, left))
                    if not block:
                        raise BackendError("SOURCE_PREFIX_MISMATCH", "Tệp nguồn không khớp dữ liệu đã nhận.")
                    digest.update(block)
                    left -= len(block)
                if digest.hexdigest() != receipt["sha256"]:
                    raise BackendError("SOURCE_PREFIX_MISMATCH", "Tệp nguồn không khớp dữ liệu đã nhận.")
                position += length
                cursor += 1
        if on_progress:
            on_progress(offset, size_bytes)
        if status["state"] == "uploaded":
            return {**session, **status}
        file_obj.seek(offset)
        chunk_idx = cursor
        while offset < size_bytes:
            current_chunk_size = min(effective_chunk_size, size_bytes - offset)
            chunk_data = file_obj.read(current_chunk_size)
            if len(chunk_data) != current_chunk_size:
                raise BackendError("SOURCE_SIZE_MISMATCH", "Tệp nguồn bị thiếu dữ liệu.")
            self.upload_chunk(upload_id, capability, chunk_idx, offset, chunk_data)
            offset += len(chunk_data)
            chunk_idx += 1
            if on_progress:
                on_progress(offset, size_bytes)
        complete_info = self.complete_upload(upload_id, capability)
        return {**session, **complete_info}

    @staticmethod
    def _validate_data(data, kind):
        try:
            return validate_response(data, kind)
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise BackendError("INVALID_BACKEND_RESPONSE", "Phản hồi từ dịch vụ phân tích dữ liệu không hợp lệ.") from exc

    def _response_json(self, res, kind):
        try:
            data = res.json()
        except (ValueError, RecursionError) as exc:
            raise BackendError("INVALID_BACKEND_RESPONSE", "Phản hồi từ dịch vụ phân tích dữ liệu không hợp lệ.") from exc
        return self._validate_data(data, kind)

    @staticmethod
    def _error_code(res):
        try:
            data = res.json()
            code = data["error"]["code"]
            if not isinstance(code, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code) is None:
                raise ValueError("Invalid error envelope")
            return code
        except (ValueError, TypeError, KeyError, RecursionError):
            return f"HTTP_{res.status_code}"

    def _handle_error_response(self, res: httpx.Response) -> None:
        code = self._error_code(res)
        msg = self._translate_error(code)
        raise BackendError(code, sanitize_message(msg), status_code=res.status_code)

    @staticmethod
    def _translate_error(code: str) -> str:
        translations = {
            "INVALID_ANALYSIS_REQUEST": "Cấu hình phân tích không hợp lệ. Vui lòng kiểm tra các ngưỡng.",
            "ONE_ACTIVE_UPLOAD": "Một phiên upload đang hoạt động đang chặn upload mới. Hãy khôi phục phiên gốc hoặc liên hệ người vận hành; không tự hủy phiên.",
            "ACCESS_DENIED": "Xác thực thất bại. Vui lòng kiểm tra Owner Key.",
            "LOCAL_ONLY": "Truy cập bị từ chối: backend chỉ hỗ trợ kết nối loopback nội bộ.",
            "UPLOAD_NOT_SEALED": "Tệp upload chưa được niêm phong hoàn tất.",
            "DATASET_NOT_READY": "Dataset chưa sẵn sàng hoặc đang trong quá trình xử lý.",
            "ANALYSIS_NOT_RUN": "Chưa thực hiện phân tích dữ liệu cho dataset này.",
            "STORAGE_IO_FAILED": "Lỗi lưu trữ tệp trên đĩa cứng.",
            "REGISTRY_UNAVAILABLE": "Cơ sở dữ liệu quản lý dataset tạm thời không khả dụng.",
            "WORKER_INTERRUPTED": "Tiến trình xử lý bị gián đoạn (ví dụ: máy chủ khởi động lại). Có thể thử lại.",
            "XML_DTD_OR_ENTITY_FORBIDDEN": "Tệp XML không an toàn (chứa DTD hoặc thực thể bị cấm).",
            "EXPLICIT_RETRY_REQUIRED": "Dataset đã từng thất bại, yêu cầu chọn Thử lại để chạy lại.",
            "IMAGE_NOT_FOUND": "Không tìm thấy ảnh được yêu cầu trong dataset.",
            "IMAGE_SIZE_LIMIT": "Ảnh vượt quá giới hạn kích thước an toàn.",
        }
        return translations.get(code, f"Lỗi từ hệ thống backend: {code}")


def render_backend_scene_finder(images, dataset_id: str, capability: str, client: BackendClient):
    """Render Scene Finder tab using backend previews without loading large archive into RAM."""
    import pandas as pd
    import streamlit as st

    st.markdown('#### 🔎 Tìm ảnh theo thời tiết × thời điểm')
    st.caption('Lọc theo tag CVAT. unknown: đã gán nhưng chưa xác định; missing: chưa có metadata. Đây là danh sách cần kiểm tra, không tự kết luận gán nhãn sai.')
    if images is None or images.empty:
        st.info('Không có thông tin ảnh trong XML.')
        return

    df = images.copy()

    def _value(value):
        return 'missing' if pd.isna(value) or not str(value).strip() else str(value).strip()

    for key in ('weather', 'timeofday'):
        if key in df.columns:
            df[key] = df[key].map(_value)
    df['image_name'] = df['image_name'].fillna('').astype(str)

    c1, c2, c3 = st.columns([1, 1, 1.3])
    with c1:
        w_opts = sorted(df.weather.unique(), key=lambda v: (v not in ('unknown', 'missing'), v)) if 'weather' in df.columns else []
        weather = st.multiselect('Weather', w_opts, placeholder='Tất cả', key='finder_weather_backend')
    with c2:
        t_opts = sorted(df.timeofday.unique(), key=lambda v: (v not in ('unknown', 'missing'), v)) if 'timeofday' in df.columns else []
        tod = st.multiselect('Time of day', t_opts, placeholder='Tất cả', key='finder_tod_backend')
    with c3:
        keyword = st.text_input('Tên hoặc đường dẫn ảnh', placeholder='Tìm tên ảnh...', key='finder_name_backend').strip()

    filtered = df
    if weather and 'weather' in filtered.columns:
        filtered = filtered[filtered.weather.isin(weather)]
    if tod and 'timeofday' in filtered.columns:
        filtered = filtered[filtered.timeofday.isin(tod)]
    if keyword:
        filtered = filtered[filtered.image_name.str.contains(keyword, case=False, regex=False, na=False)]
    filtered = filtered.reset_index(drop=True)

    a, b, c = st.columns(3)
    a.metric('Ảnh khớp bộ lọc', len(filtered))
    b.metric('Tổng ảnh trong XML', len(df))
    if 'weather' in df.columns and 'timeofday' in df.columns:
        c.metric('Ảnh có metadata chưa rõ', int((df.weather.isin(['unknown', 'missing']) | df.timeofday.isin(['unknown', 'missing'])).sum()))

    if filtered.empty:
        st.info('Không tìm thấy ảnh khớp điều kiện.')
        return

    cols = [col for col in ['image_id', 'image_name', 'weather', 'timeofday'] if col in filtered.columns]
    st.dataframe(filtered[cols], hide_index=True, use_container_width=True, height=min(410, 80 + 35 * len(filtered)))
    st.download_button('⬇️ Tải danh sách ảnh CSV', filtered[cols].to_csv(index=False).encode('utf-8-sig'), 'scene_filtered_images.csv', 'text/csv', key='finder_csv_backend')

    with st.expander('🖼️ Xem ảnh để kiểm tra', expanded=True):
        per_page = st.selectbox('Ảnh mỗi trang', [4, 8, 12, 20], index=1, key='finder_page_size_backend')
        max_page = max(1, (len(filtered) + per_page - 1) // per_page)
        page = st.number_input('Trang', min_value=1, max_value=max_page, value=1, key='finder_page_backend')
        cells = st.columns(2)
        for i, (_, r) in enumerate(filtered.iloc[(page - 1) * per_page:page * per_page].iterrows()):
            with cells[i % 2]:
                with st.container(border=True):
                    st.markdown('**' + str(r.image_name) + '**')
                    st.caption('Weather: ' + str(r.get('weather', '')) + ' | Time of day: ' + str(r.get('timeofday', '')))
                    try:
                        raw = client.fetch_preview(dataset_id, capability, str(r.image_name), annotated=False)
                        st.image(raw, use_container_width=True)
                    except Exception as exc:
                        st.warning('Không đọc được ảnh: ' + str(exc))


from ui.browser_upload import render_browser_upload  # noqa: E402

