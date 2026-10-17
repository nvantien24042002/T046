import { ResultIdentity } from '../components/ResultStatus'
import { useEffect, useState } from 'react'
import { config } from '../config'
import { decodeOverview, overviewTotals, type OverviewData } from '../api/overview'
import { loadMockOverview, type OverviewScenario } from '../mocks/overview'
import { EmptyState, ErrorState, LoadingState } from '../components/States'
import { StatusPieChart, ClassBarChart } from '../components/Charts'
import { useVerifiedResults } from '../context/VerifiedResultsContext'

type State = { kind: 'loading' } | { kind: 'ready'; data: OverviewData } | { kind: 'error' }
type Loader = (scenario: OverviewScenario) => Promise<OverviewData>

export function Overview({ load = loadMockOverview }: { load?: Loader }) {
  const [scenario, setScenario] = useState<OverviewScenario>('annotated')
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<State>({ kind: 'loading' })
  const verified = useVerifiedResults()

  useEffect(() => {
    if (config.mode !== 'mock') return
    let active = true
    load(scenario).then((data) => decodeOverview(data.summary, data.results)).then(
      (data) => { if (active) setState({ kind: 'ready', data }) },
      () => { if (active) setState({ kind: 'error' }) },
    )
    return () => { active = false }
  }, [scenario, attempt, load])

  const retry = () => { setState({ kind: 'loading' }); setAttempt((value) => value + 1) }

  // In backend mode, check for active verified result
  if (config.mode === 'backend') {
    if (verified?.loading) return <><p className="intro">Khu vực chức năng</p><h1>Tổng quan dữ liệu</h1><LoadingState /></>
    if (verified?.error) return <><p className="intro">Khu vực chức năng</p><h1>Tổng quan dữ liệu</h1><ErrorState message={verified.error} /></>

    if (verified?.activeResult) {
      const payload = verified.activeResult.payload
      const summaryObj = verified.activeSummary
      if (!summaryObj?.ready) return <EmptyState>Chưa có summary từ API cho dataset này.</EmptyState>
      let data: OverviewData | null = null
      let decodeErr: string | null = null
      try {
        data = decodeOverview(summaryObj, {
          dataset_id: verified.activeResult.payload.dataset_id,
          quality_rows: payload.quality_rows,
          audit_rows: payload.audit_rows,
          classes: payload.classes,
        })
      } catch (err: unknown) {
        decodeErr = err instanceof Error ? err.message : 'Invalid verified result'
      }

      if (decodeErr) {
        return <><p className="intro">Khu vực chức năng</p><h1>Tổng quan dữ liệu</h1><ErrorState message={decodeErr} /></>
      }
      if (data) {
        return <>
          <p className="intro">Khu vực chức năng</p>
          <h1>Tổng quan dữ liệu</h1>
          <ResultIdentity />
          <OverviewResults data={data} />
        </>
      }
    }

    return <>
      <p className="intro">Khu vực chức năng</p>
      <h1>Tổng quan dữ liệu</h1>
      <p>Thống kê chỉ đọc. Các tín hiệu review không xác nhận annotation đúng hoặc sai.</p>
      <EmptyState>Chưa có phiên dataset được cấp quyền. Contract bàn giao dataset và capability cho trình duyệt cần được phê duyệt trước khi gọi API summary/results. Không dùng dữ liệu mẫu trong Backend mode.</EmptyState>
    </>
  }

  return <>
    <h1>Tổng quan dữ liệu</h1>
    <p>Thống kê chỉ đọc. Các tín hiệu review không xác nhận annotation đúng hoặc sai.</p>
    <p className="notice"><strong>Dữ liệu mẫu — Mock mode.</strong> Số liệu từ Python baseline; không phải dataset thật đang kết nối.</p>
    <label>Dataset mẫu <select value={scenario} onChange={(event) => {
      setState({ kind: 'loading' }); setScenario(event.target.value as OverviewScenario)
    }}><option value="annotated">Có annotation</option><option value="images-only">Chỉ có ảnh</option><option value="empty">Dataset trống (mẫu UI)</option><option value="error">Lỗi tải dữ liệu (mẫu UI)</option></select></label>
    {state.kind === 'loading' && <LoadingState />}
    {state.kind === 'error' && <><ErrorState message="Không tải được tổng quan mẫu. Hãy thử lại hoặc chọn dataset mẫu khác." /><button onClick={retry}>Thử lại tổng quan</button></>}
    {state.kind === 'ready' && (state.data.results.quality_rows.length === 0 ? <EmptyState>Dataset mẫu chưa có kết quả phân tích ảnh.</EmptyState> : <OverviewResults data={state.data} />)}
  </>
}

function Counts({ title, values }: { title: string; values: Record<string, number> }) {
  return <section aria-label={title}><h2>{title}</h2><table><thead><tr><th scope="col">Trạng thái</th><th scope="col">Số lượng</th></tr></thead><tbody>{Object.entries(values).map(([name, count]) => <tr key={name}><th scope="row">{name}</th><td>{count}</td></tr>)}</tbody></table></section>
}

function OverviewResults({ data }: { data: OverviewData }) {
  const totals = overviewTotals(data)
  const metrics = [ ['Tổng ảnh', totals.images], ['Ảnh cần review', totals.reviewImages], ['CVAT boxes', totals.boxes], ['Box cần review', totals.reviewBoxes], ['Số class', totals.classes] ] as const
  const statuses: Record<string, number> = {}
  const priorities: Record<string, number> = {}
  for (const row of data.results.quality_rows) statuses[row.status] = (statuses[row.status] ?? 0) + 1
  for (const row of data.results.audit_rows) priorities[row.priority] = (priorities[row.priority] ?? 0) + 1
  const boxTotal = data.results.classes.reduce((sum, row) => sum + row.boxes, 0)
  const statusData = Object.entries(statuses).map(([name, value]) => ({ name, value }))
  const priorityData = Object.entries(priorities).map(([name, value]) => ({ name, value }))
  const classData = data.results.classes.map((row) => ({ name: row.class, value: row.boxes }))
  return <>
    <p>Dataset: {data.summary.dataset_id} — Ảnh trong archive: {data.summary.images} — Annotation: {data.summary.annotations ? 'Có' : 'Không'}</p>
    <dl className="overview-metrics">{metrics.map(([label, value]) => <div role="group" aria-label={label} key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <section aria-labelledby="status-chart"><h2 id="status-chart">Trạng thái ảnh</h2><StatusPieChart data={statusData} /></section>
    <Counts title="Chi tiết trạng thái ảnh" values={statuses} />
    {!data.summary.annotations ? <p role="status">Không có annotation CVAT; thống kê nhãn và class chưa có dữ liệu. Image Quality vẫn khả dụng.</p> : data.results.audit_rows.length === 0 ? <EmptyState>XML chưa có bounding box để thống kê class.</EmptyState> : <>
      <section aria-labelledby="class-chart"><h2 id="class-chart">Phân bố class</h2><ClassBarChart data={classData} /></section>
      <section aria-labelledby="class-heading"><h2 id="class-heading">Số bounding box theo class</h2><div className="overview-table"><table><caption>Phân bố class từ kết quả Python, giữ nguyên share_pct</caption><thead><tr><th scope="col">Class</th><th scope="col">Boxes</th><th scope="col">Ảnh có class</th><th scope="col">Tỷ lệ (%)</th><th scope="col">Phân bố số box</th></tr></thead><tbody>{data.results.classes.map((row) => <tr key={row.class}><th scope="row">{row.class}</th><td>{row.boxes}</td><td>{row.images}</td><td>{row.share_pct}</td><td><meter aria-label={`Số box ${row.class}`} min={0} max={Math.max(1, boxTotal)} value={row.boxes}>{row.boxes}</meter></td></tr>)}</tbody></table></div></section>
      <section aria-labelledby="priority-chart"><h2 id="priority-chart">Mức ưu tiên kiểm tra box</h2><StatusPieChart data={priorityData} /></section>
      <Counts title="Chi tiết mức ưu tiên" values={priorities} />
    </>}
  </>
}
