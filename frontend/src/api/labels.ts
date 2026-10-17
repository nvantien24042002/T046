import { z } from 'zod'
// Confirmed producer fields and enums: core/label_audit.py, core/cvat_parser.py.
const metric = z.number().finite().nullable()
export const labelPriorities = ['HIGH', 'MEDIUM', 'LOW'] as const
export const labelStatuses = ['REVIEW', 'REVIEW_A_ONLY', 'NO_SIGNAL', 'A_ONLY'] as const
const auditRow = z.object({
  image_id: z.string(), image_name: z.string(), box_id: z.string(), label: z.string(), occluded: z.string(),
  xtl: metric, ytl: metric, xbr: metric, ybr: metric,
  image_available: z.boolean(), image_error: z.string().nullable(),
  area_pct: metric, aspect_ratio: metric, edge_density: metric, texture_variance: metric,
  laplacian_variance: metric, foreground_contrast: metric, boundary_support: metric,
  geometry_issues: z.string(), visual_issues: z.string(), issues: z.string(), issue_count: z.number().int().nonnegative(),
  priority: z.enum(labelPriorities), status: z.enum(labelStatuses),
})
const summaryProjection = z.object({ dataset_id: z.string().min(1), annotations: z.boolean() })
const resultsProjection = z.object({ dataset_id: z.string().min(1), audit_rows: z.array(auditRow) })
export function decodeLabels(summary: unknown, results: unknown) {
  const parsedSummary = summaryProjection.parse(summary)
  const parsedResults = resultsProjection.parse(results)
  if (parsedSummary.dataset_id !== parsedResults.dataset_id) throw Error('Dataset mismatch')
  if (!parsedSummary.annotations && parsedResults.audit_rows.length) throw Error('Annotation contradiction')
  return { summary: parsedSummary, results: parsedResults }
}
export type LabelData = ReturnType<typeof decodeLabels>
export type AuditRow = LabelData['results']['audit_rows'][number]
export interface LabelFilters { classes: string[]; priorities: string[]; onlyReview: boolean }
export function filterLabels(rows: AuditRow[], filters: LabelFilters) {
  return rows.filter((row) => filters.classes.includes(row.label) && filters.priorities.includes(row.priority)
    && (!filters.onlyReview || row.status.startsWith('REVIEW')))
}
// Match pandas round(1): scale, round ties to even, then restore one decimal.
function roundPercentage(value: number) {
  const scaled = value * 10
  const lower = Math.floor(scaled)
  const fraction = scaled - lower
  return (fraction === 0.5 ? lower % 2 === 0 ? lower : lower + 1 : Math.round(scaled)) / 10
}
export function labelSummary(rows: AuditRow[]) {
  const groups = new Map<string, { boxes: number; reviews: number; images: Set<string> }>()
  const issues = new Map<string, number>()
  let reviews = 0, high = 0, imagesAvailable = 0
  for (const row of rows) {
    const review = row.status.startsWith('REVIEW')
    if (review) reviews++
    if (review && row.priority === 'HIGH') high++
    if (row.image_available) imagesAvailable++
    const group = groups.get(row.label) ?? { boxes: 0, reviews: 0, images: new Set<string>() }
    group.boxes++; if (review) group.reviews++; group.images.add(row.image_name); groups.set(row.label, group)
    for (const issue of row.issues.split(',').map((value) => value.trim()).filter(Boolean)) issues.set(issue, (issues.get(issue) ?? 0) + 1)
  }
  return {
    boxes: rows.length, reviews, high, imagesAvailable, issues: [...issues],
    classes: [...groups].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([label, group]) => ({
      label, boxes: group.boxes, reviews: group.reviews, images: group.images.size,
      // Same presentation aggregation as Streamlit class summary; no audit rules rerun.
      reviewRatePct: roundPercentage(group.reviews / group.boxes * 100),
    })),
  }
}
