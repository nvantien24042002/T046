import { PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts'

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
