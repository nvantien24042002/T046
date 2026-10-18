import { z } from 'zod'
import { browserJson } from './browserSession'
import { payloadSchema } from './verifiedResults'
import { decodeDatasetSummary } from './datasets'

const nonnegative = z.number().finite().nonnegative()
const intensity = nonnegative.max(255)
export const imageConfigurationSchema = z.object({
  blur_threshold: nonnegative,
  dark_threshold: intensity,
  bright_threshold: intensity,
  contrast_threshold: nonnegative,
  noise_threshold: nonnegative,
  min_width: z.number().int().positive().safe(),
  min_height: z.number().int().positive().safe(),
  black_threshold: intensity,
  white_threshold: intensity,
}).strict().refine((c) => c.dark_threshold <= c.bright_threshold, 'Ngưỡng tối không được lớn hơn ngưỡng quá sáng.')
  .refine((c) => c.black_threshold <= c.white_threshold, 'Ngưỡng pixel đen không được lớn hơn ngưỡng pixel trắng.')
export type ImageConfiguration = z.infer<typeof imageConfigurationSchema>

export const imageConfigurationFields: { key: keyof ImageConfiguration; label: string; hint: string; max?: number; min: number; step: string }[] = [
  { key: 'blur_threshold', label: 'Độ nét tối thiểu', hint: 'Laplacian variance; thấp hơn ngưỡng tạo cảnh báo BLUR.', min: 0, step: 'any' },
  { key: 'dark_threshold', label: 'Ngưỡng tối', hint: 'Brightness trung bình thấp hơn ngưỡng tạo cảnh báo DARK (0–255).', min: 0, max: 255, step: 'any' },
  { key: 'bright_threshold', label: 'Ngưỡng quá sáng', hint: 'Brightness trung bình cao hơn ngưỡng tạo cảnh báo OVEREXPOSED (0–255).', min: 0, max: 255, step: 'any' },
  { key: 'contrast_threshold', label: 'Tương phản tối thiểu', hint: 'Độ lệch chuẩn grayscale; thấp hơn ngưỡng tạo LOW_CONTRAST.', min: 0, step: 'any' },
  { key: 'noise_threshold', label: 'Ngưỡng nhiễu MAD', hint: 'Noise score cao hơn ngưỡng tạo POSSIBLE_HIGH_NOISE khi đo được.', min: 0, step: 'any' },
  { key: 'min_width', label: 'Chiều rộng tối thiểu', hint: 'Số nguyên pixel; ảnh nhỏ hơn ngưỡng tạo LOW_RESOLUTION.', min: 1, step: '1' },
  { key: 'min_height', label: 'Chiều cao tối thiểu', hint: 'Số nguyên pixel; ảnh nhỏ hơn ngưỡng tạo LOW_RESOLUTION.', min: 1, step: '1' },
  { key: 'black_threshold', label: 'Ngưỡng pixel đen', hint: 'Gray thấp hơn ngưỡng được tính vào black_pct (0–255).', min: 0, max: 255, step: 'any' },
  { key: 'white_threshold', label: 'Ngưỡng pixel trắng', hint: 'Gray cao hơn ngưỡng được tính vào white_pct (0–255).', min: 0, max: 255, step: 'any' },
]

export async function readImageConfigurationDefaults(dataset: string, signal: AbortSignal) {
  const value = await browserJson(`/datasets/${encodeURIComponent(dataset)}/configurations`, { signal })
  return z.object({ default: z.object({ image_cfg: imageConfigurationSchema }) }).parse(value).default.image_cfg
}

export async function reanalyzeImageQuality(dataset: string, configuration: ImageConfiguration, signal: AbortSignal) {
  const image_cfg = imageConfigurationSchema.parse(configuration)
  const payload = payloadSchema.parse(await browserJson(`/datasets/${encodeURIComponent(dataset)}/analyze`, {
    method: 'POST', body: JSON.stringify({ image_cfg }), signal,
  }))
  if (payload.dataset_id !== dataset || !payload.request_fingerprint) throw new Error('Kết quả phân tích không khớp bộ dữ liệu đã chọn.')
  const summary = decodeDatasetSummary(await browserJson(`/datasets/${encodeURIComponent(dataset)}/summary`, { signal }))
  if (!summary.ready || summary.dataset_id !== dataset) throw new Error('Bộ dữ liệu chưa sẵn sàng sau phân tích.')
  return { detail: { payload, descriptor: null, provenance: null }, summary }
}
