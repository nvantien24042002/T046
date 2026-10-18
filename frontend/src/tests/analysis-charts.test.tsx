import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, it } from 'vitest'
import { QualityAnalysisCharts, CoverageComparisonChart, histogram } from '../components/AnalysisCharts'
import type { DashboardView } from '../api/dashboard'

function view(key: string, rows: Record<string, unknown>[]): DashboardView {
  return { schema_version: 'dashboard.v1', dataset_id: 'dataset-a', page: key === 'quality' ? 'quality' : 'coverage',
    state: 'ready', reason: null, source: { request_fingerprint: 'a'.repeat(64), result_id: null, payload_sha256: null },
    metrics: [], charts: [], controls: [], notes: [], tables: [{ key, title: key, rows, export_filename: null }] }
}

it('bins backend values once including the final boundary, without converting null to zero', () => {
  expect(histogram([0, 10, 100, NaN], 10, [0, 100]).map((r) => r.count)).toEqual([1, 1, 0, 0, 0, 0, 0, 0, 0, 1])
  expect(histogram([42, 42], 35)).toEqual([{ interval: '42', count: 2 }])
  expect(histogram([], 35)).toEqual([])
})

it('renders actual quality distributions and excludes incomplete scatter pairs', () => {
  render(<QualityAnalysisCharts view={view('quality', [
    { filename: 'a.png', status: 'PASS', blur_score: 200, score: 100, brightness: 100, contrast: 30 },
    { filename: 'b.png', status: 'REVIEW', blur_score: 200, score: 60, brightness: 200, contrast: null },
    { filename: 'broken.png', status: 'ERROR', blur_score: null, score: 0, brightness: null, contrast: null },
  ])} />)
  expect(screen.getByRole('heading', { name: 'Phân bố blur score' })).toBeVisible()
  expect(screen.getByText('2 ảnh có chỉ số; 1 ảnh không có chỉ số hữu hạn. Khoảng cuối bao gồm cận trên.')).toBeVisible()
  expect(screen.getByText('1 ảnh có đủ hai chỉ số; 2 ảnh thiếu dữ liệu. Màu thể hiện trạng thái từ kết quả phân tích.')).toBeVisible()
  fireEvent.click(screen.getByText('Dữ liệu phân bố blur score'))
  const table = screen.getByRole('table', { name: 'Phân bố blur score' })
  expect(within(table).getByText('200')).toBeVisible()
  expect(within(table).getByText('2')).toBeVisible()
})

it.each([{ rows: [] }, { rows: [{ blur_score: null, score: null, brightness: null, contrast: null }] }])('shows empty states without fabricated quality metrics: %j', ({ rows }) => {
  render(<QualityAnalysisCharts view={view('quality', rows)} />)
  expect(screen.getByText('Chưa có dữ liệu hữu hạn để vẽ phân bố blur score.')).toBeVisible()
  expect(screen.getByText('Chưa có dữ liệu hữu hạn để vẽ phân bố quality score.')).toBeVisible()
  expect(screen.getByText('Chưa có cặp brightness–contrast hữu hạn để vẽ scatter plot.')).toBeVisible()
})

it('keeps server support, target, gap, attainment >100 and status, separating units', () => {
  render(<CoverageComparisonChart view={view('evaluation', [
    { slice: 'rain', unit: 'images', support: 15, target: 10, gap: 0, attainment_pct: 150, status: 'Đạt' },
    { slice: 'car', unit: 'objects', support: 2, target: 8, gap: 6, attainment_pct: 25, status: 'Thiếu' },
  ])} />)
  fireEvent.click(screen.getByText('Giá trị support và target (images)'))
  fireEvent.click(screen.getByText('Giá trị support và target (objects)'))
  const images = screen.getByRole('table', { name: 'So sánh slice (images)' })
  expect(within(images).getByText('15')).toBeVisible()
  expect(within(images).getByText('150%')).toBeVisible()
  expect(within(images).getByText('Đạt')).toBeVisible()
  const objects = screen.getByRole('table', { name: 'So sánh slice (objects)' })
  expect(within(objects).getByText('6')).toBeVisible()
  expect(within(objects).getByText('25%')).toBeVisible()
  expect(screen.getByText(/không cộng gap giữa các slice/)).toBeVisible()
})

it('does not fabricate coverage when no evaluation is returned', () => {
  render(<CoverageComparisonChart view={view('evaluation', [])} />)
  expect(screen.getByText('Chưa có kết quả theo targets để vẽ biểu đồ.')).toBeVisible()
})
