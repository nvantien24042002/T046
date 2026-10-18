import { useEffect, useRef } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { BrowserSessionProvider } from '../context/BrowserSessionProvider'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { BackendDashboard } from '../components/BackendDashboard'
import { imageConfigurationSchema, type ImageConfiguration } from '../api/imageQualityConfiguration'
import type { DatasetSummary } from '../api/datasets'

vi.mock('../config', () => ({ config: { mode: 'backend', baseUrl: '', analyticsBaseUrl: '' } }))
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
const defaults: ImageConfiguration = { blur_threshold: 100, dark_threshold: 40, bright_threshold: 215,
  contrast_threshold: 25, noise_threshold: 10, min_width: 320, min_height: 240, black_threshold: 20, white_threshold: 245 }
function analysis(dataset = 'dataset-a', fingerprint = 'a'.repeat(64), score = 73) {
  return { dataset_id: dataset, request_fingerprint: fingerprint, quality_rows: [{ filename: 'real-analysis.png', status: 'REVIEW' as const,
    score, issues: ['BLUR'], width: 640, height: 480, size_kb: 12, blur_score: 50, brightness: 123, contrast: 22,
    noise_score: null, black_pct: 1, white_pct: 2, error: '' }], audit_rows: [], classes: [], attribute_distribution: [],
    distribution_images: [], distribution_boxes: [], attributes: [] }
}
function summary(dataset = 'dataset-a'): DatasetSummary {
  return { dataset_id: dataset, upload_id: 'upload-a', ready: true, state: 'ready', images: 1, entries: 1, annotations: false,
    archive_bytes: 12, archive_sha256: 'c'.repeat(64), capabilities: { image_quality: true, label_audit: false, distribution: false, coverage: false, scene_finder: false } }
}
function projection(payload = analysis(), configuration = { ...defaults, blur_threshold: 150 }) {
  return { schema_version: 'dashboard.v1', dataset_id: payload.dataset_id, page: 'quality', state: 'ready', reason: null,
    source: { request_fingerprint: payload.request_fingerprint, result_id: null, payload_sha256: null },
    metrics: [{ key: 'mean_score', label: 'Điểm từ máy chủ', value: payload.quality_rows[0]!.score }], charts: [], controls: [], notes: [],
    tables: [{ key: 'quality', title: 'Image Quality', rows: payload.quality_rows, export_filename: 'quality.csv' },
      { key: 'thresholds', title: 'Ngưỡng thực thi từ backend', rows: Object.entries(configuration).map(([parameter, value]) => ({ parameter, value })), export_filename: null }] }
}
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
function mount(analyze: (configuration: ImageConfiguration) => Promise<Response> = async () => response(analysis('dataset-a', 'b'.repeat(64), 91)), loadDefaults = async () => response({ default: { image_cfg: defaults } })) {
  let currentConfig = { ...defaults, blur_threshold: 150 }
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/session')) return response({})
    if (url.endsWith('/configurations')) return loadDefaults()
    if (url.endsWith('/analyze')) { currentConfig = JSON.parse(String(init?.body)).image_cfg as ImageConfiguration; return analyze(currentConfig) }
    if (url.endsWith('/summary')) return response(summary(url.includes('dataset-b') ? 'dataset-b' : 'dataset-a'))
    if (url.endsWith('/dashboard')) {
      const body = JSON.parse(String(init?.body)) as { expected_fingerprint: string }
      const dataset = url.includes('dataset-b') ? 'dataset-b' : 'dataset-a'
      return response(projection(analysis(dataset, body.expected_fingerprint, dataset === 'dataset-b' ? 88 : body.expected_fingerprint === 'b'.repeat(64) ? 91 : 73), currentConfig))
    }
    throw new Error(`Unexpected path ${url}`)
  })
  vi.stubGlobal('fetch', fetcher)
  function Seed() {
    const state = useVerifiedResults()!
    const once = useRef(false)
    useEffect(() => { if (!once.current) { once.current = true; state.setManualResult({ payload: analysis(), descriptor: null, provenance: null }, summary()) } }, [state])
    return <button onClick={() => state.setManualResult({ payload: analysis('dataset-b'), descriptor: null, provenance: null }, summary('dataset-b'))}>Chọn dataset B</button>
  }
  render(<BrowserSessionProvider><Seed /><BackendDashboard page="quality" /></BrowserSessionProvider>)
  fireEvent.click(screen.getByText('Cấu hình Image Quality và phân tích lại'))
  return fetcher
}

it('loads current producer thresholds, validates them, sends image_cfg and refreshes from the new analysis', async () => {
  let finish!: (value: Response) => void
  const fetcher = mount(() => new Promise<Response>((resolve) => { finish = resolve }))
  await waitFor(() => expect(screen.getByLabelText('Độ nét tối thiểu')).toHaveValue(150))
  fireEvent.change(screen.getByLabelText('Độ nét tối thiểu'), { target: { value: '200' } })
  fireEvent.click(screen.getByText('Phân tích lại với ngưỡng đã chọn'))
  expect(await screen.findByText('Đang phân tích lại dữ liệu…')).toBeVisible()
  expect(screen.queryByText('Phân bố blur score')).not.toBeInTheDocument()
  const call = fetcher.mock.calls.find(([url]) => url.endsWith('/analyze'))!
  expect(call[0]).toBe('/api/browser/v1/datasets/dataset-a/analyze')
  expect(call[1]).toMatchObject({ method: 'POST', credentials: 'same-origin', headers: expect.objectContaining({ 'X-Requested-With': 'N2-05B' }) })
  expect(JSON.parse(String(call[1]?.body))).toEqual({ image_cfg: { ...defaults, blur_threshold: 200 } })
  await act(async () => finish(response(analysis('dataset-a', 'b'.repeat(64), 91))))
  expect(await screen.findByText(/Phân tích hoàn tất lúc/)).toBeVisible()
  expect(await screen.findByRole('heading', { name: 'Phân bố blur score' })).toBeVisible()
  expect(screen.getAllByText('91').length).toBeGreaterThan(0)
  expect(fetcher.mock.calls.some(([url, init]) => url.endsWith('/dashboard') && String(init?.body).includes('b'.repeat(64)))).toBe(true)
})

it.each([
  ['Ngưỡng tối', '256'], ['Ngưỡng quá sáng', '20'], ['Chiều rộng tối thiểu', '0'],
  ['Chiều cao tối thiểu', '1.5'], ['Độ nét tối thiểu', '-1'], ['Ngưỡng pixel đen', '250'], ['Ngưỡng nhiễu MAD', ''],
])('rejects invalid %s=%s before sending analyze', async (label, value) => {
  const fetcher = mount()
  await waitFor(() => expect(screen.getByLabelText('Độ nét tối thiểu')).toHaveValue(150))
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
  fireEvent.click(screen.getByText('Phân tích lại với ngưỡng đã chọn'))
  expect(screen.getByRole('alert')).toHaveTextContent('Ngưỡng phải là số hữu hạn')
  expect(fetcher.mock.calls.some(([url]) => url.endsWith('/analyze'))).toBe(false)
})

it('shows analysis errors without preserving stale dashboard metrics', async () => {
  mount(async () => response({ error: { code: 'PIPELINE_EXECUTION_FAILED' } }, 500))
  await waitFor(() => expect(screen.getByLabelText('Độ nét tối thiểu')).toHaveValue(150))
  fireEvent.click(screen.getByText('Phân tích lại với ngưỡng đã chọn'))
  expect(await screen.findByText(/Phân tích chưa hoàn tất/)).toBeVisible()
  expect(screen.queryByRole('heading', { name: 'Phân bố blur score' })).not.toBeInTheDocument()
  expect(screen.queryByText(/Phân tích hoàn tất lúc/)).not.toBeInTheDocument()
  expect(screen.getByText('Phân tích lại với ngưỡng đã chọn')).toBeEnabled()
})

it('ignores late analysis results after dataset selection changes', async () => {
  let finish!: (value: Response) => void
  mount(() => new Promise<Response>((resolve) => { finish = resolve }))
  await waitFor(() => expect(screen.getByLabelText('Độ nét tối thiểu')).toHaveValue(150))
  fireEvent.click(screen.getByText('Phân tích lại với ngưỡng đã chọn'))
  await screen.findByText('Đang phân tích lại dữ liệu…')
  fireEvent.click(screen.getByText('Chọn dataset B'))
  await waitFor(() => expect(screen.getAllByText('88').length).toBeGreaterThan(0))
  await act(async () => finish(response(analysis('dataset-a', 'b'.repeat(64), 91))))
  expect(screen.queryByText('91')).not.toBeInTheDocument()
  expect(screen.queryByText(/Phân tích hoàn tất lúc/)).not.toBeInTheDocument()
})

it('validates nonfinite configuration values at the API boundary too', () => {
  expect(imageConfigurationSchema.safeParse({ ...defaults, blur_threshold: Infinity }).success).toBe(false)
  expect(imageConfigurationSchema.safeParse({ ...defaults, noise_threshold: NaN }).success).toBe(false)
})

it('reports missing configuration and retries the actual configuration request', async () => {
  const load = vi.fn().mockResolvedValueOnce(response({ error: { code: 'SERVICE_UNHEALTHY' } }, 503))
    .mockImplementation(async () => response({ default: { image_cfg: defaults } }))
  mount(undefined, load)
  expect(await screen.findByText('Không tải được cấu hình ngưỡng. Hãy thử tải lại cấu hình.')).toBeVisible()
  expect(screen.getByText('Phân tích lại với ngưỡng đã chọn')).toBeDisabled()
  fireEvent.click(screen.getByText('Tải lại cấu hình'))
  await waitFor(() => expect(screen.getByLabelText('Độ nét tối thiểu')).toHaveValue(150))
  expect(load).toHaveBeenCalledTimes(2)
})
