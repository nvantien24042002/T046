import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { File as NodeFile } from 'node:buffer'
import { webcrypto } from 'node:crypto'
const payload = { dataset_id: 'dataset-a', request_fingerprint: 'a'.repeat(64), quality_rows: [], audit_rows: [], classes: [], attribute_distribution: [], distribution_images: [], distribution_boxes: [], attributes: [] }
const summary = { dataset_id: 'dataset-a', upload_id: 'upload-a', state: 'ready', ready: true, archive_bytes: 3, archive_sha256: 'b'.repeat(64), entries: 1, images: 1, annotations: false, capabilities: { image_quality: true, label_audit: false, distribution: false, coverage: false, scene_finder: false } }
beforeEach(() => { vi.resetModules() })
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
it('bootstraps opaque cookie session and sends no internal credential', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response('{}')).mockResolvedValueOnce(new Response('{"datasets":[]}'))
  vi.stubGlobal('fetch', fetcher)
  const api = await import('../api/browserSession')
  expect(await api.browserJson('/datasets')).toEqual({ datasets: [] })
  expect(fetcher.mock.calls.map((c) => c[0])).toEqual(['/api/browser/v1/session', '/api/browser/v1/datasets'])
  for (const [,init] of fetcher.mock.calls) {
    expect(init.credentials).toBe('same-origin')
    expect(init.headers).toEqual({ 'X-Requested-With': 'N2-05B' })
    expect(JSON.stringify(init)).not.toMatch(/X-Owner-Key|Upload-Capability|Authorization|Bearer/)
  }
})
it('returns bounded safe errors without upstream private messages', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response('{}')).mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'RESOURCE_NOT_FOUND', detail: 'private-sentinel' } }), { status: 404 })))
  const api = await import('../api/browserSession')
  await expect(api.browserJson('/datasets/other/summary')).rejects.toMatchObject({ status: 404, code: 'RESOURCE_NOT_FOUND', message: 'API 404: RESOURCE_NOT_FOUND' })
})
it('uses cookie transport and rejects mismatched dashboard identity', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response('{}')).mockResolvedValueOnce(new Response(JSON.stringify({ schema_version: 'dashboard.v1', dataset_id: 'other', page: 'overview', state: 'ready', reason: null, source: { request_fingerprint: 'a'.repeat(64), result_id: null, payload_sha256: null }, metrics: [], tables: [], charts: [], controls: [], notes: [] }))))
  const { readBrowserDashboard } = await import('../api/browserDashboard')
  await expect(readBrowserDashboard('overview', { payload, descriptor: null, provenance: null }, '', {}, new AbortController().signal)).rejects.toThrow('sai dataset')
})
it('lost creation/chunk acknowledgements reuse key and upload identity', async () => {
  vi.stubGlobal('crypto', webcrypto)
  let creates = 0; let chunks = 0
  const keys: string[] = []
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    const path = url.replace('/api/browser/v1', '')
    if (path === '/session') return new Response('{}')
    if (path === '/uploads') { creates++; keys.push((init.headers as Record<string, string>)['Idempotency-Key']!); if (creates === 1) throw new TypeError('lost acknowledgement'); return new Response(JSON.stringify({ upload_id: 'upload-a', chunk_size_bytes: 8, size_bytes: 3, received_bytes: 0 })) }
    if (path === '/uploads/upload-a') return new Response(JSON.stringify({ upload_id: 'upload-a', chunk_size_bytes: 8, size_bytes: 3, received_bytes: 0 }))
    if (path.endsWith('/chunks/0')) { chunks++; if (chunks === 1) throw new TypeError('lost receipt'); return new Response('{"received_bytes":3}') }
    if (path.endsWith('/complete')) return new Response('{}')
    if (path.endsWith('/validate')) return new Response('{"dataset_id":"dataset-a","state":"ready","ready":true}')
    if (path.endsWith('/analyze')) return new Response(JSON.stringify(payload))
    if (path.endsWith('/summary')) return new Response(JSON.stringify(summary))
    throw new Error('Unexpected route')
  })
  vi.stubGlobal('fetch', fetcher)
  const { uploadBrowserDataset } = await import('../api/browserSession')
  const result = await uploadBrowserDataset({ file: new NodeFile(['zip'], 'new-name.zip') as unknown as File, onProgress: vi.fn() })
  expect(result.datasetId).toBe('dataset-a')
  expect(creates).toBe(2); expect(new Set(keys).size).toBe(1); expect(chunks).toBe(2)
  expect(JSON.stringify(result)).not.toMatch(/capability|access_token|ownerKey/)
  for (const [,init] of fetcher.mock.calls) expect(init.credentials).toBe('same-origin')
})

it('product configuration cannot opt into legacy backend or intermediary destinations', async () => {
  const { readBrowserConfig } = await import('../config')
  expect(readBrowserConfig({ VITE_API_BASE_URL: 'http://outside.invalid', VITE_ANALYTICS_BASE_URL: 'http://outside.invalid' })).toEqual({ mode: 'backend', baseUrl: '', analyticsBaseUrl: '' })
})
it('dashboard timeout aborts cookie transport without manufacturing fallback metrics', async () => {
  vi.useFakeTimers()
  try {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response('{}')).mockImplementationOnce((_url: string, init: RequestInit) => new Promise((_resolve, reject) => { init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true }) }))
    vi.stubGlobal('fetch', fetcher)
    const { ensureBrowserSession } = await import('../api/browserSession')
    await ensureBrowserSession()
    const { readBrowserDashboard } = await import('../api/browserDashboard')
    const result = readBrowserDashboard('overview', { payload, descriptor: null, provenance: null }, '', {}, new AbortController().signal)
    const rejected = expect(result).rejects.toThrow('Quá thời gian chờ API dashboard.')
    await vi.advanceTimersByTimeAsync(30001)
    await rejected
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(fetcher.mock.calls[1]?.[1]?.signal.aborted).toBe(true)
  } finally { vi.useRealTimers() }
})