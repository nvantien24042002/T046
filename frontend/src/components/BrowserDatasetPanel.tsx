import { useEffect, useState } from 'react'
import { z } from 'zod'
import { browserJson } from '../api/browserSession'
import { publicText } from './presentation'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
const catalog = z.object({ datasets: z.array(z.object({ dataset_id: z.string(), state: z.string() })) })
export function BrowserDatasetPanel() {
  const state = useVerifiedResults()
  const [datasets, setDatasets] = useState<z.infer<typeof catalog>['datasets']>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    browserJson('/datasets', { signal: controller.signal }).then((d) => {
      if (!controller.signal.aborted) { setDatasets(catalog.parse(d).datasets); setLoading(false) }
    }).catch(() => { if (!controller.signal.aborted) { setError('Không đọc được session catalog.'); setDatasets([]); setLoading(false) } })
    return () => controller.abort()
  }, [attempt, state?.revision, state?.datasetId])
  return <section className="dataset-panel" aria-label="Anonymous session datasets"><h2>Dataset của phiên trình duyệt</h2>
    <p>Không đăng nhập. Chỉ tài nguyên thuộc session hiện tại được phép truy cập.</p>
    <button onClick={() => { setError(null); setLoading(true); setAttempt(attempt + 1) }}>Làm mới danh sách session</button>
    {loading && <p role="status">Đang tải danh sách bộ dữ liệu…</p>}
    {error && <p role="alert">{error}</p>}
    {!loading && !error && datasets.length === 0 && <p>Phiên này chưa có dataset. Không tự gán dữ liệu legacy.</p>}
    {datasets.map((d, index) => <button key={d.dataset_id} aria-pressed={state?.datasetId === d.dataset_id} disabled={d.state !== 'ready' || state?.loading}
      onClick={() => { void state?.loadResults(d.dataset_id, '') }}>Bộ dữ liệu {index + 1} · {publicText(d.state)}</button>)}
  </section>
}
