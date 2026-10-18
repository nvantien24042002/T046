import { z } from 'zod'

export const coverageDimensions = ['class', 'weather', 'timeofday', 'occluded'] as const
export type CoverageDimension = typeof coverageDimensions[number]
const filtersSchema = z.record(z.string(), z.string().min(1).max(512)).refine(
  (filters) => Object.keys(filters).length > 0 && Object.keys(filters).every((key) => coverageDimensions.includes(key as CoverageDimension)),
  'Slice cần ít nhất một dimension hợp lệ.',
)
export const coverageTargetSchema = z.object({
  name: z.string().max(512).nullable().optional(), filters: filtersSchema,
  unit: z.enum(['images', 'objects']).default('images'),
  target: z.number().int().positive().safe(), weight: z.number().finite().positive().default(1),
}).strict()
export const coverageTargetsSchema = z.array(coverageTargetSchema).max(250)
export const coverageGenerationSchema = z.object({
  dimensions: z.array(z.enum(coverageDimensions)).min(1).max(4).refine((v) => new Set(v).size === v.length),
  unit: z.enum(['images', 'objects']), target: z.number().int().positive().safe(),
  weight: z.number().finite().positive(), include_unobserved: z.boolean(),
}).strict()
export type CoverageTarget = z.infer<typeof coverageTargetSchema>
export type CoverageGeneration = z.infer<typeof coverageGenerationSchema>

export function targetKey(target: CoverageTarget) {
  return JSON.stringify([target.unit, coverageDimensions.filter((d) => d in target.filters).map((d) => [d, target.filters[d]])])
}
export function mergeCoverageTargets(existing: CoverageTarget[], incoming: CoverageTarget[], replace = false) {
  const targets = [...(replace ? [] : existing)]
  const seen = new Set(targets.map(targetKey))
  let duplicates = 0
  for (const item of incoming) {
    if (seen.has(targetKey(item))) { duplicates++; continue }
    targets.push(item); seen.add(targetKey(item))
  }
  return { targets: coverageTargetsSchema.parse(targets), duplicates }
}
export function targetsJSON(targets: CoverageTarget[]) {
  return JSON.stringify(coverageTargetsSchema.parse(targets), null, 2)
}
