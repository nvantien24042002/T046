import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, CartesianGrid } from 'recharts'

const COLORS = {
  PASS: '#175ba0',
  REVIEW: '#976300',
  ERROR: '#842b2b',
  HIGH: '#842b2b',
  MEDIUM: '#976300',
  LOW: '#175ba0',
  NONE: '#5C635D',
}

type PieData = { name: string; value: number; fill?: string }
type BarData = { name: string; value: number; fill?: string }

export function StatusPieChart({ data, title }: { data: PieData[]; title?: string }) {
  if (!data.length) return <p>Không có dữ liệu để hiển thị biểu đồ.</p>
  const enriched = data.map((item) => ({ ...item, fill: item.fill ?? COLORS[item.name as keyof typeof COLORS] ?? '#5C635D' }))
  return (
    <ResponsiveContainer width="100%" height={280}>
      <PieChart>
        <Pie data={enriched} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80} label isAnimationActive={false}>
          {enriched.map((entry, index) => <Cell key={`cell-${index}`} fill={entry.fill} />)}
        </Pie>
        <Tooltip contentStyle={{ backgroundColor: '#fff', borderRadius: '6px', border: '1px solid #d7e1ed', fontSize: '13px' }} />
        <Legend wrapperStyle={{ paddingTop: '8px', fontSize: '13px', color: '#475569' }} />
        {title && <text x="50%" y={20} textAnchor="middle" dominantBaseline="middle" fontSize={14} fontWeight={650} fill="#173b67">{title}</text>}
      </PieChart>
    </ResponsiveContainer>
  )
}

export function ClassBarChart({ data, title, seriesName }: { data: BarData[]; title?: string; seriesName?: string }) {
  if (!data.length) return <p>Không có dữ liệu để hiển thị biểu đồ.</p>
  const enriched = data.map((item) => ({
    ...item,
    fill: item.fill ?? COLORS[item.name as keyof typeof COLORS] ?? '#175ba0',
  }))
  const isCrowded = enriched.length > 5
  const hasLongLabels = enriched.some((d) => d.name.length > 8)
  const shouldAngle = isCrowded || hasLongLabels
  const angle = isCrowded ? -40 : (hasLongLabels ? -20 : 0)
  const bottomMargin = isCrowded ? 48 : (hasLongLabels ? 28 : 16)
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={enriched} margin={{ top: 16, right: 16, left: -10, bottom: bottomMargin }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
        <XAxis
          dataKey="name"
          tickLine={false}
          axisLine={{ stroke: '#cbd5e1' }}
          tick={{ fill: '#475569', fontSize: isCrowded ? 10.5 : 12 }}
          interval={0}
          angle={angle}
          textAnchor={shouldAngle ? 'end' : 'middle'}
          height={shouldAngle ? (isCrowded ? 48 : 30) : 24}
        />
        <YAxis
          allowDecimals={false}
          tickLine={false}
          axisLine={{ stroke: '#cbd5e1' }}
          tick={{ fill: '#475569', fontSize: 12 }}
        />
        <Tooltip
          cursor={{ fill: 'rgba(23, 91, 160, 0.05)' }}
          contentStyle={{ backgroundColor: '#fff', borderRadius: '6px', border: '1px solid #d7e1ed', fontSize: '13px' }}
        />
        <Legend
          wrapperStyle={{ paddingTop: '8px', fontSize: '13px', color: '#475569' }}
        />
        <Bar
          dataKey="value"
          name={seriesName ?? 'Số lượng'}
          maxBarSize={48}
          radius={[4, 4, 0, 0]}
          isAnimationActive={false}
        >
          {enriched.map((entry, index) => (
            <Cell key={`bar-${index}`} fill={entry.fill} />
          ))}
        </Bar>
        {title && <text x="50%" y={16} textAnchor="middle" dominantBaseline="middle" fontSize={14} fontWeight={650} fill="#173b67">{title}</text>}
      </BarChart>
    </ResponsiveContainer>
  )
}
