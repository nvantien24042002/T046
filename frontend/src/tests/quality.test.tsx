import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { ImageQuality } from '../reference/ImageQuality'
import { decodeQuality, qualitySummary } from '../api/quality'
import { loadMockQuality, qualityConfigurationKey, qualityDefaults, type QualitySnapshot } from '../mocks/quality'
import fixture from '../mocks/overview-baseline.json'

function metric(name: string, value: string) { expect(within(screen.getByRole('group', { name })).getByText(value)).toBeVisible() }
function deferred() {
  let resolve!: (snapshot: QualitySnapshot) => void
  const promise = new Promise<QualitySnapshot>((done) => { resolve = done })
  return { promise, resolve }
}

describe('Image Quality Python parity and validation', () => {
  it('preserves every baseline metric, issue, score and null', async () => {
    const original = JSON.parse(readFileSync(resolve(process.cwd(), '../tests/baseline/expected.json'), 'utf8')) as { quality: unknown }
    const snapshot = await loadMockQuality('baseline')
    expect(snapshot.data.quality_rows).toEqual(original.quality)
    expect(snapshot.data.quality_rows[1]?.noise_score).toBeNull()
    expect(qualitySummary(snapshot.data.quality_rows)).toEqual({ counts: { PASS: 0, REVIEW: 2, ERROR: 0 }, mean: 67.5, issues: [['BLUR', 1], ['LOW_CONTRAST', 1], ['LOW_RESOLUTION', 2]] })
  })
  it('pins every displayed default threshold to the Python producer', () => {
    const source = readFileSync(resolve(process.cwd(), '../core/image_quality.py'), 'utf8')
    for (const [key, value] of Object.entries(qualityDefaults)) {
      const match = source.match(new RegExp('"' + key + '": ([0-9.]+)'))
      expect(Number(match?.[1])).toBe(value)
    }
  })
  it.each([
    { ...fixture.quality[0], status: 'UNKNOWN' },
    { ...fixture.quality[0], score: null },
    { ...fixture.quality[0], brightness: '100' },
    { ...fixture.quality[0], noise_score: Number.NaN },
    { ...fixture.quality[0], contrast: Number.POSITIVE_INFINITY },
    { ...fixture.quality[0], issues: 'BLUR' },
  ])('rejects malformed metrics instead of coercing them', (row) => {
    expect(() => decodeQuality({ dataset_id: 'fixture', quality_rows: [row] })).toThrow()
  })
  it('keeps ERROR scores in the mean and null signals unavailable', () => {
    // Source-derived error shape from analyze_image and test_image_quality_corrupt_image.
    const errorRow = { filename: 'broken.png', status: 'ERROR', score: 0, issues: ['READ_ERROR'], width: 0, height: 0, size_kb: 0, blur_score: null, brightness: null, contrast: null, noise_score: null, black_pct: null, white_pct: null, error: 'internal path' }
    const data = decodeQuality({ dataset_id: 'fixture', quality_rows: [...fixture.quality, errorRow] })
    expect(qualitySummary(data.quality_rows).counts.ERROR).toBe(1)
    expect(qualitySummary(data.quality_rows).mean).toBe(45)
    expect(data.quality_rows[2]?.brightness).toBeNull()
  })
  it('treats empty data as no average, not a zero score', () => {
    expect(qualitySummary([]).mean).toBeNull()
  })
})

describe('Image Quality UI states', () => {
  it('renders the existing route with baseline values and a null marker', async () => {
    render(<MemoryRouter initialEntries={['/quality']}><App /></MemoryRouter>)
    await screen.findByRole('group', { name: 'REVIEW' })
    metric('PASS', '0'); metric('REVIEW', '2'); metric('ERROR', '0'); metric('Điểm trung bình', '67.5/100')
    expect(screen.getByRole('heading', { name: 'Image Quality' })).toBeVisible()
    expect(screen.getByText(/Kết quả Python baseline/)).toBeVisible()
    const table = screen.getByRole('table', { name: /Chỉ số Python gốc/ })
    const texture = within(table).getByText('images/texture.png').closest('tr')!
    expect(within(texture).getAllByRole('cell')[10]).toHaveTextContent('—')
    expect(within(texture).getByText('49499.867466867')).toBeVisible()
    const flat = within(table).getByText('images/flat.png').closest('tr')!
    expect(within(flat).getAllByRole('cell')[2]).toHaveTextContent('50')
  })
  it('handles empty, malformed and failed fixtures without stale scores', async () => {
    render(<ImageQuality />)
    await screen.findByRole('group', { name: 'REVIEW' })
    const selector = screen.getByRole('combobox', { name: 'Tình huống Image Quality' })
    await userEvent.selectOptions(selector, 'empty')
    expect(await screen.findByRole('heading', { name: 'Chưa có dữ liệu' })).toBeVisible()
    expect(screen.queryByRole('group', { name: 'Điểm trung bình' })).not.toBeInTheDocument()
    await userEvent.selectOptions(selector, 'malformed')
    expect(await screen.findByRole('alert')).toBeVisible()
    await userEvent.selectOptions(selector, 'error')
    expect(await screen.findByRole('alert')).toBeVisible()
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
  })
  it('exposes loading and retries a failed load successfully', async () => {
    const load = vi.fn().mockRejectedValueOnce(Error('secret')).mockImplementationOnce(() => loadMockQuality('baseline'))
    render(<ImageQuality load={load} />)
    expect(screen.getByRole('status')).toHaveTextContent('Đang tải')
    expect(await screen.findByRole('alert')).not.toHaveTextContent('secret')
    await userEvent.click(screen.getByRole('button', { name: 'Thử lại Image Quality' }))
    await screen.findByRole('group', { name: 'REVIEW' })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(load).toHaveBeenCalledTimes(2)
  })
  it('does not accept a snapshot for another configuration', async () => {
    const data = await loadMockQuality('baseline')
    const load = vi.fn().mockResolvedValue({ ...data, configurationKey: 'other-thresholds' })
    render(<ImageQuality load={load} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('không đúng cấu hình')
    expect(screen.queryByRole('group', { name: 'REVIEW' })).not.toBeInTheDocument()
    expect(data.configurationKey).toBe(qualityConfigurationKey)
  })
  it('ignores late success from a prior selection', async () => {
    const older = deferred()
    const load = vi.fn().mockReturnValueOnce(older.promise).mockImplementationOnce(() => loadMockQuality('empty'))
    render(<ImageQuality load={load} />)
    await userEvent.selectOptions(screen.getByRole('combobox'), 'empty')
    await screen.findByRole('heading', { name: 'Chưa có dữ liệu' })
    await act(async () => { older.resolve(await loadMockQuality('baseline')) })
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Chưa có dữ liệu' })).toBeVisible()
  })
})

// Historical fixture/parity harness only; production route tests use dashboard.v1.
vi.mock('../pages/ImageQuality', () => import('../reference/ImageQuality'))
