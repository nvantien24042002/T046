import { ResultStatus, ResultIdentity } from '../components/ResultStatus'
import { useState, useMemo } from 'react'
import { config } from '../config'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { EmptyState } from '../components/States'
import { downloadCSV } from '../components/Export'
import baseline from '../mocks/overview-baseline.json'

export const DIMS = ['class', 'weather', 'timeofday', 'occluded'] as const
export type Dimension = (typeof DIMS)[number]

export interface SliceFilter {
  class?: string
  weather?: string
  timeofday?: string
  occluded?: string
}

export interface TargetConfig {
  name?: string
  filters: SliceFilter
  unit: 'images' | 'objects'
  target: number
  weight: number
}

export interface EvaluatedSlice {
  slice: string
  filters: string
  unit: 'images' | 'objects'
  support: number
  target: number
  gap: number
  attainment_pct: number
  weight: number
  weighted_gap: number
  status: 'Đạt' | 'Thiếu'
  metadata_unresolved: number
  confidence: 'Tạm thời' | 'Quan sát'
  recommendation?: string
}

interface UniverseRow {
  image_id: string
  box_id: string
  class: string
  weather: string
  timeofday: string
  occluded: string
}

interface ImageMeta {
  image_id: string
  image_name: string
  weather: string
  timeofday: string
}

export function buildUniverse(
  images: ImageMeta[],
  boxes: Array<{ box_id: string; label: string; occluded: string; image_id?: string; valid_geometry?: boolean }>,
): UniverseRow[] {
  if (images.length === 0 || boxes.length === 0) return []
  const imgMap = new Map<string, ImageMeta>()
  for (const img of images) {
    const w = (img.weather || 'missing').trim() || 'missing'
    const t = (img.timeofday || 'missing').trim() || 'missing'
    imgMap.set(img.image_id, { ...img, weather: w, timeofday: t })
  }

  const rows: UniverseRow[] = []
  for (const b of boxes) {
    if (b.valid_geometry !== true || !b.image_id) continue
    const occStr = String(b.occluded || '').toLowerCase()
    const occVal = ['1', 'true', 'yes'].includes(occStr)
      ? 'yes'
      : ['0', 'false', 'no'].includes(occStr)
        ? 'no'
        : 'unknown'

    const imgId = b.image_id
    const img = imgMap.get(imgId) ?? {
      image_id: imgId,
      image_name: '',
      weather: 'missing',
      timeofday: 'missing',
    }

    rows.push({
      image_id: imgId,
      box_id: b.box_id,
      class: String(b.label),
      weather: img.weather,
      timeofday: img.timeofday,
      occluded: occVal,
    })
  }
  return rows
}

function countSupport(
  universe: UniverseRow[],
  images: ImageMeta[],
  filters: SliceFilter,
  unit: 'images' | 'objects',
): { support: number; unresolved: number } {
  const entries = Object.entries(filters) as Array<[Dimension, string]>
  if (entries.length === 0) return { support: 0, unresolved: 0 }

  const sub = universe.filter((row) => {
    return entries.every(([dim, val]) => row[dim] === val)
  })

  let support = 0
  if (unit === 'images') {
    const ids = new Set(sub.map((r) => r.image_id))
    support = ids.size
  } else {
    support = sub.length
  }

  let unresolved = 0
  for (const [dim, val] of entries) {
    if ((dim === 'weather' || dim === 'timeofday') && val !== 'missing' && val !== 'unknown') {
      for (const img of images) {
        const v = img[dim] ?? 'missing'
        if (v === 'missing' || v === 'unknown') {
          unresolved++
        }
      }
    }
  }

  return { support, unresolved }
}

export function evaluateTargets(
  universe: UniverseRow[],
  images: ImageMeta[],
  targets: TargetConfig[],
): EvaluatedSlice[] {
  const records: EvaluatedSlice[] = []
  for (const t of targets) {
    const filters = t.filters
    const unit = t.unit || 'images'
    const target = Number(t.target)
    const weight = Number(t.weight ?? 1)
    if (!filters || !Object.keys(filters).length || Object.keys(filters).some((key) => !DIMS.includes(key as Dimension)) || !Number.isFinite(target) || !Number.isFinite(weight) || target <= 0 || weight <= 0 || (unit !== 'images' && unit !== 'objects')) {
      continue
    }

    const { support, unresolved } = countSupport(universe, images, filters, unit)
    const gap = Math.max(0, target - support)
    const attainment = target > 0 ? Math.round((1000 * support) / target) / 10 : 100
    const weightedGap = Math.round((gap / target) * weight * 10000) / 10000

    const sliceName =
      t.name ||
      Object.entries(filters)
        .map(([k, v]) => `${k}=${v}`)
        .join(' × ')

    records.push({
      slice: sliceName,
      filters: JSON.stringify(filters),
      unit,
      support,
      target,
      gap,
      attainment_pct: attainment,
      weight,
      weighted_gap: weightedGap,
      status: gap === 0 ? 'Đạt' : 'Thiếu',
      metadata_unresolved: unresolved,
      confidence: unresolved > 0 ? 'Tạm thời' : 'Quan sát',
    })
  }
  return records
}

export function getRecommendations(evaluated: EvaluatedSlice[]): EvaluatedSlice[] {
  const deficits = evaluated.filter((s) => s.gap > 0)
  deficits.sort((a, b) => {
    if (b.weighted_gap !== a.weighted_gap) return b.weighted_gap - a.weighted_gap
    return b.gap - a.gap
  })
  return deficits.map((r) => ({
    ...r,
    recommendation:
      (r.metadata_unresolved > 0
        ? 'Kiểm tra và bổ sung metadata trước khi kết luận thiếu; sau đó ưu tiên thu thập/gán nhãn '
        : 'Ưu tiên bổ sung/kiểm tra annotation ') + `${r.slice} (thiếu ${r.gap} ${r.unit}).`,
  }))
}

export function Coverage() {
  const verified = useVerifiedResults()
  return <CoverageView key={config.mode === 'backend' ? verified?.revision : 'mock'} />
}

function CoverageView() {
  const verified = useVerifiedResults()

  // Slices configuration state
  const [configMode, setConfigMode] = useState<'single' | 'auto' | 'json'>('single')
  const [targets, setTargets] = useState<TargetConfig[]>([])
  const [jsonInput, setJsonInput] = useState<string>('')
  const [jsonError, setJsonError] = useState<string | null>(null)

  // Single slice builder state
  const [singleDims, setSingleDims] = useState<Dimension[]>(['class'])
  const [singleFilterVals, setSingleFilterVals] = useState<Record<string, string>>({})
  const [singleUnit, setSingleUnit] = useState<'images' | 'objects'>('images')
  const [singleTarget, setSingleTarget] = useState<number>(50)
  const [singleWeight, setSingleWeight] = useState<number>(1.0)

  // Auto-generate state
  const [autoDims, setAutoDims] = useState<Dimension[]>(['class'])
  const [autoIncludeCartesian, setAutoIncludeCartesian] = useState<boolean>(false)
  const [autoUnit, setAutoUnit] = useState<'images' | 'objects'>('images')
  const [autoTarget, setAutoTarget] = useState<number>(50)
  const [autoWeight, setAutoWeight] = useState<number>(1.0)

  // Data extraction wrapped in useMemo
  const { images, rawBoxes } = useMemo(() => {
    if (config.mode === 'backend') {
      if (!verified?.activeResult) {
        return { images: [], rawBoxes: [] }
      }
      const payload = verified.activeResult.payload
      const imgs: ImageMeta[] = payload.distribution_images.map((im) => ({
        image_id: im.image_id,
        image_name: im.image_name,
        weather: im.weather,
        timeofday: im.timeofday,
      }))
      const bxs = payload.distribution_boxes
      return { images: imgs, rawBoxes: bxs }
    } else {
      const imgs: ImageMeta[] = baseline.quality.map((q, idx) => ({
        image_id: String(idx + 1),
        image_name: q.filename,
        weather: 'clear',
        timeofday: 'day',
      }))
      const bxs = baseline.audit.map((a, idx) => ({
        box_id: a.box_id,
        label: a.label,
        occluded: a.occluded,
        image_id: String((idx % imgs.length) + 1),
        valid_geometry: true,
      }))
      return { images: imgs, rawBoxes: bxs }
    }
  }, [verified?.activeResult])

  const universe = useMemo(() => buildUniverse(images, rawBoxes), [images, rawBoxes])

  // Available choices for dimensions
  const choices = useMemo(() => {
    const res: Record<Dimension, string[]> = {
      class: [],
      weather: [],
      timeofday: [],
      occluded: ['yes', 'no', 'unknown'],
    }
    const classSet = new Set<string>()
    for (const b of rawBoxes) if (b.label) classSet.add(String(b.label))
    res.class = [...classSet].sort()

    const weatherSet = new Set<string>()
    const todSet = new Set<string>()
    for (const im of images) {
      if (im.weather) weatherSet.add(im.weather)
      if (im.timeofday) todSet.add(im.timeofday)
    }
    res.weather = [...weatherSet].sort()
    res.timeofday = [...todSet].sort()
    return res
  }, [rawBoxes, images])

  // Class overview calculations
  const classStats = useMemo(() => {
    if (universe.length === 0 || images.length === 0) return []
    const totalImgs = images.length
    const totalObjs = universe.length

    const map = new Map<string, { objects: number; imageIds: Set<string> }>()
    for (const r of universe) {
      const cur = map.get(r.class) ?? { objects: 0, imageIds: new Set<string>() }
      cur.objects++
      cur.imageIds.add(r.image_id)
      map.set(r.class, cur)
    }

    return [...map.entries()]
      .map(([cls, data]) => ({
        class: cls,
        objects: data.objects,
        images: data.imageIds.size,
        image_coverage_pct: Math.round((data.imageIds.size / totalImgs) * 10000) / 100,
        object_share_pct: Math.round((data.objects / totalObjs) * 10000) / 100,
      }))
      .sort((a, b) => b.objects - a.objects)
  }, [universe, images])

  // Evaluation results
  const evaluated = useMemo(() => evaluateTargets(universe, images, targets), [universe, images, targets])
  const recommendations = useMemo(() => getRecommendations(evaluated), [evaluated])

  const totalSlices = evaluated.length
  const metSlices = evaluated.filter((s) => s.gap === 0).length
  const deficitSlices = evaluated.filter((s) => s.gap > 0).length
  const totalWeight = evaluated.reduce((sum, s) => sum + s.weight, 0)
  const metWeight = evaluated.filter((s) => s.gap === 0).reduce((sum, s) => sum + s.weight, 0)
  const weightedCoverage = totalWeight > 0 ? (Math.round((metWeight / totalWeight) * 1000) / 10).toFixed(1) + '%' : '0.0%'

  // Add single slice handler
  function handleAddSingleSlice() {
    const filters: SliceFilter = {}
    for (const d of singleDims) {
      const first = choices[d][0]
      const fallback = typeof first === 'string' ? first : ''
      const val = singleFilterVals[d] || fallback
      if (val) filters[d] = val
    }
    if (Object.keys(filters).length === 0) return

    const name = Object.entries(filters)
      .map(([k, v]) => `${k}=${v}`)
      .join(' × ')

    setTargets((prev) => [
      ...prev,
      {
        name,
        filters,
        unit: singleUnit,
        target: singleTarget,
        weight: singleWeight,
      },
    ])
  }

  // Auto-generate slices
  function handleAutoGenerate() {
    if (autoDims.length === 0) return
    const generated: TargetConfig[] = []
    const cap = 250

    if (autoIncludeCartesian) {
      function cartesian(arr: string[][]): string[][] {
        return arr.reduce<string[][]>((a, b) => a.flatMap((d) => b.map((e) => [...d, e])), [[]])
      }
      const dimChoices = autoDims.map((d) => choices[d])
      const combos = cartesian(dimChoices)
      for (const combo of combos) {
        if (generated.length >= cap) break
        const filters: SliceFilter = {}
        autoDims.forEach((d, idx) => {
          const val = combo[idx]
          if (typeof val === 'string') {
            filters[d] = val
          }
        })
        const name = Object.entries(filters)
          .map(([k, v]) => `${k}=${v}`)
          .join(' × ')
        generated.push({ name, filters, unit: autoUnit, target: autoTarget, weight: autoWeight })
      }
    } else {
      const seen = new Set<string>()
      for (const row of universe) {
        if (generated.length >= cap) break
        const filters: SliceFilter = {}
        for (const d of autoDims) {
          filters[d] = row[d]
        }
        const key = JSON.stringify(filters)
        if (!seen.has(key)) {
          seen.add(key)
          const name = Object.entries(filters)
            .map(([k, v]) => `${k}=${v}`)
            .join(' × ')
          generated.push({ name, filters, unit: autoUnit, target: autoTarget, weight: autoWeight })
        }
      }
    }

    setTargets(generated)
  }

  // Apply JSON targets
  function handleApplyJson() {
    setJsonError(null)
    try {
      const parsed = JSON.parse(jsonInput)
      if (!Array.isArray(parsed)) throw new Error('Cần JSON array chứa danh sách target')
      for (const item of parsed) {
        if (!item.filters || typeof item.filters !== 'object') throw new Error('Mỗi item cần có filters')
        if (Array.isArray(item.filters) || !Object.keys(item.filters).length || Object.keys(item.filters).some((d) => !DIMS.includes(d as Dimension)) || Object.values(item.filters).some((v) => typeof v !== 'string')) throw new Error('Filters không hợp lệ')
        if (!Number.isFinite(item.target) || Math.trunc(item.target) <= 0) throw new Error('Target phải > 0')
        item.target = Math.trunc(item.target)
        if (item.weight === undefined) item.weight = 1
        if (!Number.isFinite(item.weight) || item.weight <= 0) throw new Error('Weight phải > 0')
        if (item.unit === undefined) item.unit = 'images'
        if (item.unit !== 'images' && item.unit !== 'objects') throw new Error('Unit không hợp lệ')
      }
      setTargets(parsed)
    } catch (err: unknown) {
      setJsonError(err instanceof Error ? err.message : 'Invalid JSON')
    }
  }

  // Backend mode empty state check after all hooks
  if (config.mode === 'backend' && !verified?.activeResult) {
    return (
      <>
        <p className="intro">Khu vực chức năng</p>
        <h1>Coverage & Slice</h1>
        {config.mode === 'backend' && <ResultIdentity />}
      <p className="parity-notice">Chưa triển khai trong M1 — Bản xem trước Parity Track B</p>
        <ResultStatus />
      </>
    )
  }

  return (
    <>
      <p className="intro">Khu vực chức năng</p>
      <h1>Coverage & Slice</h1>
      {config.mode === 'backend' && <ResultIdentity />}
      <p className="parity-notice">Chưa triển khai trong M1 — Bản xem trước Parity Track B</p>

      {images.length === 0 ? (
        <EmptyState>Cần annotations.xml có thẻ image để tính coverage.</EmptyState>
      ) : (
        <>
          <section aria-labelledby="class-dist-heading" className="coverage-section">
            <h2 id="class-dist-heading">1. Phân bố quan sát theo class</h2>
            <p className="coverage-caption">
              Coverage quan sát từ annotation; không có GT. Target do Leader đặt. Các slice có thể giao nhau nên không cộng dồn gap giữa các slice.
            </p>

            {classStats.length === 0 ? (
              <p>Không có box hợp lệ; class coverage bằng 0 hoặc chưa xác định.</p>
            ) : (
              <div className="class-coverage-table-wrapper">
                <table>
                  <caption>Tỷ lệ quan sát theo từng class ({classStats.length} class)</caption>
                  <thead>
                    <tr>
                      <th scope="col">Class</th>
                      <th scope="col">Số object</th>
                      <th scope="col">Số ảnh</th>
                      <th scope="col">Tỷ lệ ảnh (%)</th>
                      <th scope="col">Tỷ lệ object (%)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {classStats.map((c) => (
                      <tr key={c.class}>
                        <th scope="row">{c.class}</th>
                        <td>{c.objects}</td>
                        <td>{c.images}</td>
                        <td>{c.image_coverage_pct.toFixed(2)}%</td>
                        <td>{c.object_share_pct.toFixed(2)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section aria-labelledby="target-config-heading" className="coverage-section">
            <h2 id="target-config-heading">2. Thiết lập mục tiêu</h2>
            <div className="filter-panel" role="radiogroup" aria-label="Cách cấu hình slice">
              <label>
                <input
                  type="radio"
                  name="config-mode"
                  checked={configMode === 'single'}
                  onChange={() => setConfigMode('single')}
                />{' '}
                Thêm một slice
              </label>
              <label>
                <input
                  type="radio"
                  name="config-mode"
                  checked={configMode === 'auto'}
                  onChange={() => setConfigMode('auto')}
                />{' '}
                Tạo nhiều slice tự động
              </label>
              <label>
                <input
                  type="radio"
                  name="config-mode"
                  checked={configMode === 'json'}
                  onChange={() => setConfigMode('json')}
                />{' '}
                Nhập JSON
              </label>
            </div>

            {configMode === 'single' && (
              <div className="slice-builder-box">
                <fieldset>
                  <legend>Chiều phân tích</legend>
                  {DIMS.map((d) => (
                    <label key={d} style={{ marginRight: '1rem' }}>
                      <input
                        type="checkbox"
                        checked={singleDims.includes(d)}
                        onChange={(e) => {
                          if (e.target.checked) setSingleDims((prev) => [...prev, d])
                          else setSingleDims((prev) => prev.filter((x) => x !== d))
                        }}
                      />{' '}
                      {d}
                    </label>
                  ))}
                </fieldset>

                <div className="slice-inputs-grid" style={{ marginTop: '0.75rem' }}>
                  {singleDims.map((d) => (
                    <label key={d} style={{ display: 'inline-block', marginRight: '1rem' }}>
                      {d}:{' '}
                      <select
                        value={singleFilterVals[d] || choices[d][0] || ''}
                        onChange={(e) =>
                          setSingleFilterVals((prev) => ({ ...prev, [d]: e.target.value }))
                        }
                      >
                        {choices[d].map((v) => (
                          <option key={v} value={v}>
                            {v}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>

                <div className="slice-unit-target" style={{ marginTop: '0.75rem' }}>
                  <label style={{ marginRight: '1rem' }}>
                    Đơn vị:{' '}
                    <select
                      value={singleUnit}
                      onChange={(e) => setSingleUnit(e.target.value as 'images' | 'objects')}
                    >
                      <option value="images">images</option>
                      <option value="objects">objects</option>
                    </select>
                  </label>
                  <label style={{ marginRight: '1rem' }}>
                    Mục tiêu tối thiểu:{' '}
                    <input
                      type="number"
                      min="1"
                      value={singleTarget}
                      onChange={(e) => setSingleTarget(Number(e.target.value))}
                    />
                  </label>
                  <label style={{ marginRight: '1rem' }}>
                    Trọng số:{' '}
                    <input
                      type="number"
                      min="0.1"
                      step="0.5"
                      value={singleWeight}
                      onChange={(e) => setSingleWeight(Number(e.target.value))}
                    />
                  </label>
                  <button type="button" onClick={handleAddSingleSlice}>
                    Thêm slice
                  </button>
                </div>
              </div>
            )}

            {configMode === 'auto' && (
              <div className="auto-slice-box">
                <fieldset>
                  <legend>Kết hợp chiều</legend>
                  {DIMS.map((d) => (
                    <label key={d} style={{ marginRight: '1rem' }}>
                      <input
                        type="checkbox"
                        checked={autoDims.includes(d)}
                        onChange={(e) => {
                          if (e.target.checked) setAutoDims((prev) => [...prev, d])
                          else setAutoDims((prev) => prev.filter((x) => x !== d))
                        }}
                      />{' '}
                      {d}
                    </label>
                  ))}
                </fieldset>
                <div style={{ margin: '0.5rem 0' }}>
                  <label>
                    <input
                      type="checkbox"
                      checked={autoIncludeCartesian}
                      onChange={(e) => setAutoIncludeCartesian(e.target.checked)}
                    />{' '}
                    Bao gồm tổ hợp chưa xuất hiện (Cartesian product)
                  </label>
                </div>
                <div className="slice-unit-target">
                  <label style={{ marginRight: '1rem' }}>
                    Đơn vị:{' '}
                    <select
                      value={autoUnit}
                      onChange={(e) => setAutoUnit(e.target.value as 'images' | 'objects')}
                    >
                      <option value="images">images</option>
                      <option value="objects">objects</option>
                    </select>
                  </label>
                  <label style={{ marginRight: '1rem' }}>
                    Mục tiêu cho mỗi slice:{' '}
                    <input
                      type="number"
                      min="1"
                      value={autoTarget}
                      onChange={(e) => setAutoTarget(Number(e.target.value))}
                    />
                  </label>
                  <label style={{ marginRight: '1rem' }}>
                    Trọng số:{' '}
                    <input
                      type="number"
                      min="0.1"
                      step="0.5"
                      value={autoWeight}
                      onChange={(e) => setAutoWeight(Number(e.target.value))}
                    />
                  </label>
                  <button type="button" onClick={handleAutoGenerate}>
                    Tạo slice
                  </button>
                </div>
              </div>
            )}

            {configMode === 'json' && (
              <div className="json-slice-box">
                <label htmlFor="json-targets-textarea">JSON danh sách target:</label>
                <textarea
                  id="json-targets-textarea"
                  rows={6}
                  style={{ width: '100%', fontFamily: 'monospace' }}
                  placeholder={`[
  {
    "name": "bus-night",
    "filters": { "class": "bus", "timeofday": "night" },
    "unit": "images",
    "target": 50,
    "weight": 1.5
  }
]`}
                  value={jsonInput}
                  onChange={(e) => setJsonInput(e.target.value)}
                />
                {jsonError && (
                  <p className="error-text" role="alert">
                    {jsonError}
                  </p>
                )}
                <button type="button" onClick={handleApplyJson} style={{ marginTop: '0.5rem' }}>
                  Áp dụng JSON
                </button>
              </div>
            )}
          </section>

          {targets.length > 0 && (
            <>
              <section aria-labelledby="target-list-heading" className="coverage-section">
                <h2 id="target-list-heading">3. Chỉnh mục tiêu theo từng slice ({targets.length} slice)</h2>
                <div className="targets-table-wrapper">
                  <table>
                    <caption>Danh sách các slice mục tiêu đã định nghĩa</caption>
                    <thead>
                      <tr>
                        <th scope="col">Slice</th>
                        <th scope="col">Đơn vị</th>
                        <th scope="col">Mục tiêu</th>
                        <th scope="col">Trọng số</th>
                        <th scope="col">Thao tác</th>
                      </tr>
                    </thead>
                    <tbody>
                      {targets.map((t, idx) => (
                        <tr key={idx}>
                          <th scope="row">{t.name}</th>
                          <td>{t.unit}</td>
                          <td>
                            <input
                              type="number"
                              min="1"
                              value={t.target}
                              onChange={(e) => {
                                const val = Number(e.target.value)
                                setTargets((prev) =>
                                  prev.map((item, i) => (i === idx ? { ...item, target: val } : item)),
                                )
                              }}
                              style={{ width: '80px' }}
                            />
                          </td>
                          <td>
                            <input
                              type="number"
                              min="0.1"
                              step="0.5"
                              value={t.weight}
                              onChange={(e) => {
                                const val = Number(e.target.value)
                                setTargets((prev) =>
                                  prev.map((item, i) => (i === idx ? { ...item, weight: val } : item)),
                                )
                              }}
                              style={{ width: '70px' }}
                            />
                          </td>
                          <td>
                            <button
                              type="button"
                              onClick={() => setTargets((prev) => prev.filter((_, i) => i !== idx))}
                            >
                              Xóa
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section aria-labelledby="eval-heading" className="coverage-section">
                <h2 id="eval-heading">4. So sánh thực tế với mục tiêu</h2>
                <dl className="overview-metrics">
                  <div>
                    <dt>Tổng slice</dt>
                    <dd>{totalSlices}</dd>
                  </div>
                  <div>
                    <dt>Slice đạt</dt>
                    <dd>{metSlices}</dd>
                  </div>
                  <div>
                    <dt>Slice thiếu</dt>
                    <dd>{deficitSlices}</dd>
                  </div>
                  <div>
                    <dt>Weighted coverage</dt>
                    <dd>{weightedCoverage}</dd>
                  </div>
                </dl>

                <div className="eval-table-wrapper">
                  <table>
                    <caption>Kết quả so sánh quan sát và mục tiêu</caption>
                    <thead>
                      <tr>
                        <th scope="col">Slice</th>
                        <th scope="col">Đơn vị</th>
                        <th scope="col">Support</th>
                        <th scope="col">Mục tiêu</th>
                        <th scope="col">Gap</th>
                        <th scope="col">Đạt (%)</th>
                        <th scope="col">Trọng số</th>
                        <th scope="col">Weighted Gap</th>
                        <th scope="col">Trạng thái</th>
                        <th scope="col">Metadata chưa rõ</th>
                        <th scope="col">Độ tin cậy</th>
                      </tr>
                    </thead>
                    <tbody>
                      {evaluated.map((r, idx) => (
                        <tr key={idx}>
                          <th scope="row">{r.slice}</th>
                          <td>{r.unit}</td>
                          <td>{r.support}</td>
                          <td>{r.target}</td>
                          <td>{r.gap}</td>
                          <td>{r.attainment_pct.toFixed(1)}%</td>
                          <td>{r.weight}</td>
                          <td>{r.weighted_gap.toFixed(4)}</td>
                          <td>
                            <span className={`status-tag status-${r.status === 'Đạt' ? 'pass' : 'error'}`}>
                              {r.status}
                            </span>
                          </td>
                          <td>{r.metadata_unresolved}</td>
                          <td>{r.confidence}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section aria-labelledby="rec-heading" className="coverage-section">
                <h2 id="rec-heading">5. Đề xuất slice cần bổ sung</h2>
                {recommendations.length === 0 ? (
                  <p className="success-note">Các slice đã cấu hình đều đạt mục tiêu quan sát.</p>
                ) : (
                  <>
                    <div className="rec-table-wrapper">
                      <table>
                        <caption>Danh sách đề xuất ưu tiên theo weighted gap</caption>
                        <thead>
                          <tr>
                            <th scope="col">Slice</th>
                            <th scope="col">Thiếu</th>
                            <th scope="col">Đơn vị</th>
                            <th scope="col">Trọng số</th>
                            <th scope="col">Metadata chưa rõ</th>
                            <th scope="col">Độ tin cậy</th>
                            <th scope="col">Đề xuất</th>
                          </tr>
                        </thead>
                        <tbody>
                          {recommendations.map((r, idx) => (
                            <tr key={idx}>
                              <th scope="row">{r.slice}</th>
                              <td>{r.gap}</td>
                              <td>{r.unit}</td>
                              <td>{r.weight}</td>
                              <td>{r.metadata_unresolved}</td>
                              <td>{r.confidence}</td>
                              <td>{r.recommendation}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p className="rec-warning">
                      Các đề xuất là ưu tiên review/thu thập dữ liệu, không chứng minh dataset sai hoặc annotation thiếu.
                    </p>
                  </>
                )}

                <div className="export-actions" style={{ marginTop: '1rem', display: 'flex', gap: '0.75rem' }}>
                  <button
                    type="button"
                    onClick={() => {
                      downloadCSV(evaluated as unknown as Record<string, unknown>[], 'coverage_results.csv', config.mode === 'backend' && verified?.activeResult ? { datasetId: verified.activeResult.payload.dataset_id, resultId: verified.activeResult.descriptor?.result_id ?? null } : undefined)
                    }}
                  >
                    Tải coverage CSV
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      downloadCSV(recommendations as unknown as Record<string, unknown>[], 'slice_recommendations.csv', config.mode === 'backend' && verified?.activeResult ? { datasetId: verified.activeResult.payload.dataset_id, resultId: verified.activeResult.descriptor?.result_id ?? null } : undefined)
                    }}
                  >
                    Tải recommendations CSV
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const jsonStr = JSON.stringify(targets, null, 2)
                      const blob = new Blob([jsonStr], { type: 'application/json' })
                      const url = URL.createObjectURL(blob)
                      const a = document.createElement('a')
                      a.href = url
                      a.download = 'coverage_targets.json'
                      a.click()
                      URL.revokeObjectURL(url)
                    }}
                  >
                    Tải cấu hình targets JSON
                  </button>
                </div>
              </section>
            </>
          )}
        </>
      )}
    </>
  )
}
