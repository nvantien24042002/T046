import { z } from 'zod'
import { config } from '../config'
import type { ActiveResult } from './verifiedResults'

export type DashboardPage = 'overview' | 'quality' | 'labels' | 'distribution' | 'review' | 'reports' | 'coverage' | 'scene-finder'
export type DashboardQuery = Record<string, unknown>
const scalar = z.union([z.string(), z.number().finite(), z.boolean(), z.null()])
export const dashboardSchema = z.object({
  schema_version: z.literal('dashboard.v1'), dataset_id: z.string().min(1), page: z.string(),
  state: z.enum(['ready', 'empty', 'unsupported', 'no_results']), reason: z.string().nullable(),
  source: z.object({ request_fingerprint: z.string().nullable(), result_id: z.string().nullable(), payload_sha256: z.string().nullable() }),
  metrics: z.array(z.object({ key: z.string(), label: z.string(), value: scalar })),
  tables: z.array(z.object({ key: z.string(), title: z.string(), rows: z.array(z.record(z.string(), z.unknown())), export_filename: z.string().nullable() })),
  charts: z.array(z.object({ key: z.string(), title: z.string(), rows: z.array(z.object({ name: z.string(), value: z.number().finite().nonnegative() })) })),
  controls: z.array(z.object({ key: z.string(), label: z.string(), choices: z.array(z.string()), value: z.union([z.string(), z.array(z.string())]).nullable() })),
  notes: z.array(z.string()),
  parameters: z.array(z.object({ key: z.string(), label: z.string(), value: z.number().finite() })).optional(),
  can_submit_targets: z.boolean().optional(),
  keyword_search: z.boolean().optional(),
})
export type DashboardView = z.infer<typeof dashboardSchema>

export async function readDashboard(page: DashboardPage, result: ActiveResult, token: string,
  query: DashboardQuery, signal: AbortSignal): Promise<DashboardView> {
  const dataset = result.payload.dataset_id
  const descriptor = result.descriptor
  const fingerprint = descriptor?.request_fingerprint ?? result.payload.request_fingerprint
  if (!descriptor && !fingerprint) throw new Error('Kết quả này thiếu fingerprint từ backend. Cần phân tích lại; không dùng kết quả mới nhất thay thế.')
  const rawBase = descriptor ? config.analyticsBaseUrl : config.baseUrl
  const base = rawBase ? new URL(rawBase) : new URL('/', globalThis.location?.origin ?? 'http://localhost')
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash) throw new Error('Invalid backend destination')
  const path = descriptor
    ? `/analytics/v1/datasets/${encodeURIComponent(dataset)}/results/${encodeURIComponent(descriptor.result_id)}/dashboard`
    : `/api/v1/datasets/${encodeURIComponent(dataset)}/dashboard`
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal.addEventListener('abort', abort, { once: true })
  if (signal.aborted) abort()
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; controller.abort() }, 30_000)
  try {
    const response = await fetch(base.toString().replace(/\/$/, '') + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json',
        ...(descriptor ? { Authorization: `Bearer ${token}` } : { 'Upload-Capability': token }) },
      body: JSON.stringify({ page, query, ...(descriptor ? { expected_payload_sha256: descriptor.payload_sha256 } : { expected_fingerprint: fingerprint }) }),
      signal: controller.signal, credentials: 'omit', redirect: 'error', cache: 'no-store',
    })
    if (!response.ok) {
      if (response.status === 404 || response.status === 501) throw new Error('Backend chưa hỗ trợ API dashboard.v1 cho trang này.')
      if (response.status === 409) throw new Error('Kết quả backend đã thay đổi hoặc chưa sẵn sàng. Hãy chọn/phân tích lại dataset.')
      throw new Error(`Không tải được dữ liệu dashboard từ API (HTTP ${response.status}).`)
    }
    const view = dashboardSchema.parse(await response.json())
    if (view.dataset_id !== dataset || view.page !== page || (descriptor ? view.source.result_id !== descriptor.result_id || view.source.payload_sha256 !== descriptor.payload_sha256 : view.source.request_fingerprint !== fingerprint || view.source.result_id !== null)) {
      throw new Error('Dashboard trả về sai dataset hoặc kết quả phân tích.')
    }
    if (controller.signal.aborted) throw new Error('Yêu cầu đã bị hủy.')
    return view
  } catch (error) {
    if (timedOut) throw new Error('Quá thời gian chờ API dashboard.')
    throw error
  } finally {
    clearTimeout(timer)
    signal.removeEventListener('abort', abort)
  }
}
