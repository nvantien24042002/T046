import { z } from 'zod'
import { intermediaryOrigin } from './analyticsV1/schemas'

export const configurationNumberSchema = z.union([
  z.object({ kind: z.literal('integer'), decimal: z.string().regex(/^(0|[1-9][0-9]*|-[1-9][0-9]*)$/) }),
  z.object({ kind: z.literal('binary64'), bits: z.string().regex(/^[0-9a-f]{16}$/) }),
])

export const effectiveConfigurationSchema = z.object({
  image_cfg: z.record(z.string(), configurationNumberSchema),
  label_cfg: z.record(z.string(), configurationNumberSchema),
})

export const descriptorSchema = z.object({
  result_id: z.string().min(1),
  dataset_id: z.string().min(1),
  archive_sha256: z.string().regex(/^[0-9a-f]{64}$/),
  config_id: z.string().min(1),
  request_fingerprint: z.string().regex(/^[0-9a-f]{64}$/).nullable(),
  configuration_schema_revision: z.string().min(1),
  effective_config: effectiveConfigurationSchema,
  defaults_version: z.string().min(1),
  canonicalization_version: z.string().min(1),
  algorithm_version: z.string().min(1),
  producer_build_id: z.string().min(1),
  payload_sha256: z.string().regex(/^[0-9a-f]{64}$/),
  verification: z.literal('verified'),
})

export type Descriptor = z.infer<typeof descriptorSchema>

export const verifiedResultListSchema = z.object({
  schema_version: z.literal('analytics.v1'),
  dataset_id: z.string().min(1),
  results: z.array(descriptorSchema),
})

export type VerifiedResultList = z.infer<typeof verifiedResultListSchema>

const metric = z.number().finite().nullable()

export const qualityRowSchema = z.object({
  filename: z.string().min(1),
  status: z.enum(['PASS', 'REVIEW', 'ERROR']),
  score: z.number().min(0).max(100),
  issues: z.array(z.string()),
  width: z.number().int().nonnegative(),
  height: z.number().int().nonnegative(),
  size_kb: z.number().nonnegative(),
  blur_score: metric,
  brightness: metric,
  contrast: metric,
  noise_score: metric,
  black_pct: metric,
  white_pct: metric,
  error: z.string(),
})

export type QualityRow = z.infer<typeof qualityRowSchema>

export const auditRowSchema = z.object({
  image_id: z.string(),
  image_name: z.string(),
  box_id: z.string(),
  label: z.string(),
  occluded: z.string(),
  xtl: metric,
  ytl: metric,
  xbr: metric,
  ybr: metric,
  image_available: z.boolean(),
  image_error: z.string().nullable(),
  area_pct: metric,
  aspect_ratio: metric,
  edge_density: metric,
  texture_variance: metric,
  laplacian_variance: metric,
  foreground_contrast: metric,
  boundary_support: metric,
  geometry_issues: z.string(),
  visual_issues: z.string(),
  issues: z.string(),
  issue_count: z.number().int().nonnegative(),
  priority: z.enum(['HIGH', 'MEDIUM', 'LOW']),
  status: z.enum(['REVIEW', 'REVIEW_A_ONLY', 'NO_SIGNAL', 'A_ONLY']),
})

export type AuditRow = z.infer<typeof auditRowSchema>

export const classRowSchema = z.object({
  class: z.string(),
  boxes: z.number().int().nonnegative(),
  images: z.number().int().nonnegative(),
  share_pct: z.number().min(0).max(100),
})

export type ClassRow = z.infer<typeof classRowSchema>

export const attributeRowSchema = z.object({
  attribute: z.string(),
  value: z.string(),
  label: z.string(),
  count: z.number().int().nonnegative(),
})

export type AttributeRow = z.infer<typeof attributeRowSchema>

export const distributionImageSchema = z.object({
  image_id: z.string(),
  image_name: z.string(),
  weather: z.string(),
  timeofday: z.string(),
})

export type DistributionImage = z.infer<typeof distributionImageSchema>

export const distributionBoxSchema = z.object({
  image_id: z.string(),
  image_name: z.string(),
  box_id: z.string(),
  label: z.string(),
  occluded: z.string(),
  valid_geometry: z.boolean(),
  area_pct: metric,
  attributes: z.record(z.string(), z.string().nullable()),
})

export type DistributionBox = z.infer<typeof distributionBoxSchema>

export const payloadSchema = z.object({
  dataset_id: z.string().min(1),
  request_fingerprint: z.string().regex(/^[0-9a-f]{64}$/).optional(),
  quality_rows: z.array(qualityRowSchema),
  audit_rows: z.array(auditRowSchema),
  classes: z.array(classRowSchema),
  attribute_distribution: z.array(attributeRowSchema),
  distribution_images: z.array(distributionImageSchema),
  distribution_boxes: z.array(distributionBoxSchema),
  attributes: z.array(z.string()),
})

export type VerifiedPayload = z.infer<typeof payloadSchema>

export const verifiedProvenanceSchema = z.object({
  verification: z.literal('verified'),
  config_id: z.string().min(1),
  payload_sha256: z.string().regex(/^[0-9a-f]{64}$/),
})

export const verifiedResultDetailSchema = z.object({
  schema_version: z.literal('analytics.v1'),
  descriptor: descriptorSchema,
  provenance: verifiedProvenanceSchema,
  payload: payloadSchema,
})

export type VerifiedResultDetail = z.infer<typeof verifiedResultDetailSchema>

// The integrated API returns analysis rows, without a verification envelope.
// Do not manufacture result IDs or provenance for that response.
export interface AnalysisResultDetail {
  payload: VerifiedPayload
  descriptor: null
  provenance: null
}
export type ActiveResult = VerifiedResultDetail | AnalysisResultDetail

export class VerifiedResultError extends Error {
  constructor(
    public readonly kind: 'configuration' | 'http' | 'network' | 'timeout' | 'cancelled' | 'response' | 'integrity',
    public readonly status?: number,
    public readonly code?: string,
  ) {
    super('Verified result error: ' + kind + ' (status: ' + (status ?? 'none') + ', code: ' + (code ?? 'none') + ')')
    this.name = 'VerifiedResultError'
  }
}

export function decodeVerifiedResultList(value: unknown): VerifiedResultList {
  try {
    return verifiedResultListSchema.parse(value)
  } catch {
    throw new VerifiedResultError('response')
  }
}

export function decodeVerifiedResultDetail(
  value: unknown,
  expectedDatasetId?: string,
  expectedResultId?: string,
): VerifiedResultDetail {
  let parsed: VerifiedResultDetail
  try {
    parsed = verifiedResultDetailSchema.parse(value)
  } catch {
    throw new VerifiedResultError('response')
  }

  if (expectedDatasetId && (parsed.descriptor.dataset_id !== expectedDatasetId || parsed.payload.dataset_id !== expectedDatasetId)) {
    throw new VerifiedResultError('integrity', 502, 'DATASET_MISMATCH')
  }
  if (expectedResultId && parsed.descriptor.result_id !== expectedResultId) {
    throw new VerifiedResultError('integrity', 502, 'RESULT_ID_MISMATCH')
  }
  if (parsed.provenance.config_id !== parsed.descriptor.config_id) {
    throw new VerifiedResultError('integrity', 502, 'CONFIG_ID_MISMATCH')
  }
  if (parsed.provenance.payload_sha256 !== parsed.descriptor.payload_sha256) {
    throw new VerifiedResultError('integrity', 502, 'PAYLOAD_HASH_MISMATCH')
  }

  return parsed
}

export interface VerifiedResultsTransport {
  listResults(datasetId: string, token: string, signal?: AbortSignal): Promise<VerifiedResultList>
  readResult(datasetId: string, resultId: string, token: string, signal?: AbortSignal): Promise<VerifiedResultDetail>
  fetchPreview(datasetId: string, imagePath: string, annotated: boolean, token: string, signal?: AbortSignal): Promise<Blob>
}

export function createVerifiedResultsTransport(
  origin: string = intermediaryOrigin,
  fetcher: typeof fetch = fetch,
): VerifiedResultsTransport {
  async function request<T>(
    path: string,
    decode: (val: unknown) => T,
    token: string,
    signal?: AbortSignal,
  ): Promise<T> {
    const controller = new AbortController()
    let timedOut = false
    const abort = () => controller.abort()
    if (signal) {
      signal.addEventListener('abort', abort, { once: true })
      if (signal.aborted) abort()
    }
    const timer = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, 10_000)

    try {
      const response = await fetcher(origin + path, {
        method: 'GET',
        headers: {
          Accept: 'application/json',
          Authorization: 'Bearer ' + token,
        },
        signal: controller.signal,
        credentials: 'omit',
        redirect: 'error',
        cache: 'no-store',
      })

      let value: unknown
      try {
        value = await response.json()
      } catch {
        if (controller.signal.aborted) throw new VerifiedResultError('cancelled')
        throw new VerifiedResultError('response')
      }

      if (!response.ok) {
        const errorBody = value as { error?: { code?: string } } | undefined
        throw new VerifiedResultError('http', response.status, errorBody?.error?.code)
      }

      return decode(value)
    } catch (error) {
      if (error instanceof VerifiedResultError) throw error
      if (timedOut) throw new VerifiedResultError('timeout')
      if (controller.signal.aborted) throw new VerifiedResultError('cancelled')
      throw new VerifiedResultError('network')
    } finally {
      clearTimeout(timer)
      if (signal) signal.removeEventListener('abort', abort)
    }
  }

  return {
    listResults(datasetId: string, token: string, signal?: AbortSignal) {
      return request(
        '/analytics/v1/datasets/' + encodeURIComponent(datasetId) + '/results',
        decodeVerifiedResultList,
        token,
        signal,
      )
    },
    readResult(datasetId: string, resultId: string, token: string, signal?: AbortSignal) {
      return request(
        '/analytics/v1/datasets/' + encodeURIComponent(datasetId) + '/results/' + encodeURIComponent(resultId),
        (val) => decodeVerifiedResultDetail(val, datasetId, resultId),
        token,
        signal,
      )
    },
    async fetchPreview(
      datasetId: string,
      imagePath: string,
      annotated: boolean,
      token: string,
      signal?: AbortSignal,
    ): Promise<Blob> {
      const controller = new AbortController()
      let timedOut = false
      const abort = () => controller.abort()
      if (signal) {
        signal.addEventListener('abort', abort, { once: true })
        if (signal.aborted) abort()
      }
      const timer = setTimeout(() => {
        timedOut = true
        controller.abort()
      }, 15_000)

      try {
        const path =
          '/analytics/v1/datasets/' +
          encodeURIComponent(datasetId) +
          '/preview?image_path=' +
          encodeURIComponent(imagePath) +
          '&annotated=' +
          (annotated ? 'true' : 'false')

        const response = await fetcher(origin + path, {
          method: 'GET',
          headers: {
            Accept: 'image/*',
            Authorization: 'Bearer ' + token,
          },
          signal: controller.signal,
          credentials: 'omit',
          redirect: 'error',
          cache: 'no-store',
        })

        if (!response.ok) {
          let code: string | undefined
          try {
            const errorBody = (await response.json()) as { error?: { code?: string } }
            code = errorBody?.error?.code
          } catch {
            // non-json body
          }
          throw new VerifiedResultError('http', response.status, code)
        }

        return await response.blob()
      } catch (error) {
        if (error instanceof VerifiedResultError) throw error
        if (timedOut) throw new VerifiedResultError('timeout')
        if (controller.signal.aborted) throw new VerifiedResultError('cancelled')
        throw new VerifiedResultError('network')
      } finally {
        clearTimeout(timer)
        if (signal) signal.removeEventListener('abort', abort)
      }
    },
  }
}
