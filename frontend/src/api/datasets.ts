import { z } from 'zod'
// Existing integrated_api.py / DatasetManager.status fields, not proposed APIs.
const id = z.string().min(1)
const count = z.number().int().nonnegative()
const errorCode = z.string().nullable().optional()
const activeState = z.enum(['validating', 'indexing', 'publishing', 'failed', 'cancelled'])
export const lifecycleSchema = z.discriminatedUnion('state', [
  z.object({ upload_id: id, dataset_id: id, state: z.literal('ready'), ready: z.literal(true), error_code: errorCode }),
  z.object({ upload_id: id, dataset_id: id, state: activeState, ready: z.literal(false), error_code: errorCode }),
  z.object({ upload_id: id, state: z.literal('not_requested'), ready: z.literal(false), error_code: errorCode }),
])
const readySummary = z.object({
  upload_id: id, dataset_id: id, state: z.literal('ready'), ready: z.literal(true),
  archive_bytes: count, archive_sha256: z.string().regex(/^[0-9a-f]{64}$/), entries: count, images: count,
  annotations: z.boolean(), xml_records: z.record(z.string(), count).optional(),
  capabilities: z.object({ image_quality: z.boolean(), label_audit: z.boolean(), distribution: z.boolean(), coverage: z.boolean(), scene_finder: z.boolean() }),
})
const pendingSummary = z.object({ upload_id: id, dataset_id: id, state: activeState, ready: z.literal(false), error_code: errorCode })
export const datasetSummarySchema = z.discriminatedUnion('ready', [readySummary, pendingSummary])
export function decodeLifecycle(value: unknown) { return lifecycleSchema.parse(value) }
export function decodeDatasetSummary(value: unknown) { return datasetSummarySchema.parse(value) }
export type DatasetSummary = ReturnType<typeof decodeDatasetSummary>
