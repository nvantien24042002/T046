// Presentation only: references still travel unchanged through authenticated APIs.
const privateText = /\b[^\s]+\.[a-zA-Z][a-zA-Z0-9]{0,11}\b|\b[a-f0-9]{32,}\b|\b[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\b|\b(?:Bearer|storage_key|access_token|upload_capability)\b/i
export function publicText(value: string, fallback = 'Thông tin chi tiết được ẩn'): string {
  return (value.startsWith('/') || value.includes(' /') || value.includes(String.fromCharCode(92)) || ['http://', 'https://', 'storage/', 'uploads/', 'datasets/', 'cache/', 'images/'].some((part) => value.toLowerCase().includes(part)) || privateText.test(value)) ? fallback : value
}
export function publicColumn(key: string): boolean {
  return !/(?:file|path|storage|source|(?:^|_)id$|(?:^|_)key$|sha256|fingerprint|capability|token|image_name|(?:^|_)error$)/i.test(key)
}
export function publicCell(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) return value.map(publicCell).join(', ')
  if (typeof value === 'object') return Object.entries(value).filter(([key]) => publicColumn(key))
    .map(([key, item]) => `${publicText(key)}: ${publicCell(item)}`).join(', ') || '—'
  return typeof value === 'string' ? publicText(value) : String(value)
}
