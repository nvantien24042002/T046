import { z } from 'zod'
import { createApiClient, type RequestOptions } from './client'
// Confirmed in backend/integrated_api.py. No dataset/auth contract is introduced.
const healthSchema = z.object({ status: z.literal('ok'), stage: z.string(), ready_datasets: z.boolean() })
export type Health = z.infer<typeof healthSchema>
// Async also converts URL-construction failures into handled request rejections.
export async function readHealth(baseUrl: string, options: RequestOptions = {}) {
  return createApiClient(baseUrl).get('/health', (value) => healthSchema.parse(value), options)
}
