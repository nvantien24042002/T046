// Isolated Stage 3 test client. No File/ZIP bytes in Web Storage or Python UI.
const $ = id => document.getElementById(id);
const storageKey = 'cvat-stage3-session';
let saved = null;
try { saved = JSON.parse(sessionStorage.getItem(storageKey)); } catch { /* Invalid tab storage is not trusted. */ }
if (saved?.ownerKey) $('owner-key').value = saved.ownerKey;
let busy = false, paused = false, xhr = null, task = null;
const persist = () => sessionStorage.setItem(storageKey, JSON.stringify(saved));
const headers = () => ({'X-Owner-Key': $('owner-key').value,
                        'Upload-Capability': saved?.capability || ''});
const endpoint = () => '/api/v1/uploads/' + saved.id;
const message = text => { $('status').textContent = text; };
const hashBlob = async blob => {
  // A single negotiated chunk only (server maximum 8 MiB), never the whole ZIP.
  const hash = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
};
const api = async (url, options = {}) => {
  const response = await fetch(url, {...options, headers: {...headers(), ...options.headers}, cache: 'no-store'});
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.error?.code || 'HTTP_' + response.status);
    error.status = response.status;
    throw error;
  }
  return body;
};
const put = (index, offset, blob, sha, total) => new Promise((resolve, reject) => {
  xhr = new XMLHttpRequest();
  xhr.open('PUT', endpoint() + '/chunks/' + index);
  xhr.timeout = 120000;
  for (const [key, value] of Object.entries({...headers(), 'Upload-Offset': String(offset),
      'X-Chunk-SHA256': sha, 'Content-Type': 'application/octet-stream'})) xhr.setRequestHeader(key, value);
  xhr.upload.onprogress = event => message('Acknowledged ' + offset + ' / ' + total +
      ' bytes; current request transferred ' + event.loaded + ' bytes (not yet acknowledged).');
  xhr.onerror = xhr.ontimeout = xhr.onabort = () => reject(new Error(paused ? 'PAUSED' : 'NETWORK_INTERRUPTED'));
  xhr.onload = () => {
    let body;
    try { body = JSON.parse(xhr.responseText); } catch { return reject(new Error('INVALID_RESPONSE')); }
    if (xhr.status === 200) return resolve(body);
    const error = new Error(body.error?.code || 'HTTP_' + xhr.status);
    error.status = xhr.status;
    reject(error);
  };
  xhr.send(blob);
});
async function verifyPrefix(file, status) {
  let cursor = 0, position = 0;
  while (position < status.received_bytes) {
    if (paused) throw new Error('PAUSED');
    const page = await api(endpoint() + '/receipts?start=' + cursor + '&limit=128');
    if (!page.receipts.length) throw new Error('MISSING_PREFIX_RECEIPTS');
    for (const receipt of page.receipts) {
      if (receipt.chunk_index !== cursor || receipt.offset_bytes !== position ||
          position + receipt.length_bytes > status.received_bytes) throw new Error('INVALID_PREFIX_RECEIPTS');
      message('Verifying reselected source prefix: ' + position + ' / ' + status.received_bytes + ' bytes.');
      if (await hashBlob(file.slice(position, position + receipt.length_bytes)) !== receipt.sha256)
        throw new Error('SOURCE_PREFIX_MISMATCH');
      position += receipt.length_bytes;
      cursor++;
    }
  }
  return position;
}
async function sendWithRetry(index, offset, blob, sha, total) {
  for (let attempt = 0; ; attempt++) {
    if (paused) throw new Error('PAUSED');
    try { return await put(index, offset, blob, sha, total); }
    catch (error) {
      if (paused || attempt >= 3 || (error.status && error.status < 500 && ![408,429].includes(error.status))) throw error;
      message('Retrying from the last durable acknowledgement.');
      await new Promise(resolve => setTimeout(resolve, 250 * (2 ** attempt) + Math.random() * 100));
      if (paused) throw new Error('PAUSED');
      const current = await api(endpoint());
      if (!current.durable) throw new Error('UPLOAD_NOT_RESUMABLE');
      // Replaying even after an ambiguous successful response is safe: server
      // checks the receipt and consumes/verifies the entire repeated chunk.
    }
  }
}
async function upload() {
  const file = $('source').files[0];
  if (!file || !file.name.toLowerCase().endsWith('.zip')) throw new Error('SELECT_ZIP');
  if ($('owner-key').value.length < 32) throw new Error('ENTER_OWNER_KEY');
  if (saved && (saved.name !== file.name || saved.size !== file.size)) throw new Error('RESELECT_SAME_SOURCE');
  if (!saved) saved = {name: file.name, size: file.size, requestKey: crypto.randomUUID()};
  saved.ownerKey = $('owner-key').value;
  persist(); // Preserve idempotency key before a possibly lost creation response.
  if (!saved.id) {
    const created = await api('/api/v1/uploads', {method: 'POST', headers: {
        'Content-Type': 'application/json', 'Idempotency-Key': saved.requestKey},
        body: JSON.stringify({filename: file.name, size_bytes: file.size})});
    saved.id = created.upload_id;
    saved.capability = created.upload_capability;
    persist();
  }
  let status = await api(endpoint());
  if (!status.durable) throw new Error(status.error_code || 'UPLOAD_NOT_RESUMABLE');
  if (status.chunk_size_bytes > 8 * 1024 * 1024 || status.chunk_size_bytes <= 0) throw new Error('INVALID_CHUNK_LIMIT');
  let offset = await verifyPrefix(file, status);
  $('progress').value = 100 * offset / file.size;
  while (offset < file.size) {
    if (paused) throw new Error('PAUSED');
    const blob = file.slice(offset, Math.min(offset + status.chunk_size_bytes, file.size));
    const digest = await hashBlob(blob);
    status = await sendWithRetry(Math.floor(offset / status.chunk_size_bytes), offset, blob, digest, file.size);
    if (status.received_bytes !== offset + blob.size || !status.durable) throw new Error('INVALID_ACKNOWLEDGEMENT');
    offset = status.received_bytes;
    $('progress').value = 100 * offset / file.size;
    message('Acknowledged ' + offset + ' / ' + file.size + ' bytes.');
  }
  if (paused) throw new Error('PAUSED');
  status = await api(endpoint() + '/complete', {method: 'POST'});
  $('result').textContent = JSON.stringify(status, null, 2);
  message('Uploaded bytes are durable for process restart. Unvalidated; no dataset registered.');
}
$('upload').onclick = () => {
  if (busy) return;
  busy = true;
  paused = false;
  $('upload').disabled = $('source').disabled = $('forget').disabled = true;
  task = upload().catch(error => { message(error.message + '. Reselect the same file and resume when possible.'); })
    .finally(() => { busy = false; xhr = null; $('upload').disabled = $('source').disabled = $('forget').disabled = false; });
};
$('pause').onclick = () => { paused = true; if (xhr) xhr.abort(); };
$('cancel').onclick = async () => {
  paused = true;
  if (xhr) xhr.abort();
  if (task) await task;
  if (!saved?.id) return message('No saved upload to cancel.');
  try {
    const result = await api(endpoint(), {method: 'DELETE'});
    $('result').textContent = JSON.stringify(result, null, 2);
    message(result.cleanup_pending ? 'Cancelled; safe cleanup remains pending.' : 'Cancelled and temporary files removed.');
  } catch (error) { message(error.message); }
};
$('forget').onclick = () => {
  if (busy) return;
  saved = null;
  sessionStorage.removeItem(storageKey);
  $('owner-key').value = '';
  $('result').textContent = '';
  message('Credentials forgotten in this tab; upload files were not deleted.');
};
if (saved?.id) message('Saved session found. Reselect the same source; every acknowledged prefix chunk will be verified.');
