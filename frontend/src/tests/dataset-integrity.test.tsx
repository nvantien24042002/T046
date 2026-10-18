import { useEffect, type ReactNode } from 'react'
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { VerifiedResultsProvider, useVerifiedResults, type VerifiedResultsState } from '../context/VerifiedResultsContext'
import { type AnalysisResultDetail, type VerifiedResultDetail, type VerifiedResultsTransport } from '../api/verifiedResults'
import { type DatasetSummary } from '../api/datasets'
import { Overview } from '../reference/Overview'
import { ImageQuality } from '../reference/ImageQuality'
import { LabelCheck } from '../reference/LabelCheck'
import { Distribution } from '../reference/Distribution'
import { Review } from '../reference/Review'
import { Reports } from '../reference/Reports'
import { Coverage, buildUniverse, evaluateTargets } from '../reference/Coverage'
import { SceneFinder } from '../reference/SceneFinder'
import { scopedExport } from '../components/Export'
import { AnalyticsSummaryPanel } from '../components/AnalyticsSummaryPanel'
import { uploadDatasetWorkflow } from '../api/upload'
import baseline from '../mocks/overview-baseline.json'

vi.mock('../config', () => ({ config: { mode: 'backend', baseUrl: 'http://127.0.0.1:8011', analyticsBaseUrl: 'http://127.0.0.1:8012' } }))
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

function analysis(id: string, count = 1): AnalysisResultDetail {
  return { descriptor: null, provenance: null, payload: {
    dataset_id: id,
    quality_rows: Array.from({ length: count }, (_, i) => ({ ...baseline.quality[0]!, status: 'REVIEW' as const, filename: `${id}-${i}.jpg` })),
    audit_rows: [], classes: [{ class: id, boxes: 1, images: 1, share_pct: 100 }],
    attribute_distribution: [], attributes: [],
    distribution_images: Array.from({ length: count }, (_, i) => ({ image_id: String(i), image_name: `${id}-${i}.jpg`, weather: id, timeofday: 'day' })),
    distribution_boxes: [{ image_id: '0', image_name: `${id}-0.jpg`, box_id: '0:0', label: id, occluded: '0', valid_geometry: true, area_pct: 1, attributes: {} }],
  } }
}
function envelope(id: string, resultId = `server-${id}`): VerifiedResultDetail {
  return { schema_version: 'analytics.v1', payload: analysis(id).payload,
    descriptor: { result_id: resultId, dataset_id: id, archive_sha256: 'a'.repeat(64), config_id: 'server-config',
      request_fingerprint: null, configuration_schema_revision: '1', effective_config: { image_cfg: {}, label_cfg: {} },
      defaults_version: '1', canonicalization_version: '1', algorithm_version: '1', producer_build_id: 'server-build',
      payload_sha256: 'b'.repeat(64), verification: 'verified' },
    provenance: { verification: 'verified', config_id: 'server-config', payload_sha256: 'b'.repeat(64) } }
}
function summary(id: string): Extract<DatasetSummary, { ready: true }> {
  return { upload_id: `upload-${id}`, dataset_id: id, state: 'ready', ready: true,
    archive_bytes: 1, archive_sha256: 'a'.repeat(64), entries: 1, images: 1, annotations: true,
    capabilities: { image_quality: true, label_audit: true, distribution: true, coverage: true, scene_finder: true } }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function hook(transport?: VerifiedResultsTransport) {
  return renderHook(() => useVerifiedResults()!, { wrapper: ({ children }) =>
    <VerifiedResultsProvider {...(transport ? { transport } : {})}>{children}</VerifiedResultsProvider> })
}
function transportFor(readResult: VerifiedResultsTransport['readResult']): VerifiedResultsTransport {
  return { listResults: vi.fn(async (id: string) => ({ schema_version: 'analytics.v1' as const, dataset_id: id, results: [envelope(id).descriptor] })),
    readResult, fetchPreview: vi.fn() }
}

describe('dataset identity and concurrent requests', () => {
  it('clears old rows immediately, aborts the old request and ignores its late success', async () => {
    const a = deferred<VerifiedResultDetail>()
    const b = deferred<VerifiedResultDetail>()
    const transport = transportFor(vi.fn((id) => id === 'a' ? a.promise : b.promise))
    const { result } = hook(transport)
    act(() => result.current.setManualResult(analysis('old'), summary('old'), 'old-token'))
    let first!: Promise<void>
    await act(async () => { first = result.current.loadResults('a', 'token-a') })
    expect(result.current.activeResult).toBeNull()
    expect(result.current.activeSummary).toBeNull()
    let second!: Promise<void>
    await act(async () => { second = result.current.loadResults('b', 'token-b', undefined, summary('b')) })
    expect(vi.mocked(transport.readResult).mock.calls[0]?.[3]?.aborted).toBe(true)
    await act(async () => { a.resolve(envelope('a')); await first })
    expect(result.current.loading).toBe(true)
    expect(result.current.datasetId).toBe('b')
    await act(async () => { b.resolve(envelope('b')); await second })
    expect(result.current.activeResult?.payload.dataset_id).toBe('b')
    expect(result.current.activeSummary?.dataset_id).toBe('b')
    expect(result.current.token).toBe('token-b')
  })

  it('ignores an old failure after a new dataset is published', async () => {
    const pending = deferred<VerifiedResultDetail>()
    const { result } = hook(transportFor(() => pending.promise))
    let request!: Promise<void>
    await act(async () => { request = result.current.loadResults('a', 'token') })
    act(() => result.current.setManualResult(analysis('b'), summary('b'), 'b-token'))
    await act(async () => { pending.reject(new Error('old')); await request })
    expect(result.current.activeResult?.payload.dataset_id).toBe('b')
    expect(result.current.error).toBeNull()
  })

  it.each(['clear', 'abort'])('does not republish after %s', async (action) => {
    const pending = deferred<VerifiedResultDetail>()
    const { result } = hook(transportFor(() => pending.promise))
    const controller = new AbortController()
    let request!: Promise<void>
    await act(async () => { request = result.current.loadResults('a', 'token', controller.signal) })
    act(() => { if (action === 'clear') result.current.clearResults(); else controller.abort() })
    expect(result.current.loading).toBe(false)
    await act(async () => { pending.resolve(envelope('a')); await request })
    expect(result.current.activeResult).toBeNull()
    expect(result.current.loading).toBe(false)
  })

  it('selects a result atomically with its dataset and rejects a wrong result ID', async () => {
    const read = vi.fn().mockResolvedValueOnce(envelope('b')).mockResolvedValueOnce(envelope('b', 'wrong'))
    const { result } = hook(transportFor(read))
    act(() => result.current.setManualResult(analysis('a'), summary('a'), 'a-token'))
    await act(async () => { await result.current.selectResult('server-b', 'b', 'b-token') })
    expect(result.current.datasetId).toBe('b')
    expect(result.current.activeSummary).toBeNull()
    expect(result.current.activeResult?.payload.dataset_id).toBe('b')
    await act(async () => { await result.current.selectResult('server-b', 'b', 'b-token') })
    expect(result.current.activeResult).toBeNull()
    expect(result.current.error).toBeTruthy()
  })

  it('rejects a wrong list dataset before reading any detail', async () => {
    const transport = transportFor(vi.fn())
    vi.mocked(transport.listResults).mockResolvedValue({ schema_version: 'analytics.v1', dataset_id: 'other', results: [] })
    const { result } = hook(transport)
    await act(async () => { await result.current.loadResults('a', 'token') })
    expect(transport.readResult).not.toHaveBeenCalled()
    expect(result.current.error).toBeTruthy()
  })

  it('rejects summary mismatches and incomplete payloads without preserving previous rows', () => {
    const { result } = hook()
    act(() => result.current.setManualResult(analysis('a'), summary('a'), 'token'))
    act(() => result.current.setManualResult(analysis('b'), summary('a'), 'token'))
    expect(result.current.activeResult).toBeNull()
    expect(result.current.error).toBeTruthy()
    const partial = analysis('c')
    // Simulate a malformed network response despite the TypeScript declaration.
    Reflect.deleteProperty(partial.payload, 'distribution_boxes')
    act(() => result.current.setManualResult(partial, summary('c'), 'token'))
    expect(result.current.activeResult).toBeNull()
  })

  it('rejects a summary response for a superseded dataset request', () => {
    const { result } = hook()
    let first = 0
    act(() => { first = result.current.beginRequest('a') })
    act(() => result.current.beginRequest('b'))
    act(() => result.current.setSummary(summary('a'), first))
    expect(result.current.datasetId).toBe('b')
    expect(result.current.activeSummary).toBeNull()
  })

  it('rejects an archive mismatch between a server descriptor and summary', async () => {
    const { result } = hook(transportFor(vi.fn(async () => envelope('a'))))
    await act(async () => { await result.current.loadResults('a', 'token', undefined, { ...summary('a'), archive_sha256: 'c'.repeat(64) }) })
    expect(result.current.activeResult).toBeNull()
    expect(result.current.error).toBeTruthy()
  })

  it('invalidates a failed upload and ignores late upload completion', () => {
    const { result } = hook()
    let request = 0
    act(() => result.current.setManualResult(analysis('old'), summary('old'), 'token'))
    act(() => { request = result.current.beginRequest() })
    expect(result.current.activeResult).toBeNull()
    act(() => result.current.failRequest(request, 'Upload failed'))
    act(() => result.current.setManualResult(analysis('new'), summary('new'), 'token', request))
    expect(result.current.activeResult).toBeNull()
    expect(result.current.error).toBe('Upload failed')
  })

  it('does not invent verification, IDs or provenance for direct analysis', () => {
    const { result } = hook()
    act(() => result.current.setManualResult(analysis('a'), summary('a'), 'token'))
    expect(result.current.activeResult?.descriptor).toBeNull()
    expect(result.current.activeResult?.provenance).toBeNull()
    expect(result.current.availableResults).toEqual([])
  })

  it('binds direct preview to the active dataset and never retries it on a different transport', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('', { status: 403 }))
    vi.stubGlobal('fetch', fetcher)
    const transport = transportFor(vi.fn())
    const { result } = hook(transport)
    act(() => result.current.setManualResult(analysis('a'), summary('a'), 'cap-a'))
    await expect(result.current.fetchPreview('a-0.jpg')).rejects.toMatchObject({ status: 403 })
    expect(fetcher.mock.calls[0]?.[0]).toContain('/datasets/a/preview?image_path=a-0.jpg')
    expect(fetcher.mock.calls[0]?.[1].headers).toMatchObject({ 'Upload-Capability': 'cap-a' })
    expect(transport.fetchPreview).not.toHaveBeenCalled()
  })

  it('rejects a late preview from the previous dataset', async () => {
    const response = deferred<Response>()
    vi.stubGlobal('fetch', vi.fn(() => response.promise))
    const { result } = hook()
    act(() => result.current.setManualResult(analysis('a'), summary('a'), 'cap-a'))
    const preview = result.current.fetchPreview('a.jpg')
    const rejected = expect(preview).rejects.toMatchObject({ kind: 'cancelled' })
    act(() => result.current.setManualResult(analysis('b'), summary('b'), 'cap-b'))
    response.resolve(new Response('image'))
    await rejected
  })
})

function mountPage(page: ReactNode) {
  let state!: VerifiedResultsState
  function Probe() {
    const current = useVerifiedResults()!
    useEffect(() => { state = current }, [current])
    return null
  }
  render(<VerifiedResultsProvider><Probe />{page}</VerifiedResultsProvider>)
  return () => state
}

describe('historical reference pages (not production projections)', () => {
  it.each([
    ['Overview', Overview], ['Image Quality', ImageQuality], ['Label Check', LabelCheck],
    ['Distribution', Distribution], ['Review', Review], ['Reports', Reports], ['Coverage', Coverage], ['Scene Finder', SceneFinder],
  ])('%s clears old content on loading and displays API errors without fixtures', (_name, Page) => {
    const state = mountPage(<Page />)
    act(() => state().setManualResult(analysis('dataset-a'), summary('dataset-a'), 'token'))
    expect(screen.getByText('Đang hiển thị kết quả của bộ dữ liệu đã chọn.')).toBeVisible()
    let request = 0
    act(() => { request = state().beginRequest('dataset-b') })
    expect(screen.queryByText('Đang hiển thị kết quả của bộ dữ liệu đã chọn.')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toBeVisible()
    act(() => state().failRequest(request, 'API failed for dataset-b'))
    expect(screen.getByRole('alert')).toHaveTextContent('API failed for dataset-b')
    expect(screen.queryByRole('button', { name: /Xuất CSV|Tải .*CSV/ })).not.toBeInTheDocument()
  })

  it.each([['Review', Review], ['Scene Finder', SceneFinder]])('%s resets pagination on dataset change', (_name, Page) => {
    const state = mountPage(<Page />)
    act(() => state().setManualResult(analysis('a', 8), summary('a'), null))
    fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }))
    expect(screen.getByText(/Trang 2 \/ 2/)).toBeVisible()
    act(() => state().setManualResult(analysis('b'), summary('b'), null))
    expect(screen.getByRole('heading', { level: 3, name: 'b-0.jpg' })).toBeVisible()
    expect(screen.queryByText(/Trang 2/)).not.toBeInTheDocument()
  })

  it('resets scene filters and keyword on a new result of the same dataset', () => {
    const state = mountPage(<SceneFinder />)
    act(() => state().setManualResult(analysis('a'), summary('a'), null))
    fireEvent.change(screen.getByPlaceholderText('Nhập tên ảnh...'), { target: { value: 'no-match' } })
    expect(screen.getByText('Không có ảnh phù hợp với bộ lọc hiện tại.')).toBeVisible()
    act(() => state().setManualResult(analysis('a'), summary('a'), null))
    expect(screen.getByPlaceholderText('Nhập tên ảnh...')).toHaveValue('')
    expect(screen.getByRole('heading', { name: 'a-0.jpg' })).toBeVisible()
  })

  it('resets Review status filters and selected label image when the dataset changes', () => {
    const state = mountPage(<Review />)
    const first = analysis('a')
    first.payload.audit_rows = baseline.audit.map((row) => ({ ...row, image_name: 'a-0.jpg' })) as typeof first.payload.audit_rows
    act(() => state().setManualResult(first, summary('a'), null))
    fireEvent.change(screen.getByLabelText(/Lọc theo trạng thái/), { target: { value: 'ERROR' } })
    act(() => state().setManualResult(first, summary('a'), null))
    expect(screen.getByLabelText(/Lọc theo trạng thái/)).toHaveValue('ALL')
    fireEvent.click(screen.getByRole('radio', { name: /Ảnh có box CVAT/ }))
    fireEvent.change(screen.getByLabelText(/Chọn ảnh/), { target: { value: 'a-0.jpg' } })
    const second = analysis('b')
    second.payload.audit_rows = first.payload.audit_rows.map((row) => ({ ...row, image_name: 'b-0.jpg' }))
    act(() => state().setManualResult(second, summary('b'), null))
    fireEvent.click(screen.getByRole('radio', { name: /Ảnh có box CVAT/ }))
    expect(screen.getByLabelText(/Chọn ảnh/)).toHaveValue('b-0.jpg')
  })

  it('exports only the displayed dataset after switching Reports', async () => {
    const create = vi.fn<(blob: Blob) => string>(() => 'blob:csv')
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: vi.fn() })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const state = mountPage(<Reports />)
    act(() => state().setManualResult(analysis('old'), summary('old'), null))
    act(() => state().setManualResult(analysis('new'), summary('new'), null))
    fireEvent.click(screen.getByRole('button', { name: 'Tải Image Quality CSV' }))
    const blob = create.mock.calls[0]?.[0] as Blob | undefined
    expect(blob).toBeDefined()
    const text = await new Promise<string>((resolve) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.readAsText(blob!)
    })
    expect(text).toContain('dataset_id')
    expect(text).toContain('new-0.jpg')
    expect(text).not.toContain('old-0.jpg')
    expect(text).not.toContain('result_id')
  })

  it('clears Coverage targets when switching datasets', () => {
    const state = mountPage(<Coverage />)
    act(() => state().setManualResult(analysis('a'), summary('a'), null))
    fireEvent.click(screen.getByRole('button', { name: 'Thêm slice' }))
    expect(screen.getByText(/3. Chỉnh mục tiêu theo từng slice/)).toBeVisible()
    act(() => state().setManualResult(analysis('b'), summary('b'), null))
    expect(screen.queryByText(/3. Chỉnh mục tiêu theo từng slice/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Tải coverage CSV' })).not.toBeInTheDocument()
  })

  it('does not recreate the handoff session when uploading a result changes the context', () => {
    const state = mountPage(<><AnalyticsSummaryPanel /><SceneFinder /></>)
    act(() => state().setManualResult(analysis('a'), summary('a'), null))
    expect(state().activeResult?.payload.dataset_id).toBe('a')
    expect(screen.getByRole('heading', { name: 'a-0.jpg' })).toBeVisible()
    expect(screen.getByText('Annotations available')).toBeVisible()
  })
})

describe('historical computation reference (production calculations run at BE)', () => {
  it('includes dataset identity in exports without manufacturing a result ID', () => {
    expect(scopedExport([{ filename: 'b.jpg', dataset_id: 'stale', result_id: 'stale' }], { datasetId: 'b', resultId: null }))
      .toEqual([{ filename: 'b.jpg', dataset_id: 'b' }])
    expect(scopedExport([{}], { datasetId: 'b', resultId: 'server-result' }))
      .toEqual([{ dataset_id: 'b', result_id: 'server-result' }])
  })

  it('matches coverage_dynamic.build_universe: only explicitly valid objects, no fabricated image ID', () => {
    const images = analysis('a').payload.distribution_images
    const valid = analysis('a').payload.distribution_boxes[0]!
    expect(buildUniverse(images, [valid, { ...valid, valid_geometry: false },
      { box_id: 'unknown', label: 'a', occluded: '0' }])).toHaveLength(1)
  })

  it('does not convert zero weight to one or normalize unresolved metadata beyond Python rules', () => {
    const images = [{ image_id: '0', image_name: 'a.jpg', weather: 'UNKNOWN', timeofday: 'day' }]
    const targets = [{ filters: { weather: 'rain' }, unit: 'images' as const, target: 1, weight: 1 }]
    expect(evaluateTargets([], images, targets)[0]?.metadata_unresolved).toBe(0)
    expect(evaluateTargets([], images, [{ ...targets[0]!, weight: 0 }])).toEqual([])
  })

  it('matches the executed Python coverage reference for valid/invalid geometry and missing scene metadata', () => {
    // Captured with backend .venv Python -B, core.coverage_dynamic.evaluate_targets.
    // This numeric reference covers these inputs, not all Python rounding cases.
    const images = [
      { image_id: '0', image_name: '0.jpg', weather: 'sunny', timeofday: 'day' },
      { image_id: '1', image_name: '1.jpg', weather: 'unknown', timeofday: 'day' },
      { image_id: '2', image_name: '2.jpg', weather: 'missing', timeofday: 'night' },
    ]
    const boxes = [
      { image_id: '0', box_id: 'b0', label: 'car', occluded: '0', valid_geometry: true },
      { image_id: '1', box_id: 'b1', label: 'car', occluded: '1', valid_geometry: true },
      { image_id: '0', box_id: 'b2', label: 'bus', occluded: '0', valid_geometry: false },
    ]
    const universe = buildUniverse(images, boxes)
    expect(universe).toHaveLength(2)
    const evaluated = evaluateTargets(universe, images, [
      { filters: { class: 'car' }, unit: 'images', target: 3, weight: 2 },
      { filters: { weather: 'sunny' }, unit: 'objects', target: 3, weight: 1 },
      { filters: { timeofday: 'day' }, unit: 'images', target: 1, weight: 1 },
    ])
    expect(evaluated.map(({ support, gap, attainment_pct, weighted_gap, metadata_unresolved }) =>
      ({ support, gap, attainment_pct, weighted_gap, metadata_unresolved }))).toEqual([
      { support: 2, gap: 1, attainment_pct: 66.7, weighted_gap: 0.6667, metadata_unresolved: 0 },
      { support: 1, gap: 2, attainment_pct: 33.3, weighted_gap: 0.6667, metadata_unresolved: 2 },
      { support: 2, gap: 0, attainment_pct: 200, weighted_gap: 0, metadata_unresolved: 0 },
    ])
  })
})

describe('direct upload response identity (mocked HTTP, not live integration)', () => {
  const reply = (value: unknown) => new Response(JSON.stringify(value), { status: 200 })
  function workflow(analysisId: string, summaryId: string, uploadId = 'upload-a') {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(reply({ upload_id: 'upload-a', upload_capability: 'cap-a', chunk_size_bytes: 8388608, received_bytes: 0 }))
      .mockResolvedValueOnce(reply({ received_bytes: 3 }))
      .mockResolvedValueOnce(reply({ state: 'uploaded' }))
      .mockResolvedValueOnce(reply({ state: 'ready' }))
      .mockResolvedValueOnce(reply({ upload_id: 'upload-a', dataset_id: 'a', state: 'ready', ready: true }))
      .mockResolvedValueOnce(reply(analysis(analysisId).payload))
      .mockResolvedValueOnce(reply({ ...summary(summaryId), upload_id: uploadId }))
    vi.stubGlobal('fetch', fetcher)
    return uploadDatasetWorkflow({ file: new File(['zip'], 'dataset.zip'), ownerKey: 'k'.repeat(32),
      baseUrl: 'http://127.0.0.1:8011', onProgress: vi.fn() })
  }
  it.each([['wrong', 'a', 'upload-a'], ['a', 'wrong', 'upload-a'], ['a', 'a', 'wrong-upload']])(
    'rejects analyze/summary identity mismatch: %s / %s / %s', async (analysisId, summaryId, uploadId) => {
      await expect(workflow(analysisId, summaryId, uploadId)).rejects.toThrow('DATASET_MISMATCH')
    },
  )
  it('retains analysis rows without fabricating a verification envelope', async () => {
    const result = await workflow('a', 'a')
    expect(result.detail.payload.dataset_id).toBe('a')
    expect(result.detail.descriptor).toBeNull()
    expect(result.detail.provenance).toBeNull()
    expect(result.summary.dataset_id).toBe('a')
  })
})
