import { z } from 'zod'
// Confirmed producer: core/image_quality.py; JSON serialization: integrated_api.py.
const metric = z.number().finite().nullable()
const qualityRow = z.object({
  filename: z.string().min(1), status: z.enum(['PASS', 'REVIEW', 'ERROR']),
  score: z.number().min(0).max(100), issues: z.array(z.string()),
  width: z.number().int().nonnegative(), height: z.number().int().nonnegative(), size_kb: z.number().nonnegative(),
  blur_score: metric, brightness: metric, contrast: metric, noise_score: metric,
  black_pct: metric, white_pct: metric, error: z.string(),
})
const resultsProjection = z.object({ dataset_id: z.string().min(1), quality_rows: z.array(qualityRow) })
export function decodeQuality(value: unknown) { return resultsProjection.parse(value) }
export type QualityData = ReturnType<typeof decodeQuality>
export type QualityRow = QualityData['quality_rows'][number]
export function qualitySummary(rows: QualityRow[]) {
  const counts = { PASS: 0, REVIEW: 0, ERROR: 0 }
  const issues = new Map<string, number>()
  let total = 0
  for (const row of rows) {
    counts[row.status]++; total += row.score
    for (const issue of row.issues) issues.set(issue, (issues.get(issue) ?? 0) + 1)
  }
  return { counts, mean: rows.length ? total / rows.length : null, issues: [...issues] }
}
