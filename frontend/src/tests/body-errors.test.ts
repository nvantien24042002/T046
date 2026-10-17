import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, createApiClient } from '../api/client'

afterEach(() => vi.useRealTimers())

function stalledBody(status = 200) {
  let markBodyStarted!: () => void
  const bodyStarted = new Promise<void>((resolve) => { markBodyStarted = resolve })
  const transport: typeof fetch = async (_url, options) => {
    const response = new Response(new ReadableStream({
      start(controller) {
        options?.signal?.addEventListener('abort', () => {
          controller.error(new DOMException('Body aborted', 'AbortError'))
        }, { once: true })
      },
    }), { status })
    const originalJson = response.json.bind(response)
    response.json = () => { markBodyStarted(); return originalJson() }
    return response
  }
  return { bodyStarted, transport }
}

describe('response-body error classification', () => {
  it.each([200, 500])('preserves body timeout after HTTP %i headers and cleans up the deadline', async (status) => {
    vi.useFakeTimers()
    const { transport, bodyStarted } = stalledBody(status)
    const request = createApiClient('http://localhost', transport).get('/health', (value) => value, { timeoutMs: 100 }).catch((error: unknown) => error)
    const expected = {
      name: 'ApiError', kind: 'timeout', status: undefined,
      message: 'Backend request timed out. Its server-side outcome is unknown.',
    }
    await bodyStarted
    await vi.advanceTimersByTimeAsync(100)
    const error: unknown = await request
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject(expected)
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each([200, 500])('preserves cancellation during HTTP %i body consumption before the deadline', async (status) => {
    vi.useFakeTimers()
    const { transport, bodyStarted } = stalledBody(status)
    const controller = new AbortController()
    const request = createApiClient('http://localhost', transport).get('/health', (value) => value, { signal: controller.signal, timeoutMs: 100 }).catch((error: unknown) => error)
    const expected = {
      name: 'ApiError', kind: 'cancelled', status: undefined,
      message: 'Request stopped in this browser; server work may continue.',
    }
    await bodyStarted
    controller.abort()
    const error: unknown = await request
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject(expected)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('keeps malformed JSON classified as a response error without HTTP-error metadata', async () => {
    const transport = vi.fn<typeof fetch>().mockResolvedValue(new Response('{broken json'))
    const error: unknown = await createApiClient('http://localhost', transport).get('/health', (value) => value).catch((value: unknown) => value)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ kind: 'response', status: undefined, message: 'Backend returned an invalid response.' })
  })
})
