import { ResultStatus, ResultIdentity } from '../components/ResultStatus'
import { config } from '../config'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { ExportButton } from '../components/Export'
import type { QualityRow } from '../api/quality'
import type { AuditRow } from '../api/labels'
import type { DistributionBox } from '../api/verifiedResults'
import baseline from '../mocks/overview-baseline.json'

export function Reports() {
  const verified = useVerifiedResults()

  let qualityRows: QualityRow[] = []
  let auditRows: AuditRow[] = []
    let boxes: (DistributionBox | AuditRow)[] = []

  if (config.mode === 'backend') {
    if (!verified?.activeResult) {
      return <>
        <p className="intro">Khu vực chức năng</p>
        <h1>Xuất báo cáo</h1>
        <ResultStatus />
      </>
    }
    const payload = verified.activeResult.payload
    qualityRows = payload.quality_rows
    auditRows = payload.audit_rows
        boxes = payload.distribution_boxes
  } else {
    qualityRows = baseline.quality as unknown as QualityRow[]
    auditRows = baseline.audit as unknown as AuditRow[]
        boxes = baseline.audit as unknown as (DistributionBox | AuditRow)[]
  }

  // Generate class summary table
  const classSummaryMap = new Map<string, { total: number; reviews: number }>()
  for (const a of auditRows) {
    const entry = classSummaryMap.get(a.label) ?? { total: 0, reviews: 0 }
    entry.total++
    if (a.status.startsWith('REVIEW')) entry.reviews++
    classSummaryMap.set(a.label, entry)
  }

  const classSummary = [...classSummaryMap.entries()].map(([label, s]) => ({
    label,
    total_boxes: s.total,
    review_boxes: s.reviews,
    review_rate_pct: s.total > 0 ? Number(((s.reviews / s.total) * 100).toFixed(1)) : 0,
  }))

  const qualityExport = qualityRows.map((r) => ({
    ...r,
    issues: Array.isArray(r.issues) ? r.issues.join(', ') : r.issues,
  }))

  return <>
    <p className="intro">Khu vực chức năng</p>
    <h1>Xuất báo cáo</h1>
      {config.mode === 'backend' && <ResultIdentity />}
      <p className="parity-notice">Chưa triển khai trong M1 — Bản xem trước Parity Track B</p>
    <p className="notice">
      PASS/NO_SIGNAL chỉ nghĩa là không kích hoạt các quy tắc hiện tại; không xác nhận annotation đúng. Visual metrics phụ thuộc chất lượng ảnh và ngưỡng.
    </p>

    <section aria-labelledby="report-quality">
      <h2 id="report-quality">1. Báo cáo Image Quality ({qualityRows.length} ảnh)</h2>
      <p>Bao gồm đầy đủ các chỉ số: độ nét (blur), độ sáng, tương phản, nhiễu, tỷ lệ điểm đen/trắng và mã lỗi.</p>
      <ExportButton data={qualityExport} filename="image_quality_report.csv" label="Tải Image Quality CSV" />
    </section>

    <section aria-labelledby="report-labels">
      <h2 id="report-labels">2. Báo cáo Label Check A+B ({auditRows.length} bounding box)</h2>
      <p>Bao gồm kết quả kiểm tra hình học Geometry A và tín hiệu trực quan Visual B cho từng bounding box.</p>
      <ExportButton data={auditRows} filename="label_check_ab_report.csv" label="Tải Label Check A+B CSV" />
    </section>

    <section aria-labelledby="report-class-summary">
      <h2 id="report-class-summary">3. Báo cáo tổng hợp theo Class ({classSummary.length} class)</h2>
      <p>Thống kê số lượng box và tỷ lệ cần review cho từng class nhãn.</p>
      <table>
        <caption>Tổng hợp review rate theo class</caption>
        <thead>
          <tr>
            <th scope="col">Class</th>
            <th scope="col">Tổng box</th>
            <th scope="col">Box cần review</th>
            <th scope="col">Tỷ lệ review (%)</th>
          </tr>
        </thead>
        <tbody>
          {classSummary.map((s) => (
            <tr key={s.label}>
              <th scope="row">{s.label}</th>
              <td>{s.total_boxes}</td>
              <td>{s.review_boxes}</td>
              <td>{s.review_rate_pct}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ExportButton data={classSummary} filename="class_summary.csv" label="Tải Class Summary CSV" />
    </section>

    {boxes.length > 0 && <section aria-labelledby="report-boxes">
      <h2 id="report-boxes">4. Báo cáo phân bố Box ({boxes.length} box)</h2>
      <p>Dữ liệu phân bố kích thước, độ che khuất và thuộc tính của bounding box.</p>
      <ExportButton data={boxes} filename="box_distribution.csv" label="Tải Box Distribution CSV" />
    </section>}
  </>
}
