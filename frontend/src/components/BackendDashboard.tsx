import { useEffect, useState } from 'react'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { type DashboardPage, type DashboardQuery, type DashboardView } from '../api/dashboard'
import { ResultIdentity, ResultStatus } from './ResultStatus'
import { EmptyState, ErrorState, LoadingState } from './States'
import { ClassBarChart } from './Charts'
import { BrowserExportButton } from './BrowserExportButton'
import { Pagination, paginate } from './Pagination'
import { ImagePreview } from './ImagePreview'
import type { DatasetSummary } from '../api/datasets'

const titles: Record<DashboardPage, string> = { overview: 'Tổng quan dữ liệu', quality: 'Image Quality', labels: 'Label Check A+B',
  distribution: 'Phân bố & thuộc tính', review: 'Review ảnh', reports: 'Xuất báo cáo', coverage: 'Coverage & Slice', 'scene-finder': 'Scene Finder' }

function capabilityBlock(page: DashboardPage, summary: DatasetSummary | null): string | null {
  if (!['overview', 'review', 'reports'].includes(page)) return null
  if (!summary?.ready) return 'Chưa có summary capability từ backend để hiển thị trang tổng hợp.'
  const required: (keyof typeof summary.capabilities)[] = ['image_quality']
  if (summary.annotations) {
    required.push('label_audit')
    if (page !== 'review') required.push('distribution')
  }
  const unavailable = required.filter((key) => summary.capabilities[key] !== true)
  return unavailable.length ? `Backend chưa hỗ trợ capability: ${unavailable.join(', ')}. Chưa hiển thị trang tổng hợp vì projection chưa khai báo capability cho từng phần.` : null
}

export function BackendDashboard({ page }: { page: DashboardPage }) {
  const state = useVerifiedResults()
  const blocked = capabilityBlock(page, state?.activeSummary ?? null)
  return <><h1>{titles[page]}</h1><ResultIdentity />
    {!state?.activeResult || state.loading || state.error ? <ResultStatus />
      : blocked ? <EmptyState>{blocked}</EmptyState>
      : <DashboardSession key={`${state.revision}:${page}`} page={page} />}</>
}

function DashboardSession({ page }: { page: DashboardPage }) {
  const state = useVerifiedResults()!
  const [query, setQuery] = useState<DashboardQuery>({})
  const [view, setView] = useState<DashboardView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [canSubmitTargets, setCanSubmitTargets] = useState(false)
  const result = state.activeResult
  const token = state.token
  useEffect(() => {
    if (!result) return
    let active = true
    const controller = new AbortController()
    void (state.readDashboard ?? (() => Promise.reject(new Error('Dashboard transport unavailable'))))(page, result, token ?? '', query, controller.signal).then((next) => {
      if (active) { setView(next); setCanSubmitTargets(next.state !== 'unsupported' && next.can_submit_targets === true) }
    }, (failure: unknown) => {
      if (active) setError(failure instanceof Error ? failure.message : 'Không tải được dữ liệu API.')
    })
    return () => { active = false; controller.abort() }
  }, [page, result, token, query, attempt, state.readDashboard])
  const update = (next: DashboardQuery) => { setView(null); setError(null); setQuery(next) }
  return <>
    <p>Các thông số và kết quả nghiệp vụ do backend cung cấp. Không có dữ liệu mẫu thay thế.</p>
    {canSubmitTargets && <TargetInput onApply={(targets) => update({ ...query, targets })} />}
    {error ? <><ErrorState message={error} /><button onClick={() => { setError(null); setView(null); setAttempt(attempt + 1) }}>Thử lại API</button>
      <button onClick={() => update({})}>Xóa bộ lọc và tham số</button></>
      : !view ? <LoadingState /> : <>
        {view.state !== 'ready' && <EmptyState>{view.reason ?? 'Chưa có kết quả phù hợp.'}</EmptyState>}
        {view.notes.map((note, i) => <p key={i}>{note}</p>)}
        {view.state !== 'unsupported' && <>
        {view.parameters && <ServerParameters parameters={view.parameters} onApply={(values) => update({ ...query, ...values })} />}
        {view.keyword_search && <KeywordInput onApply={(keyword) => update({ ...query, keyword: keyword || null })} />}
        {view.controls.map((control) => <label key={control.key}>{control.label}
          <select value={typeof query[control.key] === 'string' ? query[control.key] as string : control.value ?? ''}
            onChange={(event) => update({ ...query, [control.key]: event.target.value || null })}>
            <option value="">Tất cả</option>{control.choices.map((choice) => <option key={choice}>{choice}</option>)}
          </select></label>)}
        <dl className="overview-metrics">{view.metrics.map((metric) => <div key={metric.key} role="group" aria-label={metric.label}>
          <dt>{metric.label}</dt><dd>{metric.value === null ? 'Chưa có kết quả' : String(metric.value)}</dd></div>)}</dl>
        {view.charts.map((chart) => <section key={chart.key}><h2>{chart.title}</h2><ClassBarChart data={chart.rows} /></section>)}
        {view.tables.map((table) => <ServerTable key={table.key} table={table} pageName={page} query={query} />)}
        </>}
      </>}
  </>
}

function KeywordInput({ onApply }: { onApply: (keyword: string) => void }) {
  const [keyword, setKeyword] = useState('')
  return <form onSubmit={(event) => { event.preventDefault(); onApply(keyword) }}>
    <label>Tìm tên ảnh<input value={keyword} onChange={(event) => setKeyword(event.target.value)} /></label>
    <button type="submit">Tìm qua backend</button></form>
}

function TargetInput({ onApply }: { onApply: (targets: unknown) => void }) {
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  return <form onSubmit={(event) => {
    event.preventDefault()
    try { const targets: unknown = JSON.parse(draft); setError(null); onApply(targets) }
    catch { setError('Targets phải là JSON hợp lệ; không tạo targets tự động.') }
  }}>
    <label>Targets do người dùng cung cấp (JSON)<textarea value={draft} onChange={(event) => setDraft(event.target.value)} /></label>
    <p>Mỗi target gồm filters, unit, target và weight. Backend kiểm tra và tính kết quả; targets chưa được lưu trên server.</p>
    <button type="submit">Đánh giá targets tại backend</button>{error && <ErrorState message={error} />}
  </form>
}

function ServerParameters({ parameters, onApply }: {
  parameters: NonNullable<DashboardView['parameters']>; onApply: (values: DashboardQuery) => void
}) {
  const [draft, setDraft] = useState(() => Object.fromEntries(parameters.map((p) => [p.key, String(p.value)])))
  return <form onSubmit={(event) => { event.preventDefault(); onApply(Object.fromEntries(parameters.map((p) => [p.key, Number(draft[p.key])])) ) }}>
    {parameters.map((parameter) => <label key={parameter.key}>{parameter.label}<input type="number" step="any" required
      value={draft[parameter.key] ?? ''} onChange={(event) => setDraft({ ...draft, [parameter.key]: event.target.value })} /></label>)}
    <button type="submit">Áp dụng tham số tại backend</button>
  </form>
}

function cell(value: unknown) {
  if (value === null || value === undefined) return '—'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}
function ServerTable({ table, pageName, query }: { table: DashboardView['tables'][number]; pageName: DashboardPage; query: DashboardQuery }) {
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(10)
  const [sort, setSort] = useState<string | null>(null)
  const [selectedImage, setSelectedImage] = useState<string | null>(null)
  const [annotated, setAnnotated] = useState(false)
  const columns = Object.keys(table.rows[0] ?? {})
  const rows = sort ? [...table.rows].sort((a, b) => cell(a[sort]).localeCompare(cell(b[sort]), undefined, { numeric: true })) : table.rows
  return <section><h2>{table.title}</h2>
    {table.export_filename && <BrowserExportButton tableKey={table.key} page={pageName} query={query} label={`Tải ${table.title} CSV`} />}
    {!rows.length ? <p>Backend trả về danh sách rỗng.</p> : <>
      <table><caption>{table.title}</caption><thead><tr>{columns.map((column) => <th key={column} scope="col">
        <button onClick={() => { setSort(column); setPage(0) }}>{column}</button></th>)}</tr></thead>
        <tbody>{paginate(rows, page, pageSize).map((row, i) => <tr key={i}>{columns.map((column) => <td key={column}>{cell(row[column])}</td>)}</tr>)}</tbody></table>
      <Pagination total={rows.length} page={page} pageSize={pageSize} onPageChange={setPage} onPageSizeChange={setPageSize} />
      {table.key === 'preview_images' && <><label>Chọn ảnh xem trước<select value={selectedImage ?? ''} onChange={(event) => setSelectedImage(event.target.value || null)}>
        <option value="">Chọn ảnh</option>{table.rows.map((row, i) => typeof row.image_path === 'string' && <option key={i} value={row.image_path}>{cell(row.image_name)}</option>)}</select></label>
        <label><input type="checkbox" checked={annotated} onChange={(event) => setAnnotated(event.target.checked)} />Hiển thị box CVAT (không mã hóa priority)</label>
        {selectedImage && <ImagePreview imagePath={selectedImage} annotated={annotated} alt={selectedImage} caption="Ảnh do backend cung cấp." />}</>}
    </>}
  </section>
}
