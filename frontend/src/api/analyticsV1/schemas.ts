import { z } from 'zod'
import { decodeDatasetSummary } from '../datasets'

export const intermediaryOrigin = 'http://127.0.0.1:8012'
export const grantLifetimeMs = 10 * 60 * 1000
const id = z.string().min(1)
const utc = z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/).refine((value) => {
  const time = Date.parse(value)
  return Number.isFinite(time) && new Date(time).toISOString().replace('.000Z', 'Z') === value
})
export const grantSchema = z.object({
  schema_version: z.literal('analytics.v1'), access_token: z.string().min(1).regex(/^[\x21-\x7e]+$/),
  token_type: z.literal('Bearer'), server_time: utc, expires_at: utc,
  dataset_id: id, result_ids: z.array(id), scopes: z.array(z.enum(['summary:read', 'results:read'])).min(1),
}).superRefine((grant, context) => {
  if (!grant.scopes.includes('summary:read') || (!grant.scopes.includes('results:read') && grant.result_ids.length !== 0)) {
    context.addIssue({ code: 'custom', message: 'Invalid scope intersection' })
  }
  const lifetime = Date.parse(grant.expires_at) - Date.parse(grant.server_time)
  if (lifetime !== grantLifetimeMs) context.addIssue({ code: 'custom', message: 'Invalid absolute lifetime' })
})
export type MemoryGrant = z.infer<typeof grantSchema>

const summaryMetadata = z.object({ schema_version: z.literal('analytics.v1'), error_code: z.string().nullable() })
export function decodeSummary(value: unknown) {
  const metadata = summaryMetadata.parse(value)
  const summary = decodeDatasetSummary(value)
  // Counts are not covered by A2 configuration encoding. Until the complete field
  // inventory is approved, reject values this frontend cannot represent exactly.
  if (summary.ready && ![summary.archive_bytes, summary.entries, summary.images, ...Object.values(summary.xml_records ?? {})].every(Number.isSafeInteger)) {
    throw Error('Unsupported integer range')
  }
  return { ...summary, ...metadata }
}
export type AnalyticsSummary = ReturnType<typeof decodeSummary>
