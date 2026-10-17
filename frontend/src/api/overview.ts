import { z } from 'zod'

// Projections of confirmed summary/results fields, not new endpoint contracts.
// Sources: backend/integrated_api.py and ui/backend_responses.py.
const summarySchema = z.object({
  dataset_id: z.string().min(1), state: z.literal('ready'), ready: z.literal(true),
  images: z.number().int().nonnegative(), annotations: z.boolean(),
})
const resultsSchema = z.object({
  dataset_id: z.string().min(1),
  quality_rows: z.array(z.object({ status: z.enum(['PASS', 'REVIEW', 'ERROR']) })),
  audit_rows: z.array(z.object({ label: z.string(), status: z.string(), priority: z.string() })),
  classes: z.array(z.object({
    class: z.string(), boxes: z.number().int().nonnegative(),
    images: z.number().int().nonnegative(), share_pct: z.number().min(0).max(100),
  })),
})
export function decodeOverview(summary: unknown, results: unknown) {
  const parsedSummary = summarySchema.parse(summary)
  const parsedResults = resultsSchema.parse(results)
  if (parsedSummary.dataset_id !== parsedResults.dataset_id) throw new Error('Dataset identity mismatch')
  if (!parsedSummary.annotations && (parsedResults.audit_rows.length || parsedResults.classes.length)) {
    throw new Error('Annotation data contradicts dataset summary')
  }
  return { summary: parsedSummary, results: parsedResults }
}
export type OverviewData = ReturnType<typeof decodeOverview>
export function overviewTotals(data: OverviewData) {
  return {
    images: data.results.quality_rows.length,
    reviewImages: data.results.quality_rows.filter((row) => row.status === 'REVIEW').length,
    boxes: data.results.audit_rows.length,
    reviewBoxes: data.results.audit_rows.filter((row) => row.status.startsWith('REVIEW')).length,
    classes: new Set(data.results.audit_rows.map((row) => row.label)).size,
  }
}
