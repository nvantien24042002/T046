import { ResultIdentity } from '../components/ResultStatus'
import { useEffect, useState } from 'react'
import { config } from '../config'
import { decodeQuality, qualitySummary, type QualityData } from '../api/quality'
import { loadMockQuality, qualityDefaults, qualityConfigurationKey, type QualityScenario, type QualitySnapshot } from '../mocks/quality'
import { EmptyState, ErrorState, LoadingState } from '../components/States'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { ExportButton } from '../components/Export'

const labels: Record<string, string> = {
  BLUR: 'Ảnh mờ', DARK: 'Thiếu sáng', OVEREXPOSED: 'Quá sáng', LOW_CONTRAST: 'Tương phản thấp',
  LOW_RESOLUTION: 'Độ phân giải thấp', POSSIBLE_HIGH_NOISE: 'Nghi nhiễu cao', READ_ERROR: 'Không đọc được ảnh',
}
type State = { kind: 'loading' } | { kind: 'ready'; data: QualityData } | { kind: 'error' }
type Loader = (scenario: QualityScenario) => Promise<QualitySnapshot>

export function ImageQuality({ load = loadMockQuality }: { load?: Loader }) {
  const [scenario, setScenario] = useState<QualityScenario>('baseline')
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<State>({ kind: 'loading' })
  const verified = useVerifiedResults()

  useEffect(() => {
    if (config.mode !== 'mock') return
    let active = true
    load(scenario).then((snapshot) => ({ ...snapshot, data: decodeQuality(snapshot.data) })).then((snapshot) => {
      if (!active) return
      if (snapshot.configurationKey !== qualityConfigurationKey) { setState({ kind: 'error' }); return }
      setState({ kind: 'ready', data: snapshot.data })
    }, () => { if (active) setState({ kind: 'error' }) })
    return () => { active = false }
  }, [scenario, attempt, load])

  if (config.mode === 'backend') {
    if (verified?.loading) return <><p className="intro">Khu vực chức năng</p><h1>Image Quality</h1><LoadingState /></>
    if (verified?.error) return <><p className="intro">Khu vực chức năng</p><h1>Image Quality</h1><ErrorState message={verified.error} /></>

    if (verified?.activeResult) {
      const payload = verified.activeResult.payload
      let data: QualityData | null = null
      let decodeErr: string | null = null
      try {
        data = decodeQuality({
          dataset_id: verified.activeResult.payload.dataset_id,
          quality_rows: payload.quality_rows,
        })
      } catch (err: unknown) {
        decodeErr = err instanceof Error ? err.message : 'Invalid verified result'
      }

      if (decodeErr) {
        return <><p className="intro">Khu vực chức năng</p><h1>Image Quality</h1><ErrorState message={decodeErr} /></>
      }
      if (data) {
        return <>
          <p className="intro">Khu vực chức năng</p>
          <h1>Image Quality</h1>
          <ResultIdentity />
          {data.quality_rows.length ? <QualityResults data={data} /> : <EmptyState>Chưa có dữ liệu. Không hiển thị điểm trung bình bằng 0.</EmptyState>}
        </>
      }
    }

    return <>
      <p className="intro">Khu vực chức năng</p>
      <h1>Image Quality</h1>
      <p>Phân tích chất lượng ảnh. PASS chỉ nghĩa là không kích hoạt quy tắc hiện tại.</p>
      <EmptyState>Chưa có phiên dataset và contract xác định kết quả cho cấu hình được duyệt. Tích hợp Image Quality thật đang bị chặn; không thay thế bằng fixtures.</EmptyState>
    </>
  }

  return <>
    <h1>Image Quality</h1><p>Phân tích chất lượng ảnh. PASS chỉ nghĩa là không kích hoạt quy tắc hiện tại.</p>
    <p className="notice"><strong>Dữ liệu mẫu — Mock mode.</strong> Kết quả Python baseline với cấu hình cố định. Không chạy phân tích mới.</p>
    <details><summary>Ngưỡng tham chiếu Python (chỉ đọc)</summary><p>Không sửa ngưỡng khi chưa có kết quả đúng cấu hình.</p><dl className="quality-thresholds">{Object.entries(qualityDefaults).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl></details>
    <label>Tình huống Image Quality <select value={scenario} onChange={(event) => { setState({ kind: 'loading' }); setScenario(event.target.value as QualityScenario) }}><option value="baseline">Python baseline</option><option value="empty">Kết quả trống (mẫu UI)</option><option value="malformed">Response không hợp lệ (mẫu UI)</option><option value="error">Lỗi tải (mẫu UI)</option></select></label>
    {state.kind === 'loading' && <LoadingState />}
    {state.kind === 'error' && <><ErrorState message="Không tải được Image Quality hoặc kết quả không đúng cấu hình. Hãy thử lại hoặc chọn tình huống mẫu khác." /><button onClick={() => { setState({ kind: 'loading' }); setAttempt((value) => value + 1) }}>Thử lại Image Quality</button></>}
    {state.kind === 'ready' && (state.data.quality_rows.length ? <QualityResults data={state.data} /> : <EmptyState>Chưa có dữ liệu. Không hiển thị điểm trung bình bằng 0.</EmptyState>)}
  </>
}

function QualityResults({ data }: { data: QualityData }) {
  const summary = qualitySummary(data.quality_rows)

  return <>
    <dl className="quality-summary">
      <div role="group" aria-label="PASS"><dt>PASS</dt><dd>{summary.counts.PASS}</dd></div>
      <div role="group" aria-label="REVIEW"><dt>REVIEW</dt><dd>{summary.counts.REVIEW}</dd></div>
      <div role="group" aria-label="ERROR"><dt>ERROR</dt><dd>{summary.counts.ERROR}</dd></div>
      <div role="group" aria-label="Điểm trung bình"><dt>Điểm trung bình</dt><dd>{summary.mean === null ? '—' : `${summary.mean.toFixed(1)}/100`}</dd></div>
    </dl>
    {summary.mean !== null && <p role="status"><meter aria-label="Điểm trung bình" min={0} max={100} value={summary.mean}>{summary.mean.toFixed(1)}</meter></p>}
    <div className="table-controls">
      <ExportButton data={data.quality_rows} filename="image_quality.csv" label="Xuất CSV Image Quality" />
    </div>
    <div className="quality-table-wrapper">
      <table>
        <caption>Chỉ số Python gốc từ benchmark</caption>
        <thead>
          <tr>
            <th scope="col">filename</th>
            <th scope="col">status</th>
            <th scope="col">issues</th>
            <th scope="col">score</th>
            <th scope="col">width</th>
            <th scope="col">height</th>
            <th scope="col">size_kb</th>
            <th scope="col">blur_score</th>
            <th scope="col">brightness</th>
            <th scope="col">contrast</th>
            <th scope="col">black_pct</th>
            <th scope="col">noise_score</th>
            <th scope="col">white_pct</th>
            <th scope="col">error</th>
          </tr>
        </thead>
        <tbody>
          {data.quality_rows.map((row) => (
            <tr key={row.filename}>
              <th scope="row">{row.filename}</th>
              <td>{row.status}</td>
              <td>{row.issues.map((i) => labels[i] || i).join(', ') || '—'}</td>
              <td>{row.score}</td>
              <td>{row.width}</td>
              <td>{row.height}</td>
              <td>{row.size_kb}</td>
              <td>{row.blur_score !== null && row.blur_score !== undefined ? String(row.blur_score) : '—'}</td>
              <td>{row.brightness !== null && row.brightness !== undefined ? String(row.brightness) : '—'}</td>
              <td>{row.contrast !== null && row.contrast !== undefined ? String(row.contrast) : '—'}</td>
              <td>{row.black_pct !== null && row.black_pct !== undefined ? String(row.black_pct) : '—'}</td>
              <td>{row.noise_score !== null && row.noise_score !== undefined ? String(row.noise_score) : '—'}</td>
              <td>{row.white_pct !== null && row.white_pct !== undefined ? String(row.white_pct) : '—'}</td>
              <td>{row.error || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  </>
}
