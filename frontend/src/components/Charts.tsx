import {
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
  CartesianGrid,
} from 'recharts'

const COLORS = {
  PASS: '#10b981',
  REVIEW: '#d97706',
  ERROR: '#dc2626',
  HIGH: '#dc2626',
  MEDIUM: '#d97706',
  LOW: '#2563eb',
  NONE: '#64748b',
}

const WEATHER_PALETTE: Record<string, string> = {
  sunny: '#f59e0b',
  clear: '#f59e0b',
  rainy: '#2563eb',
  rain: '#2563eb',
  cloudy: '#64748b',
  overcast: '#475569',
  foggy: '#94a3b8',
  fog: '#94a3b8',
  snow: '#38bdf8',
  missing: '#cbd5e1',
  unknown: '#94a3b8',
}

const TIMEOFDAY_PALETTE: Record<string, string> = {
  day: '#0284c7',
  daytime: '#0284c7',
  night: '#1e1b4b',
  nighttime: '#1e1b4b',
  dusk: '#ea580c',
  dawn: '#db2777',
  missing: '#cbd5e1',
  unknown: '#94a3b8',
}

const DEFAULT_SERIES = ['#175ba0', '#0ea5e9', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#64748b']

type PieData = { name: string; value: number; fill?: string }
type BarData = { name: string; value: number; fill?: string }

export function StatusPieChart({ data, title }: { data: PieData[]; title?: string }) {
  if (!data.length) return <p>Không có dữ liệu để hiển thị biểu đồ.</p>
  const enriched = data.map((item) => ({ ...item, fill: item.fill ?? COLORS[item.name as keyof typeof COLORS] ?? '#64748b' }))
  return (
    <ResponsiveContainer width="100%" height={300}>
      <PieChart>
        <Pie data={enriched} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80} label>
          {enriched.map((entry, index) => <Cell key={`cell-${index}`} fill={entry.fill} />)}
        </Pie>
        <Tooltip />
        <Legend />
        {title && <text x="50%" y={20} textAnchor="middle" dominantBaseline="middle" fontSize={14}>{title}</text>}
      </PieChart>
    </ResponsiveContainer>
  )
}

export function ClassBarChart({ data, title }: { data: BarData[]; title?: string }) {
  if (!data.length) return <p>Không có dữ liệu để hiển thị biểu đồ.</p>
  const enriched = data.map((item) => ({ ...item, fill: item.fill ?? '#175ba0' }))
  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={enriched}>
        <XAxis dataKey="name" />
        <YAxis />
        <Tooltip />
        <Legend />
        <Bar dataKey="value" />
        {title && <text x="50%" y={20} textAnchor="middle" dominantBaseline="middle" fontSize={14}>{title}</text>}
      </BarChart>
    </ResponsiveContainer>
  )
}

/* =========================================================================
   ENHANCED VISUALIZATIONS FOR N2-05B
   ========================================================================= */

export type ClassDistributionItem = {
  name: string
  value: number
  share_pct?: number | undefined
  images?: number | undefined
}

interface TooltipPayloadItem {
  name?: string | undefined
  value?: number | undefined
  payload?: ClassDistributionItem | undefined
}

interface CustomTooltipProps {
  active?: boolean | undefined
  payload?: TooltipPayloadItem[] | undefined
  label?: string | undefined
}

function ClassTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload?.length) return null
  const item = payload[0]?.payload
  return (
    <div className="custom-chart-tooltip">
      <p className="tooltip-title"><strong>Class:</strong> {label}</p>
      <p className="tooltip-row">
        <span>Số lượng box:</span> <strong>{item?.value?.toLocaleString() ?? 0} box</strong>
      </p>
      {typeof item?.share_pct === 'number' && (
        <p className="tooltip-row">
          <span>Tỷ lệ phân bố:</span> <strong>{item.share_pct}%</strong>
        </p>
      )}
      {typeof item?.images === 'number' && (
        <p className="tooltip-row">
          <span>Số ảnh chứa class:</span> <strong>{item.images} ảnh</strong>
        </p>
      )}
    </div>
  )
}

export function ClassDistributionChart({ data }: { data: ClassDistributionItem[] }) {
  if (!data.length) return <p>Không có dữ liệu để hiển thị biểu đồ.</p>
  const total = data.reduce((sum, d) => sum + d.value, 0)
  const enriched = data.map((d, index) => ({
    ...d,
    share_pct: d.share_pct ?? (total > 0 ? Number(((d.value / total) * 100).toFixed(1)) : 0),
    fill: DEFAULT_SERIES[index % DEFAULT_SERIES.length] ?? '#175ba0',
  }))

  return (
    <div className="chart-wrapper">
      <ResponsiveContainer width="100%" height={320}>
        <BarChart data={enriched} margin={{ top: 20, right: 20, left: 0, bottom: 40 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
          <XAxis
            dataKey="name"
            tick={{ fill: '#475569', fontSize: 13 }}
            interval={0}
            angle={-25}
            textAnchor="end"
            height={50}
          />
          <YAxis tick={{ fill: '#475569', fontSize: 12 }} />
          <Tooltip content={<ClassTooltip />} />
          <Bar dataKey="value" name="Số lượng box" radius={[6, 6, 0, 0]}>
            {enriched.map((entry, idx) => (
              <Cell key={`class-bar-${idx}`} fill={entry.fill} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="chart-legend-chips">
        {enriched.map((item) => (
          <span key={item.name} className="legend-chip">
            <span className="chip-dot" style={{ background: item.fill }} />
            <strong>{item.name}:</strong> {item.value.toLocaleString()} box ({item.share_pct}%)
          </span>
        ))}
      </div>
    </div>
  )
}

interface DonutItem {
  name: string
  value: number
  fill: string
  percent?: number | undefined
}

interface DonutTooltipProps {
  active?: boolean
  payload?: { payload?: DonutItem }[]
}

function DonutTooltip({ active, payload }: DonutTooltipProps) {
  if (!active || !payload?.length) return null
  const item = payload[0]?.payload
  return (
    <div className="custom-chart-tooltip">
      <p className="tooltip-title"><strong>{item?.name}</strong></p>
      <p className="tooltip-row">
        <span>Số lượng:</span> <strong>{item?.value?.toLocaleString() ?? 0}</strong>
      </p>
      {typeof item?.percent === 'number' && (
        <p className="tooltip-row">
          <span>Tỷ lệ:</span> <strong>{item.percent}%</strong>
        </p>
      )}
    </div>
  )
}

export function CategoryDonutChart({
  data,
  colorScheme = 'default',
}: {
  data: { name: string; value: number }[]
  colorScheme?: 'weather' | 'timeofday' | 'status' | 'priority' | 'default'
}) {
  if (!data.length) return <p>Không có dữ liệu để hiển thị biểu đồ.</p>
  const total = data.reduce((sum, d) => sum + d.value, 0)
  const palette = colorScheme === 'weather' ? WEATHER_PALETTE
    : colorScheme === 'timeofday' ? TIMEOFDAY_PALETTE
    : COLORS

  const enriched: DonutItem[] = data.map((item, index) => {
    const key = item.name.toLowerCase().trim()
    const fill: string = (palette as Record<string, string>)[key]
      ?? (palette as Record<string, string>)[item.name]
      ?? DEFAULT_SERIES[index % DEFAULT_SERIES.length]
      ?? '#175ba0'
    return {
      name: item.name || 'Không xác định',
      value: item.value,
      fill,
      percent: total > 0 ? Number(((item.value / total) * 100).toFixed(1)) : 0,
    }
  })

  return (
    <div className="donut-chart-container">
      <ResponsiveContainer width="100%" height={260}>
        <PieChart>
          <Pie
            data={enriched}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            innerRadius={55}
            outerRadius={85}
            paddingAngle={3}
          >
            {enriched.map((entry, index) => (
              <Cell key={`donut-${index}`} fill={entry.fill} stroke="#ffffff" strokeWidth={2} />
            ))}
          </Pie>
          <Tooltip content={<DonutTooltip />} />
        </PieChart>
      </ResponsiveContainer>
      <div className="donut-legend-list">
        {enriched.map((item) => (
          <div key={item.name} className="donut-legend-row">
            <span className="donut-legend-color" style={{ background: item.fill }} />
            <span className="donut-legend-name">{item.name}</span>
            <span className="donut-legend-stat">
              <strong>{item.value}</strong> ({item.percent}%)
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

export type BoxSizeItem = {
  label: string
  count: number
  mean: number
  median: number
  min: number
  max: number
}

interface BoxSizeTooltipProps {
  active?: boolean
  payload?: { payload?: BoxSizeItem }[]
}

function BoxSizeTooltip({ active, payload }: BoxSizeTooltipProps) {
  if (!active || !payload?.length) return null
  const item = payload[0]?.payload
  if (!item) return null
  return (
    <div className="custom-chart-tooltip">
      <p className="tooltip-title"><strong>Class:</strong> {item.label}</p>
      <p className="tooltip-row">
        <span>Diện tích TB (Mean):</span> <strong>{item.mean.toFixed(2)}%</strong>
      </p>
      <p className="tooltip-row">
        <span>Trung vị (Median):</span> <strong>{item.median.toFixed(2)}%</strong>
      </p>
      <p className="tooltip-row">
        <span>Phạm vi [Min - Max]:</span> <strong>[{item.min.toFixed(2)}% - {item.max.toFixed(2)}%]</strong>
      </p>
      <p className="tooltip-row">
        <span>Số box hợp lệ:</span> <strong>{item.count} box</strong>
      </p>
    </div>
  )
}

export function BoxSizeChart({ data }: { data: BoxSizeItem[] }) {
  if (!data.length) return <p>Không có dữ liệu để hiển thị biểu đồ.</p>
  const formatted = data.map((d) => ({
    ...d,
    meanRounded: Number(d.mean.toFixed(2)),
    medianRounded: Number(d.median.toFixed(2)),
  }))

  return (
    <div className="chart-wrapper">
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={formatted} margin={{ top: 20, right: 20, left: 0, bottom: 30 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: '#475569', fontSize: 13 }} />
          <YAxis unit="%" tick={{ fill: '#475569', fontSize: 12 }} />
          <Tooltip content={<BoxSizeTooltip />} />
          <Legend wrapperStyle={{ paddingTop: 8 }} />
          <Bar dataKey="meanRounded" name="Diện tích TB (Mean %)" fill="#175ba0" radius={[4, 4, 0, 0]} />
          <Bar dataKey="medianRounded" name="Trung vị (Median %)" fill="#0ea5e9" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
      <p className="chart-footnote">
        * Thống kê diện tích Bounding Box tính theo % diện tích ảnh do Backend cung cấp (Mean, Median, Min, Max).
      </p>
    </div>
  )
}

export type OcclusionRow = {
  label: string
  occluded: boolean | string | number
  boxes: number
}

interface OcclusionAggregated {
  label: string
  occluded: number
  unoccluded: number
  total: number
  rate: number
}

interface OcclusionTooltipProps {
  active?: boolean
  payload?: { payload?: OcclusionAggregated }[]
}

function OcclusionTooltip({ active, payload }: OcclusionTooltipProps) {
  if (!active || !payload?.length) return null
  const item = payload[0]?.payload
  if (!item) return null
  return (
    <div className="custom-chart-tooltip">
      <p className="tooltip-title"><strong>Class:</strong> {item.label}</p>
      <p className="tooltip-row">
        <span>Không che khuất:</span> <strong>{item.unoccluded} box</strong>
      </p>
      <p className="tooltip-row">
        <span>Bị che khuất:</span> <strong>{item.occluded} box</strong>
      </p>
      <p className="tooltip-row">
        <span>Tỷ lệ che khuất:</span> <strong>{item.rate}%</strong>
      </p>
    </div>
  )
}

export function OcclusionChart({ rows }: { rows: OcclusionRow[] }) {
  if (!rows.length) return <p>Không có dữ liệu để hiển thị biểu đồ.</p>
  const classMap = new Map<string, { occluded: number; unoccluded: number }>()
  for (const r of rows) {
    const isOccluded = r.occluded === true || r.occluded === 'true' || r.occluded === 1
    const entry = classMap.get(r.label) ?? { occluded: 0, unoccluded: 0 }
    if (isOccluded) entry.occluded += r.boxes
    else entry.unoccluded += r.boxes
    classMap.set(r.label, entry)
  }

  const data: OcclusionAggregated[] = Array.from(classMap.entries()).map(([label, stats]) => {
    const total = stats.occluded + stats.unoccluded
    return {
      label,
      occluded: stats.occluded,
      unoccluded: stats.unoccluded,
      total,
      rate: total > 0 ? Number(((stats.occluded / total) * 100).toFixed(1)) : 0,
    }
  }).sort((a, b) => b.total - a.total)

  const totalOccluded = data.reduce((sum, d) => sum + d.occluded, 0)
  const totalUnoccluded = data.reduce((sum, d) => sum + d.unoccluded, 0)
  const grandTotal = totalOccluded + totalUnoccluded
  const overallRate = grandTotal > 0 ? Number(((totalOccluded / grandTotal) * 100).toFixed(1)) : 0

  return (
    <div className="chart-wrapper">
      <div className="occlusion-summary-banner">
        <span>Tổng quan che khuất:</span>
        <strong className="badge-occluded">{totalOccluded.toLocaleString()} box bị che ({overallRate}%)</strong>
        <span className="badge-clear">{totalUnoccluded.toLocaleString()} box rõ ràng</span>
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data} margin={{ top: 20, right: 20, left: 0, bottom: 30 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: '#475569', fontSize: 13 }} />
          <YAxis tick={{ fill: '#475569', fontSize: 12 }} />
          <Tooltip content={<OcclusionTooltip />} />
          <Legend wrapperStyle={{ paddingTop: 8 }} />
          <Bar dataKey="unoccluded" name="Rõ ràng (Không che)" stackId="a" fill="#10b981" />
          <Bar dataKey="occluded" name="Bị che khuất (Occluded)" stackId="a" fill="#f59e0b" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

export function IssuesBarChart({ data }: { data: { name: string; value: number }[] }) {
  if (!data.length) return <p>Không có dữ liệu để hiển thị biểu đồ.</p>
  const sorted = [...data].sort((a, b) => b.value - a.value)
  return (
    <div className="chart-wrapper">
      <ResponsiveContainer width="100%" height={Math.max(240, sorted.length * 36)}>
        <BarChart layout="vertical" data={sorted} margin={{ top: 10, right: 30, left: 120, bottom: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
          <XAxis type="number" tick={{ fill: '#475569', fontSize: 12 }} />
          <YAxis
            type="category"
            dataKey="name"
            tick={{ fill: '#334155', fontSize: 12 }}
            width={110}
          />
          <Tooltip formatter={(value: unknown) => [`${value} trường hợp`, 'Số lượng cảnh báo']} />
          <Bar dataKey="value" name="Tần suất cảnh báo" fill="#dc2626" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
