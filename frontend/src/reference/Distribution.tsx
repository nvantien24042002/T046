import { ResultStatus, ResultIdentity } from '../components/ResultStatus'
import { useState } from 'react'
import { config } from '../config'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { ClassBarChart } from '../components/Charts'
import { ExportButton } from '../components/Export'
import baseline from '../mocks/overview-baseline.json'

export function Distribution() {
  const verified = useVerifiedResults()
  return <DistributionView key={config.mode === 'backend' ? verified?.revision : 'mock'} />
}

function DistributionView() {
  const verified = useVerifiedResults()
  const [minCount, setMinCount] = useState(30)
  const [minShare, setMinShare] = useState(1.0)
  const [maxShare, setMaxShare] = useState(60.0)

  // Determine data source
  let classes = [] as Array<{ class: string; boxes: number; images: number; share_pct: number }>
  let boxes = [] as Array<{ box_id: string; label: string; occluded: string; area_pct: number | null }>
  let images = [] as Array<{ image_id: string; image_name: string; weather: string; timeofday: string }>
  let attributes = [] as Array<{ attribute: string; value: string; label: string; count: number }>
  let detectedAttrs = [] as string[]

  if (config.mode === 'backend') {
    if (!verified?.activeResult) {
      return <>
        <p className="intro">Khu vực chức năng</p>
        <h1>Phân bố & thuộc tính</h1>
        <ResultStatus />
      </>
    }
    const payload = verified.activeResult.payload
    classes = payload.classes
    boxes = payload.distribution_boxes
    images = payload.distribution_images
    attributes = payload.attribute_distribution
    detectedAttrs = payload.attributes
  } else {
    // Mock baseline
    classes = baseline.classes
    boxes = baseline.audit.map((a) => ({
      box_id: a.box_id,
      label: a.label,
      occluded: a.occluded,
      area_pct: a.area_pct,
    }))
    images = baseline.quality.map((q, idx) => ({
      image_id: String(idx + 1),
      image_name: q.filename,
      weather: 'clear',
      timeofday: 'day',
    }))
    attributes = [
      { attribute: 'pose', value: 'standing', label: 'person', count: 1 },
      { attribute: 'type', value: 'sedan', label: 'car', count: 2 },
    ]
    detectedAttrs = ['pose', 'type']
  }

  const classChartData = classes.map((c) => ({ name: c.class, value: c.boxes }))
  const totalBoxes = classes.reduce((sum, c) => sum + c.boxes, 0)

  // Imbalance warnings
  const shareError = minShare > maxShare
  const imbalanceAlerts = shareError ? [] : classes.filter(
    (c) => c.boxes < minCount || c.share_pct < minShare || c.share_pct > maxShare
  )

  // Size distribution by class
  const classSizes = new Map<string, number[]>()
  for (const b of boxes) {
    if (b.area_pct !== null && Number.isFinite(b.area_pct)) {
      const list = classSizes.get(b.label) ?? []
      list.push(b.area_pct)
      classSizes.set(b.label, list)
    }
  }

  // Occlusion by class
  const occlusionMap = new Map<string, Record<string, number>>()
  for (const b of boxes) {
    const occ = occlusionMap.get(b.label) ?? {}
    occ[b.occluded] = (occ[b.occluded] ?? 0) + 1
    occlusionMap.set(b.label, occ)
  }

  // Weather & Time of day
  const weatherCounts: Record<string, number> = {}
  const todCounts: Record<string, number> = {}
  const weatherTodMatrix: Record<string, Record<string, number>> = {}
  for (const im of images) {
    weatherCounts[im.weather] = (weatherCounts[im.weather] ?? 0) + 1
    todCounts[im.timeofday] = (todCounts[im.timeofday] ?? 0) + 1
    const row = weatherTodMatrix[im.weather] ?? (weatherTodMatrix[im.weather] = {})
    row[im.timeofday] = (row[im.timeofday] ?? 0) + 1
  }

  return <>
    <p className="intro">Khu vực chức năng</p>
    <h1>Phân bố & thuộc tính</h1>
      {config.mode === 'backend' && <ResultIdentity />}
      <p className="parity-notice">Chưa triển khai trong M1 — Bản xem trước Parity Track B</p>
    <p className="lead">Tự phát hiện class/attribute từ annotations.xml. Cảnh báo mất cân bằng là tín hiệu thống kê, không phải lỗi annotation.</p>

    <dl className="overview-metrics">
      <div role="group" aria-label="Ảnh có annotation"><dt>Ảnh có annotation record</dt><dd>{images.length}</dd></div>
      <div role="group" aria-label="Tổng box"><dt>Tổng box</dt><dd>{totalBoxes || boxes.length}</dd></div>
      <div role="group" aria-label="Số class"><dt>Số class</dt><dd>{classes.length}</dd></div>
      <div role="group" aria-label="Thuộc tính phát hiện"><dt>Thuộc tính phát hiện</dt><dd>{detectedAttrs.length}</dd></div>
    </dl>

    <section aria-labelledby="dist-class-chart">
      <h2 id="dist-class-chart">Phân bố class</h2>
      {classes.length > 0 ? <>
        <ClassBarChart data={classChartData} />
        <div className="overview-table">
          <table>
            <caption>Thống kê số lượng box và tỷ lệ theo class</caption>
            <thead>
              <tr>
                <th scope="col">Class</th>
                <th scope="col">Boxes</th>
                <th scope="col">Ảnh có class</th>
                <th scope="col">Tỷ lệ (%)</th>
              </tr>
            </thead>
            <tbody>
              {classes.map((c) => (
                <tr key={c.class}>
                  <th scope="row">{c.class}</th>
                  <td>{c.boxes}</td>
                  <td>{c.images}</td>
                  <td>{c.share_pct.toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </> : <p>Chưa có dữ liệu class bounding box.</p>}
    </section>

    <section aria-labelledby="imbalance-heading">
      <h2 id="imbalance-heading">Cảnh báo class lệch tỷ lệ</h2>
      <div className="filter-panel">
        <label>
          Số box tối thiểu:{' '}
          <input type="number" min={0} value={minCount} onChange={(e) => setMinCount(Number(e.target.value))} />
        </label>
        <label>
          Tỷ lệ tối thiểu (%):{' '}
          <input type="number" min={0} max={100} step={0.5} value={minShare} onChange={(e) => setMinShare(Number(e.target.value))} />
        </label>
        <label>
          Tỷ lệ tối đa (%):{' '}
          <input type="number" min={0} max={100} step={5} value={maxShare} onChange={(e) => setMaxShare(Number(e.target.value))} />
        </label>
      </div>
      {shareError ? (
        <p className="notice" role="alert">Tỷ lệ tối thiểu không được lớn hơn tỷ lệ tối đa.</p>
      ) : imbalanceAlerts.length === 0 ? (
        <p className="notice" role="status">Không có class nào vượt các ngưỡng cảnh báo đã chọn.</p>
      ) : (
        <>
          <p className="notice" role="alert">{imbalanceAlerts.length} class cần xem xét theo ngưỡng cấu hình.</p>
          <table>
            <caption>Danh sách class vượt ngưỡng cảnh báo</caption>
            <thead>
              <tr>
                <th scope="col">Class</th>
                <th scope="col">Boxes</th>
                <th scope="col">Tỷ lệ (%)</th>
                <th scope="col">Lý do</th>
              </tr>
            </thead>
            <tbody>
              {imbalanceAlerts.map((c) => (
                <tr key={c.class}>
                  <th scope="row">{c.class}</th>
                  <td>{c.boxes}</td>
                  <td>{c.share_pct.toFixed(1)}%</td>
                  <td>
                    {c.boxes < minCount ? `Dưới số box tối thiểu (${minCount}). ` : ''}
                    {c.share_pct < minShare ? `Dưới tỷ lệ tối thiểu (${minShare}%). ` : ''}
                    {c.share_pct > maxShare ? `Vượt tỷ lệ tối đa (${maxShare}%). ` : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>

    <section aria-labelledby="size-heading">
      <h2 id="size-heading">Kích thước box theo class</h2>
      <p className="caption">Ngưỡng 0.1% và 1% chỉ là cách phân nhóm minh họa, không phải tiêu chuẩn lỗi.</p>
      <table>
        <caption>Thống kê diện tích box (% ảnh) theo từng class</caption>
        <thead>
          <tr>
            <th scope="col">Class</th>
            <th scope="col">Số box</th>
            <th scope="col">Trung bình (%)</th>
            <th scope="col">Nhỏ nhất (%)</th>
            <th scope="col">Lớn nhất (%)</th>
          </tr>
        </thead>
        <tbody>
          {[...classSizes.entries()].map(([label, vals]) => {
            const sum = vals.reduce((a, b) => a + b, 0)
            const avg = vals.length ? sum / vals.length : 0
            const min = vals.length ? Math.min(...vals) : 0
            const max = vals.length ? Math.max(...vals) : 0
            return (
              <tr key={label}>
                <th scope="row">{label}</th>
                <td>{vals.length}</td>
                <td>{avg.toFixed(2)}%</td>
                <td>{min.toFixed(2)}%</td>
                <td>{max.toFixed(2)}%</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>

    <section aria-labelledby="occlusion-heading">
      <h2 id="occlusion-heading">Độ che khuất theo class</h2>
      <table>
        <caption>Số lượng box theo độ che khuất (occluded)</caption>
        <thead>
          <tr>
            <th scope="col">Class</th>
            <th scope="col">Occluded (1)</th>
            <th scope="col">Not Occluded (0)</th>
          </tr>
        </thead>
        <tbody>
          {[...occlusionMap.entries()].map(([label, counts]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              <td>{counts['1'] ?? counts['true'] ?? 0}</td>
              <td>{counts['0'] ?? counts['false'] ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>

    <section aria-labelledby="scene-heading">
      <h2 id="scene-heading">Thời tiết và thời điểm (đếm theo ảnh)</h2>
      <div className="overview-metrics">
        <div>
          <h3>Thời tiết</h3>
          <ul>
            {Object.entries(weatherCounts).map(([w, cnt]) => (
              <li key={w}>{w}: <strong>{cnt}</strong> ảnh</li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Thời điểm</h3>
          <ul>
            {Object.entries(todCounts).map(([t, cnt]) => (
              <li key={t}>{t}: <strong>{cnt}</strong> ảnh</li>
            ))}
          </ul>
        </div>
      </div>
      <h3>Phân bố kết hợp thời tiết × thời điểm</h3>
      <table>
        <caption>Ma trận phân bố thời tiết × thời điểm</caption>
        <thead>
          <tr>
            <th scope="col">Thời tiết</th>
            <th scope="col">Thời điểm</th>
            <th scope="col">Số ảnh</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(weatherTodMatrix).flatMap(([w, tods]) =>
            Object.entries(tods).map(([t, cnt]) => (
              <tr key={`${w}-${t}`}>
                <th scope="row">{w}</th>
                <td>{t}</td>
                <td>{cnt}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </section>

    {attributes.length > 0 && <section aria-labelledby="attr-heading">
      <h2 id="attr-heading">Thuộc tính phát hiện động</h2>
      <p>Tên thuộc tính: {detectedAttrs.join(', ') || 'Không có attribute tùy chỉnh'}</p>
      <table>
        <caption>Phân bố giá trị thuộc tính</caption>
        <thead>
          <tr>
            <th scope="col">Thuộc tính</th>
            <th scope="col">Giá trị</th>
            <th scope="col">Class</th>
            <th scope="col">Số lượng</th>
          </tr>
        </thead>
        <tbody>
          {attributes.map((a, idx) => (
            <tr key={`${a.attribute}-${a.value}-${idx}`}>
              <th scope="row">{a.attribute}</th>
              <td>{a.value}</td>
              <td>{a.label}</td>
              <td>{a.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>}

    <section aria-labelledby="dist-export-heading">
      <h2 id="dist-export-heading">Xuất dữ liệu phân bố</h2>
      <div className="table-controls">
        <ExportButton data={classes} filename="class_distribution.csv" label="Tải class distribution CSV" />
        <ExportButton data={attributes} filename="attribute_distribution.csv" label="Tải attributes CSV" />
        <ExportButton data={boxes} filename="box_distribution.csv" label="Tải box metrics CSV" />
      </div>
    </section>
  </>
}
