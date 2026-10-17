import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { Overview } from '../reference/Overview'
import { decodeOverview, overviewTotals } from '../api/overview'
import { loadMockOverview } from '../mocks/overview'
import fixture from '../mocks/overview-baseline.json'

const summary = { dataset_id: 'fixture', state: 'ready', ready: true, images: 2, annotations: true }
const results = { dataset_id: 'fixture', quality_rows: fixture.quality, audit_rows: fixture.audit, classes: fixture.classes }
function metric(name: string, value: number) { expect(within(screen.getByRole('group', { name })).getByText(String(value))).toBeVisible() }

describe('Overview contract and Python baseline parity', () => {
  it('copies reference values without changing numbers or null metrics', () => {
    const original = JSON.parse(readFileSync(resolve(process.cwd(), '../tests/baseline/expected.json'), 'utf8')) as Record<string, unknown>
    expect(fixture).toEqual({ quality: original.quality, audit: original.audit, classes: original.classes })
    expect(fixture.quality[1]?.noise_score).toBeNull()
  })
  it('matches Streamlit totals and class distribution including invalid-geometry boxes', async () => {
    const data = await loadMockOverview('annotated')
    expect(overviewTotals(data)).toEqual({ images: 2, reviewImages: 2, boxes: 5, reviewBoxes: 4, classes: 3 })
    expect(data.results.classes).toEqual([
      { class: 'car', boxes: 2, images: 1, share_pct: 40 },
      { class: 'person', boxes: 2, images: 2, share_pct: 40 },
      { class: 'bus', boxes: 1, images: 1, share_pct: 20 },
    ])
  })
  it('counts REVIEW prefixes without treating A_ONLY or NO_SIGNAL as review', () => {
    const data = decodeOverview(summary, { ...results, audit_rows: [
      { label: 'car', priority: 'HIGH', status: 'REVIEW_A_ONLY' },
      { label: 'car', priority: 'LOW', status: 'A_ONLY' },
      { label: 'car', priority: 'LOW', status: 'NO_SIGNAL' },
    ] })
    expect(overviewTotals(data).reviewBoxes).toBe(1)
  })
  it('rejects malformed rows, dataset mismatch, non-ready summary and annotation contradictions', () => {
    expect(() => decodeOverview(summary, { ...results, classes: [{ class: 'car', boxes: '2' }] })).toThrow()
    expect(() => decodeOverview(summary, { ...results, dataset_id: 'other' })).toThrow()
    expect(() => decodeOverview({ ...summary, ready: false, state: 'indexing' }, results)).toThrow()
    expect(() => decodeOverview({ ...summary, annotations: false }, results)).toThrow()
  })
})

describe('Overview interaction', () => {
  it('renders the route, numerical totals and accessible class meters', async () => {
    render(<MemoryRouter initialEntries={['/overview']}><App /></MemoryRouter>)
    expect(screen.getByRole('heading', { level: 1, name: 'Tổng quan dữ liệu' })).toBeVisible()
    expect(await screen.findByRole('group', { name: 'Tổng ảnh' })).toBeVisible()
    metric('Tổng ảnh', 2); metric('Ảnh cần review', 2); metric('CVAT boxes', 5); metric('Box cần review', 4); metric('Số class', 3)
    expect(screen.getByRole('meter', { name: 'Số box car' })).toHaveAttribute('value', '2')
    expect(screen.getByText(/Số liệu từ Python baseline/)).toBeVisible()
    expect(screen.getByRole('link', { name: 'Tổng quan dữ liệu' })).toHaveAttribute('aria-current', 'page')
  })
  it('preserves quality totals with no annotations and handles empty mock data', async () => {
    render(<Overview />)
    await screen.findByRole('group', { name: 'Tổng ảnh' })
    const selector = screen.getByRole('combobox', { name: 'Dataset mẫu' })
    await userEvent.selectOptions(selector, 'images-only')
    await screen.findByText(/Không có annotation CVAT/)
    metric('Tổng ảnh', 2); metric('CVAT boxes', 0); metric('Số class', 0)
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
    await userEvent.selectOptions(selector, 'empty')
    expect(await screen.findByRole('heading', { name: 'Chưa có dữ liệu' })).toBeVisible()
    expect(screen.queryByRole('group', { name: 'Tổng ảnh' })).not.toBeInTheDocument()
  })
  it('exposes loading, error and retry without retaining stale results', async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error('private')).mockImplementationOnce(() => loadMockOverview('annotated'))
    render(<Overview load={load} />)
    expect(screen.getByRole('status')).toHaveTextContent('Đang tải')
    expect(await screen.findByRole('alert')).not.toHaveTextContent('private')
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Thử lại tổng quan' }))
    await screen.findByRole('group', { name: 'Tổng ảnh' })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(load).toHaveBeenCalledTimes(2)
  })
  it('ignores an older fixture request after selection changes', async () => {
    let resolveOld!: (value: Awaited<ReturnType<typeof loadMockOverview>>) => void
    const old = new Promise<Awaited<ReturnType<typeof loadMockOverview>>>((resolve) => { resolveOld = resolve })
    const load = vi.fn().mockReturnValueOnce(old).mockImplementationOnce(() => loadMockOverview('images-only'))
    render(<Overview load={load} />)
    await userEvent.selectOptions(screen.getByRole('combobox'), 'images-only')
    await screen.findByText(/Không có annotation CVAT/)
    resolveOld(await loadMockOverview('annotated'))
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
    metric('CVAT boxes', 0)
  })
})

// Historical fixture/parity harness only; production route tests use dashboard.v1.
vi.mock('../pages/Overview', () => import('../reference/Overview'))
