import { readDashboard as legacyDashboard } from '../api/dashboard'
import { createContext, useContext, useState, useCallback, useMemo, useRef, useEffect, type ReactNode } from 'react'
import {
  createVerifiedResultsTransport,
  type Descriptor,
  type ActiveResult,
  decodeVerifiedResultDetail,
  payloadSchema,
  VerifiedResultError,
  type VerifiedResultsTransport,
} from '../api/verifiedResults'
import { decodeDatasetSummary, type DatasetSummary } from '../api/datasets'
import { config } from '../config'

export interface VerifiedResultsState {
  readDashboard?: typeof legacyDashboard
  revision: number
  activeResult: ActiveResult | null
  activeSummary: DatasetSummary | null
  availableResults: Descriptor[]
  loading: boolean
  error: string | null
  datasetId: string | null
  token: string | null
  loadResults: (datasetId: string, token: string, signal?: AbortSignal, summary?: DatasetSummary) => Promise<void>
  selectResult: (resultId: string, datasetId: string, token: string, signal?: AbortSignal) => Promise<void>
  fetchPreview: (imagePath: string, annotated?: boolean, signal?: AbortSignal) => Promise<Blob>
  beginRequest: (datasetId?: string | null) => number
  isCurrentRequest: (request: number) => boolean
  failRequest: (request: number, message: string) => void
  setSummary: (summary: DatasetSummary | null, request?: number) => void
  clearResults: () => void
  setManualResult: (result: ActiveResult | null, summary?: DatasetSummary | null, token?: string | null, request?: number) => void
}

export const VerifiedResultsContext = createContext<VerifiedResultsState | null>(null)

export function VerifiedResultsProvider({
  children,
  transport: customTransport,
}: {
  children: ReactNode
  transport?: VerifiedResultsTransport
}) {
  const [snapshot, setSnapshot] = useState<{
    revision: number; activeResult: ActiveResult | null; activeSummary: DatasetSummary | null;
    availableResults: Descriptor[]; loading: boolean; error: string | null;
    datasetId: string | null; token: string | null;
  }>({ revision: 0, activeResult: null, activeSummary: null, availableResults: [],
    loading: false, error: null, datasetId: null, token: null })
  const sequence = useRef(0)
  const requestedDataset = useRef<string | null>(null)
  const inFlight = useRef<AbortController | null>(null)
  const transport = useMemo(
    () => customTransport ?? createVerifiedResultsTransport(config.analyticsBaseUrl), [customTransport],
  )
  const isCurrentRequest = useCallback((request: number) => sequence.current === request, [])
  const beginRequest = useCallback((datasetId: string | null = null) => {
    inFlight.current?.abort()
    inFlight.current = null
    const revision = ++sequence.current
    requestedDataset.current = datasetId
    setSnapshot({ revision, datasetId, activeResult: null, activeSummary: null,
      availableResults: [], token: null, loading: true, error: null })
    return revision
  }, [])
  const clearResults = useCallback(() => {
    const revision = beginRequest()
    setSnapshot((s) => ({ ...s, revision, loading: false }))
  }, [beginRequest])
  useEffect(() => () => { sequence.current++; inFlight.current?.abort() }, [])

  const failRequest = useCallback((request: number, message: string) => {
    if (!isCurrentRequest(request)) return
    inFlight.current?.abort()
    inFlight.current = null
    const revision = ++sequence.current
    setSnapshot((s) => ({ ...s, revision, activeResult: null, activeSummary: null,
      availableResults: [], token: null, loading: false, error: message }))
  }, [isCurrentRequest])

  const setManualResult = useCallback((result: ActiveResult | null, summary?: DatasetSummary | null,
    manualToken?: string | null, expectedRequest?: number) => {
    if (expectedRequest !== undefined && !isCurrentRequest(expectedRequest)) return
    const request = expectedRequest ?? beginRequest(result?.payload.dataset_id)
    if (!result) { failRequest(request, 'Chưa có kết quả phân tích.'); return }
    try {
      const payload = payloadSchema.parse(result.payload)
      if (requestedDataset.current && requestedDataset.current !== payload.dataset_id) throw new Error('DATASET_MISMATCH')
      const detail: ActiveResult = result.descriptor
        ? decodeVerifiedResultDetail(result, payload.dataset_id)
        : { payload, descriptor: null, provenance: null }
      const checkedSummary = summary ? decodeDatasetSummary(summary) : null
      if (checkedSummary && (!checkedSummary.ready || checkedSummary.dataset_id !== payload.dataset_id)) {
        throw new Error('DATASET_MISMATCH')
      }
      if (checkedSummary?.ready && detail.descriptor && checkedSummary.archive_sha256 !== detail.descriptor.archive_sha256) throw new Error('ARCHIVE_MISMATCH')
      setSnapshot({ revision: request, activeResult: detail, activeSummary: checkedSummary,
        datasetId: payload.dataset_id, token: manualToken ?? null, loading: false, error: null,
        availableResults: detail.descriptor ? [detail.descriptor] : [] })
    } catch { failRequest(request, 'Kết quả hoặc summary không hợp lệ / sai dataset.') }
  }, [beginRequest, failRequest, isCurrentRequest])

  const setSummary = useCallback((summary: DatasetSummary | null, expectedRequest?: number) => {
    if (expectedRequest !== undefined && !isCurrentRequest(expectedRequest)) return
    if (!summary) { clearResults(); return }
    try {
      const checked = decodeDatasetSummary(summary)
      if (expectedRequest !== undefined && requestedDataset.current && requestedDataset.current !== checked.dataset_id) throw new Error('DATASET_MISMATCH')
      const request = expectedRequest ?? beginRequest(checked.dataset_id)
      setSnapshot((s) => ({ ...s, revision: request, datasetId: checked.dataset_id,
        activeResult: null, availableResults: [], token: null, activeSummary: checked, loading: false, error: null }))
    } catch {
      failRequest(expectedRequest ?? beginRequest(), 'Summary không hợp lệ.')
    }
  }, [beginRequest, clearResults, failRequest, isCurrentRequest])

  const read = useCallback(async (targetDatasetId: string, authToken: string,
    signal?: AbortSignal, resultId?: string, summary?: DatasetSummary | null, descriptors?: Descriptor[]) => {
    const request = beginRequest(targetDatasetId)
    const controller = new AbortController()
    inFlight.current = controller
    const abort = () => {
      controller.abort()
      failRequest(request, 'Yêu cầu đã bị hủy. Không hiển thị kết quả cũ.')
    }
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) { abort(); signal.removeEventListener('abort', abort); return }
    const current = () => isCurrentRequest(request) && !controller.signal.aborted
    try {
      const checkedSummary = summary ? decodeDatasetSummary(summary) : null
      if (checkedSummary && (!checkedSummary.ready || checkedSummary.dataset_id !== targetDatasetId)) throw new Error('DATASET_MISMATCH')
      const list = resultId ? null : await transport.listResults(targetDatasetId, authToken, controller.signal)
      if (!current()) return
      if (list && (list.dataset_id !== targetDatasetId || list.results.some((r) => r.dataset_id !== targetDatasetId))) {
        throw new Error('DATASET_MISMATCH')
      }
      const selectedId = resultId ?? list?.results[0]?.result_id
      const detail = selectedId
        ? decodeVerifiedResultDetail(await transport.readResult(targetDatasetId, selectedId, authToken, controller.signal), targetDatasetId, selectedId)
        : null
      if (!current()) return
      if (checkedSummary?.ready && detail && checkedSummary.archive_sha256 !== detail.descriptor.archive_sha256) throw new Error('ARCHIVE_MISMATCH')
      setSnapshot((s) => ({ ...s, activeResult: detail, availableResults: list?.results ?? (descriptors?.length ? descriptors : (detail ? [detail.descriptor] : [])),
        token: authToken, activeSummary: checkedSummary, loading: false, error: null }))
    } catch {
      if (isCurrentRequest(request)) failRequest(request, controller.signal.aborted
        ? 'Yêu cầu đã bị hủy. Không hiển thị kết quả cũ.' : 'Không tải được kết quả API hoặc dataset không khớp.')
    } finally {
      signal?.removeEventListener('abort', abort)
      if (isCurrentRequest(request)) {
        inFlight.current = null
        if (controller.signal.aborted) failRequest(request, 'Yêu cầu đã bị hủy.')
      }
    }
  }, [beginRequest, failRequest, isCurrentRequest, transport])
  const loadResults = useCallback((datasetId: string, token: string, signal?: AbortSignal, summary?: DatasetSummary) =>
    read(datasetId, token, signal, undefined, summary), [read])
  const selectResult = useCallback((resultId: string, datasetId: string, token: string, signal?: AbortSignal) =>
    read(datasetId, token, signal, resultId, snapshot.activeSummary?.dataset_id === datasetId ? snapshot.activeSummary : null, snapshot.datasetId === datasetId ? snapshot.availableResults : undefined), [read, snapshot.activeSummary, snapshot.datasetId, snapshot.availableResults])

  const fetchPreview = useCallback(async (imagePath: string, annotated = false, signal?: AbortSignal) => {
    const { activeResult, datasetId, token, revision } = snapshot
    if (!activeResult || !token || !datasetId || activeResult.payload.dataset_id !== datasetId || !isCurrentRequest(revision)) {
      throw new Error('No authorized dataset result available')
    }
    let blob: Blob
    if (activeResult.descriptor) {
      blob = await transport.fetchPreview(datasetId, imagePath, annotated, token, signal)
    } else {
      const url = config.baseUrl.replace(/\/$/, '') + '/api/v1/datasets/' + encodeURIComponent(datasetId)
        + '/preview?image_path=' + encodeURIComponent(imagePath) + '&annotated=' + String(annotated)
      const response = await fetch(url, { headers: { 'Upload-Capability': token, Accept: 'image/*' },
        ...(signal ? { signal } : {}), cache: 'no-store', credentials: 'omit', redirect: 'error' })
      if (!response.ok) throw new VerifiedResultError('http', response.status)
      blob = await response.blob()
    }
    if (!isCurrentRequest(revision) || signal?.aborted) throw new VerifiedResultError('cancelled')
    return blob
  }, [snapshot, isCurrentRequest, transport])

  const value = useMemo<VerifiedResultsState>(() => ({ readDashboard: legacyDashboard, ...snapshot, beginRequest, isCurrentRequest,
    failRequest, loadResults, selectResult, fetchPreview, setSummary, clearResults, setManualResult }),
  [snapshot, beginRequest, isCurrentRequest, failRequest, loadResults, selectResult, fetchPreview,
    setSummary, clearResults, setManualResult])

  return <VerifiedResultsContext.Provider value={value}>{children}</VerifiedResultsContext.Provider>
}

export function useVerifiedResults() {
  return useContext(VerifiedResultsContext)
}
