import { z } from 'zod'
import { decodeDatasetSummary } from './datasets'
import { decodeOverview } from './overview'
import { decodeQuality } from './quality'
import { decodeLabels } from './labels'

export interface AnalysisSelection {
  datasetId: string
  requestFingerprint: string
  // Frontend verification requirements only; no corresponding wire fields assumed.
  configurationId?: string
  resultId?: string
}
export class ContractPendingError extends Error {
  constructor() { super('Separate configuration/result ID verification requires an approved contract.'); this.name = 'ContractPendingError' }
}
export class IntegrityError extends Error {
  constructor() { super('Response does not match the selected dataset or configuration.'); this.name = 'IntegrityError' }
}
export class DatasetNotReadyError extends Error {
  constructor(public readonly state: string) { super('Dataset is not ready for cached analytics.'); this.name = 'DatasetNotReadyError' }
}
const identitySchema = z.object({ dataset_id: z.string().min(1), request_fingerprint: z.string().regex(/^[0-9a-f]{64}$/) })
function validateSelection(selection: AnalysisSelection) {
  if (selection.configurationId !== undefined || selection.resultId !== undefined) throw new ContractPendingError()
  identitySchema.parse({ dataset_id: selection.datasetId, request_fingerprint: selection.requestFingerprint })
}
export function verifyAnalysis<T>(value: unknown, selection: AnalysisSelection, decode: (value: unknown) => T) {
  // Expected fingerprint must come from an independently trusted selection, never
  // copied out of the response being verified. Canonical mapping awaits BE approval.
  const expected = { ...selection }
  validateSelection(expected)
  const identity = identitySchema.parse(value)
  if (identity.dataset_id !== expected.datasetId || identity.request_fingerprint !== expected.requestFingerprint) throw new IntegrityError()
  const data = decode(value)
  // This verifies identity/shape only, not accuracy of analytics or server trust.
  return { verification: 'dataset-and-fingerprint-matched' as const, selection: expected, data }
}
// Injectable raw-response boundary. No endpoint names, credentials or live transport.
export interface AnalyticsTransport {
  readSummary(datasetId: string, signal?: AbortSignal): Promise<unknown>
  readResults(datasetId: string, signal?: AbortSignal): Promise<unknown>
}
export function createAnalyticsAdapter(transport: AnalyticsTransport) {
  async function read(selection: AnalysisSelection, signal?: AbortSignal) {
    // Freeze expectations before any await; external mutation cannot change identity.
    const expected = { ...selection }
    validateSelection(expected)
    const summary = decodeDatasetSummary(await transport.readSummary(expected.datasetId, signal))
    if (summary.dataset_id !== expected.datasetId) throw new IntegrityError()
    if (!summary.ready) throw new DatasetNotReadyError(summary.state)
    const results = await transport.readResults(expected.datasetId, signal)
    return { expected, summary, results }
  }
  return {
    async overview(selection: AnalysisSelection, signal?: AbortSignal) {
      const { expected, summary, results } = await read(selection, signal)
      return verifyAnalysis(results, expected, (value) => decodeOverview(summary, value))
    },
    async quality(selection: AnalysisSelection, signal?: AbortSignal) {
      const { expected, summary, results } = await read(selection, signal)
      if (!summary.capabilities.image_quality) throw new Error('Image Quality is unavailable for this dataset.')
      return verifyAnalysis(results, expected, decodeQuality)
    },
    async labels(selection: AnalysisSelection, signal?: AbortSignal) {
      const { expected, summary, results } = await read(selection, signal)
      return verifyAnalysis(results, expected, (value) => decodeLabels(summary, value))
    },
  }
}
