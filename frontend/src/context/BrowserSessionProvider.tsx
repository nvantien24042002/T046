import { readBrowserDashboard } from '../api/browserDashboard'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { VerifiedResultsContext, type VerifiedResultsState } from './VerifiedResultsContext'
import { browserJson, browserPreview, loadBrowserResult } from '../api/browserSession'
import { decodeDatasetSummary } from '../api/datasets'
import { payloadSchema } from '../api/verifiedResults'

export function BrowserSessionProvider({ children }: { children: ReactNode }) {
  const generation = useRef(0)
  const flight = useRef<AbortController | null>(null)
  type Snapshot = Pick<VerifiedResultsState, 'revision' | 'activeResult' | 'activeSummary' | 'datasetId' | 'loading' | 'error'>
  const [state, setState] = useState<Snapshot>({ revision: 0, activeResult: null, activeSummary: null, datasetId: null, loading: false, error: null })
  useEffect(() => () => { generation.current++; flight.current?.abort() }, [])
  const beginRequest = useCallback((datasetId: string | null = null) => {
    flight.current?.abort()
    const revision = ++generation.current
    setState({ revision, datasetId, activeResult: null, activeSummary: null, loading: true, error: null })
    return revision
  }, [])
  const isCurrentRequest = useCallback((request: number) => request === generation.current, [])
  const failRequest = useCallback((request: number, error: string) => {
    if (request !== generation.current) return
    flight.current?.abort()
    const revision = ++generation.current
    setState((s) => ({ ...s, revision, activeResult: null, activeSummary: null, loading: false, error }))
  }, [])
  const setManualResult: VerifiedResultsState['setManualResult'] = (result, summary, _unused, request) => {
    const revision = request ?? beginRequest(result?.payload.dataset_id)
    if (!isCurrentRequest(revision)) return
    try {
      if (!result || result.descriptor) throw new Error('Immutable provider chưa được cấu hình')
      const payload = payloadSchema.parse(result.payload)
      const checked = summary ? decodeDatasetSummary(summary) : null
      if (!checked?.ready || checked.dataset_id !== payload.dataset_id || !payload.request_fingerprint) throw new Error('Identity mismatch')
      setState({ revision, datasetId: payload.dataset_id, activeResult: { payload, descriptor: null, provenance: null }, activeSummary: checked, loading: false, error: null })
    } catch { failRequest(revision, 'Dataset/result identity không hợp lệ.') }
  }
  const loadResults: VerifiedResultsState['loadResults'] = async (datasetId, _unused, signal) => {
    const request = beginRequest(datasetId)
    const controller = new AbortController(); flight.current = controller
    const abort = () => controller.abort(); signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) controller.abort()
    try {
      const summary = decodeDatasetSummary(await browserJson(`/datasets/${encodeURIComponent(datasetId)}/summary`, { signal: controller.signal }))
      const result = await loadBrowserResult(datasetId, controller.signal)
      if (!controller.signal.aborted && request === generation.current) setManualResult(result, summary, undefined, request)
    } catch { failRequest(request, 'Không tải được kết quả session hoặc phiên đã hết hạn.') }
    finally { signal?.removeEventListener('abort', abort) }
  }
  return <VerifiedResultsContext.Provider value={{ readDashboard: readBrowserDashboard, ...state, token: null, availableResults: [],
    beginRequest, isCurrentRequest, failRequest, setManualResult, loadResults,
    selectResult: async () => { throw new Error('Immutable result selection chưa có provider được duyệt.') },
    setSummary: (summary, request) => { const revision = request ?? beginRequest(summary?.dataset_id); if (isCurrentRequest(revision)) setState((s) => ({ ...s, revision, activeSummary: summary, loading: false })) },
    clearResults: () => { const revision = beginRequest(); setState({ revision, datasetId: null, activeResult: null, activeSummary: null, loading: false, error: null }) },
    fetchPreview: async (path, annotated = false, signal) => {
      const request = generation.current
      if (!state.datasetId || !state.activeResult) throw new Error('No selected result')
      const blob = await browserPreview(state.datasetId, path, annotated, signal)
      if (request !== generation.current || signal?.aborted) throw new Error('Selection changed')
      return blob
    },
  }}>{children}</VerifiedResultsContext.Provider>
}
