import { useEffect, useRef, useState } from 'react'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { imageConfigurationFields, imageConfigurationSchema, readImageConfigurationDefaults, reanalyzeImageQuality, type ImageConfiguration } from '../api/imageQualityConfiguration'

export function ImageQualityConfigurationPanel() {
  const state = useVerifiedResults()
  return state?.datasetId ? <ConfigurationEditor key={state.datasetId} dataset={state.datasetId} /> : null
}

function ConfigurationEditor({ dataset }: { dataset: string }) {
  const state = useVerifiedResults()!
  const result = state.activeResult
  const readDashboard = state.readDashboard
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [current, setCurrent] = useState(false)
  const [completed, setCompleted] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)
  const flight = useRef<AbortController | null>(null)
  const requestRef = useRef<number | null>(null)
  const failRequest = state.failRequest
  const isCurrentRequest = state.isCurrentRequest
  useEffect(() => () => {
    flight.current?.abort()
    if (requestRef.current !== null && isCurrentRequest(requestRef.current)) failRequest(requestRef.current, 'Đã dừng chờ phân tích khi rời trang; công việc máy chủ có thể vẫn tiếp tục.')
  }, [failRequest, isCurrentRequest])
  useEffect(() => {
    if (!result || result.descriptor) return
    const controller = new AbortController()
    async function load() {
      setLoading(true)
      try {
        const defaults = await readImageConfigurationDefaults(dataset, controller.signal)
        const view = await readDashboard?.('quality', result!, '', {}, controller.signal)
        const thresholds = view?.tables.find((t) => t.key === 'thresholds')
        const values: ImageConfiguration = thresholds
          ? imageConfigurationSchema.parse(Object.fromEntries(thresholds.rows.map((r) => [String(r.parameter), r.value]))) : defaults
        if (controller.signal.aborted) return
        setDraft(Object.fromEntries(Object.entries(values).map(([key, value]) => [key, String(value)])))
        setCurrent(Boolean(thresholds)); setError(null)
      } catch {
        if (!controller.signal.aborted) setError('Không tải được cấu hình ngưỡng. Hãy thử tải lại cấu hình.')
      } finally { if (!controller.signal.aborted) setLoading(false) }
    }
    void load()
    return () => controller.abort()
  }, [dataset, result, readDashboard, retry])
  async function submit() {
    const values = Object.fromEntries(imageConfigurationFields.map(({ key }) => [key, draft[key]?.trim() ? Number(draft[key]) : NaN]))
    const parsed = imageConfigurationSchema.safeParse(values)
    if (!parsed.success) { setError('Ngưỡng phải là số hữu hạn, không âm; kích thước là số nguyên > 0, intensity trong 0–255 và các cặp ngưỡng không được đảo thứ tự.'); return }
    const controller = new AbortController(); flight.current = controller
    const request = state.beginRequest(dataset)
    requestRef.current = request
    setBusy(true); setError(null); setCompleted(null)
    try {
      const next = await reanalyzeImageQuality(dataset, parsed.data, controller.signal)
      if (controller.signal.aborted || !state.isCurrentRequest(request)) return
      state.setManualResult(next.detail, next.summary, undefined, request)
      setCompleted(new Date().toISOString())
    } catch {
      if (!controller.signal.aborted && state.isCurrentRequest(request)) {
        setError('Phân tích chưa hoàn tất. Hãy kiểm tra kết nối và thử lại; thao tác có thể vẫn tiếp tục trên máy chủ.')
        state.failRequest(request, 'Không tải được kết quả phân tích mới.')
      }
    } finally { requestRef.current = null; if (!controller.signal.aborted) setBusy(false) }
  }
  return <section className="feature-panel" aria-labelledby="image-config-title">
    <details>
      <summary id="image-config-title">Cấu hình Image Quality và phân tích lại</summary>
      <p>{current ? 'Giá trị từ lần phân tích hiện tại.' : 'Giá trị mặc định do máy chủ cung cấp; chưa xác nhận được cấu hình của lần phân tích hiện tại.'}</p>
      <p>Lần chạy này phân tích lại dataset với ngưỡng ảnh đã chọn; Label Check dùng ngưỡng mặc định của API. Đóng yêu cầu ở trình duyệt không hủy công việc trên máy chủ.</p>
      {loading && <p role="status">Đang tải cấu hình ngưỡng…</p>}
      <form noValidate onSubmit={(event) => { event.preventDefault(); void submit() }}>
        <fieldset disabled={loading || busy || state.loading || Object.keys(draft).length === 0}>
          <legend>Ngưỡng thực thi</legend>
          <div className="feature-fields">
            {imageConfigurationFields.map((field) => <div key={field.key}><label>
              {field.label}<input type="number" min={field.min} max={field.max} step={field.step} value={draft[field.key] ?? ''}
                onChange={(event) => setDraft({ ...draft, [field.key]: event.target.value })} aria-describedby={`hint-${field.key}`} />
              </label>
              <small id={`hint-${field.key}`}>{field.hint}</small>
            </div>)}
          </div>
          <button className="btn-primary" type="submit">Phân tích lại với ngưỡng đã chọn</button>
        </fieldset>
      </form>
      {error && <p role="alert">{error}</p>}
      {error && !busy && Object.keys(draft).length === 0 && <button type="button" onClick={() => { setRetry(retry + 1); setError(null) }}>Tải lại cấu hình</button>}
    </details>
    {busy && <p role="status">Đang phân tích lại dữ liệu…</p>}
    {completed && <p role="status">Phân tích hoàn tất lúc <time dateTime={completed}>{new Date(completed).toLocaleString('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh' })}</time>. Bảng và biểu đồ dùng kết quả mới.</p>}
  </section>
}
