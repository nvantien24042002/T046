"""Direct browser-to-backend chunk upload component (Stage 6C).

Transfers bounded chunks directly from the browser to the FastAPI backend
without buffering files through Streamlit's server or Python RAM.
"""
from __future__ import annotations

import streamlit as st

HTML = """
<div style="border: 1px solid rgba(128, 128, 128, 0.3); border-radius: 8px; padding: 16px; margin-bottom: 12px; font-family: sans-serif;">
  <label for="cvat-browser-zip" style="font-weight: 600; display: block; margin-bottom: 8px;">
    Chọn tệp ZIP (Tải lên trực tiếp từ trình duyệt tới Backend — Bounded Chunks):
  </label>
  <input id="cvat-browser-zip" type="file" accept=".zip" style="margin-bottom: 12px; display: block; width: 100%;" />
  <div style="display: flex; gap: 8px; margin-bottom: 12px;">
    <button id="cvat-btn-upload" type="button" style="padding: 6px 14px; cursor: pointer; border-radius: 4px; border: 1px solid #1E88E5; background: #1E88E5; color: white;">
      Bắt đầu tải lên
    </button>
    <button id="cvat-btn-pause" type="button" disabled style="padding: 6px 14px; cursor: pointer; border-radius: 4px; border: 1px solid #ccc; background: #eee;">
      Tạm dừng
    </button>
    <button id="cvat-btn-cancel" type="button" disabled style="padding: 6px 14px; cursor: pointer; border-radius: 4px; border: 1px solid #e53935; background: #e53935; color: white;">
      Hủy
    </button>
  </div>
  <progress id="cvat-upload-progress" max="100" value="0" style="width: 100%; height: 18px; margin-bottom: 8px;"></progress>
  <p id="cvat-upload-status" role="status" style="margin: 0; font-size: 13px; color: #555;">
    Sẵn sàng tải lên trực tiếp không qua RAM server Streamlit.
  </p>
</div>
"""

JS = """
export default function ({data, parentElement, setStateValue}) {
  const input = parentElement.querySelector('#cvat-browser-zip');
  const btnUpload = parentElement.querySelector('#cvat-btn-upload');
  const btnPause = parentElement.querySelector('#cvat-btn-pause');
  const btnCancel = parentElement.querySelector('#cvat-btn-cancel');
  const progress = parentElement.querySelector('#cvat-upload-progress');
  const status = parentElement.querySelector('#cvat-upload-status');

  const state = parentElement.__cvatBrowserUpload || {
    busy: false,
    paused: false,
    xhr: null,
    uploadId: data.upload_id || null,
    capability: data.capability || null,
  };
  parentElement.__cvatBrowserUpload = state;

  const apiBase = (data.api_base || 'http://127.0.0.1:8011').replace(/\\/+$/, '');
  const uploadsEndpoint = apiBase + '/api/v1/uploads';
  const chunkLimit = data.chunk_size_bytes || 8 * 1024 * 1024; // 8 MiB bounded chunk

  const setMessage = (msg) => { status.textContent = msg; };

  const hashBlob = async (blob) => {
    // Only a single 8 MiB chunk at a time is digested, never the entire ZIP.
    const buffer = await blob.arrayBuffer();
    const digest = await crypto.subtle.digest('SHA-256', buffer);
    return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
  };

  const getHeaders = () => {
    const h = {};
    if (state.capability) h['Upload-Capability'] = state.capability;
    if (data.owner_key) h['X-Owner-Key'] = data.owner_key;
    return h;
  };

  const sendChunk = (uploadId, chunkIndex, offset, blob, sha256, total) => new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    state.xhr = xhr;
    xhr.open('PUT', `${uploadsEndpoint}/${uploadId}/chunks/${chunkIndex}`);
    xhr.timeout = 120000;
    
    const headers = {
      ...getHeaders(),
      'Upload-Offset': String(offset),
      'X-Chunk-SHA256': sha256,
      'Content-Type': 'application/octet-stream',
    };
    for (const [k, v] of Object.entries(headers)) {
      xhr.setRequestHeader(k, v);
    }

    xhr.upload.onprogress = (evt) => {
      const currentTransferred = offset + evt.loaded;
      progress.value = Math.min(100, Math.floor(100 * currentTransferred / total));
      setMessage(`Đang tải: ${offset + evt.loaded} / ${total} bytes (chưa xác nhận chunk này).`);
    };

    xhr.onerror = xhr.ontimeout = xhr.onabort = () => {
      reject(new Error(state.paused ? 'PAUSED' : 'NETWORK_INTERRUPTED'));
    };

    xhr.onload = () => {
      if (xhr.status === 200) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch {
          reject(new Error('INVALID_ACKNOWLEDGEMENT'));
        }
      } else {
        let code = 'HTTP_' + xhr.status;
        try {
          const body = JSON.parse(xhr.responseText);
          code = body.error?.code || code;
        } catch {}
        const err = new Error(code);
        err.status = xhr.status;
        reject(err);
      }
    };
    xhr.send(blob);
  });

  const sendChunkWithRetry = async (uploadId, chunkIndex, offset, blob, sha256, total) => {
    for (let attempt = 0; ; attempt++) {
      if (state.paused) throw new Error('PAUSED');
      try {
        return await sendChunk(uploadId, chunkIndex, offset, blob, sha256, total);
      } catch (err) {
        if (state.paused || attempt >= 3 || (err.status && err.status < 500 && ![408, 429].includes(err.status))) {
          throw err;
        }
        setMessage(`Mất kết nối. Đang thử lại chunk ${chunkIndex} (lần ${attempt + 1})...`);
        await new Promise(r => setTimeout(r, 300 * Math.pow(2, attempt)));
      }
    }
  };

  const verifyPrefix = async (uploadId, file, receivedBytes) => {
    let cursor = 0, position = 0;
    while (position < receivedBytes) {
      if (state.paused) throw new Error('PAUSED');
      const res = await fetch(`${uploadsEndpoint}/${uploadId}/receipts?start=${cursor}&limit=128`, {
        headers: getHeaders(),
      });
      if (!res.ok) throw new Error('RECEIPTS_FETCH_FAILED');
      const data = await res.json();
      if (!data.receipts || data.receipts.length === 0) throw new Error('MISSING_PREFIX_RECEIPTS');
      for (const receipt of data.receipts) {
        if (receipt.chunk_index !== cursor || receipt.offset_bytes !== position) {
          throw new Error('INVALID_PREFIX_RECEIPTS');
        }
        setMessage(`Đang kiểm tra prefix đã lưu: ${position} / ${receivedBytes} bytes...`);
        const slice = file.slice(position, position + receipt.length_bytes);
        const digest = await hashBlob(slice);
        if (digest !== receipt.sha256) {
          throw new Error('SOURCE_PREFIX_MISMATCH');
        }
        position += receipt.length_bytes;
        cursor++;
      }
    }
    return position;
  };

  btnUpload.onclick = async () => {
    if (state.busy) return;
    const file = input.files[0];
    if (!file || !file.name.toLowerCase().endsWith('.zip')) {
      setMessage('Vui lòng chọn một tệp .zip hợp lệ.');
      return;
    }

    state.busy = true;
    state.paused = false;
    btnUpload.disabled = input.disabled = true;
    btnPause.disabled = btnCancel.disabled = false;

    try {
      let uploadId = state.uploadId;
      let capability = state.capability;
      let effectiveChunkSize = chunkLimit;

      // 1. Create upload session if not provided
      if (!uploadId) {
        setMessage('Đang khởi tạo phiên upload bền vững trên backend...');
        const initRes = await fetch(uploadsEndpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Idempotency-Key': crypto.randomUUID().replace(/-/g, ''),
            ...getHeaders(),
          },
          body: JSON.stringify({ filename: file.name, size_bytes: file.size }),
        });
        if (!initRes.ok) {
          let errCode = 'SESSION_CREATE_FAILED';
          try { errCode = (await initRes.json()).error?.code || errCode; } catch {}
          throw new Error(errCode);
        }
        const session = await initRes.json();
        uploadId = session.upload_id;
        capability = session.upload_capability;
        state.uploadId = uploadId;
        state.capability = capability;
        effectiveChunkSize = session.chunk_size_bytes || chunkLimit;
      }

      // 2. Check current status
      const statusRes = await fetch(`${uploadsEndpoint}/${uploadId}`, { headers: getHeaders() });
      if (!statusRes.ok) throw new Error('STATUS_CHECK_FAILED');
      const uploadStatus = await statusRes.json();
      let offset = uploadStatus.received_bytes || 0;

      // 3. Verify prefix if resuming
      if (offset > 0) {
        offset = await verifyPrefix(uploadId, file, offset);
      }

      // 4. Sequential bounded chunk transfer
      while (offset < file.size) {
        if (state.paused) throw new Error('PAUSED');
        const remaining = file.size - offset;
        const currentSize = Math.min(effectiveChunkSize, remaining);
        const blob = file.slice(offset, offset + currentSize);
        const digest = await hashBlob(blob);
        const chunkIndex = Math.floor(offset / effectiveChunkSize);

        const ack = await sendChunkWithRetry(uploadId, chunkIndex, offset, blob, digest, file.size);
        offset = ack.received_bytes;
        progress.value = Math.min(100, Math.floor(100 * offset / file.size));
        setMessage(`Đã xác nhận bền vững: ${offset} / ${file.size} bytes (${progress.value}%).`);
      }

      // 5. Complete upload
      setMessage('Đang niêm phong (seal) payload trên máy chủ...');
      const completeRes = await fetch(`${uploadsEndpoint}/${uploadId}/complete`, {
        method: 'POST',
        headers: getHeaders(),
      });
      if (!completeRes.ok) throw new Error('COMPLETE_FAILED');
      const completeData = await completeRes.json();

      setMessage(`Tải lên hoàn tất! Payload đã niêm phong (SHA-256: ${completeData.sha256 || 'OK'}).`);
      setStateValue('result', {
        upload_id: uploadId,
        upload_capability: capability,
        filename: file.name,
        size_bytes: file.size,
        state: 'uploaded',
        ready: false,
      });
    } catch (err) {
      if (err.message === 'PAUSED') {
        setMessage('Đã tạm dừng tải lên. Nhấn Tiếp tục để tiếp tục gửi từ chunk gần nhất.');
      } else {
        setMessage(`Lỗi: ${err.message}.`);
        setStateValue('result', { state: 'failed', error_code: err.message, ready: false });
      }
    } finally {
      state.busy = false;
      state.xhr = null;
      btnUpload.disabled = input.disabled = false;
      btnPause.disabled = btnCancel.disabled = true;
    }
  };

  btnPause.onclick = () => {
    state.paused = true;
    if (state.xhr) state.xhr.abort();
    setMessage('Đang tạm dừng...');
  };

  btnCancel.onclick = async () => {
    state.paused = true;
    if (state.xhr) state.xhr.abort();
    if (state.uploadId) {
      try {
        await fetch(`${uploadsEndpoint}/${state.uploadId}`, {
          method: 'DELETE',
          headers: getHeaders(),
        });
        setMessage('Đã hủy phiên tải lên và giải phóng tài nguyên.');
        setStateValue('result', { state: 'cancelled', ready: false });
      } catch {
        setMessage('Lỗi khi hủy phiên tải.');
      }
    }
  };

  return () => {
    if (state.xhr) state.xhr.abort();
    btnUpload.onclick = btnPause.onclick = btnCancel.onclick = null;
  };
}
"""

_DURABLE_COMPONENT = st.components.v2.component(
    "cvat_durable_browser_upload",
    html=HTML,
    js=JS,
)


def render_browser_upload(
    api_base: str,
    owner_key: str | None = None,
    upload_id: str | None = None,
    capability: str | None = None,
    chunk_size_bytes: int = 8 * 1024 * 1024,
) -> dict | None:
    """Mounts the direct browser-to-backend chunk uploader.
    
    Never loads files into Streamlit server memory or Python RAM.
    """
    comp_result = _DURABLE_COMPONENT(
        key="cvat-durable-uploader",
        data={
            "api_base": api_base,
            "owner_key": owner_key or "",
            "upload_id": upload_id,
            "capability": capability,
            "chunk_size_bytes": chunk_size_bytes,
        },
        default={"result": None},
        on_result_change=lambda: None,
    )
    if comp_result and hasattr(comp_result, "result"):
        return comp_result.result
    return None
