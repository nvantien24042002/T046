import { useEffect, useState } from 'react'
import { z } from 'zod'
import { browserJson } from '../api/browserSession'
import { useVerifiedResults } from '../context/VerifiedResultsContext'

const catalog = z.object({
  datasets: z.array(z.object({ dataset_id: z.string(), state: z.string() })),
})

export function BrowserDatasetPanel() {
  const state = useVerifiedResults()
  const [datasets, setDatasets] = useState<z.infer<typeof catalog>['datasets']>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    browserJson('/datasets', { signal: controller.signal })
      .then((d) => {
        if (!controller.signal.aborted) setDatasets(catalog.parse(d).datasets)
      })
      .catch(() => {
        if (!controller.signal.aborted) setError('Không tải được danh sách dữ liệu. Vui lòng thử lại sau.')
      })
    return () => controller.abort()
  }, [state?.revision, state?.datasetId])

  return (
    <section aria-label="Chọn bộ dữ liệu" className="browser-session-ribbon">
      <div className="session-ribbon-content">
        <div className="session-ribbon-left">
          <span className="session-icon-badge" aria-hidden="true">🗂️</span>
          <span className="session-title-label">Bộ dữ liệu:</span>
          {datasets.length === 0 && (
            <span className="session-empty-badge">
              Chưa có dữ liệu. Vui lòng tải lên tệp ZIP.
            </span>
          )}
          {datasets.map((d, index) => {
            const isSelected = state?.datasetId === d.dataset_id
            return (
              <button
                key={d.dataset_id}
                type="button"
                className={`btn-session-pill ${isSelected ? 'active' : ''}`}
                disabled={d.state !== 'ready' || state?.loading}
                onClick={() => {
                  void state?.loadResults(d.dataset_id, '')
                }}
                aria-pressed={isSelected}
              >
                <span className="pill-dot" aria-hidden="true" />
                <span className="pill-text">Bộ dữ liệu {index + 1}{d.state !== 'ready' ? ' · Chưa sẵn sàng' : ''}</span>
              </button>
            )
          })}
        </div>


      </div>

      {error && <p role="alert" className="session-alert-msg">{error}</p>}
    </section>
  )

}
