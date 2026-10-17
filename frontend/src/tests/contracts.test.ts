import { describe, expect, it, vi } from 'vitest'
import { decodeLifecycle, decodeDatasetSummary } from '../api/datasets'
import { normalizeApiFailure } from '../api/errors'
import { createApiClient } from '../api/client'
import { createAnalyticsAdapter, verifyAnalysis, ContractPendingError, IntegrityError, DatasetNotReadyError } from '../api/analytics'
import { decodeQuality } from '../api/quality'
import baseline from '../mocks/overview-baseline.json'

const fingerprint = 'a'.repeat(64)
const selection = { datasetId: 'dataset-a', requestFingerprint: fingerprint }
const summary = {
  dataset_id: 'dataset-a', upload_id: 'upload-a', ready: true, state: 'ready',
  archive_bytes: 100, archive_sha256: 'b'.repeat(64), entries: 3, images: 2, annotations: true,
  capabilities: { image_quality: true, label_audit: true, distribution: true, coverage: true, scene_finder: true },
}
const bundle = { dataset_id: 'dataset-a', request_fingerprint: fingerprint, quality_rows: baseline.quality, audit_rows: baseline.audit, classes: baseline.classes }
function transport(summaryValue: unknown = summary, resultValue: unknown = bundle) {
  return { readSummary: vi.fn().mockResolvedValue(summaryValue), readResults: vi.fn().mockResolvedValue(resultValue) }
}

describe('confirmed dataset lifecycle and summary contracts', () => {
  it('allows absent optional fields without inventing defaults', () => {
    expect(decodeLifecycle({ upload_id: 'upload-a', state: 'not_requested', ready: false })).not.toHaveProperty('dataset_id')
    const parsed = decodeDatasetSummary(summary)
    expect(parsed).not.toHaveProperty('xml_records')
    expect(parsed).not.toHaveProperty('error_code')
  })
  it.each(['validating', 'indexing', 'publishing', 'failed', 'cancelled'])('accepts %s lifecycle state with nullable errors', (state) => {
    expect(decodeLifecycle({ dataset_id: 'dataset-a', upload_id: 'upload-a', state, ready: false, error_code: null }).state).toBe(state)
  })
  it('accepts optional XML counts and strips unknown metadata', () => {
    const parsed = decodeDatasetSummary({ ...summary, xml_records: { nodes: 8 }, extra: 'not consumed' })
    expect(parsed).toHaveProperty('xml_records.nodes', 8)
    expect(parsed).not.toHaveProperty('extra')
  })
  it.each([
    { upload_id: 'u', dataset_id: 'd', state: 'ready', ready: false },
    { upload_id: 'u', state: 'indexing', ready: false },
    { upload_id: 'u', dataset_id: 'd', state: 'done', ready: true },
  ])('rejects invalid states, inconsistent flags or missing identities', (value) => { expect(() => decodeLifecycle(value)).toThrow() })
  it('rejects malformed summaries and preserves non-ready states', () => {
    expect(() => decodeDatasetSummary({ ...summary, images: '2' })).toThrow()
    expect(() => decodeDatasetSummary({ ...summary, capabilities: {} })).toThrow()
    expect(decodeDatasetSummary({ dataset_id: 'dataset-a', upload_id: 'upload-a', ready: false, state: 'failed', error_code: 'VALIDATION_FAILED' }).ready).toBe(false)
  })
})

describe('verified cached analytics adapters, injected transport only', () => {
  it('validates all three page projections and preserves null metrics', async () => {
    const adapter = createAnalyticsAdapter(transport())
    const quality = await adapter.quality(selection)
    expect(quality.verification).toBe('dataset-and-fingerprint-matched')
    expect(quality.data.quality_rows).toEqual(baseline.quality)
    expect(quality.data.quality_rows[1]?.noise_score).toBeNull()
    expect((await adapter.overview(selection)).data.results.classes).toEqual(baseline.classes)
    expect((await adapter.labels(selection)).data.results.audit_rows).toEqual(baseline.audit)
  })
  it('passes cancellation signal to both transport operations', async () => {
    const injected = transport()
    const controller = new AbortController()
    await createAnalyticsAdapter(injected).quality(selection, controller.signal)
    expect(injected.readSummary).toHaveBeenCalledWith('dataset-a', controller.signal)
    expect(injected.readResults).toHaveBeenCalledWith('dataset-a', controller.signal)
  })
  it('rejects wrong summary IDs before requesting results', async () => {
    const injected = transport({ ...summary, dataset_id: 'dataset-b' })
    await expect(createAnalyticsAdapter(injected).quality(selection)).rejects.toBeInstanceOf(IntegrityError)
    expect(injected.readResults).not.toHaveBeenCalled()
  })
  it.each([
    { ...bundle, dataset_id: 'dataset-b' },
    { ...bundle, request_fingerprint: 'c'.repeat(64) },
  ])('rejects a different result dataset or configuration fingerprint', async (value) => {
    await expect(createAnalyticsAdapter(transport(summary, value)).quality(selection)).rejects.toBeInstanceOf(IntegrityError)
  })
  it('does not mark legacy missing fingerprints or malformed rows verified', async () => {
    const { request_fingerprint: _unused, ...legacy } = bundle
    expect(_unused).toBe(fingerprint)
    await expect(createAnalyticsAdapter(transport(summary, legacy)).quality(selection)).rejects.toThrow()
    await expect(createAnalyticsAdapter(transport(summary, { ...bundle, quality_rows: [{ status: 'PASS' }] })).quality(selection)).rejects.toThrow()
  })
  it.each([{ configurationId: 'config-b' }, { resultId: 'result-b' }])('blocks unconfirmed ID contracts instead of treating IDs as fingerprints', async (requirement) => {
    const injected = transport(summary, { ...bundle, configuration_id: 'config-a', result_id: 'result-a' })
    await expect(createAnalyticsAdapter(injected).quality({ ...selection, ...requirement })).rejects.toBeInstanceOf(ContractPendingError)
    expect(injected.readSummary).not.toHaveBeenCalled()
    // Proposed response fields cannot be used to claim ID verification.
  })
  it('rejects malformed expected fingerprints without reading transport', async () => {
    const injected = transport()
    await expect(createAnalyticsAdapter(injected).quality({ ...selection, requestFingerprint: 'configuration-name' })).rejects.toThrow()
    expect(injected.readSummary).not.toHaveBeenCalled()
  })
  it('does not request results until the dataset is ready', async () => {
    const injected = transport({ dataset_id: 'dataset-a', upload_id: 'upload-a', state: 'indexing', ready: false })
    await expect(createAnalyticsAdapter(injected).overview(selection)).rejects.toBeInstanceOf(DatasetNotReadyError)
    expect(injected.readResults).not.toHaveBeenCalled()
  })
  it('does not synthesize data or fallback when results fail', async () => {
    const injected = transport()
    const failure = new Error('transport failure')
    injected.readResults.mockRejectedValue(failure)
    await expect(createAnalyticsAdapter(injected).overview(selection)).rejects.toBe(failure)
  })
  it('preserves empty quality results as empty rather than a successful fixture fallback', async () => {
    const result = await createAnalyticsAdapter(transport(summary, { ...bundle, quality_rows: [] })).quality(selection)
    expect(result.data.quality_rows).toEqual([])
  })
  it('snapshots expected identity before awaiting the transport', async () => {
    const current = { ...selection }
    const pending = createAnalyticsAdapter(transport()).quality(current)
    current.datasetId = 'dataset-b'; current.requestFingerprint = 'c'.repeat(64)
    expect((await pending).selection).toEqual(selection)
  })
  it('does not ignore mismatched expectation in the pure verification boundary', () => {
    expect(() => verifyAnalysis(bundle, { ...selection, datasetId: 'other' }, decodeQuality)).toThrow(IntegrityError)
  })
})

describe('sanitized API error metadata', () => {
  it('recognizes application errors without retaining raw contents', () => {
    expect(normalizeApiFailure({ error: { code: 'ANALYSIS_NOT_RUN', message: 'private path' } })).toEqual({ format: 'application', code: 'ANALYSIS_NOT_RUN' })
    expect(normalizeApiFailure({ error: { code: 'private path' } })).toEqual({ format: 'application' })
    expect(normalizeApiFailure({ detail: [{ msg: 'private', type: 'missing', input: 'secret' }] })).toEqual({ format: 'validation' })
    expect(normalizeApiFailure({ error: 'bad shape' })).toEqual({ format: 'unknown' })
  })
  it('attaches safe code/format to HTTP errors without leaking body text', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('{"error":{"code":"ANALYSIS_NOT_RUN","message":"private path"}}', { status: 409 }))
    await expect(createApiClient('http://localhost', fetcher).get('/health', (value) => value)).rejects.toMatchObject({ kind: 'http', status: 409, code: 'ANALYSIS_NOT_RUN', format: 'application', message: 'Backend request failed (HTTP 409).' })
  })
  it('keeps malformed HTTP-error bodies classified as HTTP errors', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('not json', { status: 500 }))
    await expect(createApiClient('http://localhost', fetcher).get('/health', (value) => value)).rejects.toMatchObject({ kind: 'http', status: 500, code: undefined, format: 'unknown' })
  })
})
