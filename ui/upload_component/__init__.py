"""Inline CCv2 transport spike; no npm, React, file bytes or secrets in results."""
from __future__ import annotations

import streamlit as st

HTML = """
<label for="zip-file">Select a test ZIP (maximum 16 MiB)</label>
<input id="zip-file" type="file" accept=".zip" />
<button id="send" type="button">Upload test ZIP</button>
<progress id="progress" max="100" value="0" aria-label="Upload progress"></progress>
<p id="status" role="status">Choose a ZIP to test direct browser-to-API transfer.</p>
"""

JS = """
export default function ({data, parentElement, setStateValue}) {
  const input = parentElement.querySelector('#zip-file');
  const button = parentElement.querySelector('#send');
  const progress = parentElement.querySelector('#progress');
  const message = parentElement.querySelector('#status');
  // Streamlit rerenders call this function again; retain the live File/XHR locally.
  const state = parentElement.__cvatSpike || {busy: false, xhr: null};
  parentElement.__cvatSpike = state;
  const url = data.api_base + '/prototype/v1/uploads';
  button.disabled = state.busy;
  input.disabled = state.busy;

  const sendChunk = (endpoint, blob, token, offset, total) => new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    state.xhr = xhr;
    xhr.open('PUT', endpoint);
    xhr.timeout = 120000;
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.setRequestHeader('Authorization', 'Bearer ' + token);
    xhr.setRequestHeader('Upload-Offset', String(offset));
    xhr.upload.onprogress = event => {
      progress.value = 100 * (offset + event.loaded) / total;
      message.textContent = 'Transferring; server received ' + offset + ' / ' + total + ' bytes (not crash-durable).';
    };
    xhr.onerror = () => reject(new Error('Connection interrupted. This prototype has no retry/resume; restart API before another attempt.'));
    xhr.ontimeout = () => reject(new Error('Chunk timed out. This prototype has no retry/resume.'));
    xhr.onabort = () => reject(new Error('Transfer stopped. This prototype has no resume.'));
    xhr.onload = () => {
      if (xhr.status !== 200) return reject(new Error('Chunk rejected (HTTP ' + xhr.status + ').'));
      try { resolve(JSON.parse(xhr.responseText)); } catch { reject(new Error('Invalid acknowledgement.')); }
    };
    xhr.send(blob);
  });

  button.onclick = async () => {
    if (state.busy) return;
    const file = input.files[0];
    if (!file || !file.name.toLowerCase().endsWith('.zip') || file.size < 1 || file.size > data.max_bytes) {
      message.textContent = 'Choose a nonempty ZIP no larger than 16 MiB.';
      return;
    }
    state.busy = true;
    button.disabled = input.disabled = true;
    progress.value = 0;
    try {
      const created = await fetch(url, {method: 'POST', headers: {
        'Content-Type': 'application/json', 'X-Prototype-Key': data.bootstrap_key
      }, body: JSON.stringify({filename: file.name, size_bytes: file.size})});
      if (!created.ok) throw new Error('Session rejected (HTTP ' + created.status + ').');
      const session = await created.json();
      let offset = session.received_bytes;
      let index = 0;
      while (offset < file.size) {
        const blob = file.slice(offset, Math.min(file.size, offset + session.chunk_size_bytes));
        const ack = await sendChunk(url + '/' + session.upload_id + '/chunks/' + index,
                                    blob, session.upload_token, offset, file.size);
        if (ack.received_bytes !== offset + blob.size) throw new Error('Unexpected server offset.');
        offset = ack.received_bytes;
        index++;
        progress.value = 100 * offset / file.size;
        message.textContent = 'Server received ' + offset + ' / ' + file.size + ' bytes (not crash-durable).';
      }
      const finished = await fetch(url + '/' + session.upload_id + '/complete', {
        method: 'POST', headers: {'Authorization': 'Bearer ' + session.upload_token}
      });
      if (!finished.ok) throw new Error('Completion rejected (HTTP ' + finished.status + ').');
      const receipt = await finished.json();
      message.textContent = 'Received, unvalidated. No dataset registered. SHA-256: ' + receipt.sha256;
      // Only small nonsecret metadata crosses to Python. No File/Blob/bytes/token.
      setStateValue('result', {upload_id: receipt.upload_id, state: receipt.state,
                    received_bytes: receipt.received_bytes, sha256: receipt.sha256,
                    ready: false, durable: false});
    } catch (error) {
      message.textContent = error.message;
      setStateValue('result', {state: 'failed', message: error.message, ready: false, durable: false});
    } finally {
      state.busy = false;
      state.xhr = null;
      button.disabled = input.disabled = false;
    }
  };
  return () => {
    if (state.xhr) state.xhr.abort();
    button.onclick = null;
  };
}
"""

_UPLOAD = st.components.v2.component("cvat_small_zip_transport_spike", html=HTML, js=JS)


def upload_test_zip(api_base: str, bootstrap_key: str):
    """Mount with stable identity; the key is configured out of band, never logged."""
    return _UPLOAD(
        key="cvat-upload-spike",
        data={"api_base": api_base, "bootstrap_key": bootstrap_key, "max_bytes": 16 * 1024 * 1024},
        default={"result": None}, on_result_change=lambda: None,
    )
