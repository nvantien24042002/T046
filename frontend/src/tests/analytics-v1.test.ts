import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createAnalyticsV1Session, TrackAError } from '../api/analyticsV1'
import { intermediaryOrigin } from '../api/analyticsV1/schemas'

const baseGrant = {
  schema_version: 'analytics.v1', access_token: 'synthetic-bearer', token_type: 'Bearer',
  server_time: '2026-10-17T07:00:00Z', expires_at: '2026-10-17T07:10:00Z',
  dataset_id: 'dataset-a', result_ids: ['result-a'], scopes: ['summary:read', 'results:read'],
}
const baseSummary = {
  schema_version: 'analytics.v1', error_code: null, dataset_id: 'dataset-a', upload_id: 'upload-a',
  state: 'ready', ready: true, archive_bytes: 100, archive_sha256: 'a'.repeat(64), entries: 3, images: 2, annotations: true,
  capabilities: { image_quality: true, label_audit: true, distribution: true, coverage: true, scene_finder: true },
}
const reply = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
const sessions: ReturnType<typeof createAnalyticsV1Session>[] = []
beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
})
afterEach(() => {
  for (const session of sessions) session.dispose()
  sessions.length = 0; vi.restoreAllMocks(); vi.useRealTimers()
})
function harness(fetcher = vi.fn<typeof fetch>()) {
  let mono = 1000, wall = 1_000_000
  const session = createAnalyticsV1Session({ fetcher, now: () => mono, wallNow: () => wall })
  sessions.push(session)
  return { session, fetcher,
    shift(ms: number) { mono += ms; wall += ms },
    setMono(value: number) { mono = value }, setWall(value: number) { wall = value },
    async advance(ms: number) { mono += ms; wall += ms; await vi.advanceTimersByTimeAsync(ms) },
  }
}
function assertCleared(session: ReturnType<typeof createAnalyticsV1Session>) {
  expect(session.getState()).toMatchObject({ status: 'signed-out', selection: null, summary: null })
}

describe('Track A fixed intermediary transport and grant exchange', () => {
  it('does nothing on construction and exposes no Track B operations', () => {
    const h = harness()
    expect(h.fetcher).not.toHaveBeenCalled()
    expect(h.session).not.toHaveProperty('readResults')
    expect(h.session).not.toHaveProperty('issueGrant')
    expect(h.session).not.toHaveProperty('readResult')
  })
  it('uses only approved routes, keeps secrets out of URLs/public state/storage/logs', async () => {
    const h = harness()
    const local = vi.spyOn(Storage.prototype, 'setItem')
    const log = vi.spyOn(console, 'log'), warn = vi.spyOn(console, 'warn'), error = vi.spyOn(console, 'error')
    h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockResolvedValueOnce(reply(baseSummary))
    const listener = vi.fn(); h.session.subscribe(listener)
    await h.session.exchange('synthetic-exchange-code', 'dataset-a')
    await h.session.readSummary('dataset-a')
    expect(h.fetcher).toHaveBeenNthCalledWith(1, intermediaryOrigin + '/analytics/v1/grants/exchange', expect.objectContaining({ method: 'POST', body: '{"exchange_code":"synthetic-exchange-code"}', credentials: 'omit', redirect: 'error', cache: 'no-store' }))
    const summaryCall = h.fetcher.mock.calls[1]!
    expect(summaryCall[0]).toBe(intermediaryOrigin + '/analytics/v1/datasets/dataset-a/summary')
    expect(summaryCall[1]?.headers).toEqual({ Accept: 'application/json', Authorization: 'Bearer synthetic-bearer' })
    const publicValues = JSON.stringify([h.session.getState(), listener.mock.calls])
    expect(publicValues).not.toContain('synthetic-bearer')
    expect(publicValues).not.toContain('synthetic-exchange-code')
    expect(local).not.toHaveBeenCalled(); expect(log).not.toHaveBeenCalled(); expect(warn).not.toHaveBeenCalled(); expect(error).not.toHaveBeenCalled()
  })
  it.each(['http://127.0.0.1:8011', 'https://external.example', 'http://127.0.0.1:8012/path', 'http://localhost:8012'])('rejects unapproved destination %s before transmitting credentials', (origin) => {
    const fetcher = vi.fn<typeof fetch>()
    expect(() => createAnalyticsV1Session({ origin, fetcher })).toThrow(TrackAError)
    expect(fetcher).not.toHaveBeenCalled()
  })
  it.each([
    { ...baseGrant, schema_version: 'analytics.v2' },
    { ...baseGrant, token_type: 'Basic' },
    { ...baseGrant, access_token: 'bad\r\ntoken' },
    { ...baseGrant, scopes: ['results:read'] },
    { ...baseGrant, scopes: ['summary:read'] }, // nonempty result_ids violates summary-only rule
    { ...baseGrant, scopes: [] },
    { ...baseGrant, scopes: ['admin'] },
    { ...baseGrant, server_time: '2026-10-17T07:00:00+00:00' },
    { ...baseGrant, server_time: '2026-02-30T07:00:00Z' },
    { ...baseGrant, expires_at: '2026-10-17T07:11:00Z' },
    { ...baseGrant, expires_at: '2026-10-17T07:00:00Z' },
    { ...baseGrant, dataset_id: 'dataset-b' },
  ])('rejects invalid/versioned grant responses and clears state', async (value) => {
    const h = harness(); h.fetcher.mockResolvedValue(reply(value))
    await expect(h.session.exchange('code', 'dataset-a')).rejects.toBeInstanceOf(TrackAError)
    assertCleared(h.session)
    expect(h.fetcher).toHaveBeenCalledTimes(1)
  })
  it.each([
    { ...baseGrant, scopes: ['summary:read'], result_ids: [] },
    { ...baseGrant, result_ids: [] },
  ])('accepts the approved empty result scopes without exposing results transport', async (value) => {
    const h = harness(); h.fetcher.mockResolvedValue(reply(value))
    await h.session.exchange('code', 'dataset-a')
    expect(h.session.getState().status).toBe('active')
  })
  it('does not replay an exchange after ambiguous network failure', async () => {
    const h = harness(); h.fetcher.mockRejectedValue(new Error('private code token path'))
    const failure = await h.session.exchange('code', 'dataset-a').catch((error: unknown) => error)
    expect(failure).toMatchObject({ kind: 'network' })
    expect(String(failure)).not.toContain('private')
    expect(h.fetcher).toHaveBeenCalledTimes(1); assertCleared(h.session)
  })
})

describe('Track A expiry, suspension and state clearing', () => {
  it('subtracts the whole exchange duration including body consumption', async () => {
    const h = harness()
    const response = reply(baseGrant)
    response.json = async () => { h.shift(2000); return baseGrant }
    h.fetcher.mockResolvedValue(response)
    await h.session.exchange('code', 'dataset-a')
    await h.advance(597_999)
    expect(h.session.getState().status).toBe('active')
    await h.advance(1)
    assertCleared(h.session)
  })
  it('rejects a grant consumed entirely by exchange duration', async () => {
    const h = harness(); const response = reply(baseGrant)
    response.json = async () => { h.shift(600_000); return baseGrant }
    h.fetcher.mockResolvedValue(response)
    await expect(h.session.exchange('code', 'dataset-a')).rejects.toMatchObject({ kind: 'grant-invalid' })
    assertCleared(h.session)
  })
  it('clears selection and displayed protected summary at absolute expiry', async () => {
    const h = harness(); h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockResolvedValueOnce(reply(baseSummary))
    await h.session.exchange('code', 'dataset-a'); await h.session.readSummary('dataset-a')
    expect(h.session.getState().summary?.dataset_id).toBe('dataset-a')
    await h.advance(600_000); assertCleared(h.session)
    await expect(h.session.readSummary('dataset-a')).rejects.toBeInstanceOf(TrackAError)
    expect(h.fetcher).toHaveBeenCalledTimes(2)
  })
  it('rechecks expiry before accepting success even when timers have not fired', async () => {
    const h = harness(); const pending = deferred<Response>()
    h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockReturnValueOnce(pending.promise)
    await h.session.exchange('code', 'dataset-a')
    const request = h.session.readSummary('dataset-a').catch((error: unknown) => error)
    h.shift(600_000); pending.resolve(reply(baseSummary))
    expect(await request).toBeInstanceOf(TrackAError); assertCleared(h.session)
  })
  it.each(['monotonic', 'wall'])('invalidates a %s clock rollback instead of extending expiry', async (which) => {
    const h = harness(); h.fetcher.mockResolvedValue(reply(baseGrant))
    await h.session.exchange('code', 'dataset-a')
    if (which === 'monotonic') h.setMono(0); else h.setWall(0)
    await expect(h.session.readSummary('dataset-a')).rejects.toMatchObject({ kind: 'grant-invalid' })
    assertCleared(h.session)
  })
  it.each(['hidden', 'pagehide', 'freeze', 'bfcache'])('invalidates and clears state on %s', async (event) => {
    const h = harness(); h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockResolvedValueOnce(reply(baseSummary))
    await h.session.exchange('code', 'dataset-a'); await h.session.readSummary('dataset-a')
    if (event === 'hidden') { vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden'); document.dispatchEvent(new Event('visibilitychange')) }
    if (event === 'pagehide') window.dispatchEvent(new Event('pagehide'))
    if (event === 'freeze') document.dispatchEvent(new Event('freeze'))
    if (event === 'bfcache') { const restored = new Event('pageshow'); Object.defineProperty(restored, 'persisted', { value: true }); window.dispatchEvent(restored) }
    assertCleared(h.session)
    expect(h.fetcher).toHaveBeenCalledTimes(2)
  })
  it('never restores a grant when the page becomes visible again', async () => {
    const h = harness(); h.fetcher.mockResolvedValue(reply(baseGrant)); await h.session.exchange('code', 'dataset-a')
    window.dispatchEvent(new Event('pagehide')); window.dispatchEvent(new Event('pageshow'))
    await expect(h.session.readSummary('dataset-a')).rejects.toBeInstanceOf(TrackAError)
    assertCleared(h.session)
  })
})

describe('Track A summary integrity, errors, retry and grant generations', () => {
  it.each([
    { ...baseSummary, schema_version: 'analytics.v2' },
    { ...baseSummary, error_code: undefined },
    { ...baseSummary, images: '2' },
    { ...baseSummary, images: 9007199254740992 },
    { ...baseSummary, dataset_id: 'dataset-b' },
    { schema_version: 'analytics.v1', error_code: null, dataset_id: 'dataset-a', upload_id: 'u', state: 'not_requested', ready: false },
  ])('rejects malformed or unsupported summaries without fixture fallback', async (value) => {
    const h = harness(); h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockResolvedValueOnce(reply(value))
    await h.session.exchange('code', 'dataset-a')
    await expect(h.session.readSummary('dataset-a')).rejects.toMatchObject({ kind: 'response' })
    expect(h.session.getState()).toMatchObject({ status: 'error', summary: null })
  })
  it('accepts nonready lifecycle data and required nullable error metadata', async () => {
    const pending = { schema_version: 'analytics.v1', error_code: null, dataset_id: 'dataset-a', upload_id: 'u', state: 'indexing', ready: false }
    const h = harness(); h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockResolvedValueOnce(reply(pending))
    await h.session.exchange('code', 'dataset-a')
    expect(await h.session.readSummary('dataset-a')).toEqual(pending)
  })
  it('denies a dataset outside the grant locally without sending the bearer', async () => {
    const h = harness(); h.fetcher.mockResolvedValue(reply(baseGrant)); await h.session.exchange('code', 'dataset-a')
    await expect(h.session.readSummary('dataset-b')).rejects.toMatchObject({ status: 403, code: 'ACCESS_SCOPE_DENIED' })
    expect(h.fetcher).toHaveBeenCalledTimes(1)
    expect(h.session.getState().summary).toBeNull()
  })
  it('clears current grant and protected state on observed 401 invalidation', async () => {
    const h = harness(); h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockResolvedValueOnce(reply(baseSummary)).mockResolvedValueOnce(reply({ error: { code: 'ACCESS_GRANT_INVALID' } }, 401))
    await h.session.exchange('code', 'dataset-a'); await h.session.readSummary('dataset-a')
    await expect(h.session.readSummary('dataset-a')).rejects.toMatchObject({ status: 401, code: 'ACCESS_GRANT_INVALID' })
    assertCleared(h.session)
  })
  it('supports a manual summary retry after an error without replaying exchange', async () => {
    const h = harness(); const states: string[] = []; h.session.subscribe((state) => states.push(state.status))
    h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockResolvedValueOnce(reply({ error: { code: 'BACKEND_UNAVAILABLE' } }, 503)).mockResolvedValueOnce(reply(baseSummary))
    await h.session.exchange('code', 'dataset-a')
    await expect(h.session.readSummary('dataset-a')).rejects.toMatchObject({ code: 'BACKEND_UNAVAILABLE' })
    expect(h.session.getState().summary).toBeNull()
    await h.session.readSummary('dataset-a')
    expect(states).toEqual(['signed-out', 'signed-out', 'exchanging', 'active', 'loading-summary', 'error', 'loading-summary', 'active'])
    expect(h.fetcher).toHaveBeenCalledTimes(3)
  })
  it.each(['success', 'failure'])('discards stale summary %s after a replacement grant', async (outcome) => {
    const h = harness(); const old = deferred<Response>()
    h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockReturnValueOnce(old.promise).mockResolvedValueOnce(reply({ ...baseGrant, access_token: 'replacement' })).mockResolvedValueOnce(reply(baseSummary))
    await h.session.exchange('code', 'dataset-a')
    const obsolete = h.session.readSummary('dataset-a').catch((error: unknown) => error)
    await h.session.exchange('new-code', 'dataset-a'); await h.session.readSummary('dataset-a')
    if (outcome === 'success') old.resolve(reply({ ...baseSummary, images: 99 })); else old.resolve(reply({ error: { code: 'ACCESS_GRANT_INVALID' } }, 401))
    expect(await obsolete).toMatchObject({ kind: 'stale' })
    expect(h.session.getState()).toMatchObject({ status: 'active', summary: { images: 2 } })
  })
  it('discards an exchange completed after its generation was invalidated', async () => {
    const h = harness(); const old = deferred<Response>(); h.fetcher.mockReturnValue(old.promise)
    const obsolete = h.session.exchange('code', 'dataset-a').catch((error: unknown) => error)
    window.dispatchEvent(new Event('pagehide')); old.resolve(reply(baseGrant))
    expect(await obsolete).toMatchObject({ kind: 'stale' }); assertCleared(h.session)
  })
  it('sanitizes unknown errors and does not retain raw response bodies', async () => {
    const h = harness(); h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockResolvedValueOnce(reply({ error: { code: 'secret/raw/path', message: 'synthetic-bearer' } }, 500))
    await h.session.exchange('code', 'dataset-a')
    const error = await h.session.readSummary('dataset-a').catch((value: unknown) => value)
    expect(error).toMatchObject({ kind: 'http', status: 500, code: undefined })
    expect(JSON.stringify(h.session.getState())).not.toMatch(/secret|raw\/path|synthetic-bearer/)
  })
})
describe('Track A additional race and timeout regression cases', () => {
  it.each(['success', 'failure'])('discards stale exchange %s after a newer handoff', async (outcome) => {
    const h = harness(); const old = deferred<Response>()
    h.fetcher.mockReturnValueOnce(old.promise).mockResolvedValueOnce(reply({ ...baseGrant, dataset_id: 'dataset-b', access_token: 'replacement' }))
    const obsolete = h.session.exchange('old-code', 'dataset-a').catch((error: unknown) => error)
    await h.session.exchange('new-code', 'dataset-b')
    if (outcome === 'success') old.resolve(reply(baseGrant)); else old.resolve(reply({ error: { code: 'ACCESS_GRANT_INVALID' } }, 401))
    expect(await obsolete).toMatchObject({ kind: 'stale' })
    expect(h.session.getState()).toMatchObject({ status: 'active', selection: { datasetId: 'dataset-b' } })
  })
  it('ignores an older summary failure after retry in the same grant', async () => {
    const h = harness(); const old = deferred<Response>()
    h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockReturnValueOnce(old.promise).mockResolvedValueOnce(reply(baseSummary))
    await h.session.exchange('code', 'dataset-a')
    const obsolete = h.session.readSummary('dataset-a').catch((error: unknown) => error)
    await h.session.readSummary('dataset-a')
    old.resolve(reply({ error: { code: 'ACCESS_GRANT_INVALID' } }, 401))
    expect(await obsolete).toMatchObject({ kind: 'stale' })
    expect(h.session.getState()).toMatchObject({ status: 'active', summary: { images: 2 } })
  })
  it('does not expose expired protected state when the expiry timer is delayed', async () => {
    const h = harness(); h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockResolvedValueOnce(reply(baseSummary))
    await h.session.exchange('code', 'dataset-a'); await h.session.readSummary('dataset-a')
    h.shift(600_000) // No timer callback is executed.
    assertCleared(h.session)
  })
  it('does not replay a timed-out exchange or expose its code in the error', async () => {
    const h = harness()
    h.fetcher.mockImplementation((_url, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new Error('raw synthetic-exchange-code')), { once: true })
    }))
    const pending = h.session.exchange('synthetic-exchange-code', 'dataset-a').catch((error: unknown) => error)
    await h.advance(10_000)
    expect(await pending).toMatchObject({ kind: 'timeout' })
    expect(String(await pending)).not.toContain('synthetic-exchange-code')
    expect(h.fetcher).toHaveBeenCalledTimes(1); assertCleared(h.session)
  })
  it('preserves timeout classification while consuming an HTTP error body', async () => {
    const h = harness()
    h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockImplementationOnce(async (_url, options) => {
      const response = reply({}, 503)
      response.json = () => new Promise((_resolve, reject) => {
        options?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
      })
      return response
    })
    await h.session.exchange('code', 'dataset-a')
    const pending = h.session.readSummary('dataset-a').catch((error: unknown) => error)
    await Promise.resolve(); await h.advance(10_000)
    expect(await pending).toMatchObject({ kind: 'timeout', status: undefined })
    expect(h.session.getState()).toMatchObject({ status: 'error', summary: null })
  })
  it('allows snapshots to be read without exposing mutable internal state', async () => {
    const h = harness(); h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockResolvedValueOnce(reply(baseSummary))
    await h.session.exchange('code', 'dataset-a'); await h.session.readSummary('dataset-a')
    const snapshot = h.session.getState()
    snapshot.selection!.datasetId = 'other'
    if (snapshot.summary?.ready) snapshot.summary.images = 999
    expect(h.session.getState()).toMatchObject({ selection: { datasetId: 'dataset-a' }, summary: { images: 2 } })
  })
  it('does not let a failed subscriber prevent protected-state clearing', async () => {
    const h = harness(); h.session.subscribe(() => { throw Error('broken view') })
    const publish = vi.fn(); h.session.subscribe(publish)
    h.fetcher.mockResolvedValue(reply(baseGrant)); await h.session.exchange('code', 'dataset-a')
    h.session.invalidate()
    expect(publish).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'signed-out', selection: null, summary: null }))
  })
  it('requires a new handoff after a failed replacement exchange', async () => {
    const h = harness(); h.fetcher.mockResolvedValueOnce(reply(baseGrant)).mockResolvedValueOnce(reply({ error: { code: 'INVALID_ACCESS_REQUEST' } }, 422))
    await h.session.exchange('code', 'dataset-a')
    await expect(h.session.exchange('new-code', 'dataset-a')).rejects.toMatchObject({ status: 422 })
    assertCleared(h.session)
  })
  it('disposes handlers and timers and refuses reuse', async () => {
    const h = harness(); h.fetcher.mockResolvedValue(reply(baseGrant)); await h.session.exchange('code', 'dataset-a')
    h.session.dispose(); assertCleared(h.session)
    expect(vi.getTimerCount()).toBe(0)
    await expect(h.session.exchange('new-code', 'dataset-a')).rejects.toBeInstanceOf(TrackAError)
    expect(h.fetcher).toHaveBeenCalledTimes(1)
  })
})