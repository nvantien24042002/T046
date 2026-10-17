import { z } from 'zod'
import { payloadSchema, type AnalysisResultDetail } from './verifiedResults'
import { decodeDatasetSummary } from './datasets'
import type { UploadProgress } from './upload'
export const browserPrefix = '/api/browser/v1'
export const browserHeaders = { 'X-Requested-With': 'N2-05B' }
let boot: Promise<void> | null = null
const fileKeys = new WeakMap<File, string>()
export class BrowserApiError extends Error {
  constructor(public status: number, public code: string) { super(`API ${status}: ${code}`) }
}
async function raw(path: string, init: RequestInit = {}) {
  const response = await fetch(browserPrefix + path, { ...init, credentials: 'same-origin', redirect: 'error', cache: 'no-store',
    headers: { ...browserHeaders, ...(init.body && typeof init.body === 'string' ? { 'Content-Type': 'application/json' } : {}), ...init.headers } })
  if (!response.ok) {
    let code = 'REQUEST_FAILED'
    try { const d: unknown = await response.json(); const parsed = z.object({ error: z.object({ code: z.string().regex(/^[A-Z0-9_]{1,64}$/) }) }).safeParse(d); if (parsed.success) code = parsed.data.error.code } catch { /* fixed public error */ }
    throw new BrowserApiError(response.status, code)
  }
  return response
}
export async function ensureBrowserSession() {
  if (!boot) boot = raw('/session', { method: 'POST' }).then(async (response) => { await response.arrayBuffer() }).catch((e: unknown) => { boot = null; throw e })
  await boot
}
export async function browserJson(path: string, init: RequestInit = {}): Promise<unknown> {
  await ensureBrowserSession()
  return (await raw(path, init)).json()
}
export async function browserPreview(dataset: string, image: string, annotated: boolean, signal?: AbortSignal) {
  await ensureBrowserSession()
  return (await raw(`/datasets/${encodeURIComponent(dataset)}/preview?image_path=${encodeURIComponent(image)}&annotated=${annotated}`, { ...(signal ? { signal } : {}) })).blob()
}
export async function browserExport(dataset: string, body: Record<string, unknown>) {
  await ensureBrowserSession()
  return (await raw(`/datasets/${encodeURIComponent(dataset)}/export`, { method: 'POST', body: JSON.stringify(body) })).blob()
}
export async function loadBrowserResult(dataset: string, signal?: AbortSignal): Promise<AnalysisResultDetail> {
  const payload = payloadSchema.parse(await browserJson(`/datasets/${encodeURIComponent(dataset)}/results`, { ...(signal ? { signal } : {}) }))
  if (payload.dataset_id !== dataset || !payload.request_fingerprint) throw new Error('Dataset/result identity mismatch')
  return { payload, descriptor: null, provenance: null }
}
const uploadSchema = z.object({ upload_id: z.string().min(1), chunk_size_bytes: z.number().int().positive(), size_bytes: z.number().int().positive(), received_bytes: z.number().int().nonnegative() })
const lifecycleSchema = z.object({ dataset_id: z.string().min(1), state: z.string(), ready: z.boolean() })
export async function uploadBrowserDataset({ file, onProgress, signal, onUpload }: { file: File; onProgress: (p: UploadProgress) => void; signal?: AbortSignal; onUpload?: (uploadId: string) => void }) {
  const options = signal ? { signal } : {}
  const hash = async (blob: Blob) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer()))).map((b) => b.toString(16).padStart(2, '0')).join('')
  // A retry of the same selected File reuses its opaque idempotency key.
  // A reselected file never adopts an old upload by filename/size alone.
  const key = fileKeys.get(file) ?? crypto.randomUUID()
  fileKeys.set(file, key)
  const progress = (phase: UploadProgress['phase'], percent: number, transferredBytes = 0) => onProgress({ phase, percent, transferredBytes, totalBytes: file.size, message: phase })
  progress('creating_session', 0)
  const create = async () => uploadSchema.parse(await browserJson('/uploads', { ...options, method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify({ filename: file.name, size_bytes: file.size }) }))
  // Stable identity and server principal preserve lost-create acknowledgement.
  let upload
  try { upload = await create() } catch (e) { if (signal?.aborted || e instanceof BrowserApiError) throw e; upload = await create() }
  onUpload?.(upload.upload_id)
  const id = encodeURIComponent(upload.upload_id)
  const status = uploadSchema.parse(await browserJson(`/uploads/${id}`, options))
  for (let offset = status.received_bytes; offset < file.size; offset += upload.chunk_size_bytes) {
    const part = file.slice(offset, Math.min(offset + upload.chunk_size_bytes, file.size))
    const sha = await hash(part)
    let acknowledged = false
    for (let attempt = 0; attempt < 3 && !acknowledged; attempt++) {
      try {
        const receipt = z.object({ received_bytes: z.number().int().nonnegative() }).parse(await browserJson(`/uploads/${id}/chunks/${Math.floor(offset / upload.chunk_size_bytes)}`, { ...options, method: 'PUT', headers: { 'Content-Type': 'application/octet-stream', 'Upload-Offset': String(offset), 'X-Chunk-SHA256': sha }, body: part }))
        if (receipt.received_bytes < offset + part.size) throw new Error('Invalid chunk receipt')
        acknowledged = true
      } catch (e) { if (signal?.aborted || (e instanceof BrowserApiError && e.status < 500 && e.status !== 408) || attempt === 2) throw e }
    }
    progress('uploading', Math.floor(80 * (offset + part.size) / file.size), offset + part.size)
  }
  progress('completing', 80, file.size)
  await browserJson(`/uploads/${id}/complete`, { ...options, method: 'POST' })
  progress('validating_dataset', 85, file.size)
  const lifecycle = lifecycleSchema.parse(await browserJson(`/uploads/${id}/validate`, { ...options, method: 'POST' }))
  if (!lifecycle.ready || lifecycle.state !== 'ready') throw new Error('Validation chưa ready; dùng status và explicit retry, không tự tạo attempt mới.')
  progress('analyzing', 90, file.size)
  const payload = payloadSchema.parse(await browserJson(`/datasets/${encodeURIComponent(lifecycle.dataset_id)}/analyze`, { ...options, method: 'POST', body: '{}' }))
  if (payload.dataset_id !== lifecycle.dataset_id || !payload.request_fingerprint) throw new Error('Analysis identity mismatch')
  const summary = decodeDatasetSummary(await browserJson(`/datasets/${encodeURIComponent(lifecycle.dataset_id)}/summary`, options))
  progress('ready', 100, file.size)
  return { datasetId: lifecycle.dataset_id, uploadId: upload.upload_id, detail: { payload, descriptor: null, provenance: null } as AnalysisResultDetail, summary }
}
