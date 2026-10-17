import { useState } from 'react'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { browserExport } from '../api/browserSession'
import type { DashboardPage, DashboardQuery } from '../api/dashboard'
export function BrowserExportButton({ tableKey, page, query, label }: { tableKey: string; page: DashboardPage; query: DashboardQuery; label: string }) {
  const state = useVerifiedResults()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const download = async () => {
    const request = state?.revision
    if (!state?.datasetId || !state.activeResult?.payload.request_fingerprint) return
    setBusy(true); setError(null)
    try {
      const blob = await browserExport(state.datasetId, { page, query, table_key: tableKey, expected_fingerprint: state.activeResult.payload.request_fingerprint })
      if (!state.isCurrentRequest(request!)) return
      const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'dataset-export.csv'; a.click(); URL.revokeObjectURL(url)
    } catch { setError('Không export được kết quả được cấp quyền.') }
    finally { setBusy(false) }
  }
  return <><button disabled={busy} onClick={() => { void download() }}>{label}</button>{error && <p role="alert">{error}</p>}</>
}
