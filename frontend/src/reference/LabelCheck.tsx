import { ResultIdentity } from '../components/ResultStatus'
import { useState, useEffect, useMemo } from 'react'
import { config } from '../config'
import { loadMockLabels, labelConfigurationKey, LabelScenario, LabelSnapshot } from '../mocks/labels'
import { decodeLabels, filterLabels, labelSummary, labelPriorities, AuditRow, LabelData } from '../api/labels'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { EmptyState, ErrorState, LoadingState } from '../components/States'

type State = { kind: 'loading' } | { kind: 'ready'; data: LabelData } | { kind: 'error'; message?: string }
type Loader = (scenario: LabelScenario) => Promise<LabelSnapshot>

interface Props {
  load?: Loader
}

function LabelView({
  rows,
}: {
  rows: AuditRow[]
}) {
  const allAvailableClasses = useMemo(() => {
    const s = new Set<string>()
    for (const r of rows) s.add(r.label)
    return Array.from(s).sort()
  }, [rows])

  const [excludedClasses, setExcludedClasses] = useState<string[]>([])
  const selectedClasses = useMemo(() => {
    return allAvailableClasses.filter((c) => !excludedClasses.includes(c))
  }, [allAvailableClasses, excludedClasses])

  const [selectedPriorities, setSelectedPriorities] = useState<string[]>([...labelPriorities])
  const [onlyReview, setOnlyReview] = useState(true)

  const filteredRows = useMemo(() => {
    return filterLabels(rows, {
      classes: selectedClasses,
      priorities: selectedPriorities,
      onlyReview,
    })
  }, [rows, selectedClasses, selectedPriorities, onlyReview])

  const summary = useMemo(() => {
    return labelSummary(filteredRows)
  }, [filteredRows])

  const handleResetFilters = () => {
    setExcludedClasses([])
    setSelectedPriorities([...labelPriorities])
    setOnlyReview(true)
  }

  return (
    <div className="label-check-content">
      {/* Metrics Groups */}
      <div className="metrics-grid">
        <div role="group" aria-label="Tổng box trong bộ lọc" className="metric-card">
          <span className="metric-label">Tổng box trong bộ lọc</span>
          <span className="metric-value">{summary.boxes}</span>
        </div>
        <div role="group" aria-label="Box cần review" className="metric-card">
          <span className="metric-label">Box cần review</span>
          <span className="metric-value">{summary.reviews}</span>
        </div>
        <div role="group" aria-label="HIGH cần review" className="metric-card">
          <span className="metric-label">HIGH cần review</span>
          <span className="metric-value">{summary.high}</span>
        </div>
        <div role="group" aria-label="Có ảnh kiểm tra B" className="metric-card">
          <span className="metric-label">Có ảnh kiểm tra B</span>
          <span className="metric-value">{summary.imagesAvailable}</span>
        </div>
      </div>

      {/* Filters Form */}
      <div className="filters-panel">
        <label>
          <input
            type="checkbox"
            checked={onlyReview}
            onChange={(e) => setOnlyReview(e.target.checked)}
          />
          Chỉ hiện box có cảnh báo
        </label>

        <div className="priority-filters">
          {labelPriorities.map((p) => (
            <label key={p}>
              <input
                type="checkbox"
                checked={selectedPriorities.includes(p)}
                onChange={(e) => {
                  if (e.target.checked) setSelectedPriorities([...selectedPriorities, p])
                  else setSelectedPriorities(selectedPriorities.filter((x) => x !== p))
                }}
              />
              Priority {p}
            </label>
          ))}
        </div>

        <div className="class-filters">
          {allAvailableClasses.map((cls) => (
            <label key={cls}>
              <input
                type="checkbox"
                checked={selectedClasses.includes(cls)}
                onChange={(e) => {
                  if (e.target.checked) setExcludedClasses(excludedClasses.filter((x) => x !== cls))
                  else setExcludedClasses([...excludedClasses, cls])
                }}
              />
              Class {cls}
            </label>
          ))}
        </div>

        <button type="button" onClick={handleResetFilters}>
          Đặt lại bộ lọc
        </button>
      </div>

      {/* Conditional Chart / Image */}
      {summary.issues.length > 0 ? (
        <div className="issues-chart">
          <img src="/placeholder-chart.svg" alt="Biểu đồ phân bố cảnh báo" />
        </div>
      ) : (
        <p className="no-signal">Không có tín hiệu trong các box đang hiển thị.</p>
      )}

      {/* If no filtered rows, do not render unfiltered tables */}
      {filteredRows.length === 0 ? (
        <p className="empty-message">Không có box khớp bộ lọc</p>
      ) : (
        <>
          {/* Class Summary Table */}
          <section aria-label="Thống kê theo class (đã lọc)">
            <h2>Thống kê theo class (đã lọc)</h2>
            <table>
              <caption>Tổng hợp review rate theo class</caption>
              <thead>
                <tr>
                  <th scope="col">Class</th>
                  <th scope="col">Boxes</th>
                  <th scope="col">Reviews</th>
                  <th scope="col">Images</th>
                  <th scope="col">Review Rate (%)</th>
                </tr>
              </thead>
              <tbody>
                {summary.classes.map((c) => (
                  <tr key={c.label}>
                    <th scope="row">{c.label}</th>
                    <td>{c.boxes}</td>
                    <td>{c.reviews}</td>
                    <td>{c.images}</td>
                    <td>{c.reviewRatePct}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {/* Details Table */}
          <div className="details-table-container">
            <table>
              <caption>Kiểm tra nhãn Geometry A và Visual B riêng biệt</caption>
              <thead>
                <tr>
                  <th scope="col">box_id</th>
                  <th scope="col">label</th>
                  <th scope="col">image_name</th>
                  <th scope="col">status</th>
                  <th scope="col">priority</th>
                  <th scope="col">geometry_issues</th>
                  <th scope="col">visual_issues</th>
                  <th scope="col">xtl</th>
                  <th scope="col">ytl</th>
                  <th scope="col">xbr</th>
                  <th scope="col">image_available</th>
                  <th scope="col">ybr</th>
                  <th scope="col">occluded</th>
                  <th scope="col">area_pct</th>
                  <th scope="col">aspect_ratio</th>
                  <th scope="col">edge_density</th>
                  <th scope="col">texture_variance</th>
                  <th scope="col">laplacian_variance</th>
                  <th scope="col">foreground_contrast</th>
                  <th scope="col">boundary_support</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((row) => (
                  <tr key={row.box_id}>
                    <td>{row.box_id}</td>
                    <td>{row.label}</td>
                    <td>{row.image_name}</td>
                    <td>{row.status}</td>
                    <td>{row.priority}</td>
                    <td>{row.geometry_issues || '—'}</td>
                    <td>{row.visual_issues || '—'}</td>
                    <td>{row.xtl ?? '—'}</td>
                    <td>{row.ytl ?? '—'}</td>
                    <td>{row.xbr ?? '—'}</td>
                    <td>{String(row.image_available)}</td>
                    <td>{row.ybr ?? '—'}</td>
                    <td>{row.occluded || '—'}</td>
                    <td>{row.area_pct ?? '—'}</td>
                    <td>{row.aspect_ratio ?? '—'}</td>
                    <td>{row.edge_density ?? '—'}</td>
                    <td>{row.texture_variance ?? '—'}</td>
                    <td>{row.laplacian_variance ?? '—'}</td>
                    <td>{row.foreground_contrast ?? '—'}</td>
                    <td>{row.boundary_support ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

export function LabelCheck({ load = loadMockLabels }: Props) {
  const [scenario, setScenario] = useState<LabelScenario>('baseline')
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<State>({ kind: 'loading' })
  const verified = useVerifiedResults()

  useEffect(() => {
    if (config.mode !== 'mock') return
    let active = true
    load(scenario)
      .then((snapshot) => {
        if (snapshot.configurationKey !== labelConfigurationKey) {
          throw new Error('Dữ liệu không đúng cấu hình')
        }
        return {
          ...snapshot,
          data: decodeLabels(snapshot.data.summary, snapshot.data.results),
        }
      })
      .then(
        (snapshot) => {
          if (!active) return
          setState({ kind: 'ready', data: snapshot.data })
        },
        (err) => {
          if (active) {
            setState({
              kind: 'error',
              message: err?.message?.includes('không đúng cấu hình')
                ? 'Dữ liệu không đúng cấu hình'
                : 'Không thể tải dữ liệu kiểm tra nhãn',
            })
          }
        }
      )

    return () => {
      active = false
    }
  }, [scenario, attempt, load])

  if (config.mode === 'backend') {
    if (verified?.loading) {
      return (
        <>
          <p className="intro">Khu vực chức năng</p>
          <h1>Label Check A+B</h1>
          <LoadingState />
        </>
      )
    }

    if (verified?.error) {
      return (
        <>
          <p className="intro">Khu vực chức năng</p>
          <h1>Label Check A+B</h1>
          <ErrorState message={verified.error} />
        </>
      )
    }

    if (verified?.activeResult) {
      if (!verified.activeSummary?.ready) return <EmptyState>Chưa có summary từ API cho dataset này.</EmptyState>
      const payload = verified.activeResult.payload
      let data: LabelData | null = null
      let decodeErr: string | null = null
      try {
        data = decodeLabels(
          { dataset_id: verified.activeResult.payload.dataset_id, annotations: verified.activeSummary.annotations },
          {
            dataset_id: verified.activeResult.payload.dataset_id,
            audit_rows: (payload.audit_rows || []) as AuditRow[],
          }
        )
      } catch (err: unknown) {
        decodeErr = err instanceof Error ? err.message : 'Invalid verified result'
      }

      if (decodeErr) {
        return (
          <>
            <p className="intro">Khu vực chức năng</p>
            <h1>Label Check A+B</h1>
            <ErrorState message={decodeErr} />
          </>
        )
      }
      if (data) {
        return (
          <div className="label-check-page">
            <p className="intro">Khu vực chức năng</p>
            <h1>Label Check A+B</h1>
            <ResultIdentity />
            {data.results.audit_rows.length ? (
              <LabelView key={verified.revision} rows={data.results.audit_rows} />
            ) : (
              <EmptyState>Chưa có dữ liệu box trong kết quả kiểm tra.</EmptyState>
            )}
          </div>
        )
      }
    }

    return (
      <>
        <p className="intro">Khu vực chức năng</p>
        <h1>Label Check A+B</h1>
        <p>Kiểm tra nhãn. PASS chỉ nghĩa là không kích hoạt quy tắc hiện tại.</p>
        <EmptyState>
          Chưa có phiên dataset và contract xác định kết quả cho cấu hình được duyệt. Tích hợp Label Check thật đang bị chặn; không thay thế bằng fixtures.
        </EmptyState>
      </>
    )
  }

  // Mock mode rendering
  return (
    <div className="label-check-page">
      <p className="intro">Khu vực chức năng</p>
      <h1>Label Check A+B</h1>

      <div className="scenario-selector">
        <label htmlFor="scenario-select">Tình huống Label Check</label>
        <select
          id="scenario-select"
          aria-label="Tình huống Label Check"
          value={scenario}
          onChange={(e) => setScenario(e.target.value as LabelScenario)}
        >
          <option value="baseline">baseline</option>
          <option value="no-annotations">no-annotations</option>
          <option value="no-boxes">no-boxes</option>
          <option value="empty">empty</option>
          <option value="malformed">malformed</option>
          <option value="error">error</option>
        </select>
      </div>

      {state.kind === 'loading' && <LoadingState />}

      {state.kind === 'error' && (
        <>
          <div role="alert" className="error-banner">
            {state.message || 'Không thể tải dữ liệu kiểm tra nhãn'}
          </div>
          <button type="button" onClick={() => setAttempt((a) => a + 1)}>
            Thử lại Label Check
          </button>
        </>
      )}

      {state.kind === 'ready' && (
        <>
          {!state.data.summary.annotations ? (
            <div className="notice">
              <p>Thiếu annotation CVAT</p>
            </div>
          ) : state.data.results.audit_rows.length === 0 ? (
            <div className="notice">
              <p>Có annotation nhưng audit rows trống</p>
            </div>
          ) : (
            <LabelView rows={state.data.results.audit_rows} />
          )}
        </>
      )}
    </div>
  )
}
