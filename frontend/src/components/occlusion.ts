export type OcclusionRow = {
  label: string
  occluded?: boolean | string | number | null
  boxes: number
}

export interface OcclusionAggregated {
  label: string
  occluded: number
  unoccluded: number
  unknown: number
  total: number
  rate: number
}

export function normalizeOcclusion(value: OcclusionRow['occluded']): 'occluded' | 'unoccluded' | 'unknown' {
  const normalized = value == null ? '' : String(value).trim().toLowerCase()
  if (['1', 'true', 'yes'].includes(normalized)) return 'occluded'
  if (['0', 'false', 'no'].includes(normalized)) return 'unoccluded'
  return 'unknown'
}

export function aggregateOcclusion(rows: readonly OcclusionRow[]): OcclusionAggregated[] {
  const classes = new Map<string, { occluded: number; unoccluded: number; unknown: number }>()
  for (const row of rows) {
    const counts = classes.get(row.label) ?? { occluded: 0, unoccluded: 0, unknown: 0 }
    counts[normalizeOcclusion(row.occluded)] += row.boxes
    classes.set(row.label, counts)
  }
  return Array.from(classes, ([label, counts]) => {
    const total = counts.occluded + counts.unoccluded + counts.unknown
    return { label, ...counts, total, rate: total > 0 ? Number((100 * counts.occluded / total).toFixed(1)) : 0 }
  }).sort((a, b) => b.total - a.total)
}
