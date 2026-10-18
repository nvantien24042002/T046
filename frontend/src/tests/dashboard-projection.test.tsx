import { useEffect, useRef } from 'react'
import { act, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { readDashboard, type DashboardPage, type DashboardView } from '../api/dashboard'
import { BackendDashboard } from '../components/BackendDashboard'
import { VerifiedResultsProvider, useVerifiedResults, type VerifiedResultsState } from '../context/VerifiedResultsContext'
import type { DatasetSummary } from '../api/datasets'
import type { AnalysisResultDetail } from '../api/verifiedResults'

vi.mock('../config', () => ({ config: { mode: 'backend', baseUrl: 'http://127.0.0.1:8011', analyticsBaseUrl: 'http://127.0.0.1:8012' } }))
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
const result: AnalysisResultDetail = { descriptor: null, provenance: null, payload: {
  dataset_id: 'dataset-a', request_fingerprint: 'a'.repeat(64), quality_rows: [], audit_rows: [], classes: [],
  attribute_distribution: [], distribution_images: [], distribution_boxes: [], attributes: [],
} }
function summary(datasetId = 'dataset-a'): Extract<DatasetSummary, { ready: true }> {
  return { upload_id: 'upload-test', dataset_id: datasetId, state: 'ready', ready: true,
    archive_bytes: 1, archive_sha256: 'c'.repeat(64), entries: 1, images: 431, annotations: true,
    capabilities: { image_quality: true, label_audit: true, distribution: true, coverage: true, scene_finder: true } }
}
function view(page: DashboardPage = 'overview'): DashboardView {
  return { schema_version: 'dashboard.v1', dataset_id: 'dataset-a', page, state: 'ready', reason: null,
    source: { request_fingerprint: 'a'.repeat(64), result_id: null, payload_sha256: null },
    metrics: [{ key: 'server-count', label: 'Ảnh trong archive', value: 431 }], tables: [], charts: [], controls: [], notes: [] }
}
const reply = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })

it('requests the server projection with its actual analysis fingerprint and capability', async () => {
  const fetcher = vi.fn().mockResolvedValue(reply(view()))
  vi.stubGlobal('fetch', fetcher)
  const data = await readDashboard('overview', result, 'test-capability', {}, new AbortController().signal)
  expect(data.metrics[0]?.value).toBe(431)
  expect(fetcher).toHaveBeenCalledWith('http://127.0.0.1:8011/api/v1/datasets/dataset-a/dashboard', expect.objectContaining({
    method: 'POST', credentials: 'omit', headers: expect.objectContaining({ 'Upload-Capability': 'test-capability' }),
    body: JSON.stringify({ page: 'overview', query: {}, expected_fingerprint: 'a'.repeat(64) }),
  }))
})
it.each(['dataset', 'fingerprint', 'page'])('rejects a mismatched %s from the response', async (field) => {
  const data = view()
  if (field === 'dataset') data.dataset_id = 'dataset-b'
  if (field === 'fingerprint') data.source.request_fingerprint = 'b'.repeat(64)
  if (field === 'page') data.page = 'quality'
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(data)))
  await expect(readDashboard('overview', result, 'test', {}, new AbortController().signal)).rejects.toThrow('sai dataset')
})
it('does not silently read the latest cache when the source has no fingerprint', async () => {
  const fetcher = vi.fn()
  vi.stubGlobal('fetch', fetcher)
  const legacy = { ...result, payload: { ...result.payload } }
  delete legacy.payload.request_fingerprint
  await expect(readDashboard('overview', legacy, 'test', {}, new AbortController().signal)).rejects.toThrow('thiếu fingerprint')
  expect(fetcher).not.toHaveBeenCalled()
})
it.each<DashboardPage>(['overview', 'quality', 'labels', 'distribution', 'review', 'reports', 'coverage', 'scene-finder'])('%s renders backend metrics unchanged rather than counting raw payload rows', async (page) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(view(page))))
  function Initializer() {
    const state = useVerifiedResults()
    const once = useRef(false)
    useEffect(() => { if (!once.current) { once.current = true; state?.setManualResult(result, summary(), 'test') } }, [state])
    return null
  }
  render(<VerifiedResultsProvider><Initializer /><BackendDashboard page={page} /></VerifiedResultsProvider>)
  expect(await screen.findByText('431')).toBeVisible()
  expect(screen.queryByText('dataset-a')).not.toBeInTheDocument()
  expect(screen.queryByText('a'.repeat(64))).not.toBeInTheDocument()
  expect(screen.queryByText(/Phiên trực tiếp|Chữ ký & Chẩn đoán|Backend Realtime Engine/)).not.toBeInTheDocument()
  expect(screen.queryByText('0')).not.toBeInTheDocument()
})
it('clears a previous projection while a replacement dataset request is in flight', async () => {
  let state!: VerifiedResultsState
  function Probe() {
    const current = useVerifiedResults()!
    useEffect(() => { state = current }, [current])
    return null
  }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(view())))
  render(<VerifiedResultsProvider><Probe /><BackendDashboard page="overview" /></VerifiedResultsProvider>)
  act(() => state.setManualResult(result, summary(), 'test'))
  await screen.findByText('431')
  act(() => state.beginRequest('dataset-b'))
  expect(screen.queryByText('431')).not.toBeInTheDocument()
  expect(screen.getByRole('status')).toBeVisible()
})
it('reports an unsupported API without returning fixtures', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({}, 404)))
  await expect(readDashboard('overview', result, 'test', {}, new AbortController().signal)).rejects.toThrow('chưa hỗ trợ API')
})

it.each(['headers', 'body'])('times out while awaiting projection %s and aborts transport', async (phase) => {
  vi.useFakeTimers()
  let transportSignal!: AbortSignal
  vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => {
    transportSignal = options.signal as AbortSignal
    const pending = () => new Promise<never>((_resolve, reject) => {
      transportSignal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    })
    return phase === 'headers' ? pending() : Promise.resolve({ ok: true, json: pending })
  }))
  const outcome = readDashboard('overview', result, 'test', {}, new AbortController().signal).catch((error: unknown) => error)
  await vi.advanceTimersByTimeAsync(30_000)
  expect(await outcome).toEqual(expect.objectContaining({ message: 'Quá thời gian chờ API dashboard.' }))
  expect(transportSignal.aborted).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
})

it('shows a missing capability without metrics or raw-payload fallback', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({ ...view(), state: 'unsupported',
    reason: 'Capability image_quality không được hỗ trợ', metrics: [] })))
  function Init() {
    const state = useVerifiedResults()!
    useEffect(() => { state.setManualResult(result, summary(), 'test') }, []) // eslint-disable-line react-hooks/exhaustive-deps
    return null
  }
  render(<VerifiedResultsProvider><Init /><BackendDashboard page="overview" /></VerifiedResultsProvider>)
  expect(await screen.findByText('Capability image_quality không được hỗ trợ')).toBeVisible()
  expect(screen.queryByText('431')).not.toBeInTheDocument()
})

it('ignores a late projection after changing dataset even when fetch ignores abort', async () => {
  let state!: VerifiedResultsState
  let resolveOld!: (response: Response) => void
  const next = { ...view(), dataset_id: 'dataset-b', source: { ...view().source, request_fingerprint: 'b'.repeat(64) },
    metrics: [{ key: 'new', label: 'Backend B', value: 732 }] }
  vi.stubGlobal('fetch', vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveOld = resolve }))
    .mockResolvedValueOnce(reply(next)))
  function Probe() {
    const current = useVerifiedResults()!
    useEffect(() => { state = current }, [current])
    return null
  }
  render(<VerifiedResultsProvider><Probe /><BackendDashboard page="overview" /></VerifiedResultsProvider>)
  act(() => state.setManualResult(result, summary(), 'test'))
  act(() => state.setManualResult({ ...result, payload: { ...result.payload, dataset_id: 'dataset-b', request_fingerprint: 'b'.repeat(64) } }, summary('dataset-b'), 'test'))
  expect(await screen.findByText('732')).toBeVisible()
  await act(async () => { resolveOld(reply(view())) })
  expect(screen.getByText('732')).toBeVisible()
  expect(screen.queryByText('431')).not.toBeInTheDocument()
})

it.each([false, true])('pins immutable result route, bearer and hash (tampered=%s)', async (tampered) => {
  const descriptor = { result_id: 'result-1', dataset_id: 'dataset-a', archive_sha256: 'c'.repeat(64),
    config_id: 'config-1', request_fingerprint: null, configuration_schema_revision: '1',
    effective_config: { image_cfg: {}, label_cfg: {} }, defaults_version: '1', canonicalization_version: '1',
    algorithm_version: '1', producer_build_id: 'test-build', payload_sha256: 'd'.repeat(64), verification: 'verified' as const }
  const data = view()
  data.source = { request_fingerprint: null, result_id: 'result-1', payload_sha256: (tampered ? 'e' : 'd').repeat(64) }
  const fetcher = vi.fn().mockResolvedValue(reply(data))
  vi.stubGlobal('fetch', fetcher)
  const request = readDashboard('overview', { schema_version: 'analytics.v1', payload: result.payload, descriptor,
    provenance: { verification: 'verified', config_id: descriptor.config_id, payload_sha256: descriptor.payload_sha256 } },
  'test-grant', {}, new AbortController().signal)
  if (tampered) await expect(request).rejects.toThrow('sai dataset')
  else expect((await request).source.result_id).toBe('result-1')
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(fetcher).toHaveBeenCalledWith('http://127.0.0.1:8012/analytics/v1/datasets/dataset-a/results/result-1/dashboard',
    expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer test-grant' }),
      body: JSON.stringify({ page: 'overview', query: {}, expected_payload_sha256: 'd'.repeat(64) }) }))
})


it.each<DashboardPage>(['overview', 'review', 'reports'])('%s suppresses unsupported products and never falls back to raw payload KPIs', async (page) => {
  const unsupported: DashboardView = { ...view(page), state: 'unsupported', reason: 'CAPABILITY_DISABLED',
    // Even stale/contradictory product fields must not render for unsupported state.
    metrics: [{ key: 'blocked', label: 'BLOCKED_PRODUCT', value: 987654 }],
    tables: [{ key: 'quality', title: 'BLOCKED_TABLE', rows: [{ score: 73 }], export_filename: 'blocked.csv' }],
    charts: [], can_submit_targets: true }
  const fetcher = vi.fn().mockResolvedValue(reply(unsupported))
  vi.stubGlobal('fetch', fetcher)
  function Initializer() {
    const state = useVerifiedResults()
    const once = useRef(false)
    useEffect(() => { if (!once.current) { once.current = true; state?.setManualResult(result, summary(), 'test') } }, [state])
    return null
  }
  render(<VerifiedResultsProvider><Initializer /><BackendDashboard page={page} /></VerifiedResultsProvider>)
  expect(await screen.findByText('CAPABILITY_DISABLED')).toBeVisible()
  expect(screen.queryByText('BLOCKED_PRODUCT')).not.toBeInTheDocument()
  expect(screen.queryByText('987654')).not.toBeInTheDocument()
  expect(screen.queryByText('BLOCKED_TABLE')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /CSV/ })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Targets|targets/ })).not.toBeInTheDocument()
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(fetcher.mock.calls[0]?.[0]).toMatch(/\/dashboard$/)
})

it.each<DashboardPage>(['overview', 'review', 'reports'])('%s preserves an empty capability-gated projection without deriving a zero KPI', async (page) => {
  const projected: DashboardView = { ...view(page), metrics: [], charts: [], tables: [], notes: ['CAPABILITY_PRODUCT_OMITTED'] }
  const fetcher = vi.fn().mockResolvedValue(reply(projected))
  vi.stubGlobal('fetch', fetcher)
  function Initializer() {
    const state = useVerifiedResults()
    const once = useRef(false)
    useEffect(() => { if (!once.current) { once.current = true; state?.setManualResult(result, summary(), 'test') } }, [state])
    return null
  }
  render(<VerifiedResultsProvider><Initializer /><BackendDashboard page={page} /></VerifiedResultsProvider>)
  expect(await screen.findByText('CAPABILITY_PRODUCT_OMITTED')).toBeVisible()
  expect(screen.queryByText('0')).not.toBeInTheDocument()
  expect(screen.queryByRole('group', { name: 'Anh trong archive' })).not.toBeInTheDocument()
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(fetcher.mock.calls[0]?.[0]).toMatch(/\/dashboard$/)
})

const compositePages: DashboardPage[] = ['overview', 'review', 'reports']
const capabilityKeys = ['image_quality', 'label_audit', 'distribution', 'coverage', 'scene_finder'] as const
it.each(compositePages.flatMap((page) => capabilityKeys.map((capability) => ({ page, capability }))))(
  '$page handles disabled $capability without deriving replacement products', async ({ page, capability }) => {
    const checked = summary()
    checked.capabilities[capability] = false
    const blocked = capability === 'image_quality' || capability === 'label_audit' || (capability === 'distribution' && page !== 'review')
    const fetcher = vi.fn().mockResolvedValue(reply(view(page)))
    vi.stubGlobal('fetch', fetcher)
    let state!: VerifiedResultsState
    function Probe() {
      const current = useVerifiedResults()!
      useEffect(() => { state = current }, [current])
      return null
    }
    render(<VerifiedResultsProvider><Probe /><BackendDashboard page={page} /></VerifiedResultsProvider>)
    const raw = { ...result, payload: { ...result.payload,
      classes: [{ class: 'RAW_SENTINEL', boxes: 998877, images: 1, share_pct: 100 }] } }
    act(() => state.setManualResult(raw, checked, 'test'))
    if (blocked) {
      expect(screen.getByText(/Backend chưa hỗ trợ capability:/)).toHaveTextContent(capability)
      expect(fetcher).not.toHaveBeenCalled()
      expect(screen.queryByText('431')).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /CSV/ })).not.toBeInTheDocument()
    } else {
      expect(await screen.findByText('431')).toBeVisible()
      expect(fetcher).toHaveBeenCalledTimes(1)
    }
    expect(screen.queryByText('RAW_SENTINEL')).not.toBeInTheDocument()
    expect(screen.queryByText('998877')).not.toBeInTheDocument()
  })

it.each(compositePages)('%s fails closed without capability summary', (page) => {
  const fetcher = vi.fn()
  vi.stubGlobal('fetch', fetcher)
  let state!: VerifiedResultsState
  function Probe() {
    const current = useVerifiedResults()!
    useEffect(() => { state = current }, [current])
    return null
  }
  render(<VerifiedResultsProvider><Probe /><BackendDashboard page={page} /></VerifiedResultsProvider>)
  act(() => state.setManualResult(result, null, 'test'))
  expect(screen.getByText(/Chưa có summary capability/)).toBeVisible()
  expect(fetcher).not.toHaveBeenCalled()
})

it.each(compositePages)('%s removes an already visible projection when replacement disables capability', async (page) => {
  const fetcher = vi.fn().mockResolvedValue(reply(view(page)))
  vi.stubGlobal('fetch', fetcher)
  let state!: VerifiedResultsState
  function Probe() {
    const current = useVerifiedResults()!
    useEffect(() => { state = current }, [current])
    return null
  }
  render(<VerifiedResultsProvider><Probe /><BackendDashboard page={page} /></VerifiedResultsProvider>)
  act(() => state.setManualResult(result, summary(), 'test'))
  await screen.findByText('431')
  const disabled = summary('dataset-b')
  disabled.capabilities.image_quality = false
  act(() => state.setManualResult({ ...result, payload: { ...result.payload, dataset_id: 'dataset-b' } }, disabled, 'test'))
  expect(screen.queryByText('431')).not.toBeInTheDocument()
  expect(screen.getByText(/Backend chưa hỗ trợ capability:/)).toBeVisible()
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it.each(compositePages)('%s renders images-only projections without requiring annotation capabilities', async (page) => {
  const checked = summary()
  checked.annotations = false
  checked.capabilities.label_audit = false
  checked.capabilities.distribution = false
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(view(page))))
  let state!: VerifiedResultsState
  function Probe() {
    const current = useVerifiedResults()!
    useEffect(() => { state = current }, [current])
    return null
  }
  render(<VerifiedResultsProvider><Probe /><BackendDashboard page={page} /></VerifiedResultsProvider>)
  act(() => state.setManualResult(result, checked, 'test'))
  expect(await screen.findByText('431')).toBeVisible()
})
