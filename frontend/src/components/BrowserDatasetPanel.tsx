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
    <section aria-label="Anonymous session datasets" className="browser-session-bar-card">
      <div className="session-bar-top">
        <div className="session-info-left">
          <span className="session-shield-badge" aria-hidden="true">🔒</span>
          <div>
            <h2>Dataset của phiên trình duyệt</h2>
            <p className="session-desc-text">
              Không đăng nhập. Chỉ tài nguyên thuộc session hiện tại được phép truy cập.
            </p>
          </div>
        </div>
        <button
          type="button"
          className="btn-refresh-session"
          onClick={() => {
            setError(null)
            setAttempt(attempt + 1)
          }}
        >
          Làm mới danh sách session
        </button>
      </div>

      {error && <p role="alert" className="session-alert-msg">{error}</p>}

      {datasets.length === 0 ? (
        <p className="session-empty-msg">
          Phiên này chưa có dataset. Không tự gán dữ liệu legacy.
        </p>
      ) : (
        <div className="session-datasets-cluster">
          {datasets.map((d) => {
            const isSelected = state?.datasetId === d.dataset_id
            return (
              <button
                key={d.dataset_id}
                type="button"
                className={`btn-session-dataset ${isSelected ? 'active-selection' : ''}`}
                disabled={d.state !== 'ready' || state?.loading}
                onClick={() => {
                  void state?.loadResults(d.dataset_id, '')
                }}
              >
                {d.dataset_id} · {d.state}
              </button>
            )
          })}
        </div>
      )}
    </section>
  )
}
