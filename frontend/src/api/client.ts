import { normalizeApiFailure, type KnownErrorCode, type ErrorFormat } from './errors'
export type ErrorKind = 'http' | 'network' | 'timeout' | 'cancelled' | 'response'
export class ApiError extends Error {
  constructor(public readonly kind: ErrorKind, message: string, public readonly status?: number, public readonly code?: KnownErrorCode, public readonly format?: ErrorFormat) {
    super(message)
    this.name = 'ApiError'
  }
}
export type Decoder<T> = (value: unknown) => T
export interface RequestOptions { signal?: AbortSignal; timeoutMs?: number }
export function createApiClient(baseUrl: string, transport: typeof fetch = fetch) {
  const base = baseUrl ? new URL(baseUrl) : new URL('/', globalThis.location?.origin ?? 'http://localhost')
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash) {
    throw new Error('Backend URL must be HTTP(S), without credentials, query or fragment.')
  }
  return {
    async get<T>(path: string, decode: Decoder<T>, options: RequestOptions = {}): Promise<T> {
      if (!path.startsWith('/') || path.startsWith('//')) throw new Error('Expected an API-relative path.')
      const url = new URL(base.toString().replace(/\/$/, '') + path)
      if (url.origin !== base.origin) throw new Error('Cross-origin API path rejected.')
      const controller = new AbortController()
      let timedOut = false
      const abort = () => controller.abort()
      options.signal?.addEventListener('abort', abort, { once: true })
      if (options.signal?.aborted) abort()
      const timer = setTimeout(() => { timedOut = true; controller.abort() }, options.timeoutMs ?? 10_000)
      try {
        const response = await transport(url.toString(), { signal: controller.signal, redirect: 'error', credentials: 'omit', headers: { Accept: 'application/json' } })
        if (!response.ok) {
          let body: unknown
          try { body = await response.json() }
          catch (error) { if (controller.signal.aborted) throw error }
          const failure = normalizeApiFailure(body)
          throw new ApiError('http', `Backend request failed (HTTP ${response.status}).`, response.status, failure.code, failure.format)
        }
        try { return decode(await response.json()) }
        catch (error) {
          // Aborts can occur after headers, while response.json() reads the body.
          // Leave those failures for the timeout/cancellation handler below.
          if (controller.signal.aborted) throw error
          throw new ApiError('response', 'Backend returned an invalid response.')
        }
      } catch (error) {
        if (error instanceof ApiError) throw error
        if (timedOut) throw new ApiError('timeout', 'Backend request timed out. Its server-side outcome is unknown.')
        if (controller.signal.aborted) throw new ApiError('cancelled', 'Request stopped in this browser; server work may continue.')
        throw new ApiError('network', 'Cannot reach the backend. Check its URL, service and allowed origins.')
      } finally {
        clearTimeout(timer)
        options.signal?.removeEventListener('abort', abort)
      }
    },
  }
}
