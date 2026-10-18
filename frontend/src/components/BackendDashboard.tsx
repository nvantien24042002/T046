import { useEffect, useState } from 'react'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { type DashboardPage, type DashboardQuery, type DashboardView } from '../api/dashboard'
import { ResultIdentity, ResultStatus } from './ResultStatus'
import { EmptyState, ErrorState, LoadingState } from './States'
import {
  ClassBarChart,
  ClassDistributionChart,
  CategoryDonutChart,
  BoxSizeChart,
  OcclusionChart,
  IssuesBarChart,
  type ClassDistributionItem,
} from './Charts'
import { QAWarningsPanel } from './QAWarningsPanel'
import { FilterAndThresholdPanel } from './FilterAndThresholdPanel'
import { BrowserExportButton } from './BrowserExportButton'
import { Pagination, paginate } from './Pagination'
import { ImagePreview } from './ImagePreview'
import { ImageGallery } from './ImageGallery'
import { publicText, publicColumn, publicCell } from './presentation'
import type { DatasetSummary } from '../api/datasets'
import { QualityAnalysisCharts, CoverageComparisonChart } from './AnalysisCharts'
import { ImageQualityConfigurationPanel } from './ImageQualityConfiguration'
import { CoverageTargetsPanel } from './CoverageTargetsPanel'
import { coverageTargetsSchema, type CoverageGeneration } from '../api/coverageTargets'

const titles: Record<DashboardPage, string> = {
  overview: 'Tổng quan dữ liệu',
  quality: 'Image Quality',
  labels: 'Label Check A+B',
  distribution: 'Phân bố & thuộc tính',
  review: 'Review ảnh',
  reports: 'Xuất báo cáo',
  coverage: 'Coverage & Slice',
  'scene-finder': 'Scene Finder',
}

function capabilityBlock(page: DashboardPage, summary: DatasetSummary | null): string | null {
  if (!['overview', 'review', 'reports'].includes(page)) return null
  if (!summary?.ready) return 'Chưa có summary capability từ backend để hiển thị trang tổng hợp.'
  const required: (keyof typeof summary.capabilities)[] = ['image_quality']
  if (summary.annotations) {
    required.push('label_audit')
    if (page !== 'review') required.push('distribution')
  }
  const unavailable = required.filter((key) => summary.capabilities[key] !== true)
  return unavailable.length
    ? `Backend chưa hỗ trợ capability: ${unavailable.join(', ')}. Chưa hiển thị trang tổng hợp vì projection chưa khai báo capability cho từng phần.`
    : null
}

export function BackendDashboard({ page }: { page: DashboardPage }) {
  const state = useVerifiedResults()
  const blocked = capabilityBlock(page, state?.activeSummary ?? null)
  return (
    <div className="backend-dashboard-page">
      <div className="dashboard-top-header">
        <div className="header-breadcrumbs">
          <span className="platform-tag">CVAT QA/QC Platform · N2-05B</span>
        </div>
        <div className="header-title-row">
          <h1>{titles[page]}</h1>
        </div>
        <ResultIdentity />
      </div>

      {page === 'quality' && !(state?.activeSummary?.ready && !state.activeSummary.capabilities.image_quality) && <ImageQualityConfigurationPanel />}

      {!state?.activeResult || state.loading || state.error ? (
        <ResultStatus />
      ) : blocked ? (
        <EmptyState>{blocked}</EmptyState>
      ) : (
        <DashboardSession key={`${state.revision}:${page}`} page={page} />
      )}
    </div>
  )
}

function getKpiTheme(key: string, label: string) {
  const lower = (key + ' ' + label).toLowerCase()
  if (lower.includes('error') || lower.includes('lỗi') || lower.includes('deficit')) {
    return 'kpi-danger'
  }
  if (lower.includes('review') || lower.includes('cảnh báo') || lower.includes('high') || lower.includes('unresolved')) {
    return 'kpi-warning'
  }
  if (lower.includes('pass') || lower.includes('met') || lower.includes('available')) {
    return 'kpi-success'
  }
  if (lower.includes('box')) {
    return 'kpi-indigo'
  }
  if (lower.includes('class')) {
    return 'kpi-teal'
  }
  if (lower.includes('score') || lower.includes('coverage') || lower.includes('attribute')) {
    return 'kpi-purple'
  }
  return 'kpi-navy'
}

function getKpiIcon(key: string, label: string) {
  const lower = (key + ' ' + label).toLowerCase()
  if (lower.includes('image') || lower.includes('ảnh')) return '🖼️'
  if (lower.includes('box')) return '📦'
  if (lower.includes('class')) return '🏷️'
  if (lower.includes('error') || lower.includes('review') || lower.includes('high') || lower.includes('unresolved')) return '⚠️'
  if (lower.includes('pass') || lower.includes('met')) return '✓'
  if (lower.includes('score') || lower.includes('coverage') || lower.includes('attribute')) return '🎯'
  return '📊'
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
    void (state.readDashboard ?? (() => Promise.reject(new Error('Dashboard transport unavailable'))))(
      page,
      result,
      token ?? '',
      query,
      controller.signal
    ).then(
      (next) => {
        if (active) {
          setView(next)
          setCanSubmitTargets(next.state !== 'unsupported' && next.can_submit_targets === true)
        }
      },
      (failure: unknown) => {
        if (active) setError(failure instanceof Error ? failure.message : 'Không tải được dữ liệu API.')
      }
    )
    return () => {
      active = false
      controller.abort()
    }
  }, [page, result, token, query, attempt, state.readDashboard])

  const update = (next: DashboardQuery) => {
    setView(null)
    setError(null)
    setQuery(next)
  }

  const handleRefresh = () => {
    setError(null)
    setView(null)
    setAttempt((val) => val + 1)
  }

  return (
    <div className="dashboard-page-container" data-page={page} data-state={view ? view.state : 'loading'}>
      <div className="dashboard-toolbar">
        <button
          type="button"
          className="btn-compact-refresh"
          onClick={handleRefresh}
          title="Tải lại dữ liệu trang"
        >
          <span className="refresh-icon" aria-hidden="true">↻</span>
          <span>Làm mới</span>
        </button>
      </div>

      {page === 'coverage' && canSubmitTargets && <CoverageTargetsPanel
        applied={view?.tables.find((table) => table.key === 'targets')?.rows ?? (view ? [] : undefined)}
        busy={!view && !error}
        choices={{
          class: [...new Set(result?.payload.distribution_boxes.map((r) => r.label))],
          weather: [...new Set(result?.payload.distribution_images.map((r) => r.weather))],
          timeofday: [...new Set(result?.payload.distribution_images.map((r) => r.timeofday))],
          occluded: ['yes', 'no', 'unknown'],
        }}
        onApply={(targets) => update({ targets })}
        onGenerate={async (generate: CoverageGeneration, signal) => {
          if (!result || !state.readDashboard) throw new Error('Không có dữ liệu để sinh tổ hợp.')
          const next = await state.readDashboard('coverage', result, token ?? '', { generate }, signal)
          if (next.state === 'unsupported') throw new Error('Không hỗ trợ sinh tổ hợp.')
          return coverageTargetsSchema.parse(next.tables.find((table) => table.key === 'targets')?.rows ?? [])
        }}
      />}

      {error ? (
        <div className="dashboard-error-container">
          <ErrorState message={error} />
          <div className="error-actions-cluster">
            <button className="btn-primary" onClick={handleRefresh}>
              Thử lại API
            </button>
            <button className="btn-secondary" onClick={() => update({})}>
              Xóa bộ lọc và tham số
            </button>
          </div>
        </div>
      ) : !view ? (
        <LoadingState />
      ) : (
        <>
          {view.state !== 'ready' && <EmptyState>{publicText(view.reason ?? 'Chưa có kết quả phù hợp.')}</EmptyState>}
          {view.notes.map((note, i) => (
            <div key={i} className="dashboard-system-note">
              <span className="note-icon" aria-hidden="true">ℹ️</span>
              <p>{publicText(note)}</p>
            </div>
          ))}

          {view.state !== 'unsupported' && (
            <>
              {/* Grouped Thresholds and Filters Panel */}
              <FilterAndThresholdPanel
                view={view}
                query={query}
                onUpdate={update}
                onReset={() => update({})}
              />

              {/* KPI Cards Grid */}
              <section aria-label="Các chỉ số KPI chính" className="kpi-section-container">
                <dl className="overview-metrics kpi-grid">
                  {view.metrics.map((metric) => {
                    const theme = getKpiTheme(metric.key, metric.label)
                    const icon = getKpiIcon(metric.key, metric.label)
                    return (
                      <div
                        key={metric.key}
                        role="group"
                        aria-label={publicText(metric.label)}
                        className={`kpi-card ${theme}`}
                      >
                        <div className="kpi-card-header">
                          <span className="kpi-icon-bubble" aria-hidden="true">{icon}</span>
                          <dt className="kpi-label">{publicText(metric.label)}</dt>
                        </div>
                        <dd className="kpi-value">
                          {metric.value === null ? 'Chưa có kết quả' : publicCell(metric.value)}
                        </dd>
                        <div className="kpi-card-footer">
                          <div className="kpi-mini-track" aria-hidden="true">
                            <div className="kpi-mini-bar" />
                          </div>
                          <span className="kpi-footer-badge">Backend Verified</span>
                        </div>
                      </div>
                    )
                  })}
                </dl>
              </section>

              {/* QA/QC Warnings and Alerts Panel */}
              <QAWarningsPanel view={view} page={page} />

              {/* Enhanced Visualizations */}
              <VisualizationsSection view={view} page={page} />
              {page === 'quality' && <QualityAnalysisCharts view={view} />}
              {page === 'coverage' && <CoverageComparisonChart key={JSON.stringify(query)} view={view} />}

              {/* Server Tables */}
              <div className="server-tables-container">
                {view.tables.map((table) => (
                  <ServerTable key={table.key} table={table} pageName={page} query={query} />
                ))}
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}

function VisualizationsSection({ view, page }: { view: DashboardView; page?: DashboardPage }) {
  const classesTable = view.tables.find((t) => t.key === 'classes')
  const sizesTable = view.tables.find((t) => t.key === 'sizes')
  const occlusionTable = view.tables.find((t) => t.key === 'occlusion')
  const auditTable = view.tables.find((t) => t.key === 'audit')
  const labelClassesTable = view.tables.find((t) => t.key === 'label_classes')
  const previewImagesTable = view.tables.find((t) => t.key === 'preview_images')

  const classesChart = view.charts.find((c) => c.key === 'classes')
  const weatherChart = view.charts.find((c) => c.key === 'weather')
  const timeofdayChart = view.charts.find((c) => c.key === 'timeofday')
  const statusesChart = view.charts.find((c) => c.key === 'statuses')
  const prioritiesChart = view.charts.find((c) => c.key === 'priorities')
  const issuesChart = view.charts.find((c) => c.key === 'issues')


  // Prepare enriched class distribution data
  let classDistData: ClassDistributionItem[] = []
  if (classesTable?.rows.length) {
    classDistData = classesTable.rows.map((r) => ({
      name: String(r['class'] ?? r['name'] ?? ''),
      value: Number(r['boxes'] ?? r['value'] ?? 0),
      share_pct: typeof r['share_pct'] === 'number' ? r['share_pct'] : undefined,
      images: typeof r['images'] === 'number' ? r['images'] : undefined,
    }))
  } else if (classesChart?.rows.length) {
    classDistData = classesChart.rows.map((r) => ({
      name: r.name,
      value: r.value,
    }))
  }

  // Derive weather and timeofday charts for scene-finder if missing from view.charts
  let effectiveWeatherChart = weatherChart
  let effectiveTimeofdayChart = timeofdayChart
  if (page === 'scene-finder' && (!weatherChart || !timeofdayChart) && previewImagesTable?.rows.length) {
    const wCounts: Record<string, number> = {}
    const tCounts: Record<string, number> = {}
    for (const r of previewImagesTable.rows) {
      const w = String(r['weather'] ?? 'missing')
      const tod = String(r['timeofday'] ?? 'missing')
      wCounts[w] = (wCounts[w] ?? 0) + 1
      tCounts[tod] = (tCounts[tod] ?? 0) + 1
    }
    if (!weatherChart && Object.keys(wCounts).length > 0) {
      effectiveWeatherChart = {
        key: 'weather',
        title: 'Phân bố Thời tiết (Filtered)',
        rows: Object.entries(wCounts).map(([name, value]) => ({ name, value })),
      }
    }
    if (!timeofdayChart && Object.keys(tCounts).length > 0) {
      effectiveTimeofdayChart = {
        key: 'timeofday',
        title: 'Phân bố Thời điểm (Filtered)',
        rows: Object.entries(tCounts).map(([name, value]) => ({ name, value })),
      }
    }
  }

  // Derive priorities chart for labels if missing from view.charts
  let effectivePrioritiesChart = prioritiesChart
  if (page === 'labels' && !prioritiesChart && auditTable?.rows.length) {
    const pCounts: Record<string, number> = { HIGH: 0, MEDIUM: 0, LOW: 0 }
    for (const r of auditTable.rows) {
      const p = String(r['priority'] ?? '').toUpperCase()
      if (p === 'HIGH' || p === 'MEDIUM' || p === 'LOW') {
        pCounts[p] = (pCounts[p] ?? 0) + 1
      }
    }
    const pRows = Object.entries(pCounts)
      .filter(([, v]) => v > 0)
      .map(([name, value]) => ({ name, value }))
    if (pRows.length > 0) {
      effectivePrioritiesChart = {
        key: 'priorities',
        title: 'Phân bố mức độ ưu tiên (Priority)',
        rows: pRows,
      }
    }
  }

  // Derive class review rate chart for labels from label_classes table
  let classReviewRateData: { name: string; value: number }[] = []
  if (page === 'labels' && labelClassesTable?.rows.length) {
    classReviewRateData = labelClassesTable.rows
      .filter((r) => typeof r['review_rate_pct'] === 'number')
      .map((r) => ({
        name: String(r['label'] ?? ''),
        value: Number(r['review_rate_pct'] ?? 0),
      }))
  }

  const elevatedKeys = new Set<string>()
  if (classDistData.length) elevatedKeys.add('classes')
  if (effectiveWeatherChart) elevatedKeys.add('weather')
  if (effectiveTimeofdayChart) elevatedKeys.add('timeofday')
  if (statusesChart) elevatedKeys.add('statuses')
  if (effectivePrioritiesChart) elevatedKeys.add('priorities')
  if (issuesChart) elevatedKeys.add('issues')

  const remainingCharts = view.charts.filter((c) => !elevatedKeys.has(c.key))

  return (
    <div className="visualizations-grid">
      {/* Class distribution Bar Chart with counts and percentage */}
      {classDistData.length > 0 && (
        <section className="visualization-card full-width" aria-labelledby="chart-classes-heading">
          <div className="card-header-bar">
            <h2 id="chart-classes-heading">{classesChart?.title ?? 'Phân bố class'}</h2>
            <span className="chart-badge">Bar Chart: Số lượng & Tỷ lệ %</span>
          </div>
          <ClassDistributionChart data={classDistData} />
        </section>
      )}

      {/* Class review rate bar chart for Label Check */}
      {classReviewRateData.length > 0 && (
        <section className="visualization-card full-width" aria-labelledby="chart-class-review-rate-heading">
          <div className="card-header-bar">
            <h2 id="chart-class-review-rate-heading">Tỷ lệ cần review theo class (%)</h2>
            <span className="chart-badge">Bar Chart: Review Rate (%)</span>
          </div>
          <ClassBarChart data={classReviewRateData} />
        </section>
      )}

      {/* BBox Sizes Chart (from sizes table) */}
      {sizesTable?.rows && sizesTable.rows.length > 0 && (
        <section className="visualization-card full-width" aria-labelledby="chart-sizes-heading">
          <div className="card-header-bar">
            <h2 id="chart-sizes-heading">{sizesTable.title}</h2>
            <span className="chart-badge">Thống kê Diện tích Box (% ảnh)</span>
          </div>
          <BoxSizeChart
            data={sizesTable.rows.map((r) => ({
              label: String(r['label'] ?? ''),
              count: Number(r['count'] ?? 0),
              mean: Number(r['mean'] ?? 0),
              median: Number(r['median'] ?? 0),
              min: Number(r['min'] ?? 0),
              max: Number(r['max'] ?? 0),
            }))}
          />
        </section>
      )}

      {/* Occlusion Chart (from occlusion table) */}
      {occlusionTable?.rows && occlusionTable.rows.length > 0 && (
        <section className="visualization-card full-width" aria-labelledby="chart-occlusion-heading">
          <div className="card-header-bar">
            <h2 id="chart-occlusion-heading">{occlusionTable.title}</h2>
            <span className="chart-badge">Stacked Bar Che khuất</span>
          </div>
          <OcclusionChart
            rows={occlusionTable.rows.map((r) => ({
              label: String(r['label'] ?? ''),
              occluded: r['occluded'] as boolean | string | number,
              boxes: Number(r['boxes'] ?? 0),
            }))}
          />
        </section>
      )}

      {/* Weather and Time of Day Donuts */}
      {(effectiveWeatherChart || effectiveTimeofdayChart) && (
        <div className="donut-row-grid">
          {effectiveWeatherChart && (
            <section className="visualization-card" aria-labelledby="chart-weather-heading">
              <div className="card-header-bar">
                <h2 id="chart-weather-heading">{effectiveWeatherChart.title}</h2>
                <span className="chart-badge">Donut Chart</span>
              </div>
              <CategoryDonutChart data={effectiveWeatherChart.rows} colorScheme="weather" />
            </section>
          )}
          {effectiveTimeofdayChart && (
            <section className="visualization-card" aria-labelledby="chart-timeofday-heading">
              <div className="card-header-bar">
                <h2 id="chart-timeofday-heading">{effectiveTimeofdayChart.title}</h2>
                <span className="chart-badge">Donut Chart</span>
              </div>
              <CategoryDonutChart data={effectiveTimeofdayChart.rows} colorScheme="timeofday" />
            </section>
          )}
        </div>
      )}

      {/* Quality Statuses and Priorities Donuts */}
      {(statusesChart || effectivePrioritiesChart) && (
        <div className="donut-row-grid">
          {statusesChart && (
            <section className="visualization-card" aria-labelledby="chart-statuses-heading">
              <div className="card-header-bar">
                <h2 id="chart-statuses-heading">{statusesChart.title}</h2>
                <span className="chart-badge">Donut Trạng thái</span>
              </div>
              <CategoryDonutChart data={statusesChart.rows} colorScheme="status" />
            </section>
          )}
          {effectivePrioritiesChart && (
            <section className="visualization-card" aria-labelledby="chart-priorities-heading">
              <div className="card-header-bar">
                <h2 id="chart-priorities-heading">{effectivePrioritiesChart.title}</h2>
                <span className="chart-badge">Donut Ưu tiên</span>
              </div>
              <CategoryDonutChart data={effectivePrioritiesChart.rows} colorScheme="priority" />
            </section>
          )}
        </div>
      )}

      {/* Issues Frequency Chart */}
      {issuesChart && issuesChart.rows.length > 0 && (
        <section className="visualization-card full-width" aria-labelledby="chart-issues-heading">
          <div className="card-header-bar">
            <h2 id="chart-issues-heading">{issuesChart.title}</h2>
            <span className="chart-badge">Tần suất Cảnh báo</span>
          </div>
          <IssuesBarChart data={issuesChart.rows} />
        </section>
      )}

      {/* Any remaining backend charts */}
      {remainingCharts.map((chart) => (
        <section key={chart.key} className="visualization-card">
          <div className="card-header-bar">
            <h2>{chart.title}</h2>
          </div>
          <ClassBarChart data={chart.rows} />
        </section>
      ))}
    </div>
  )
}

function cell(value: unknown) {
  if (value === null || value === undefined) return '—'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function ServerTable({
  table,
  pageName,
  query,
}: {
  table: DashboardView['tables'][number]
  pageName: DashboardPage
  query: DashboardQuery
}) {
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(10)
  const [sort, setSort] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [selectedImage, setSelectedImage] = useState<string | null>(null)
  const [annotated, setAnnotated] = useState(false)

  // Scene Finder Gallery state
  const isSceneFinder = pageName === 'scene-finder' && table.key === 'preview_images'
  const [sceneViewMode, setSceneViewMode] = useState<'gallery' | 'table'>('gallery')
  const [galleryPage, setGalleryPage] = useState(0)
  const [galleryPageSize, setGalleryPageSize] = useState(8)

  // Label Check Flagged Box Inspector state
  const isAuditTable = pageName === 'labels' && table.key === 'audit'
  const [selectedAuditIndex, setSelectedAuditIndex] = useState<number | null>(null)

  const columns = [...new Set(table.rows.flatMap((row) => Object.keys(row)))].filter(publicColumn)

  const handleSort = (column: string) => {
    if (sort === column) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSort(column)
      setSortDir('asc')
    }
    setPage(0)
  }

  const sortedRows = sort
    ? [...table.rows].sort((a, b) => {
        const valA = publicCell(a[sort])
        const valB = publicCell(b[sort])
        const cmp = valA.localeCompare(valB, undefined, { numeric: true })
        return sortDir === 'asc' ? cmp : -cmp
      })
    : table.rows

  const selectedAuditRow = selectedAuditIndex !== null ? sortedRows[selectedAuditIndex] : null

  return (
    <section className="server-table-card" aria-labelledby={`table-${table.key}-title`}>
      <div className="server-table-header">
        <div className="table-title-group">
          <h2 id={`table-${table.key}-title`}>{publicText(table.title)}</h2>
          <span className="table-count-tag">{table.rows.length.toLocaleString()} dòng</span>
        </div>
        <div className="table-header-controls">
          {isSceneFinder && (
            <div className="view-mode-toggle-group" role="group" aria-label="Chế độ hiển thị">
              <button
                type="button"
                className={`btn-view-toggle ${sceneViewMode === 'gallery' ? 'active' : ''}`}
                onClick={() => setSceneViewMode('gallery')}
              >
                🖼️ Thư viện ảnh ({sortedRows.length})
              </button>
              <button
                type="button"
                className={`btn-view-toggle ${sceneViewMode === 'table' ? 'active' : ''}`}
                onClick={() => setSceneViewMode('table')}
              >
                📋 Bảng chi tiết
              </button>
            </div>
          )}
          {table.export_filename && (
            <BrowserExportButton
              tableKey={table.key}
              page={pageName}
              query={query}
              label={`Tải ${publicText(table.title)} CSV`}
            />
          )}
        </div>
      </div>

      {!sortedRows.length ? (
        <p className="table-empty-msg">Backend trả về danh sách rỗng.</p>
      ) : (
        <>
          {/* Label Check Flagged Item Inspector */}
          {isAuditTable && selectedAuditRow && (
            <div className="audit-box-inspector-card" role="region" aria-label="Xem trước và kiểm tra bounding box">
              <div className="inspector-header">
                <div className="inspector-title-cluster">
                  <span className="inspector-badge">Flagged Box Inspector</span>
                  <h3>Box: {cell(selectedAuditRow.box_id)} · Class: {cell(selectedAuditRow.label)}</h3>
                  <span className={`cell-pill status-${cell(selectedAuditRow.priority).toLowerCase()}`}>
                    {cell(selectedAuditRow.priority)}
                  </span>
                  <span className={`cell-pill status-${cell(selectedAuditRow.status).toLowerCase()}`}>
                    {cell(selectedAuditRow.status)}
                  </span>
                </div>
                <div className="inspector-nav-cluster">
                  <button
                    type="button"
                    className="btn-nav"
                    disabled={selectedAuditIndex === 0}
                    onClick={() => setSelectedAuditIndex((selectedAuditIndex ?? 0) - 1)}
                    title="Box trước"
                  >
                    ← Trước
                  </button>
                  <span className="nav-counter">
                    {(selectedAuditIndex ?? 0) + 1} / {sortedRows.length}
                  </span>
                  <button
                    type="button"
                    className="btn-nav"
                    disabled={selectedAuditIndex === sortedRows.length - 1}
                    onClick={() => setSelectedAuditIndex((selectedAuditIndex ?? 0) + 1)}
                    title="Box tiếp"
                  >
                    Tiếp →
                  </button>
                  <button
                    type="button"
                    className="btn-close-inspector"
                    onClick={() => setSelectedAuditIndex(null)}
                    title="Đóng inspector"
                  >
                    ✕
                  </button>
                </div>
              </div>

              <div className="inspector-body-grid">
                <div className="inspector-preview-col">
                  <ImagePreview
                    imagePath={String(selectedAuditRow.image_name)}
                    annotated={true}
                    alt={String(selectedAuditRow.image_name)}
                    caption={`Ảnh: ${cell(selectedAuditRow.image_name)} · Box: ${cell(selectedAuditRow.box_id)}`}
                  />
                </div>
                <div className="inspector-meta-col">
                  <dl className="inspector-meta-list">
                    <dt>Ảnh</dt>
                    <dd>{cell(selectedAuditRow.image_name)}</dd>
                    <dt>Tín hiệu Geometry</dt>
                    <dd>{cell(selectedAuditRow.geometry_issues) || 'Không có cảnh báo'}</dd>
                    <dt>Tín hiệu Visual</dt>
                    <dd>{cell(selectedAuditRow.visual_issues) || 'Không có cảnh báo'}</dd>
                    <dt>Tọa độ Box (xtl, ytl, xbr, ybr)</dt>
                    <dd>{`${cell(selectedAuditRow.xtl)}, ${cell(selectedAuditRow.ytl)}, ${cell(selectedAuditRow.xbr)}, ${cell(selectedAuditRow.ybr)}`}</dd>
                    <dt>Diện tích / Ảnh (%)</dt>
                    <dd>{typeof selectedAuditRow.area_pct === 'number' ? `${selectedAuditRow.area_pct.toFixed(2)}%` : cell(selectedAuditRow.area_pct)}</dd>
                    <dt>Tỷ lệ khung hình (Aspect Ratio)</dt>
                    <dd>{typeof selectedAuditRow.aspect_ratio === 'number' ? selectedAuditRow.aspect_ratio.toFixed(2) : cell(selectedAuditRow.aspect_ratio)}</dd>
                    {selectedAuditRow.edge_density !== undefined && (
                      <>
                        <dt>Edge Density</dt>
                        <dd>{cell(selectedAuditRow.edge_density)}</dd>
                      </>
                    )}
                    {selectedAuditRow.foreground_contrast !== undefined && (
                      <>
                        <dt>Foreground Contrast</dt>
                        <dd>{cell(selectedAuditRow.foreground_contrast)}</dd>
                      </>
                    )}
                  </dl>
                </div>
              </div>
            </div>
          )}

          {/* Scene Finder Gallery View */}
          {isSceneFinder && sceneViewMode === 'gallery' ? (
            <div className="scene-finder-gallery-section">
              <div className="gallery-toolbar-row">
                <label className="checkbox-toggle-label">
                  <input
                    type="checkbox"
                    checked={annotated}
                    onChange={(e) => setAnnotated(e.target.checked)}
                  />
                  <span>Hiển thị box CVAT</span>
                </label>
                <div className="gallery-size-select">
                  <label htmlFor="gallery-page-size" className="field-label-inline">
                    Ảnh mỗi trang:
                  </label>
                  <select
                    id="gallery-page-size"
                    className="field-select-compact"
                    value={galleryPageSize}
                    onChange={(e) => {
                      setGalleryPageSize(Number(e.target.value))
                      setGalleryPage(0)
                    }}
                  >
                    <option value={4}>4 ảnh</option>
                    <option value={8}>8 ảnh</option>
                    <option value={12}>12 ảnh</option>
                    <option value={20}>20 ảnh</option>
                  </select>
                </div>
              </div>

              <div className="scene-gallery-grid">
                {paginate(sortedRows, galleryPage, galleryPageSize).map((row, i) => {
                  const imgName = String(row.image_name ?? '')
                  const imgPath = typeof row.image_path === 'string' ? row.image_path : imgName
                  const weather = String(row.weather ?? 'missing')
                  const timeofday = String(row.timeofday ?? 'missing')
                  const isWeatherUnresolved = ['missing', 'unknown'].includes(weather.toLowerCase())
                  const isTodUnresolved = ['missing', 'unknown'].includes(timeofday.toLowerCase())

                  return (
                    <div
                      key={i}
                      role="button"
                      tabIndex={0}
                      className={`scene-gallery-card ${selectedImage === imgPath ? 'card-selected' : ''}`}
                      onClick={() => setSelectedImage(imgPath)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          setSelectedImage(imgPath)
                        }
                      }}
                    >
                      <div className="gallery-card-header">
                        <strong className="gallery-card-title" title={imgName}>{imgName}</strong>
                        <div className="gallery-card-badges">
                          <span className={`meta-tag ${isWeatherUnresolved ? 'meta-unresolved' : 'meta-weather'}`}>
                            Weather: {weather}
                          </span>
                          <span className={`meta-tag ${isTodUnresolved ? 'meta-unresolved' : 'meta-tod'}`}>
                            Time: {timeofday}
                          </span>
                        </div>
                      </div>
                      <div className="gallery-card-preview-area">
                        {imgPath ? (
                          <ImagePreview
                            imagePath={imgPath}
                            annotated={annotated}
                            alt={imgName}
                            caption={`Weather: ${weather} | Time: ${timeofday}`}
                          />
                        ) : (
                          <div className="preview-placeholder">
                            <p className="preview-note">Không có đường dẫn ảnh.</p>
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>

              <Pagination
                total={sortedRows.length}
                page={galleryPage}
                pageSize={galleryPageSize}
                onPageChange={setGalleryPage}
                onPageSizeChange={setGalleryPageSize}
              />
            </div>
          ) : table.key === 'preview_images' && !isSceneFinder ? (
            <div className="review-gallery-section">
              <ImageGallery rows={paginate(sortedRows, page, pageSize)} />
              <Pagination
                total={sortedRows.length}
                page={page}
                pageSize={pageSize}
                onPageChange={setPage}
                onPageSizeChange={setPageSize}
              />
            </div>
          ) : (
            <>
              {/* Table View */}
              <div className="table-scroll-wrapper" role="region" aria-label={publicText(table.title)}>
                <table className="modern-data-table">
                  <caption>{publicText(table.title)}</caption>
                  <thead>
                    <tr>
                      {isAuditTable && <th scope="col">Xem box</th>}
                      {columns.map((column) => (
                        <th key={column} scope="col">
                          <button
                            type="button"
                            className="th-sort-btn"
                            onClick={() => handleSort(column)}
                            title={`Sắp xếp theo ${publicText(column)}`}
                          >
                            <span>{publicText(column)}</span>
                            {sort === column && (
                              <span className="sort-arrow">{sortDir === 'asc' ? ' ▲' : ' ▼'}</span>
                            )}
                          </button>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {paginate(sortedRows, page, pageSize).map((row, i) => (
                      <tr key={i} className={isAuditTable && selectedAuditIndex === page * pageSize + i ? 'row-selected' : ''}>
                        {isAuditTable && (
                          <td className="cell-action">
                            <button
                              type="button"
                              className="btn-inspect-box"
                              onClick={() => setSelectedAuditIndex(page * pageSize + i)}
                              title="Xem trước ảnh và bounding box của hàng này"
                            >
                              👁️ Xem box
                            </button>
                          </td>
                        )}
                        {columns.map((column) => {
                          const val = publicCell(row[column])
                          const isStatus = ['status', 'priority', 'quality_status'].includes(column.toLowerCase())
                          return (
                            <td key={column}>
                              {isStatus ? (
                                <span className={`cell-pill status-${val.toLowerCase()}`}>{val}</span>
                              ) : (
                                val
                              )}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <Pagination
                total={sortedRows.length}
                page={page}
                pageSize={pageSize}
                onPageChange={setPage}
                onPageSizeChange={setPageSize}
              />

              {isSceneFinder && (
                <div className="table-preview-controls">
                  <div className="preview-select-row">
                    <label className="field-label" htmlFor="select-preview-img">
                      Chọn ảnh xem trước
                    </label>
                    <select
                      id="select-preview-img"
                      className="field-select"
                      value={selectedImage ?? ''}
                      onChange={(event) => setSelectedImage(event.target.value || null)}
                    >
                      <option value="">Chọn ảnh</option>
                      {table.rows.map(
                        (row, i) =>
                          typeof row.image_path === 'string' && (
                            <option key={i} value={row.image_path}>
                              {cell(row.image_name)}
                            </option>
                          )
                      )}
                    </select>
                  </div>

                  <label className="checkbox-toggle-label">
                    <input
                      type="checkbox"
                      checked={annotated}
                      onChange={(event) => setAnnotated(event.target.checked)}
                    />
                    <span>Hiển thị box CVAT (không mã hóa priority)</span>
                  </label>

                  {selectedImage && (
                    <div className="image-preview-embed">
                      <ImagePreview
                        imagePath={selectedImage}
                        annotated={annotated}
                        alt={selectedImage}
                        caption="Ảnh do backend cung cấp."
                      />
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  )
}
