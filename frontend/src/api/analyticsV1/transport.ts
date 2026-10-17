import { z } from 'zod'
import { intermediaryOrigin, grantSchema, decodeSummary } from './schemas'

const codes = z.enum(['ACCESS_GRANT_INVALID', 'ACCESS_SCOPE_DENIED', 'DATASET_NOT_FOUND', 'RESULT_NOT_FOUND',
  'DATASET_NOT_READY', 'RESULT_PROVENANCE_UNAVAILABLE', 'INVALID_ACCESS_REQUEST', 'UPSTREAM_RESULT_INVALID',
  'BACKEND_UNAVAILABLE', 'INTERNAL_SERVER_ERROR'])
export type TrackAErrorKind = 'configuration' | 'http' | 'network' | 'timeout' | 'cancelled' | 'response' | 'grant-invalid' | 'stale'
export class TrackAError extends Error {
  constructor(public readonly kind: TrackAErrorKind, public readonly status?: number, public readonly code?: z.infer<typeof codes>) {
    super(kind === 'stale' ? 'Obsolete request discarded.' : kind === 'grant-invalid' ? 'Access expired or invalidated. A new trusted handoff is required.' : kind === 'timeout' ? 'Request timed out. Do not replay an exchange code; obtain a new trusted handoff.' : kind === 'http' ? `Analytics request failed (HTTP ${status}).` : 'Analytics request could not be completed. Check the approved configuration or obtain a new trusted handoff.')
    this.name = 'TrackAError'
  }
}
// Internal fixed-route client. No generic proxy, issuance, result transport or retry.
export function createTrackATransport(origin: string, fetcher: typeof fetch) {
  if (origin !== intermediaryOrigin) throw new TrackAError('configuration')
  async function request<T>(path: string, decode: (value: unknown) => T, signal: AbortSignal, exchangeCode?: string, token?: string): Promise<T> {
    const controller = new AbortController()
    let timeout = false
    const abort = () => controller.abort()
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
    const timer = setTimeout(() => { timeout = true; controller.abort() }, 10_000)
    try {
      const headers: Record<string, string> = { Accept: 'application/json' }
      if (exchangeCode !== undefined) headers['Content-Type'] = 'application/json'
      if (token !== undefined) headers.Authorization = `Bearer ${token}`
      const options: RequestInit = { method: exchangeCode === undefined ? 'GET' : 'POST', headers, signal: controller.signal,
        credentials: 'omit', redirect: 'error', cache: 'no-store', referrerPolicy: 'no-referrer' }
      if (exchangeCode !== undefined) options.body = JSON.stringify({ exchange_code: exchangeCode })
      const response = await fetcher(origin + path, options)
      let value: unknown
      try { value = await response.json() }
      catch (error) { if (controller.signal.aborted) throw error; if (response.ok) throw new TrackAError('response') }
      if (!response.ok) {
        const parsed = z.object({ error: z.object({ code: codes }) }).safeParse(value)
        throw new TrackAError('http', response.status, parsed.success ? parsed.data.error.code : undefined)
      }
      try { return decode(value) } catch { throw new TrackAError('response') }
    } catch (error) {
      if (error instanceof TrackAError) throw error
      if (timeout) throw new TrackAError('timeout')
      if (controller.signal.aborted) throw new TrackAError('cancelled')
      throw new TrackAError('network')
    } finally {
      clearTimeout(timer); signal.removeEventListener('abort', abort)
    }
  }
  return {
    exchange(code: string, signal: AbortSignal) {
      return request('/analytics/v1/grants/exchange', (value) => grantSchema.parse(value), signal, code)
    },
    summary(datasetId: string, token: string, signal: AbortSignal) {
      if (datasetId === '.' || datasetId === '..') throw new TrackAError('response')
      return request('/analytics/v1/datasets/' + encodeURIComponent(datasetId) + '/summary', decodeSummary, signal, undefined, token)
    },
  }
}
