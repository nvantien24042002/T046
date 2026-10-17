import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { LabelCheck } from '../reference/LabelCheck'
import { decodeLabels, filterLabels, labelSummary, labelStatuses } from '../api/labels'
import { loadMockLabels, labelDefaults, labelConfigurationKey, type LabelSnapshot } from '../mocks/labels'
import fixture from '../mocks/overview-baseline.json'

const summary = { dataset_id: 'fixture', annotations: true }
function decode(rows: unknown) { return decodeLabels(summary, { dataset_id: 'fixture', audit_rows: rows }) }
function metric(name: string, value: number) { expect(within(screen.getByRole('group', { name })).getByText(String(value))).toBeVisible() }
function details() { return screen.getByRole('table', { name: /Geometry A và Visual B riêng biệt/ }) }

describe('Label Check contracts and Python parity', () => {
  it('preserves all baseline fields, A/B issues and nulls', async () => {
    const original = JSON.parse(readFileSync(resolve(process.cwd(), '../tests/baseline/expected.json'), 'utf8')) as { audit: unknown }
    const snapshot = await loadMockLabels('baseline')
    expect(snapshot.data.results.audit_rows).toEqual(original.audit)
    expect(snapshot.data.results.audit_rows[2]?.image_available).toBe(true)
    expect(snapshot.data.results.audit_rows[2]?.edge_density).toBeNull()
    expect(snapshot.data.results.audit_rows[4]?.status).toBe('A_ONLY')
    const totals = labelSummary(snapshot.data.results.audit_rows)
    expect({ boxes: totals.boxes, reviews: totals.reviews, high: totals.high, imagesAvailable: totals.imagesAvailable }).toEqual({ boxes: 5, reviews: 4, high: 4, imagesAvailable: 4 })
    expect(totals.classes).toEqual([
      { label: 'bus', boxes: 1, reviews: 0, images: 1, reviewRatePct: 0 },
      { label: 'car', boxes: 2, reviews: 2, images: 1, reviewRatePct: 100 },
      { label: 'person', boxes: 2, reviews: 2, images: 2, reviewRatePct: 100 },
    ])
    expect(new Map(totals.issues).get('LOW_FOREGROUND_CONTRAST')).toBe(3)
    expect(totals.issues.reduce((sum, [, count]) => sum + count, 0)).toBe(17)
  })
  it('matches every default threshold to the Python producer', () => {
    const source = readFileSync(resolve(process.cwd(), '../core/label_audit.py'), 'utf8')
    for (const [key, value] of Object.entries(labelDefaults)) expect(Number(source.match(new RegExp('"' + key + '": ([0-9.]+)'))?.[1])).toBe(value)
  })
  it.each(labelStatuses)('preserves source status %s without inventing review flags', (status) => {
    // Source-enum test only: not a newly computed audit fixture.
    const data = decode([{ ...fixture.audit[0], status }])
    expect(data.results.audit_rows[0]?.status).toBe(status)
    expect(labelSummary(data.results.audit_rows).reviews).toBe(status.startsWith('REVIEW') ? 1 : 0)
  })
  it('uses OR within fields and AND across class, priority and review filters', () => {
    const rows = decode(fixture.audit).results.audit_rows
    expect(filterLabels(rows, { classes: ['car', 'person'], priorities: ['HIGH'], onlyReview: true })).toHaveLength(4)
    expect(filterLabels(rows, { classes: ['bus'], priorities: ['LOW'], onlyReview: false })[0]?.status).toBe('A_ONLY')
    expect(filterLabels(rows, { classes: ['bus'], priorities: ['LOW'], onlyReview: true })).toEqual([])
    expect(filterLabels(rows, { classes: [], priorities: ['HIGH', 'LOW'], onlyReview: false })).toEqual([])
  })
  it.each([
    { ...fixture.audit[0], image_available: 'true' },
    { ...fixture.audit[0], visual_issues: [] },
    { ...fixture.audit[0], edge_density: Number.NaN },
    { ...fixture.audit[0], priority: 'URGENT' },
    { ...fixture.audit[0], status: 'PASS' },
  ])('rejects malformed audit fields rather than coercing them', (row) => { expect(() => decode([row])).toThrow() })
  it.each([[1, 6.2], [3, 18.8]])('matches pandas half-even percentage rounding for %i/16 reviews', (reviews, expected) => {
    const rows = Array.from({ length: 16 }, (_, index) => ({ ...fixture.audit[0], box_id: String(index), status: index < reviews ? 'REVIEW' : 'NO_SIGNAL' }))
    expect(labelSummary(decode(rows).results.audit_rows).classes[0]?.reviewRatePct).toBe(expected)
  })
  it('allows nullable coordinates and rejects mismatched identity/annotations', () => {
    expect(decode([{ ...fixture.audit[0], xtl: null }]).results.audit_rows[0]?.xtl).toBeNull()
    expect(() => decodeLabels(summary, { dataset_id: 'other', audit_rows: fixture.audit })).toThrow()
    expect(() => decodeLabels({ ...summary, annotations: false }, { dataset_id: 'fixture', audit_rows: fixture.audit })).toThrow()
  })
})

describe('Label Check UI', () => {
  it('renders the existing route and default review-filtered baseline', async () => {
    render(<MemoryRouter initialEntries={['/labels']}><App /></MemoryRouter>)
    await screen.findByRole('group', { name: 'Tổng box trong bộ lọc' })
    expect(screen.getByRole('heading', { level: 1, name: 'Label Check A+B' })).toBeVisible()
    metric('Tổng box trong bộ lọc', 4); metric('Box cần review', 4); metric('HIGH cần review', 4); metric('Có ảnh kiểm tra B', 4)
    expect(within(details()).getAllByRole('row')).toHaveLength(5)
    expect(within(details()).getByRole('columnheader', { name: 'geometry_issues' })).toBeVisible()
    expect(within(details()).getByRole('columnheader', { name: 'visual_issues' })).toBeVisible()
    const invalid = within(details()).getByText('0:2').closest('tr')!
    expect(within(invalid).getAllByRole('cell')[10]).toHaveTextContent('true')
    expect(within(invalid).getAllByRole('cell')[19]).toHaveTextContent('—')
  })
  it('keeps metrics, issue counts, plot and tables in the same filter scope', async () => {
    render(<LabelCheck />)
    await screen.findByRole('group', { name: 'Tổng box trong bộ lọc' })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Chỉ hiện box có cảnh báo' }))
    metric('Tổng box trong bộ lọc', 5)
    expect(within(details()).getByText('A_ONLY')).toBeVisible()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Priority HIGH' }))
    metric('Tổng box trong bộ lọc', 1); metric('Box cần review', 0); metric('Có ảnh kiểm tra B', 0)
    expect(within(details()).getAllByRole('row')).toHaveLength(2)
    expect(within(details()).getByText('bus')).toBeVisible()
    expect(within(details()).queryByText('person')).not.toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByText('Không có tín hiệu trong các box đang hiển thị.')).toBeVisible()
    const classRegion = screen.getByRole('region', { name: 'Thống kê theo class (đã lọc)' })
    expect(within(classRegion).getAllByRole('row')).toHaveLength(2)
    expect(within(classRegion).getByText('bus')).toBeVisible()
    expect(screen.queryByText(/There is no item named/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Đặt lại bộ lọc' }))
    metric('Tổng box trong bộ lọc', 4)
    expect(screen.getByRole('img')).toBeVisible()
  })
  it('supports class filtering and no matches without showing unfiltered tables', async () => {
    render(<LabelCheck />)
    await screen.findByRole('group', { name: 'Tổng box trong bộ lọc' })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Class person' }))
    metric('Tổng box trong bộ lọc', 2)
    await userEvent.click(screen.getByRole('checkbox', { name: 'Class car' }))
    metric('Tổng box trong bộ lọc', 0)
    expect(screen.getByText(/Không có box khớp bộ lọc/)).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
  it('handles missing annotations, no boxes, empty, malformed and load failures', async () => {
    render(<LabelCheck />)
    await screen.findByRole('group', { name: 'Tổng box trong bộ lọc' })
    const selector = screen.getByRole('combobox', { name: 'Tình huống Label Check' })
    await userEvent.selectOptions(selector, 'no-annotations')
    expect(await screen.findByText(/Thiếu annotation CVAT/)).toBeVisible()
    for (const scenario of ['no-boxes', 'empty']) {
      await userEvent.selectOptions(selector, scenario)
      expect(await screen.findByText(/Có annotation nhưng audit rows trống/)).toBeVisible()
      expect(screen.queryByRole('table')).not.toBeInTheDocument()
    }
    for (const scenario of ['malformed', 'error']) {
      await userEvent.selectOptions(selector, scenario)
      expect(await screen.findByRole('alert')).toBeVisible()
      expect(screen.queryByRole('table')).not.toBeInTheDocument()
    }
  })
  it('has loading/error/retry states without leaking internal errors', async () => {
    const load = vi.fn().mockRejectedValueOnce(Error('private path')).mockImplementationOnce(() => loadMockLabels('baseline'))
    render(<LabelCheck load={load} />)
    expect(screen.getByRole('status')).toHaveTextContent('Đang tải')
    expect(await screen.findByRole('alert')).not.toHaveTextContent('private path')
    await userEvent.click(screen.getByRole('button', { name: 'Thử lại Label Check' }))
    await screen.findByRole('group', { name: 'Tổng box trong bộ lọc' })
    metric('Tổng box trong bộ lọc', 4)
    expect(load).toHaveBeenCalledTimes(2)
  })
  it('rejects snapshots for a different configuration', async () => {
    const snapshot = await loadMockLabels('baseline')
    expect(snapshot.configurationKey).toBe(labelConfigurationKey)
    render(<LabelCheck load={vi.fn().mockResolvedValue({ ...snapshot, configurationKey: 'other' })} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('không đúng cấu hình')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
  it.each(['success', 'failure'])('ignores stale %s after another scenario becomes active', async (outcome) => {
    let complete!: (value: LabelSnapshot) => void
    let fail!: (error: Error) => void
    const old = new Promise<LabelSnapshot>((resolve, reject) => { complete = resolve; fail = reject })
    const load = vi.fn().mockReturnValueOnce(old).mockImplementationOnce(() => loadMockLabels('no-annotations'))
    render(<LabelCheck load={load} />)
    await userEvent.selectOptions(screen.getByRole('combobox'), 'no-annotations')
    await screen.findByText(/Thiếu annotation CVAT/)
    await act(async () => { if (outcome === 'success') complete(await loadMockLabels('baseline')); else fail(Error('late failure')) })
    expect(screen.getByText(/Thiếu annotation CVAT/)).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})

// Historical fixture/parity harness only; production route tests use dashboard.v1.
vi.mock('../pages/LabelCheck', () => import('../reference/LabelCheck'))
