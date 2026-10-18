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
import type { DatasetSummary } from '../api/datasets'

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
    <>
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

      {canSubmitTargets && <TargetInput onApply={(targets) => update({ ...query, targets })} />}

      {error ? (
        <div className="dashboard-error-container">
          <ErrorState message={error} />
          <div className="error-actions-cluster">
            <button className="btn-primary" onClick={handleRefresh}>
              Thử lại
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
          {view.state !== 'ready' && <EmptyState>{view.reason ?? 'Chưa có kết quả phù hợp.'}</EmptyState>}
          {view.notes.map((note, i) => (
            <div key={i} className="dashboard-system-note">
              <span className="note-icon" aria-hidden="true">ℹ️</span>
              <p>{note}</p>
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
                        aria-label={metric.label}
                        className={`kpi-card ${theme}`}
                      >
                        <div className="kpi-card-header">
                          <span className="kpi-icon-bubble" aria-hidden="true">{icon}</span>
                          <dt className="kpi-label">{metric.label}</dt>
                        </div>
                        <dd className="kpi-value">
                          {metric.value === null ? 'Chưa có kết quả' : String(metric.value)}
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
              <VisualizationsSection view={view} />

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
    </>
  )
}

function VisualizationsSection({ view }: { view: DashboardView }) {
  const classesTable = view.tables.find((t) => t.key === 'classes')
  const sizesTable = view.tables.find((t) => t.key === 'sizes')
  const occlusionTable = view.tables.find((t) => t.key === 'occlusion')

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

  const elevatedKeys = new Set<string>()
  if (classDistData.length) elevatedKeys.add('classes')
  if (weatherChart) elevatedKeys.add('weather')
  if (timeofdayChart) elevatedKeys.add('timeofday')
  if (statusesChart) elevatedKeys.add('statuses')
  if (prioritiesChart) elevatedKeys.add('priorities')
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
      {(weatherChart || timeofdayChart) && (
        <div className="donut-row-grid">
          {weatherChart && (
            <section className="visualization-card" aria-labelledby="chart-weather-heading">
              <div className="card-header-bar">
                <h2 id="chart-weather-heading">{weatherChart.title}</h2>
                <span className="chart-badge">Donut Chart</span>
              </div>
              <CategoryDonutChart data={weatherChart.rows} colorScheme="weather" />
            </section>
          )}
          {timeofdayChart && (
            <section className="visualization-card" aria-labelledby="chart-timeofday-heading">
              <div className="card-header-bar">
                <h2 id="chart-timeofday-heading">{timeofdayChart.title}</h2>
                <span className="chart-badge">Donut Chart</span>
              </div>
              <CategoryDonutChart data={timeofdayChart.rows} colorScheme="timeofday" />
            </section>
          )}
        </div>
      )}

      {/* Quality Statuses and Priorities Donuts */}
      {(statusesChart || prioritiesChart) && (
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
          {prioritiesChart && (
            <section className="visualization-card" aria-labelledby="chart-priorities-heading">
              <div className="card-header-bar">
                <h2 id="chart-priorities-heading">{prioritiesChart.title}</h2>
                <span className="chart-badge">Donut Ưu tiên</span>
              </div>
              <CategoryDonutChart data={prioritiesChart.rows} colorScheme="priority" />
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

function TargetInput({ onApply }: { onApply: (targets: unknown) => void }) {
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  return (
    <form
      className="target-input-card"
      onSubmit={(event) => {
        event.preventDefault()
        try {
          const targets: unknown = JSON.parse(draft)
          setError(null)
          onApply(targets)
        } catch {
          setError('Targets phải là JSON hợp lệ; không tạo targets tự động.')
        }
      }}
    >
      <div className="target-input-header">
        <label htmlFor="target-json" className="field-label">
          Targets do người dùng cung cấp (JSON)
        </label>
        <p className="field-hint">
          Mỗi target gồm filters, unit, target và weight. Backend kiểm tra và tính kết quả; targets chưa được lưu trên server.
        </p>
      </div>
      <textarea
        id="target-json"
        className="target-textarea"
        rows={4}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder='[{"filters": {"class": "car"}, "unit": "images", "target": 50, "weight": 1.0}]'
      />
      <div className="target-actions">
        <button type="submit" className="btn-primary">
          Đánh giá targets tại backend
        </button>
        {error && <ErrorState message={error} />}
      </div>
    </form>
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

  const columns = Object.keys(table.rows[0] ?? {})

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
        const valA = cell(a[sort])
        const valB = cell(b[sort])
        const cmp = valA.localeCompare(valB, undefined, { numeric: true })
        return sortDir === 'asc' ? cmp : -cmp
      })
    : table.rows

  return (
    <section className="server-table-card" aria-labelledby={`table-${table.key}-title`}>
      <div className="server-table-header">
        <div className="table-title-group">
          <h2 id={`table-${table.key}-title`}>{table.title}</h2>
          <span className="table-count-tag">{table.rows.length.toLocaleString()} dòng</span>
        </div>
        {table.export_filename && (
          <BrowserExportButton
            tableKey={table.key}
            page={pageName}
            query={query}
            label={`Tải ${table.title} CSV`}
          />
        )}
      </div>

      {!sortedRows.length ? (
        <p className="table-empty-msg">Backend trả về danh sách rỗng.</p>
      ) : (
        <>
          <div className="table-scroll-wrapper">
            <table className="modern-data-table">
              <caption>{table.title}</caption>
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th key={column} scope="col">
                      <button
                        type="button"
                        className="th-sort-btn"
                        onClick={() => handleSort(column)}
                        title={`Sắp xếp theo ${column}`}
                      >
                        <span>{column}</span>
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
                  <tr key={i}>
                    {columns.map((column) => {
                      const val = cell(row[column])
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

          {table.key === 'preview_images' && (
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
    </section>
  )
}
