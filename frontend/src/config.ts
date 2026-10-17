export function readConfig(env: Record<string, string | undefined>) {
  const mode = env.VITE_DATA_MODE ?? 'backend'
  if (mode !== 'mock' && mode !== 'backend') throw new Error('VITE_DATA_MODE must be mock or backend.')
  return { mode, analyticsBaseUrl: env.VITE_ANALYTICS_BASE_URL ?? 'http://127.0.0.1:8012', baseUrl: env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8011' } as const
}
// Legacy helper above is retained for historical component harnesses only.
// Product bootstrap ignores legacy destinations and always uses same-origin URLs.
export function readBrowserConfig(env: Record<string, string | undefined>) {
  const mode = env.VITE_DATA_MODE ?? 'backend'
  if (mode !== 'mock' && mode !== 'backend') throw new Error('VITE_DATA_MODE must be mock or backend.')
  return { mode, baseUrl: '', analyticsBaseUrl: '' } as const
}
export const config = readBrowserConfig(import.meta.env)

