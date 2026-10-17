import { z } from 'zod'
// Safe metadata allowlist from current integrated_api.py; never expose raw details.
export const knownErrorCode = z.enum([
  'ACCESS_DENIED', 'LOCAL_ONLY', 'DATASET_NOT_FOUND', 'DATASET_NOT_READY', 'ANALYSIS_NOT_RUN',
  'INVALID_METADATA', 'INVALID_ANALYSIS_REQUEST', 'REQUEST_TOO_LARGE', 'STORAGE_IO_FAILED',
  'REGISTRY_UNAVAILABLE', 'SERVICE_UNHEALTHY', 'INTERNAL_SERVER_ERROR', 'PIPELINE_EXECUTION_FAILED',
])
export type KnownErrorCode = z.infer<typeof knownErrorCode>
export type ErrorFormat = 'application' | 'validation' | 'unknown'
const applicationError = z.object({ error: z.object({ code: z.string() }) })
const validationError = z.object({ detail: z.union([z.string(), z.array(z.object({ msg: z.string(), type: z.string() }))]) })
export function normalizeApiFailure(value: unknown): { format: ErrorFormat; code?: KnownErrorCode } {
  const application = applicationError.safeParse(value)
  if (application.success) {
    const code = knownErrorCode.safeParse(application.data.error.code)
    return code.success ? { format: 'application', code: code.data } : { format: 'application' }
  }
  return { format: validationError.safeParse(value).success ? 'validation' : 'unknown' }
}
