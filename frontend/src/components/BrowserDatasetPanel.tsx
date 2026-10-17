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
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    browserJson('/datasets', { signal: controller.signal })
      .then((d) => {
        if (!controller.signal.aborted) setDatasets(catalog.parse(d).datasets)
      })
      .catch(() => {
        if (!controller.signal.aborted) setError('Không đọc được session catalog.')
      })
    return () => controller.abort()
  }, [attempt, state?.revision, state?.datasetId])

  return (
    <section aria-label="Anonymous session datasets" className="browser-session-ribbon">
      <div className="session-ribbon-content">
        <div className="session-ribbon-left">
          <span className="session-icon-badge" aria-hidden="true">🗂️</span>
          <span className="session-title-label">Phiên Dataset:</span>
          {datasets.length === 0 && (
            <span className="session-empty-badge">
              Chưa có dataset trong phiên. Vui lòng tải lên file zip.
            </span>
          )}
          {datasets.map((d) => {
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
                title={`Chọn dataset ${d.dataset_id} (${d.state})`}
              >
                <span className="pill-dot" aria-hidden="true" />
                <span className="pill-text">{d.dataset_id} · {d.state}</span>
              </button>
            )
          })}
        </div>

        <div className="session-ribbon-right">
          <button
            type="button"
            className="btn-refresh-icon"
            onClick={() => {
              setError(null)
              setAttempt(attempt + 1)
            }}
            title="Làm mới danh sách session"
          >
            <span className="icon" aria-hidden="true">↻</span>
            <span className="btn-text">Làm mới danh sách session</span>
          </button>
        </div>
      </div>

      {error && <p role="alert" className="session-alert-msg">{error}</p>}
    </section>
  )

}
