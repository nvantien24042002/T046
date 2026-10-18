import { useEffect, useRef } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { BrowserSessionProvider } from '../context/BrowserSessionProvider'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { BackendDashboard } from '../components/BackendDashboard'
import type { CoverageTarget } from '../api/coverageTargets'

vi.mock('../config', () => ({ config: { mode: 'backend', baseUrl: '', analyticsBaseUrl: '' } }))
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
const target: CoverageTarget = { name: 'rain', filters: { weather: 'rain' }, unit: 'images', target: 9, weight: 2 }
const fingerprint = 'a'.repeat(64)
function mount(failEvaluation = false) {
  const payload = { dataset_id: 'dataset-a', request_fingerprint: fingerprint, quality_rows: [], audit_rows: [], classes: [], attribute_distribution: [],
    distribution_images: [{ image_id: '1', image_name: 'a.png', weather: 'rain', timeofday: 'missing' }], distribution_boxes: [], attributes: [] }
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/session')) return new Response('{}')
    if (!url.endsWith('/dashboard')) throw new Error('Unexpected endpoint')
    const request = JSON.parse(String(init?.body)) as { query: { targets?: CoverageTarget[]; generate?: unknown } }
    const targets = request.query.generate ? [target] : request.query.targets ?? []
    if (failEvaluation && request.query.targets) return new Response('{"error":{"code":"INVALID_DASHBOARD_REQUEST"}}', { status: 422 })
    return new Response(JSON.stringify({ schema_version: 'dashboard.v1', dataset_id: 'dataset-a', page: 'coverage',
      state: targets.length ? 'ready' : 'no_results', reason: targets.length ? null : 'Chưa có targets',
      source: { request_fingerprint: fingerprint, result_id: null, payload_sha256: null }, metrics: [], charts: [], controls: [], notes: [], can_submit_targets: true,
      tables: targets.length ? [{ key: 'targets', title: 'Targets người dùng', rows: targets, export_filename: 'targets.csv' },
        { key: 'evaluation', title: 'Coverage theo targets', rows: [{ slice: 'rain', unit: 'images', support: 3, target: 9, gap: 6, attainment_pct: 33.3, weighted_gap: 1.3333, status: 'Thiếu' }], export_filename: 'results.csv' }] : [] }))
  })
  vi.stubGlobal('fetch', fetcher)
  function Seed() {
    const state = useVerifiedResults()!; const once = useRef(false)
    useEffect(() => { if (!once.current) { once.current = true; state.setManualResult({ payload, descriptor: null, provenance: null }, {
      dataset_id: 'dataset-a', upload_id: 'upload-a', ready: true, state: 'ready', images: 1, entries: 1, annotations: true,
      archive_bytes: 12, archive_sha256: 'c'.repeat(64), capabilities: { image_quality: true, label_audit: true, distribution: true, coverage: true, scene_finder: true },
    }) } }, [state])
    return null
  }
  render(<BrowserSessionProvider><Seed /><BackendDashboard page="coverage" /></BrowserSessionProvider>)
  return fetcher
}

it('uses the production cookie dashboard API to preview, confirm and evaluate generated targets', async () => {
  const fetcher = mount()
  expect(await screen.findByText('Cấu hình Coverage & Slice')).toBeVisible()
  fireEvent.click(screen.getByText('Sinh tổ hợp từ dữ liệu máy chủ'))
  fireEvent.click(screen.getByLabelText('Sinh theo weather'))
  fireEvent.click(screen.getByText('Xem trước tổ hợp từ backend'))
  await screen.findByText('Máy chủ trả 1 target dự kiến; đang chọn 1')
  expect(screen.getByText('Chưa có kết quả theo targets để vẽ biểu đồ.')).toBeVisible()
  const generateCall = fetcher.mock.calls.find(([, init]) => String(init?.body).includes('"generate"'))!
  expect(generateCall[0]).toBe('/api/browser/v1/datasets/dataset-a/dashboard')
  expect(generateCall[1]).toMatchObject({ credentials: 'same-origin', method: 'POST' })
  expect(JSON.parse(String(generateCall[1]?.body))).toEqual({ page: 'coverage', expected_fingerprint: fingerprint,
    query: { generate: { dimensions: ['weather'], unit: 'images', target: 100, weight: 1, include_unobserved: false } } })
  fireEvent.click(screen.getByText('Xác nhận thay thế targets'))
  expect(await screen.findByText('Đang áp dụng 1 target từ kết quả máy chủ.')).toBeVisible()
  const table = screen.getByRole('table', { name: 'Coverage theo targets' })
  expect(within(table).getByText('33.3')).toBeVisible()
  expect(within(table).getByText('1.3333')).toBeVisible()
  expect(within(table).getByText('Thiếu')).toBeVisible()
  const call = fetcher.mock.calls.find(([, init]) => String(init?.body).includes('"targets"'))!
  expect(JSON.parse(String(call[1]?.body)).query).toEqual({ targets: [target] })
})

it('exposes API validation errors and does not keep an old coverage chart visible', async () => {
  mount(true)
  await screen.findByText('Cấu hình Coverage & Slice')
  fireEvent.click(screen.getByText('Nhập targets JSON'))
  fireEvent.change(screen.getByLabelText('Targets do người dùng cung cấp (JSON)'), { target: { value: JSON.stringify([target]) } })
  fireEvent.click(screen.getByText('Áp dụng JSON targets'))
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('HTTP 422'))
  expect(screen.queryByRole('table', { name: 'Coverage theo targets' })).not.toBeInTheDocument()
  expect(screen.getByText('Đánh giá targets tại backend')).toBeEnabled()
})
