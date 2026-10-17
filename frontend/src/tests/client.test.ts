import { describe, expect, it, vi } from 'vitest'
import { createApiClient } from '../api/client'
const decode = (value: unknown) => { if (typeof value !== 'object' || value === null || !('status' in value) || value.status !== 'ok') throw Error('schema'); return value }
describe('API boundary', () => {
 it('uses the configured URL without browser credentials', async () => { const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response('{"status":"ok"}')); await createApiClient('http://localhost:8011', transport).get('/health', decode); expect(transport).toHaveBeenCalledWith('http://localhost:8011/health', expect.objectContaining({ redirect: 'error', credentials: 'omit' })) })
 it('rejects secret-bearing URLs', () => { expect(() => createApiClient('http://owner:secret@localhost')).toThrow() })
 it('rejects cross-origin paths', async () => { await expect(createApiClient('http://localhost').get('//example.com', decode)).rejects.toThrow() })
 it.each([403, 422, 500])('normalizes HTTP %i without leaking response content', async (status) => { const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response('private server path', { status })); await expect(createApiClient('http://localhost', transport).get('/health', decode)).rejects.toMatchObject({ kind: 'http', status, message: `Backend request failed (HTTP ${status}).` }) })
 it.each(['not json', '{"unexpected":true}'])('rejects invalid JSON or schema: %s', async (body) => { const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response(body)); await expect(createApiClient('http://localhost', transport).get('/health', decode)).rejects.toMatchObject({ kind: 'response' }) })
 it('keeps network failure an error without mock fallback', async () => { const transport = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('private details')); await expect(createApiClient('http://localhost', transport).get('/health', decode)).rejects.toMatchObject({ kind: 'network' }) })
 const pending: typeof fetch = (_url, options) => new Promise((_resolve, reject) => { const signal = options?.signal; if (signal?.aborted) reject(new DOMException('aborted', 'AbortError')); else signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true }) })
 it('distinguishes timeout', async () => { await expect(createApiClient('http://localhost', pending).get('/health', decode, { timeoutMs: 1 })).rejects.toMatchObject({ kind: 'timeout' }) })
 it('distinguishes browser cancellation', async () => { const controller = new AbortController(); controller.abort(); await expect(createApiClient('http://localhost', pending).get('/health', decode, { signal: controller.signal })).rejects.toMatchObject({ kind: 'cancelled' }) })
})
