import { createTrackATransport, TrackAError } from './transport'
import { intermediaryOrigin, type MemoryGrant, type AnalyticsSummary } from './schemas'

export interface TrackAState {
  status: 'signed-out' | 'exchanging' | 'active' | 'loading-summary' | 'error'
  selection: { datasetId: string } | null
  summary: AnalyticsSummary | null
  error: { kind: string; message: string; status?: number; code?: string } | null
}
export interface TrackAOptions {
  origin?: string
  fetcher?: typeof fetch
  now?: () => number
  wallNow?: () => number
}
// Create explicitly from an approved handoff integration. Importing this module does
// not start requests or install global handlers. Tokens never enter public state.
export function createAnalyticsV1Session(options: TrackAOptions = {}) {
  const transport = createTrackATransport(options.origin ?? intermediaryOrigin, options.fetcher ?? fetch)
  const now = options.now ?? (() => performance.now())
  const wallNow = options.wallNow ?? (() => Date.now())
  let state: TrackAState = { status: 'signed-out', selection: null, summary: null, error: null }
  let grant: MemoryGrant | undefined
  let generation = 0, requestGeneration = 0
  let deadline = 0, wallDeadline = 0
  let lastMono = now(), lastWall = wallNow()
  let expiryTimer: ReturnType<typeof setTimeout> | undefined
  let disposed = false
  const pending = new Set<AbortController>()
  const listeners = new Set<(state: TrackAState) => void>()
  const getState = (): TrackAState => {
    // Reading a snapshot is also a display boundary; timers may have been delayed.
    if (grant) { try { currentGrant() } catch { /* protected state already cleared */ } }
    return structuredClone(state)
  }
  function publish(next: TrackAState) {
    state = next
    // A failing view subscriber must not prevent security state clearing or other views.
    for (const listener of listeners) { try { listener(getState()) } catch { /* observer isolation */ } }
  }
  function reset(error: TrackAError | null = null) {
    generation++; requestGeneration++; grant = undefined; deadline = 0; wallDeadline = 0
    clearTimeout(expiryTimer); expiryTimer = undefined
    for (const controller of pending) controller.abort()
    pending.clear()
    publish({ status: 'signed-out', selection: null, summary: null, error: error ? info(error) : null })
  }
  function info(error: TrackAError): NonNullable<TrackAState['error']> {
    return { kind: error.kind, message: error.message, ...(error.status === undefined ? {} : { status: error.status }), ...(error.code === undefined ? {} : { code: error.code }) }
  }
  function observeClocks() {
    const mono = now(), wall = wallNow()
    const valid = Number.isFinite(mono) && Number.isFinite(wall) && mono >= lastMono && wall >= lastWall
    lastMono = mono; lastWall = wall
    if (!valid) { reset(new TrackAError('grant-invalid')); throw new TrackAError('grant-invalid') }
    return { mono, wall }
  }
  function assertForeground() {
    if (disposed || (typeof document !== 'undefined' && document.visibilityState === 'hidden')) {
      reset(new TrackAError('grant-invalid')); throw new TrackAError('grant-invalid')
    }
  }
  function currentGrant() {
    assertForeground()
    const clock = observeClocks()
    if (!grant || clock.mono >= deadline || clock.wall >= wallDeadline) {
      reset(new TrackAError('grant-invalid')); throw new TrackAError('grant-invalid')
    }
    return grant
  }
  function scheduleExpiry() {
    clearTimeout(expiryTimer)
    expiryTimer = setTimeout(() => {
      try { currentGrant(); scheduleExpiry() } catch { /* reset already cleared protected state */ }
    }, Math.max(0, Math.min(deadline - now(), wallDeadline - wallNow())))
  }
  function assertCurrent(epoch: number, request?: number) {
    if (generation !== epoch || (request !== undefined && requestGeneration !== request) || disposed) throw new TrackAError('stale')
  }
  const invalidate = () => reset(new TrackAError('grant-invalid'))
  const onVisibility = () => { if (document.visibilityState === 'hidden') invalidate() }
  const onPageShow = (event: PageTransitionEvent) => { if (event.persisted) invalidate() }
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibility)
    document.addEventListener('freeze', invalidate)
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('pagehide', invalidate)
    window.addEventListener('pageshow', onPageShow)
  }
  return {
    getState,
    subscribe(listener: (state: TrackAState) => void) { listeners.add(listener); try { listener(getState()) } catch { /* observer isolation */ } return () => { listeners.delete(listener) } },
    async exchange(exchangeCode: string, expectedDatasetId: string): Promise<void> {
      // Replacing a grant immediately removes prior selection/displayed data.
      reset()
      assertForeground()
      if (typeof exchangeCode !== 'string' || !exchangeCode || typeof expectedDatasetId !== 'string' || !expectedDatasetId) {
        const error = new TrackAError('response'); reset(error); throw error
      }
      const epoch = generation
      const controller = new AbortController(); pending.add(controller)
      publish({ status: 'exchanging', selection: null, summary: null, error: null })
      const t0 = observeClocks()
      try {
        const candidate = await transport.exchange(exchangeCode, controller.signal)
        assertCurrent(epoch)
        assertForeground()
        const t1 = observeClocks()
        const lifetime = Date.parse(candidate.expires_at) - Date.parse(candidate.server_time)
        // A1 monotonic scheduling, additionally capped by wall elapsed time. Neither
        // clock can move the deadline later; rollback invalidates the generation.
        const remaining = Math.max(0, lifetime - Math.max(t1.mono - t0.mono, t1.wall - t0.wall))
        if (remaining <= 0 || candidate.dataset_id !== expectedDatasetId) throw new TrackAError('grant-invalid')
        grant = candidate; deadline = t1.mono + remaining; wallDeadline = t1.wall + remaining
        publish({ status: 'active', selection: { datasetId: candidate.dataset_id }, summary: null, error: null })
        scheduleExpiry()
      } catch (error) {
        assertCurrent(epoch)
        const safe = error instanceof TrackAError ? error : new TrackAError('response')
        reset(safe)
        throw safe
      } finally { pending.delete(controller) }
    },
    async readSummary(datasetId: string): Promise<AnalyticsSummary> {
      const active = currentGrant()
      const epoch = generation, request = ++requestGeneration
      const controller = new AbortController(); pending.add(controller)
      publish({ status: 'loading-summary', selection: { datasetId: active.dataset_id }, summary: null, error: null })
      try {
        if (datasetId !== active.dataset_id || !active.scopes.includes('summary:read')) throw new TrackAError('http', 403, 'ACCESS_SCOPE_DENIED')
        const summary = await transport.summary(datasetId, active.access_token, controller.signal)
        assertCurrent(epoch, request)
        currentGrant() // Recheck expiry even if timers have not run or the body stalled.
        if (summary.dataset_id !== datasetId) throw new TrackAError('response')
        publish({ status: 'active', selection: { datasetId }, summary, error: null })
        return structuredClone(summary)
      } catch (error) {
        assertCurrent(epoch, request)
        const safe = error instanceof TrackAError ? error : new TrackAError('response')
        currentGrant() // Errors arriving after local expiry must not republish protected state.
        if (safe.status === 401 || safe.code === 'ACCESS_GRANT_INVALID') reset(safe)
        else publish({ status: 'error', selection: { datasetId: active.dataset_id }, summary: null, error: info(safe) })
        throw safe
      } finally { pending.delete(controller) }
    },
    getGrant(): MemoryGrant | undefined {
      if (!grant) return undefined
      try { return currentGrant() } catch { return undefined }
    },
    invalidate,
    dispose() {
      reset(); disposed = true; listeners.clear()
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisibility)
        document.removeEventListener('freeze', invalidate)
      }
      if (typeof window !== 'undefined') {
        window.removeEventListener('pagehide', invalidate)
        window.removeEventListener('pageshow', onPageShow)
      }
    },
  }
}
