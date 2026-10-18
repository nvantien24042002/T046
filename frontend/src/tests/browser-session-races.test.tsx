import { BrowserDatasetPanel } from '../components/BrowserDatasetPanel'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { BrowserSessionProvider } from '../context/BrowserSessionProvider'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
const mock = vi.hoisted(() => ({ json: vi.fn(), result: vi.fn(), preview: vi.fn() }))
vi.mock('../api/browserSession', () => ({ browserJson: mock.json, loadBrowserResult: mock.result, browserPreview: mock.preview }))
const payload = (id: string) => ({ dataset_id: id, request_fingerprint: 'a'.repeat(64), quality_rows: [], audit_rows: [], classes: [], attribute_distribution: [], distribution_images: [], distribution_boxes: [], attributes: [] })
const summary = (id: string) => ({ dataset_id: id, upload_id: 'upload-'+id, state: 'ready' as const, ready: true as const, archive_bytes: 3, archive_sha256: 'b'.repeat(64), entries: 1, images: 1, annotations: false, capabilities: { image_quality: true, label_audit: false, distribution: false, coverage: false, scene_finder: false } })
const detail = (id: string) => ({ payload: payload(id), descriptor: null, provenance: null })
function Probe() {
  const state = useVerifiedResults()!
  return <><button onClick={() => { void state.loadResults('a', '') }}>A</button><button onClick={() => { void state.loadResults('b', '') }}>B</button><button onClick={state.clearResults}>Clear</button><output data-testid="selection">{state.activeResult?.payload.dataset_id ?? 'none'}</output></>
}
afterEach(() => { mock.json.mockReset(); mock.result.mockReset(); mock.preview.mockReset() })
it('ignores a late dataset response even when the transport ignores AbortSignal', async () => {
  let resolveA!: (value: ReturnType<typeof detail>) => void
  let resolveB!: (value: ReturnType<typeof detail>) => void
  mock.json.mockImplementation(async (path: string) => summary(path.includes('/a/') ? 'a' : 'b'))
  mock.result.mockImplementation((id: string) => new Promise((resolve) => { if (id === 'a') resolveA = resolve; else resolveB = resolve }))
  render(<BrowserSessionProvider><Probe /></BrowserSessionProvider>)
  fireEvent.click(screen.getByText('A')); await waitFor(() => expect(mock.result).toHaveBeenCalledTimes(1))
  fireEvent.click(screen.getByText('B')); await waitFor(() => expect(mock.result).toHaveBeenCalledTimes(2))
  await act(async () => resolveB(detail('b')))
  expect(screen.getByTestId('selection')).toHaveTextContent('b')
  await act(async () => resolveA(detail('a')))
  expect(screen.getByTestId('selection')).toHaveTextContent('b')
})
it('clearing selection invalidates an in-flight response without fallback data', async () => {
  let finish!: (value: ReturnType<typeof detail>) => void
  mock.json.mockResolvedValue(summary('a'))
  mock.result.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  render(<BrowserSessionProvider><Probe /></BrowserSessionProvider>)
  fireEvent.click(screen.getByText('A')); await waitFor(() => expect(mock.result).toHaveBeenCalledTimes(1))
  fireEvent.click(screen.getByText('Clear'))
  await act(async () => finish(detail('a')))
  expect(screen.getByTestId('selection')).toHaveTextContent('none')
})
it('refreshes the owned dataset catalog after publication within the same request revision', async () => {
  let published = false
  mock.json.mockImplementation(async () => ({ datasets: published ? [{ dataset_id: 'a', state: 'ready' }] : [] }))
  function Publisher() {
    const state = useVerifiedResults()!
    return <><button onClick={() => state.beginRequest()}>Begin upload</button><button onClick={() => { published = true; state.setManualResult(detail('a'), summary('a'), undefined, state.revision) }}>Publish result</button></>
  }
  render(<BrowserSessionProvider><BrowserDatasetPanel /><Publisher /></BrowserSessionProvider>)
  await waitFor(() => expect(mock.json).toHaveBeenCalledTimes(1))
  fireEvent.click(screen.getByText('Begin upload'))
  await waitFor(() => expect(mock.json).toHaveBeenCalledTimes(2))
  fireEvent.click(screen.getByText('Publish result'))
  expect(await screen.findByRole('button', { name: /Bộ dữ liệu 1/ })).toBeVisible()
  expect(mock.json).toHaveBeenCalledTimes(3)
})
it('hides internal identities while selecting datasets with their original IDs', async () => {
  const first = '1d072a13-c0b7-43c7-9053-1801f0732dc3'
  const second = '998c79a5-404c-483e-b81f-eebca8c3e995'
  mock.json.mockImplementation(async (path: string) => path === '/datasets'
    ? { datasets: [{ dataset_id: first, state: 'ready' }, { dataset_id: second, state: 'ready' }] }
    : summary(second))
  mock.result.mockResolvedValue(detail(second))
  const { container } = render(<BrowserSessionProvider><BrowserDatasetPanel /></BrowserSessionProvider>)
  fireEvent.click(await screen.findByRole('button', { name: 'Bộ dữ liệu 2' }))
  await waitFor(() => expect(mock.result).toHaveBeenCalledWith(second, expect.any(AbortSignal)))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Bộ dữ liệu 2' })).toHaveAttribute('aria-pressed', 'true'))
  expect(container.innerHTML).not.toContain(first)
  expect(container.innerHTML).not.toContain(second)
  expect(screen.queryByText(/Làm mới danh sách session|Phiên Dataset/)).not.toBeInTheDocument()
})
