import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { BackendDashboard } from '../components/BackendDashboard'
import { ImagePreview } from '../components/ImagePreview'
import { publicCell, publicColumn, publicText } from '../components/presentation'
import { VerifiedResultsContext, type VerifiedResultsState } from '../context/VerifiedResultsContext'
import type { DashboardPage, DashboardView } from '../api/dashboard'

vi.mock('../config', () => ({ config: { mode: 'backend' } }))
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
const pages: DashboardPage[] = ['overview', 'quality', 'labels', 'distribution', 'review', 'reports', 'coverage', 'scene-finder']
function context(page: DashboardPage): VerifiedResultsState {
  const view: DashboardView = { schema_version: 'dashboard.v1', dataset_id: 'opaque-dataset', page, state: 'ready', reason: null,
    source: { request_fingerprint: 'a'.repeat(64), result_id: null, payload_sha256: null },
    metrics: [{ key: 'images', label: 'Ảnh từ backend', value: 731 }],
    charts: [], controls: [], notes: ['Không đọc được private-secret.png'],
    tables: [{ key: 'quality', title: 'Kết quả chất lượng', export_filename: null, rows: [{ filename: 'private-secret.png',
      image_path: '/storage/uploads/private-secret.png', image_name: 'private-secret.png', image_id: 'private-id',
      status: 'REVIEW', score: 42, attributes: { storage_key: 'private-key', label: 'car', comment: 'private-secret.png' } }] }] }
  return { revision: 1, datasetId: 'opaque-dataset', loading: false, error: null, token: null,
    activeResult: { descriptor: null, provenance: null, payload: { dataset_id: 'opaque-dataset', request_fingerprint: 'a'.repeat(64),
      quality_rows: [], audit_rows: [], classes: [], attribute_distribution: [], distribution_images: [], distribution_boxes: [], attributes: [] } },
    activeSummary: { ready: true, annotations: true, capabilities: { image_quality: true, label_audit: true, distribution: true, coverage: true, scene_finder: true } },
    readDashboard: vi.fn().mockResolvedValue(view), fetchPreview: vi.fn().mockResolvedValue(new Blob(['image'], { type: 'image/png' })),
  } as unknown as VerifiedResultsState
}
it.each(['private-secret.png', 'data.zip', 'backup.sqlite3', '/storage/uploads/x', 'C:\\private\\x', 'a'.repeat(64)])('redacts private text %s', (value) => {
  expect(publicText(value)).toBe('Thông tin chi tiết được ẩn')
})
it('keeps backend business scalars and removes nested private fields', () => {
  expect(publicCell({ score: 42, storage_key: 'secret', filename: 'x.png', label: 'car' })).toBe('score: 42, label: car')
  expect(publicColumn('image_id')).toBe(false)
  expect(publicText('REVIEW')).toBe('REVIEW')
  expect(publicText('Bounding box và tín hiệu QA/QC')).toBe('Bounding box và tín hiệu QA/QC')
  expect(publicColumn('decode_error')).toBe(false)
  expect(publicCell(0.25)).toBe('0.25')
})
it.each(pages)('%s keeps backend metrics while suppressing private UI text', async (page) => {
  const { container } = render(<VerifiedResultsContext.Provider value={context(page)}><BackendDashboard page={page} /></VerifiedResultsContext.Provider>)
  expect(await screen.findByText('731')).toBeVisible()
  expect(screen.getByText('42')).toBeVisible()
  expect(container.textContent).not.toMatch(/private-secret|private-id|private-key|opaque-dataset|image_path|filename/)
})
it('opens a preview by clicking the image and handles decode errors without exposing paths', async () => {
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL = vi.fn(() => 'blob:preview')
    static revokeObjectURL = vi.fn()
  })
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true } })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.open = false } })
  const state = context('review')
  const view = await state.readDashboard!('review', state.activeResult!, '', {}, new AbortController().signal)
  view.tables = [{ key: 'preview_images', title: 'Ảnh xem trước', export_filename: null,
    rows: [{ image_name: 'private-secret.png', image_path: '/storage/uploads/private-secret.png', score: 42 }] }]
  vi.mocked(state.readDashboard!).mockResolvedValue(view)
  render(<VerifiedResultsContext.Provider value={state}><BackendDashboard page="review" /></VerifiedResultsContext.Provider>)
  const image = await screen.findByAltText('Ảnh trong bộ dữ liệu CVAT')
  fireEvent.error(image)
  expect(await screen.findByRole('alert')).toHaveTextContent('Không đọc được ảnh xem trước')
  fireEvent.click(screen.getByRole('button', { name: 'Mở ảnh xem trước' }))
  expect(await screen.findByRole('dialog')).toBeVisible()
  expect(document.body.textContent).not.toContain('private-secret.png')
  fireEvent.click(screen.getByRole('button', { name: 'Đóng ảnh' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Mở ảnh xem trước' })).toHaveFocus()
})
it('sanitizes alt text and captions even for callers with private labels', async () => {
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL = vi.fn(() => 'blob:preview')
    static revokeObjectURL = vi.fn()
  })
  render(<VerifiedResultsContext.Provider value={context('review')}><ImagePreview imagePath="x.png" alt="private-secret.png" caption="/storage/private" /></VerifiedResultsContext.Provider>)
  expect(await screen.findByAltText('Ảnh dữ liệu CVAT')).toBeVisible()
  expect(document.body.textContent).not.toContain('/storage/private')
})
