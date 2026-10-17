import { ResultStatus, ResultIdentity } from '../components/ResultStatus'
import { useState, useMemo } from 'react'
import { config } from '../config'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { EmptyState } from '../components/States'
import { ImagePreview } from '../components/ImagePreview'
import { Pagination, paginate } from '../components/Pagination'
import { downloadCSV } from '../components/Export'
import baseline from '../mocks/overview-baseline.json'

interface SceneImage {
  image_id: string
  image_name: string
  weather: string
  timeofday: string
}

export function SceneFinder() {
  const verified = useVerifiedResults()
  return <SceneFinderView key={config.mode === 'backend' ? verified?.revision : 'mock'} />
}

function SceneFinderView() {
  const verified = useVerifiedResults()

  // Filter states
  const [selectedWeather, setSelectedWeather] = useState<string>('ALL')
  const [selectedTimeOfDay, setSelectedTimeOfDay] = useState<string>('ALL')
  const [searchKeyword, setSearchKeyword] = useState<string>('')

  // Pagination
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(6)

  // Images data extracted unconditionally
  const images: SceneImage[] = useMemo(() => {
    if (config.mode === 'backend') {
      if (!verified?.activeResult) {
        return []
      }
      const payload = verified.activeResult.payload
      return payload.distribution_images.map((im) => ({
        image_id: im.image_id,
        image_name: im.image_name,
        weather: (im.weather || 'missing').trim() || 'missing',
        timeofday: (im.timeofday || 'missing').trim() || 'missing',
      }))
    } else {
      return baseline.quality.map((q, idx) => ({
        image_id: String(idx + 1),
        image_name: q.filename,
        weather: 'clear',
        timeofday: 'day',
      }))
    }
  }, [verified?.activeResult])

  // Weather & time of day choices
  const weatherChoices = useMemo(() => {
    const s = new Set<string>()
    for (const img of images) s.add(img.weather)
    return [...s].sort()
  }, [images])

  const timeOfDayChoices = useMemo(() => {
    const s = new Set<string>()
    for (const img of images) s.add(img.timeofday)
    return [...s].sort()
  }, [images])

  // Unresolved metadata count
  const unresolvedCount = useMemo(() => {
    return images.filter(
      (img) =>
        ['missing', 'unknown'].includes(img.weather.toLowerCase()) ||
        ['missing', 'unknown'].includes(img.timeofday.toLowerCase()),
    ).length
  }, [images])

  // Filtering
  const filtered = useMemo(() => {
    return images.filter((img) => {
      if (selectedWeather !== 'ALL' && img.weather !== selectedWeather) return false
      if (selectedTimeOfDay !== 'ALL' && img.timeofday !== selectedTimeOfDay) return false
      if (searchKeyword.trim() && !img.image_name.toLowerCase().includes(searchKeyword.trim().toLowerCase())) {
        return false
      }
      return true
    })
  }, [images, selectedWeather, selectedTimeOfDay, searchKeyword])

  const paged = paginate(filtered, page, pageSize)

  function handleExportCsv() {
    downloadCSV(filtered as unknown as Record<string, unknown>[], 'scene_filtered_images.csv', config.mode === 'backend' && verified?.activeResult ? { datasetId: verified.activeResult.payload.dataset_id, resultId: verified.activeResult.descriptor?.result_id ?? null } : undefined)
  }

  // Backend empty state check after all hooks
  if (config.mode === 'backend' && !verified?.activeResult) {
    return (
      <>
        <p className="intro">Khu vực chức năng</p>
        <h1>Scene Finder</h1>
        {config.mode === 'backend' && <ResultIdentity />}
      <p className="parity-notice">Chưa triển khai trong M1 — Bản xem trước Parity Track B</p>
        <ResultStatus />
      </>
    )
  }

  return (
    <>
      <p className="intro">Khu vực chức năng</p>
      <h1>Scene Finder</h1>
      {config.mode === 'backend' && <ResultIdentity />}
      <p className="parity-notice">Chưa triển khai trong M1 — Bản xem trước Parity Track B</p>

      {images.length === 0 ? (
        <EmptyState>Không có dữ liệu ảnh để tìm kiếm scene.</EmptyState>
      ) : (
        <>
          <div className="filter-panel" style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <label>
              Thời tiết:{' '}
              <select
                value={selectedWeather}
                onChange={(e) => {
                  setSelectedWeather(e.target.value)
                  setPage(0)
                }}
              >
                <option value="ALL">Tất cả ({images.length})</option>
                {weatherChoices.map((w) => (
                  <option key={w} value={w}>
                    {w}
                  </option>
                ))}
              </select>
            </label>

            <label>
              Thời điểm trong ngày:{' '}
              <select
                value={selectedTimeOfDay}
                onChange={(e) => {
                  setSelectedTimeOfDay(e.target.value)
                  setPage(0)
                }}
              >
                <option value="ALL">Tất cả ({images.length})</option>
                {timeOfDayChoices.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>

            <label>
              Tìm kiếm tên ảnh:{' '}
              <input
                type="text"
                placeholder="Nhập tên ảnh..."
                value={searchKeyword}
                onChange={(e) => {
                  setSearchKeyword(e.target.value)
                  setPage(0)
                }}
              />
            </label>
          </div>

          <dl className="overview-metrics" style={{ margin: '1rem 0' }}>
            <div>
              <dt>Ảnh khớp bộ lọc</dt>
              <dd>{filtered.length}</dd>
            </div>
            <div>
              <dt>Tổng ảnh trong XML</dt>
              <dd>{images.length}</dd>
            </div>
            <div>
              <dt>Ảnh có metadata chưa rõ</dt>
              <dd>{unresolvedCount}</dd>
            </div>
          </dl>

          {filtered.length === 0 ? (
            <p>Không có ảnh phù hợp với bộ lọc hiện tại.</p>
          ) : (
            <>
              <div className="image-cards-grid">
                {paged.map((img) => (
                  <article key={img.image_id} className="image-card">
                    <header>
                      <h3>{img.image_name}</h3>
                    </header>
                    <ImagePreview
                      imagePath={img.image_name}
                      annotated={false}
                      alt={`Ảnh scene: ${img.image_name}`}
                    />
                    <p className="scene-meta-caption">
                      Weather: <strong>{img.weather}</strong> | Time of day: <strong>{img.timeofday}</strong>
                    </p>
                  </article>
                ))}
              </div>

              <Pagination
                total={filtered.length}
                page={page}
                pageSize={pageSize}
                onPageChange={setPage}
                onPageSizeChange={(newSize) => {
                  setPageSize(newSize)
                  setPage(0)
                }}
              />

              <div className="scene-table-wrapper" style={{ marginTop: '1.5rem' }}>
                <table>
                  <caption>Danh sách ảnh khớp bộ lọc ({filtered.length} ảnh)</caption>
                  <thead>
                    <tr>
                      <th scope="col">Image ID</th>
                      <th scope="col">Tên ảnh</th>
                      <th scope="col">Thời tiết</th>
                      <th scope="col">Thời điểm</th>
                    </tr>
                  </thead>
                  <tbody>
                    {paged.map((img) => (
                      <tr key={img.image_id}>
                        <th scope="row">{img.image_id}</th>
                        <td>{img.image_name}</td>
                        <td>{img.weather}</td>
                        <td>{img.timeofday}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div style={{ marginTop: '1rem' }}>
                <button type="button" onClick={handleExportCsv}>
                  Tải scene filtered CSV
                </button>
              </div>
            </>
          )}
        </>
      )}
    </>
  )
}
