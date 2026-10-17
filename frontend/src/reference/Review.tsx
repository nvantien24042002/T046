import { ResultStatus, ResultIdentity } from '../components/ResultStatus'
import { useState } from 'react'
import { config } from '../config'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { type QualityRow, type AuditRow } from '../api/verifiedResults'
import { Pagination, paginate } from '../components/Pagination'
import { ImagePreview } from '../components/ImagePreview'
import baseline from '../mocks/overview-baseline.json'

const labels: Record<string, string> = {
  BLUR: 'Ảnh mờ',
  DARK: 'Thiếu sáng',
  OVEREXPOSED: 'Quá sáng',
  LOW_CONTRAST: 'Tương phản thấp',
  LOW_RESOLUTION: 'Độ phân giải thấp',
  POSSIBLE_HIGH_NOISE: 'Nghi nhiễu cao',
  READ_ERROR: 'Không đọc được ảnh',
}

export function Review() {
  const verified = useVerifiedResults()
  return <ReviewView key={config.mode === 'backend' ? verified?.revision : 'mock'} />
}

function ReviewView() {
  const verified = useVerifiedResults()
  const [mode, setMode] = useState<'quality' | 'label'>('quality')
  const [statusFilter, setStatusFilter] = useState<'ALL' | 'PASS' | 'REVIEW' | 'ERROR'>('ALL')
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(6)
  const [selectedImage, setSelectedImage] = useState<string>('')

  let qualityRows: QualityRow[] = []
  let auditRows: AuditRow[] = []

  if (config.mode === 'backend') {
    if (!verified?.activeResult) {
      return (
        <>
          <p className="intro">Khu vực chức năng</p>
          <h1>Review ảnh</h1>
          {config.mode === 'backend' && <ResultIdentity />}
      <p className="parity-notice">Chưa triển khai trong M1 — Bản xem trước Parity Track B</p>
          <ResultStatus />
        </>
      )
    }
    const payload = verified.activeResult.payload
    qualityRows = payload.quality_rows
    auditRows = payload.audit_rows
  } else {
    qualityRows = baseline.quality as unknown as QualityRow[]
    auditRows = baseline.audit as unknown as AuditRow[]
  }

  // Filter quality images
  const filteredQuality =
    statusFilter === 'ALL' ? qualityRows : qualityRows.filter((r) => r.status === statusFilter)
  const pagedQuality = paginate(filteredQuality, page, pageSize)

  // Unique image names for label inspection
  const uniqueImages = [...new Set(auditRows.map((r) => r.image_name))].sort()
  const currentImage = selectedImage || (uniqueImages.length > 0 ? (uniqueImages[0] ?? '') : '')
  const currentBoxes = auditRows.filter((r) => r.image_name === currentImage)

  return (
    <>
      <p className="intro">Khu vực chức năng</p>
      <h1>Review ảnh</h1>
      {config.mode === 'backend' && <ResultIdentity />}
      <p className="parity-notice">Chưa triển khai trong M1 — Bản xem trước Parity Track B</p>
      <div className="filter-panel" role="radiogroup" aria-label="Chế độ xem ảnh">
        <label>
          <input
            type="radio"
            name="review-mode"
            checked={mode === 'quality'}
            onChange={() => {
              setMode('quality')
              setPage(0)
            }}
          />{' '}
          Ảnh gốc + Image Quality
        </label>
        <label>
          <input
            type="radio"
            name="review-mode"
            checked={mode === 'label'}
            onChange={() => setMode('label')}
          />{' '}
          Ảnh có box CVAT + Label Check
        </label>
      </div>

      {mode === 'quality' ? (
        <section aria-labelledby="quality-review-heading">
          <h2 id="quality-review-heading">Danh sách ảnh và chỉ số chất lượng</h2>
          <div className="table-controls">
            <label>
              Lọc theo trạng thái:{' '}
              <select
                value={statusFilter}
                onChange={(e) => {
                  setStatusFilter(e.target.value as 'ALL' | 'PASS' | 'REVIEW' | 'ERROR')
                  setPage(0)
                }}
              >
                <option value="ALL">Tất cả ({qualityRows.length})</option>
                <option value="PASS">PASS</option>
                <option value="REVIEW">REVIEW</option>
                <option value="ERROR">ERROR</option>
              </select>
            </label>
          </div>

          {filteredQuality.length === 0 ? (
            <p>Không có ảnh phù hợp.</p>
          ) : (
            <>
              <div className="image-cards-grid">
                {pagedQuality.map((row) => (
                  <article key={row.filename} className="image-card">
                    <header>
                      <h3>{row.filename}</h3>
                      <span className={`status-tag status-${row.status.toLowerCase()}`}>{row.status}</span>
                    </header>
                    <ImagePreview
                      imagePath={row.filename}
                      annotated={false}
                      alt={`Ảnh gốc ${row.filename}`}
                    />
                    <p className="score-badge">
                      Điểm chất lượng: <strong>{row.score.toFixed(1)}/100</strong>
                    </p>
                    <p className="issues-list">
                      Vấn đề: {row.issues.map((i: string) => labels[i] || i).join(', ') || 'Không có cảnh báo'}
                    </p>
                    <details>
                      <summary>Chỉ số chi tiết</summary>
                      <dl className="quality-metrics-mini">
                        <div>
                          <dt>Độ phân giải</dt>
                          <dd>
                            {row.width} × {row.height}
                          </dd>
                        </div>
                        <div>
                          <dt>Kích thước</dt>
                          <dd>{row.size_kb.toFixed(1)} KB</dd>
                        </div>
                        <div>
                          <dt>Độ nét (Blur)</dt>
                          <dd>{row.blur_score !== null ? row.blur_score.toFixed(1) : '—'}</dd>
                        </div>
                        <div>
                          <dt>Độ sáng</dt>
                          <dd>{row.brightness !== null ? row.brightness.toFixed(1) : '—'}</dd>
                        </div>
                        <div>
                          <dt>Độ tương phản</dt>
                          <dd>{row.contrast !== null ? row.contrast.toFixed(1) : '—'}</dd>
                        </div>
                      </dl>
                    </details>
                  </article>
                ))}
              </div>
              <Pagination
                total={filteredQuality.length}
                page={page}
                pageSize={pageSize}
                onPageChange={setPage}
                onPageSizeChange={(newSize) => {
                  setPageSize(newSize)
                  setPage(0)
                }}
              />
            </>
          )}
        </section>
      ) : (
        <section aria-labelledby="label-review-heading">
          <h2 id="label-review-heading">Bounding box theo ảnh</h2>
          {uniqueImages.length === 0 ? (
            <p>Không có dữ liệu bounding box để kiểm tra.</p>
          ) : (
            <>
              <div className="label-review-controls">
                <label htmlFor="image-select">
                  Chọn ảnh:{' '}
                  <select
                    id="image-select"
                    value={currentImage}
                    onChange={(e) => setSelectedImage(e.target.value)}
                  >
                    {uniqueImages.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              {currentImage && (
                <div className="annotated-preview-section">
                  <ImagePreview
                    imagePath={currentImage}
                    annotated={true}
                    alt={`Ảnh có box CVAT: ${currentImage}`}
                    caption="Đỏ: HIGH — Cam: MEDIUM — Xanh: LOW"
                  />
                </div>
              )}

              <div className="label-table-wrapper">
                <table>
                  <caption>
                    Danh sách box trên ảnh {currentImage} ({currentBoxes.length} box)
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Box ID</th>
                      <th scope="col">Class</th>
                      <th scope="col">Ưu tiên</th>
                      <th scope="col">Trạng thái</th>
                      <th scope="col">Tọa độ [xtl, ytl, xbr, ybr]</th>
                      <th scope="col">Diện tích (%)</th>
                      <th scope="col">Tỷ lệ cạnh</th>
                      <th scope="col">Vấn đề</th>
                    </tr>
                  </thead>
                  <tbody>
                    {currentBoxes.map((b) => (
                      <tr key={b.box_id}>
                        <th scope="row">{b.box_id}</th>
                        <td>{b.label}</td>
                        <td>
                          <span className={`priority-tag priority-${b.priority.toLowerCase()}`}>
                            {b.priority}
                          </span>
                        </td>
                        <td>
                          <span className={`status-tag status-${b.status.toLowerCase()}`}>
                            {b.status}
                          </span>
                        </td>
                        <td>
                          [{b.xtl ?? '—'}, {b.ytl ?? '—'}, {b.xbr ?? '—'}, {b.ybr ?? '—'}]
                        </td>
                        <td>{b.area_pct !== null ? b.area_pct.toFixed(2) : '—'}</td>
                        <td>{b.aspect_ratio !== null ? b.aspect_ratio.toFixed(2) : '—'}</td>
                        <td>{b.issues || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      )}
    </>
  )
}
