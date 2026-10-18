import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts'
import type { DashboardView } from '../api/dashboard'
import { Pagination, paginate } from './Pagination'

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
export function histogram(values: number[], bins: number, range?: [number, number]) {
  const valid = values.filter(finite)
  if (!valid.length) return []
  const low = range?.[0] ?? valid.reduce((a, b) => Math.min(a, b))
  const high = range?.[1] ?? valid.reduce((a, b) => Math.max(a, b))
  if (low === high) return [{ interval: String(low), count: valid.length }]
  const width = (high - low) / bins
  const rows = Array.from({ length: bins }, (_, i) => ({
    interval: `${(low + i * width).toLocaleString('vi-VN', { maximumSignificantDigits: 6 })}–${(low + (i + 1) * width).toLocaleString('vi-VN', { maximumSignificantDigits: 6 })}`,
    count: 0,
  }))
  for (const value of valid) {
    if (value < low || value > high) continue
    rows[Math.min(bins - 1, Math.floor((value - low) / width))]!.count++
  }
  return rows
}

function Histogram({ title, values, missing, axis, range, bins }: {
  title: string; values: number[]; missing: number; axis: string; bins: number; range?: [number, number]
}) {
  const data = histogram(values, bins, range)
  return <section className="visualization-card">
    <h2>{title}</h2>
    <p>{values.length} ảnh có chỉ số; {missing} ảnh không có chỉ số hữu hạn. Khoảng cuối bao gồm cận trên.</p>
    {!data.length ? <p>Chưa có dữ liệu hữu hạn để vẽ {title.toLowerCase()}.</p> : <>
      <div className="chart-wrapper">
        <ResponsiveContainer width="100%" height={290}>
          <BarChart data={data} margin={{ top: 15, right: 20, left: 5, bottom: 35 }}>
            <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="interval" interval="preserveStartEnd" minTickGap={24} tick={{ fontSize: 11 }} />
            <YAxis allowDecimals={false} label={{ value: 'Số ảnh', angle: -90, position: 'insideLeft' }} />
            <Tooltip formatter={(value) => [value, 'Số ảnh']} labelFormatter={(label) => `${axis}: ${label}`} />
            <Bar dataKey="count" name="Số ảnh" fill="#175ba0" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p>{axis}</p>
      <details><summary>Dữ liệu {title.toLowerCase()}</summary><div className="table-scroll-wrapper"><table>
        <caption>{title}</caption><thead><tr><th scope="col">Khoảng {axis}</th><th scope="col">Số ảnh</th></tr></thead>
        <tbody>{data.map((row, index) => <tr key={index}><td>{row.interval}</td><td>{row.count}</td></tr>)}</tbody>
      </table></div></details>
    </>}
  </section>
}

export function QualityAnalysisCharts({ view }: { view: DashboardView }) {
  const rows = view.tables.find((table) => table.key === 'quality')?.rows ?? []
  const blur = rows.map((r) => r.blur_score).filter(finite)
  const score = rows.map((r) => r.score).filter(finite)
  const points = rows.flatMap((row) => finite(row.brightness) && finite(row.contrast)
    ? [{ brightness: row.brightness, contrast: row.contrast, filename: String(row.filename ?? ''), status: String(row.status ?? 'Chưa có trạng thái') }] : [])
  const statuses = [...new Set(points.map((p) => p.status))]
  const colors: Record<string, string> = { PASS: '#10b981', REVIEW: '#d97706', ERROR: '#dc2626' }
  return <div className="analysis-charts-grid">
    <Histogram title="Phân bố blur score" values={blur} missing={rows.length - blur.length} axis="Laplacian variance" bins={35} />
    <Histogram title="Phân bố quality score" values={score} missing={rows.length - score.length} axis="Điểm (0–100)" bins={10} range={[0, 100]} />
    <section className="visualization-card analysis-wide">
      <h2>Brightness × Contrast</h2>
      <p>{points.length} ảnh có đủ hai chỉ số; {rows.length - points.length} ảnh thiếu dữ liệu. Màu thể hiện trạng thái từ kết quả phân tích.</p>
      {!points.length ? <p>Chưa có cặp brightness–contrast hữu hạn để vẽ scatter plot.</p> : <div className="chart-wrapper">
        <ResponsiveContainer width="100%" height={320}>
          <ScatterChart margin={{ top: 15, right: 25, left: 25, bottom: 35 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis type="number" dataKey="brightness" name="Brightness" label={{ value: 'Brightness (gray 0–255)', position: 'bottom' }} />
            <YAxis type="number" dataKey="contrast" name="Contrast" label={{ value: 'Contrast (độ lệch chuẩn gray)', angle: -90, position: 'insideLeft' }} />
            <Tooltip content={({ active, payload }) => {
              const point = payload?.[0]?.payload as typeof points[number] | undefined
              return active && point ? <div className="custom-chart-tooltip"><strong>{point.filename}</strong><p>{point.status}</p><p>Brightness: {point.brightness}</p><p>Contrast: {point.contrast}</p></div> : null
            }} />
            <Legend verticalAlign="top" />
            {statuses.map((status) => <Scatter key={status} name={status} data={points.filter((p) => p.status === status)} fill={colors[status] ?? '#64748b'} />)}
          </ScatterChart>
        </ResponsiveContainer>
      </div>}
      <p>Biểu đồ phân bố điểm thay cho box plot: hiển thị tần suất điểm thực, không tạo thêm chỉ số chất lượng.</p>
    </section>
  </div>
}

export function CoverageComparisonChart({ view }: { view: DashboardView }) {
  const rows = view.tables.find((t) => t.key === 'evaluation')?.rows ?? []
  return <section className="feature-panel">
    <h2>Observed / Support so với Target</h2>
    <p>Các slice có thể chồng lấn; không cộng gap giữa các slice. Attainment và gap lấy nguyên từ kết quả máy chủ.</p>
    {!rows.length ? <p>Chưa có kết quả theo targets để vẽ biểu đồ.</p> : ['images', 'objects'].map((unit) => {
      const group = rows.filter((r) => r.unit === unit && finite(r.support) && finite(r.target))
      return group.length > 0 && <CoverageUnitChart key={unit} rows={group} unit={unit} />
    })}
  </section>
}

function CoverageUnitChart({ rows, unit }: { rows: Record<string, unknown>[]; unit: string }) {
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(8)
  const data = paginate(rows, page, pageSize)
  return <div className="coverage-unit-chart">
    <h3>Đơn vị: {unit === 'images' ? 'ảnh' : 'objects / bounding box'}</h3>
    <div className="chart-wrapper">
      <ResponsiveContainer width="100%" height={Math.max(260, data.length * 60)}>
        <BarChart layout="vertical" data={data} margin={{ top: 15, right: 20, left: 5, bottom: 10 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis type="number" allowDecimals={false} /><YAxis type="category" dataKey="slice" width={150} tick={{ fontSize: 12 }} />
          <Tooltip content={({ active, payload }) => {
            const row = payload?.[0]?.payload as Record<string, unknown> | undefined
            return active && row ? <div className="custom-chart-tooltip"><strong>{String(row.slice)}</strong>
              <p>Support: {String(row.support)} {unit}</p><p>Target: {String(row.target)} {unit}</p>
              <p>Gap: {String(row.gap)} · {String(row.status)}</p><p>Attainment: {String(row.attainment_pct)}%</p>
            </div> : null
          }} /><Legend />
          <Bar dataKey="support" name="Observed / Support" fill="#175ba0" /><Bar dataKey="target" name="Target" fill="#94a3b8" />
        </BarChart>
      </ResponsiveContainer>
    </div>
    <details><summary>Giá trị support và target ({unit})</summary><div className="table-scroll-wrapper"><table>
      <caption>So sánh slice ({unit})</caption>
      <thead><tr><th scope="col">Slice</th><th scope="col">Support</th><th scope="col">Target</th><th scope="col">Gap</th><th scope="col">Attainment</th><th scope="col">Trạng thái</th></tr></thead>
      <tbody>{data.map((row, i) => <tr key={i}><td>{String(row.slice)}</td><td>{String(row.support)}</td><td>{String(row.target)}</td><td>{String(row.gap)}</td><td>{String(row.attainment_pct)}%</td><td>{String(row.status)}</td></tr>)}</tbody>
    </table></div></details>
    <Pagination total={rows.length} page={page} pageSize={pageSize} onPageChange={setPage} onPageSizeChange={setPageSize} />
  </div>
}
