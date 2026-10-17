import { useEffect, useRef, useState, type FormEvent } from 'react'
import { createAnalyticsV1Session, TrackAError, type TrackAState } from '../api/analyticsV1'
import { config } from '../config'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { ErrorState, LoadingState } from './States'

const initialState: TrackAState = { status: 'signed-out', selection: null, summary: null, error: null }
type Session = ReturnType<typeof createAnalyticsV1Session>

export function AnalyticsSummaryPanel() {
  const session = useRef<Session | null>(null)
  const attempt = useRef(0)
  const [state, setState] = useState(initialState)
  const [code, setCode] = useState('')
  const [datasetId, setDatasetId] = useState('')
  const [configurationError, setConfigurationError] = useState<string | null>(null)
  const verifiedResults = useVerifiedResults()

  const resultsRef = useRef(verifiedResults)
  useEffect(() => { resultsRef.current = verifiedResults }, [verifiedResults])

  useEffect(() => {
    let current: Session
    let mounted = true
    try { current = createAnalyticsV1Session({ origin: config.analyticsBaseUrl }) }
    catch (error) {
      queueMicrotask(() => { if (mounted) setConfigurationError(error instanceof TrackAError ? error.message : 'Intermediary configuration is invalid.') })
      return () => { mounted = false }
    }
    session.current = current
    const unsubscribe = current.subscribe((next) => {
      setState(next)
      if (next.status === 'signed-out') {
        setCode('')
        setDatasetId('')
        const results = resultsRef.current
        if (results?.activeResult?.descriptor || (!results?.activeResult && (results?.activeSummary || (results?.loading && results?.datasetId)))) {
          results?.clearResults()
        }
      }
    })

    return () => { mounted = false; unsubscribe(); current.dispose(); session.current = null }
  }, [])

  async function exchange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const current = session.current
    if (!current) return
    const request = ++attempt.current
    const contextRequest = verifiedResults?.beginRequest(datasetId)
    const expectedId = datasetId
    const exchangeCode = code
    setCode('')
    try {
      await current.exchange(exchangeCode, expectedId)
      if (attempt.current === request && session.current === current) {
        const summary = await current.readSummary(expectedId)
        if (attempt.current !== request || session.current !== current || (contextRequest !== undefined && !verifiedResults?.isCurrentRequest(contextRequest))) return
        const grant = current.getGrant()
        if (grant && grant.scopes.includes('results:read') && summary.ready) {
          await verifiedResults?.loadResults(expectedId, grant.access_token, undefined, summary)
        } else {
          verifiedResults?.setSummary(summary, contextRequest)
        }
      }
    } catch { if (contextRequest !== undefined) verifiedResults?.failRequest(contextRequest, 'Không tải được summary API cho dataset đã chọn.') }
  }

  async function refresh() {
    const current = session.current
    const selected = current?.getState().selection
    if (!current || !selected) return
    const request = ++attempt.current
    const contextRequest = verifiedResults?.beginRequest(selected.datasetId)
    try {
      const summary = await current.readSummary(selected.datasetId)
      if (attempt.current !== request || session.current !== current || (contextRequest !== undefined && !verifiedResults?.isCurrentRequest(contextRequest))) return
      const grant = current.getGrant()
      if (grant && grant.scopes.includes('results:read') && summary.ready) {
        await verifiedResults?.loadResults(selected.datasetId, grant.access_token, undefined, summary)
      } else {
        verifiedResults?.setSummary(summary, contextRequest)
      }
    } catch { if (contextRequest !== undefined) verifiedResults?.failRequest(contextRequest, 'Không tải được summary API cho dataset đã chọn.') }
  }

  const busy = state.status === 'exchanging' || state.status === 'loading-summary' || (verifiedResults?.loading ?? false)
  const summary = verifiedResults ? verifiedResults.activeSummary : state.summary
  const displayedDatasetId = verifiedResults ? verifiedResults.datasetId : state.selection?.datasetId
  const activeResult = verifiedResults?.activeResult
  const availableResults = verifiedResults?.availableResults ?? []

  return <section className="analytics-handoff" aria-labelledby="analytics-summary-title">
    <h2 id="analytics-summary-title">Authorized dataset summary & Verified Results (Track B)</h2>
    <p id="handoff-help">Enter a code and expected dataset ID received through the approved trusted handoff. This is not a code issuance or login flow. Codes are single use; an ambiguous exchange failure requires a new trusted handoff.</p>
    <form onSubmit={(event) => { void exchange(event) }} aria-describedby="handoff-help" autoComplete="off">
      <label htmlFor="handoff-dataset">Expected dataset ID<input id="handoff-dataset" value={datasetId} onChange={(event) => setDatasetId(event.target.value)} required autoComplete="off" /></label>
      <label htmlFor="handoff-code">Exchange code<input id="handoff-code" type="password" value={code} onChange={(event) => setCode(event.target.value)} required autoComplete="off" spellCheck={false} /></label>
      <button disabled={!!configurationError || !code || !datasetId} type="submit">Exchange and read summary</button>
    </form>
    <div className="handoff-actions">
      <button type="button" disabled={!state.selection || busy} onClick={() => { void refresh() }}>Retry / refresh summary</button>
      <button type="button" onClick={() => { attempt.current++; setCode(''); setDatasetId(''); session.current?.invalidate(); verifiedResults?.clearResults() }}>Clear access</button>
    </div>
    {configurationError && <ErrorState message={configurationError} />}
    {state.error && <ErrorState message={state.error.message} />}
    {verifiedResults?.error && <ErrorState message={verifiedResults.error} />}
    {busy && <LoadingState />}
    {!busy && !state.error && !summary && <p role="status">No authorized summary loaded. No fixture data is used in this flow.</p>}
    {displayedDatasetId && <p>Authorized dataset: <strong>{displayedDatasetId}</strong></p>}
    {summary && <div aria-live="polite">
      <p>Lifecycle: <strong>{summary.state}</strong></p>
      {summary.ready ? <dl className="overview-metrics">
        <div><dt>Images</dt><dd>{summary.images}</dd></div>
        <div><dt>Archive entries</dt><dd>{summary.entries}</dd></div>
        <div><dt>Archive bytes</dt><dd>{summary.archive_bytes}</dd></div>
        <div><dt>Annotations available</dt><dd>{summary.annotations ? 'Yes' : 'No'}</dd></div>
      </dl> : <p>Summary metrics are unavailable in this lifecycle state. Refresh explicitly to check again.</p>}
    </div>}
    {availableResults.length > 0 && <div className="verified-results-picker">
      <h3>Verified Results (Track B)</h3>
      <label htmlFor="result-select">
        Select verified result:{' '}
        <select
          id="result-select"
          value={activeResult?.descriptor?.result_id ?? ''}
          onChange={(e) => {
            const token = session.current?.getGrant()?.access_token
            if (token && state.selection) {
              void verifiedResults?.selectResult(e.target.value, state.selection.datasetId, token)
            }
          }}
        >
          {availableResults.map((r) => (
            <option key={r.result_id} value={r.result_id}>
              {r.result_id} (Config: {r.config_id.slice(0, 8)}... - Build: {r.producer_build_id})
            </option>
          ))}
        </select>
      </label>
      {activeResult?.provenance && <p className="provenance-info">
        <strong>Metadata xác thực do backend cung cấp:</strong> {activeResult.provenance.verification} |{' '}
        <strong>Config ID:</strong> <code>{activeResult.provenance.config_id.slice(0, 16)}...</code> |{' '}
        <strong>Payload SHA:</strong> <code>{activeResult.provenance.payload_sha256.slice(0, 16)}...</code>
      </p>}
    </div>}
    {summary && availableResults.length === 0 && !busy && (
      <p>No verified results published yet for this dataset.</p>
    )}
  </section>
}
