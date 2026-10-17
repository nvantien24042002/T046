import { StrictMode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AnalyticsSummaryPanel } from '../components/AnalyticsSummaryPanel'
import { readConfig } from '../config'
vi.mock('../config', async (original) => ({ ...await original<typeof import('../config')>(), config: { mode: 'backend', baseUrl: 'http://127.0.0.1:8011', analyticsBaseUrl: 'http://127.0.0.1:8012' } }))
const grant = { schema_version: 'analytics.v1', access_token: 'private-token', token_type: 'Bearer', server_time: '2026-10-17T07:00:00Z', expires_at: '2026-10-17T07:10:00Z', dataset_id: 'dataset-a', result_ids: [], scopes: ['summary:read'] }
const summary = { schema_version: 'analytics.v1', error_code: null, dataset_id: 'dataset-a', upload_id: 'upload-a', state: 'ready', ready: true, archive_bytes: 100, archive_sha256: 'a'.repeat(64), entries: 3, images: 2, annotations: false, capabilities: { image_quality: false, label_audit: false, distribution: false, coverage: false, scene_finder: false } }
const reply = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
let fetcher: ReturnType<typeof vi.fn<typeof fetch>>
beforeEach(() => { fetcher = vi.fn<typeof fetch>(); vi.stubGlobal('fetch', fetcher); vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible') })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
function submit() {
  fireEvent.change(screen.getByLabelText('Expected dataset ID'), { target: { value: 'dataset-a' } })
  fireEvent.change(screen.getByLabelText('Exchange code'), { target: { value: 'trusted-code' } })
  fireEvent.click(screen.getByRole('button', { name: 'Exchange and read summary' }))
}
it('keeps intermediary configuration independent of legacy configuration', () => {
  expect(readConfig({ VITE_API_BASE_URL: 'http://legacy.example' })).toMatchObject({ baseUrl: 'http://legacy.example', analyticsBaseUrl: 'http://127.0.0.1:8012' })
})
it('requires explicit handoff, reads a validated summary under StrictMode and clears protected state on BFCache restoration', async () => {
  fetcher.mockResolvedValueOnce(reply(grant)).mockResolvedValueOnce(reply(summary))
  render(<StrictMode><AnalyticsSummaryPanel /></StrictMode>)
  expect(fetcher).not.toHaveBeenCalled()
  submit()
  expect(screen.getByLabelText('Exchange code')).toHaveValue('')
  await screen.findByText('Annotations available')
  expect(screen.getByText('No', { exact: true })).toBeInTheDocument()
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['http://127.0.0.1:8012/analytics/v1/grants/exchange', 'http://127.0.0.1:8012/analytics/v1/datasets/dataset-a/summary'])
  expect(document.body.textContent).not.toContain('private-token')
  act(() => { const event = new Event('pageshow'); Object.defineProperty(event, 'persisted', { value: true }); window.dispatchEvent(event) })
  expect(screen.queryByText('Annotations available')).not.toBeInTheDocument()
  expect(screen.queryByText('Authorized dataset:')).not.toBeInTheDocument()
  expect(screen.getByRole('alert')).toBeInTheDocument()
})
it('shows sanitized errors without fixtures and retries only summary reads', async () => {
  fetcher.mockResolvedValueOnce(reply(grant)).mockRejectedValueOnce(new Error('SECRET internal failure')).mockResolvedValueOnce(reply(summary))
  render(<AnalyticsSummaryPanel />); submit()
  await screen.findByRole('alert')
  expect(document.body.textContent).not.toContain('SECRET')
  expect(screen.queryByText('Annotations available')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Retry / refresh summary' }))
  await screen.findByText('Annotations available')
  expect(fetcher).toHaveBeenCalledTimes(3)
  expect(fetcher.mock.calls[2]?.[1]?.method).not.toBe('POST')
})
it('renders non-ready lifecycle without inventing metrics or polling', async () => {
  fetcher.mockResolvedValueOnce(reply(grant)).mockResolvedValueOnce(reply({ schema_version: 'analytics.v1', error_code: null, dataset_id: 'dataset-a', upload_id: 'upload-a', state: 'indexing', ready: false }))
  render(<AnalyticsSummaryPanel />); submit()
  await screen.findByText('indexing')
  expect(screen.queryByText('Images', { exact: true })).not.toBeInTheDocument()
  expect(fetcher).toHaveBeenCalledTimes(2)
})
it('rejects invalid summaries and never renders their metrics', async () => {
  fetcher.mockResolvedValueOnce(reply(grant)).mockResolvedValueOnce(reply({ ...summary, images: null }))
  render(<AnalyticsSummaryPanel />); submit()
  await screen.findByRole('alert')
  expect(screen.queryByText('Annotations available')).not.toBeInTheDocument()
})
it('shows loading and ignores a late response after access is cleared', async () => {
  let resolve!: (response: Response) => void
  fetcher.mockResolvedValueOnce(reply(grant)).mockImplementationOnce(() => new Promise<Response>((done) => { resolve = done }))
  render(<AnalyticsSummaryPanel />); submit()
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
  expect(screen.getByRole('status')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Clear access' }))
  await act(async () => { resolve(reply(summary)) })
  expect(screen.queryByText('Annotations available')).not.toBeInTheDocument()
  expect(screen.getByLabelText('Expected dataset ID')).toHaveValue('')
})
it('does not automatically retry an exchange failure', async () => {
  fetcher.mockResolvedValueOnce(reply({ error: { code: 'ACCESS_GRANT_INVALID' } }, 401))
  render(<AnalyticsSummaryPanel />); submit()
  await screen.findByRole('alert')
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('button', { name: 'Retry / refresh summary' })).toBeDisabled()
})
