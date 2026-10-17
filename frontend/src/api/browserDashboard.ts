import { dashboardSchema, type DashboardPage, type DashboardQuery, type DashboardView } from './dashboard'
import type { ActiveResult } from './verifiedResults'
import { browserPrefix, browserHeaders, ensureBrowserSession } from './browserSession'
export async function readBrowserDashboard(page: DashboardPage, result: ActiveResult, _unused: string,
  query: DashboardQuery, signal: AbortSignal): Promise<DashboardView> {
  const dataset = result.payload.dataset_id
  const descriptor = result.descriptor
  const fingerprint = descriptor?.request_fingerprint ?? result.payload.request_fingerprint
  if (!descriptor && !fingerprint) throw new Error('Kết quả này thiếu fingerprint từ backend. Cần phân tích lại; không dùng kết quả mới nhất thay thế.')
  if (descriptor) throw new Error('Immutable provider chưa được cấu hình cho browser session.')
  const path = `/datasets/${encodeURIComponent(dataset)}/dashboard`
  await ensureBrowserSession()
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal.addEventListener('abort', abort, { once: true })
  if (signal.aborted) abort()
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; controller.abort() }, 30_000)
  try {
    const response = await fetch(browserPrefix + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json',
        ...browserHeaders },
      body: JSON.stringify({ page, query, expected_fingerprint: fingerprint }),
      signal: controller.signal, credentials: 'same-origin', redirect: 'error', cache: 'no-store',
    })
    if (!response.ok) {
      if (response.status === 404 || response.status === 501) throw new Error('Backend chưa hỗ trợ API dashboard.v1 cho trang này.')
      if (response.status === 409) throw new Error('Kết quả backend đã thay đổi hoặc chưa sẵn sàng. Hãy chọn/phân tích lại dataset.')
      throw new Error(`Không tải được dữ liệu dashboard từ API (HTTP ${response.status}).`)
    }
    const view = dashboardSchema.parse(await response.json())
    if (view.dataset_id !== dataset || view.page !== page || view.source.request_fingerprint !== fingerprint || view.source.result_id !== null) {
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
