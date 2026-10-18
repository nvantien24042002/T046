import { useEffect, useRef, useState } from 'react'
import { coverageDimensions, coverageGenerationSchema, coverageTargetSchema, coverageTargetsSchema, mergeCoverageTargets, targetsJSON, type CoverageDimension, type CoverageGeneration, type CoverageTarget } from '../api/coverageTargets'

export function CoverageTargetsPanel({ applied, busy, choices, onApply, onGenerate }: {
  applied: unknown; busy: boolean; choices: Partial<Record<CoverageDimension, string[]>>
  onApply: (targets: CoverageTarget[]) => void
  onGenerate: (generate: CoverageGeneration, signal: AbortSignal) => Promise<CoverageTarget[]>
}) {
  const [targets, setTargets] = useState<CoverageTarget[]>([])
  const [editing, setEditing] = useState<number | null>(null)
  const [name, setName] = useState('')
  const [filters, setFilters] = useState<Partial<Record<CoverageDimension, string>>>({})
  const [unit, setUnit] = useState('images')
  const [target, setTarget] = useState('100')
  const [weight, setWeight] = useState('1')
  const [raw, setRaw] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [dimensions, setDimensions] = useState<CoverageDimension[]>([])
  const [includeUnobserved, setIncludeUnobserved] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [preview, setPreview] = useState<{ targets: CoverageTarget[]; dimensions: CoverageDimension[] } | null>(null)
  const [previewFilters, setPreviewFilters] = useState<Partial<Record<CoverageDimension, string[]>>>({})
  const flight = useRef<AbortController | null>(null)
  useEffect(() => () => flight.current?.abort(), [])
  const appliedResult = coverageTargetsSchema.safeParse(applied)
  const appliedTargets = appliedResult.success ? appliedResult.data : null
  const unavailable = busy || generating
  const validationMessage = 'Cần ít nhất một dimension với giá trị không rỗng (tối đa 512 ký tự), target là số nguyên > 0, weight hữu hạn > 0; tối đa 250 targets.'

  function saveTarget() {
    const parsed = coverageTargetSchema.safeParse({ ...(name ? { name } : {}), filters, unit,
      target: target.trim() ? Number(target) : NaN, weight: weight.trim() ? Number(weight) : NaN })
    if (!parsed.success) { setError(validationMessage); return }
    try {
      const next = editing === null ? mergeCoverageTargets(targets, [parsed.data]) : mergeCoverageTargets([], targets.map((t, i) => i === editing ? parsed.data : t), true)
      if (next.duplicates) { setError('Slice có cùng filters và đơn vị đã tồn tại. Hãy chỉnh target đang có.'); return }
      setTargets(next.targets); setEditing(null); setName(''); setFilters({}); setError(null); setNotice('Đã cập nhật bản soạn. Chọn Đánh giá targets để áp dụng.')
    } catch { setError(validationMessage) }
  }
  function applyJSON() {
    try {
      const next = coverageTargetsSchema.parse(JSON.parse(raw))
      const unique = mergeCoverageTargets([], next, true)
      if (unique.duplicates) throw new Error('Duplicate slice')
      setTargets(next); setEditing(null); setError(null); setNotice(null); onApply(next)
    } catch { setError(`JSON không hợp lệ hoặc có slice trùng. ${validationMessage}`) }
  }
  async function generate() {
    const parsed = coverageGenerationSchema.safeParse({ dimensions, unit, target: target.trim() ? Number(target) : NaN,
      weight: weight.trim() ? Number(weight) : NaN, include_unobserved: includeUnobserved })
    if (!parsed.success) { setError(`Chọn ít nhất một chiều sinh tổ hợp. ${validationMessage}`); return }
    flight.current?.abort()
    const controller = new AbortController(); flight.current = controller
    setGenerating(true); setError(null); setPreview(null); setNotice(null)
    try {
      const generated = coverageTargetsSchema.parse(await onGenerate(parsed.data, controller.signal))
      if (!controller.signal.aborted) { setPreview({ targets: generated, dimensions: [...dimensions] }); setPreviewFilters({}) }
    } catch { if (!controller.signal.aborted) setError('Không xem trước được tổ hợp từ máy chủ. Hãy thử lại; targets hiện tại vẫn được giữ.') }
    finally { if (!controller.signal.aborted) setGenerating(false) }
  }
  const selectedPreview = preview?.targets.filter((item) => preview.dimensions.every((d) => !previewFilters[d]?.length || previewFilters[d]?.includes(item.filters[d] ?? ''))) ?? []
  function confirmGenerated(replace: boolean) {
    try {
      const next = mergeCoverageTargets(targets, selectedPreview, replace)
      setTargets(next.targets); setEditing(null); setPreview(null); setError(null)
      setNotice(`Đã xác nhận ${replace ? 'thay thế' : 'thêm'} targets; bỏ qua ${next.duplicates} slice trùng (giữ target đang có).`)
      onApply(next.targets)
    } catch { setError('Tổng số targets sau khi thêm vượt giới hạn 250. Hãy chọn ít giá trị hơn hoặc thay thế danh sách.') }
  }
  function download() {
    if (!appliedTargets) return
    const url = URL.createObjectURL(new Blob([targetsJSON(appliedTargets)], { type: 'application/json;charset=utf-8' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'coverage_targets.json'; anchor.click(); URL.revokeObjectURL(url)
  }
  return <section className="feature-panel" aria-labelledby="coverage-targets-title">
    <h2 id="coverage-targets-title">Cấu hình Coverage & Slice</h2>
    <p>Target và weight do bạn đặt. Máy chủ tính support, gap, attainment và weighted coverage; cấu hình chưa lưu lâu dài trên máy chủ.</p>
    {appliedTargets && <p role="status">Đang áp dụng {appliedTargets.length} target từ kết quả máy chủ.</p>}
    <div className="feature-actions">
      <button type="button" onClick={download} disabled={unavailable || !appliedTargets?.length}>Xuất targets JSON đang áp dụng</button>
      <button type="button" disabled={unavailable || !appliedTargets} onClick={() => { if (appliedTargets) { setTargets(appliedTargets); setEditing(null); setNotice('Đã lấy cấu hình đang áp dụng vào bản soạn.'); setError(null) } }}>Lấy cấu hình đang áp dụng để sửa</button>
    </div>
    <details open>
      <summary>Thêm hoặc sửa slice</summary>
      <form noValidate onSubmit={(event) => { event.preventDefault(); saveTarget() }}>
        <fieldset disabled={unavailable}><legend>{editing === null ? 'Slice mới' : `Chỉnh sửa slice ${editing + 1}`}</legend>
          <label>Tên slice (tùy chọn)<input value={name} maxLength={512} onChange={(e) => setName(e.target.value)} /></label>
          <div className="feature-fields">
            {coverageDimensions.map((dimension) => <div key={dimension}>
              <label><input type="checkbox" checked={dimension in filters} onChange={(e) => {
                const next = { ...filters }; if (e.target.checked) next[dimension] = ''; else delete next[dimension]; setFilters(next)
              }} />Dimension {dimension}</label>
              {dimension in filters && <label>Giá trị {dimension}<input list={`coverage-values-${dimension}`} value={filters[dimension] ?? ''} maxLength={512}
                onChange={(e) => setFilters({ ...filters, [dimension]: e.target.value })} />
                <datalist id={`coverage-values-${dimension}`}>{choices[dimension]?.map((value) => <option key={value} value={value} />)}</datalist>
              </label>}
            </div>)}
          </div>
          <p>Có thể nhập giá trị chưa quan sát để đặt mục tiêu bổ sung. Occluded dùng yes / no / unknown.</p>
          <div className="feature-fields">
            <label>Counting unit<select value={unit} onChange={(e) => setUnit(e.target.value)}><option value="images">images — số ảnh</option><option value="objects">objects — số box</option></select></label>
            <label>Target tối thiểu<input type="number" min={1} step="1" value={target} onChange={(e) => setTarget(e.target.value)} /></label>
            <label>Weight ưu tiên<input type="number" min={0} step="any" value={weight} onChange={(e) => setWeight(e.target.value)} /></label>
          </div>
          <button className="btn-primary" type="submit">{editing === null ? 'Thêm target vào bản soạn' : 'Lưu sửa target'}</button>
          {editing !== null && <button type="button" onClick={() => { setEditing(null); setFilters({}); setName('') }}>Hủy sửa</button>}
        </fieldset>
      </form>
      <h3>Bản soạn targets ({targets.length}/250)</h3>
      {!targets.length ? <p>Chưa cấu hình target. Thêm slice, nhập JSON hoặc xem trước tổ hợp từ máy chủ.</p> : <div className="target-draft-list">
        {targets.map((item, index) => <div className="target-draft-row" key={index}>
          <span><strong>{item.name || Object.entries(item.filters).map(([k, v]) => `${k}=${v}`).join(' × ')}</strong><small>{Object.entries(item.filters).map(([k, v]) => `${k}=${v}`).join(' × ')} · {item.unit} · target {item.target} · weight {item.weight}</small></span>
          <button type="button" disabled={unavailable} aria-label={`Sửa target ${index + 1}`} onClick={() => { setEditing(index); setName(item.name ?? ''); setFilters({ ...item.filters }); setUnit(item.unit); setTarget(String(item.target)); setWeight(String(item.weight)) }}>Sửa</button>
          <button type="button" disabled={unavailable} aria-label={`Xóa target ${index + 1}`} onClick={() => { setTargets(targets.filter((_, i) => i !== index)); setEditing(null); setFilters({}); setName(''); setNotice('Đã xóa trong bản soạn; chọn Đánh giá targets để áp dụng.') }}>Xóa</button>
        </div>)}
      </div>}
      <button type="button" className="btn-primary" disabled={unavailable} onClick={() => { setError(null); setNotice(null); onApply(targets) }}>Đánh giá targets tại backend</button>
    </details>
    <details><summary>Nhập targets JSON</summary>
      <label htmlFor="target-json">Targets do người dùng cung cấp (JSON)</label>
      <textarea id="target-json" rows={6} value={raw} disabled={unavailable} onChange={(e) => setRaw(e.target.value)} />
      <button type="button" disabled={unavailable} onClick={applyJSON}>Áp dụng JSON targets</button>
    </details>
    <details><summary>Sinh tổ hợp từ dữ liệu máy chủ</summary>
      <fieldset disabled={unavailable}><legend>Chiều sinh tổ hợp (theo thứ tự chọn)</legend>
        <div className="feature-actions">{coverageDimensions.map((dimension) => <label key={dimension}>
          <input type="checkbox" checked={dimensions.includes(dimension)} onChange={(e) => setDimensions(e.target.checked ? [...dimensions, dimension] : dimensions.filter((d) => d !== dimension))} />Sinh theo {dimension}
        </label>)}</div>
        <label><input type="checkbox" checked={includeUnobserved} onChange={(e) => setIncludeUnobserved(e.target.checked)} />Bao gồm tổ hợp chưa xuất hiện (Cartesian product)</label>
        <p>Dùng counting unit, target và weight trong form trên. API sinh từ toàn bộ giá trị được phát hiện; ở bước xác nhận có thể lọc giá trị trong các targets trả về. Giới hạn 250 targets.</p>
        <button type="button" onClick={() => { void generate() }}>Xem trước tổ hợp từ backend</button>
      </fieldset>
      {preview && <div className="generation-confirmation" role="group" aria-label="Xác nhận targets sinh từ backend">
        <h3>Máy chủ trả {preview.targets.length} target dự kiến; đang chọn {selectedPreview.length}</h3>
        {preview.targets.length === 250 && <p>Đã chạm cap 250; có thể còn tổ hợp ngoài danh sách trả về. Bộ lọc dưới đây chỉ áp dụng trên danh sách này.</p>}
        {!preview.targets.length && <p>Không có tổ hợp phù hợp hoặc thiếu dữ liệu. Targets hiện tại không đổi.</p>}
        <div className="feature-fields">{preview.dimensions.map((dimension) => <label key={dimension}>Giá trị sinh {dimension}
          <select multiple value={previewFilters[dimension] ?? []} onChange={(e) => setPreviewFilters({ ...previewFilters, [dimension]: Array.from(e.target.selectedOptions, (o) => o.value) })}>
            {[...new Set(preview.targets.map((t) => t.filters[dimension]).filter((v): v is string => typeof v === 'string'))].map((value) => <option key={value}>{value}</option>)}
          </select><small>Không chọn giá trị: lấy tất cả giá trị trả về của chiều này.</small>
        </label>)}</div>
        <p>Thêm: giữ targets đang soạn khi trùng filters và đơn vị. Thay thế: bỏ bản soạn cũ. Cần xác nhận bên dưới trước khi áp dụng.</p>
        <div className="feature-actions">
          <button type="button" disabled={unavailable || !selectedPreview.length} onClick={() => confirmGenerated(false)}>Xác nhận thêm targets</button>
          <button type="button" disabled={unavailable || !selectedPreview.length} onClick={() => confirmGenerated(true)}>Xác nhận thay thế targets</button>
          <button type="button" onClick={() => setPreview(null)}>Hủy tổ hợp</button>
        </div>
      </div>}
    </details>
    {generating && <p role="status">Đang xem trước tổ hợp từ máy chủ…</p>}
    {busy && <p role="status">Đang đánh giá targets…</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </section>
}
