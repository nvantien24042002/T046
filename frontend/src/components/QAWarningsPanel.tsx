import type { DashboardView, DashboardPage } from '../api/dashboard'

export interface QAAlert {
  id: string
  level: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'INFO'
  title: string
  description: string
  basis: string
}

export function QAWarningsPanel({ view, page }: { view: DashboardView; page: DashboardPage }) {
  const alerts: QAAlert[] = []

  const metricsMap = new Map<string, string | number | boolean | null>(
    view.metrics.map((m) => [m.key, m.value])
  )

  // 1. Imbalance alerts (distribution page)
  const imbalanceTable = view.tables.find((t) => t.key === 'imbalance')
  if (imbalanceTable?.rows.length) {
    const params = Object.fromEntries((view.parameters ?? []).map((p) => [p.key, p.value]))
    const minCount = params['min_count'] ?? 30
    const minShare = params['min_share'] ?? 1
    const maxShare = params['max_share'] ?? 60

    for (const raw of imbalanceTable.rows) {
      const cls = String(raw['class'] ?? raw['label'] ?? 'Unknown')
      const boxes = Number(raw['boxes'] ?? 0)
      const share = Number(raw['share_pct'] ?? 0)

      if (boxes < minCount) {
        alerts.push({
          id: `imbalance-count-${cls}`,
          level: boxes < 10 ? 'HIGH' : 'MEDIUM',
          title: `Thiếu mẫu box nghiêm trọng: Class "${cls}"`,
          description: `Số lượng bounding box của class "${cls}" thấp hơn ngưỡng tối thiểu yêu cầu cho huấn luyện và kiểm định.`,
          basis: `Số liệu: ${boxes} box (Ngưỡng tối thiểu backend: ${minCount} box)`,
        })
      } else if (share < minShare) {
        alerts.push({
          id: `imbalance-share-low-${cls}`,
          level: 'MEDIUM',
          title: `Tỷ lệ phân bố quá thấp: Class "${cls}"`,
          description: `Class "${cls}" chiếm tỷ lệ quá nhỏ trong tập dữ liệu, có nguy cơ gây bias cho mô hình nhận diện.`,
          basis: `Số liệu: ${share}% (Ngưỡng tối thiểu backend: ${minShare}%)`,
        })
      } else if (share > maxShare) {
        alerts.push({
          id: `imbalance-share-high-${cls}`,
          level: 'MEDIUM',
          title: `Tỷ lệ phân bố áp đảo: Class "${cls}"`,
          description: `Class "${cls}" chiếm tỷ lệ quá lớn so với các class còn lại trong dataset.`,
          basis: `Số liệu: ${share}% (Ngưỡng tối đa backend: ${maxShare}%)`,
        })
      }
    }
  }

  // 2. Image Quality alerts
  if (page === 'quality' || page === 'overview') {
    const errors = Number(metricsMap.get('ERROR') ?? 0)
    const reviews = Number(metricsMap.get('REVIEW') ?? metricsMap.get('review_images') ?? 0)
    const score = metricsMap.get('mean_score')

    if (errors > 0) {
      alerts.push({
        id: 'quality-error',
        level: 'CRITICAL',
        title: 'Ảnh vi phạm tiêu chuẩn kỹ thuật (ERROR)',
        description: 'Tập dữ liệu chứa các ảnh không đạt tiêu chuẩn chất lượng hình ảnh hoặc có lỗi giải mã/hình học nghiêm trọng.',
        basis: `Số liệu: ${errors} ảnh ở trạng thái ERROR`,
      })
    }
    if (reviews > 0) {
      alerts.push({
        id: 'quality-review',
        level: 'MEDIUM',
        title: 'Ảnh có tín hiệu chất lượng thấp cần review',
        description: 'Phát hiện ảnh mờ, tối, độ tương phản thấp hoặc nhiễu cần người dùng thẩm định thủ công.',
        basis: `Số liệu: ${reviews} ảnh cần review${score !== undefined && score !== null ? ` · Điểm TB: ${score}` : ''}`,
      })
    }
  }

  // 3. Label Check alerts
  if (page === 'labels' || page === 'overview') {
    const high = Number(metricsMap.get('high') ?? 0)
    const reviews = Number(metricsMap.get('reviews') ?? metricsMap.get('review_boxes') ?? 0)
    const totalBoxes = Number(metricsMap.get('boxes') ?? 0)

    if (high > 0) {
      alerts.push({
        id: 'label-high',
        level: 'HIGH',
        title: 'Bounding Box độ ưu tiên cao cần thẩm định (HIGH)',
        description: 'Các box có kích thước bất thường, độ tương phản nền thấp hoặc vi phạm hình học cần ưu tiên xử lý trước.',
        basis: `Số liệu: ${high} box mức HIGH cần review`,
      })
    }
    if (reviews > 0 && page === 'labels') {
      alerts.push({
        id: 'label-reviews',
        level: 'MEDIUM',
        title: 'Tổng số Bounding Box gắn cờ QA/QC',
        description: 'Các box được backend phát hiện có dấu hiệu cần kiểm tra đối chiếu với ảnh gốc.',
        basis: `Số liệu: ${reviews} / ${totalBoxes} box cần review (${totalBoxes > 0 ? ((reviews / totalBoxes) * 100).toFixed(1) : 0}%)`,
      })
    }

    // High review rate classes from label_classes table
    const labelClassesTable = view.tables.find((t) => t.key === 'label_classes')
    if (labelClassesTable?.rows.length) {
      for (const row of labelClassesTable.rows) {
        const rate = Number(row['review_rate_pct'] ?? 0)
        const count = Number(row['reviews'] ?? 0)
        if (rate >= 50 && count > 0) {
          alerts.push({
            id: `class-high-rate-${String(row['label'])}`,
            level: 'HIGH',
            title: `Tỷ lệ review cao ở Class "${String(row['label'])}"`,
            description: `Hơn 50% số box thuộc class "${String(row['label'])}" bị gắn cờ review. Cần kiểm tra quy chuẩn gán nhãn cho class này.`,
            basis: `Số liệu: ${count}/${Number(row['boxes'])} box (${rate}%) tại ${Number(row['images'])} ảnh`,
          })
        }
      }
    }
  }

  // 4. Issue frequency chart signals
  const issuesChart = view.charts.find((c) => c.key === 'issues')
  if (issuesChart?.rows.length) {
    const topIssue = [...issuesChart.rows].sort((a, b) => b.value - a.value)[0]
    if (topIssue && topIssue.value > 0) {
      alerts.push({
        id: `top-issue-${topIssue.name}`,
        level: 'INFO',
        title: `Cảnh báo phổ biến nhất: "${topIssue.name}"`,
        description: 'Tần suất xuất hiện cảnh báo cao nhất trong phạm vi bộ lọc đang chọn.',
        basis: `Số liệu: ${topIssue.value.toLocaleString()} trường hợp ghi nhận từ backend`,
      })
    }
  }

  // 5. Scene metadata alerts (scene-finder)
  if (page === 'scene-finder') {
    const unresolved = Number(metricsMap.get('unresolved') ?? 0)
    if (unresolved > 0) {
      alerts.push({
        id: 'scene-unresolved',
        level: 'INFO',
        title: 'Ảnh thiếu thông tin siêu dữ liệu cảnh (Weather / Time of day)',
        description: 'Một số ảnh trong XML CVAT chưa có tag weather hoặc timeofday rõ ràng (missing/unknown).',
        basis: `Số liệu: ${unresolved} ảnh chưa đầy đủ metadata`,
      })
    }
  }

  return (
    <section className="qa-alerts-panel" aria-labelledby="qa-alerts-heading">
      <div className="qa-alerts-header">
        <div className="qa-alerts-title-wrapper">
          <span className="qa-badge-icon" aria-hidden="true">🛡️</span>
          <h2 id="qa-alerts-heading">Cảnh báo & Tín hiệu QA/QC</h2>
        </div>
        <span className={`qa-count-pill ${alerts.length ? 'has-alerts' : 'all-good'}`}>
          <span className="status-dot" aria-hidden="true" />
          {alerts.length ? `${alerts.length} cảnh báo cần xử lý` : '100% Đạt tiêu chuẩn kiểm định'}
        </span>
      </div>

      {!alerts.length ? (
        <div className="qa-clean-state">
          <div className="qa-clean-icon-circle" aria-hidden="true">✓</div>
          <div className="qa-clean-text">
            <strong>Tất cả chỉ số đạt tiêu chuẩn kỹ thuật Backend</strong>
            <span className="qa-clean-sub">Không có vi phạm ngưỡng hình ảnh hoặc phân bố class nào được phát hiện.</span>
          </div>
        </div>
      ) : (
        <div className="qa-alerts-grid">
          {alerts.map((alert) => (
            <div key={alert.id} className={`qa-alert-card level-${alert.level.toLowerCase()}`}>
              <div className="qa-alert-top-row">
                <span className={`qa-severity-pill pill-${alert.level.toLowerCase()}`}>
                  {alert.level === 'CRITICAL' ? '● Nghiêm trọng'
                    : alert.level === 'HIGH' ? '▲ Cảnh báo cao'
                    : alert.level === 'MEDIUM' ? '■ Cần chú ý'
                    : 'ℹ Thông tin'}
                </span>
                <span className="qa-metric-tag">{alert.basis}</span>
              </div>
              <h3 className="qa-alert-title">{alert.title}</h3>
              <p className="qa-alert-description">{alert.description}</p>
            </div>
          ))}
        </div>
      )}
    </section>
  )

}
