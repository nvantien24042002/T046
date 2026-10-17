import baseline from './overview-baseline.json'
import { decodeQuality } from '../api/quality'
// Pinned to core/image_quality.py DEFAULT_IMAGE_CFG and baseline/capture.py IMAGE_CFG.
export const qualityDefaults = Object.freeze({
  blur_threshold: 100, dark_threshold: 40, bright_threshold: 215, contrast_threshold: 25,
  noise_threshold: 10, min_width: 320, min_height: 240, black_threshold: 20, white_threshold: 245,
})
// Local fixture identity only; not the backend request_fingerprint algorithm.
export const qualityConfigurationKey = JSON.stringify(qualityDefaults)
export type QualityScenario = 'baseline' | 'empty' | 'malformed' | 'error'
export async function loadMockQuality(scenario: QualityScenario) {
  if (scenario === 'error') throw new Error('Deliberate fixture failure')
  const data = decodeQuality({
    dataset_id: 'mock-baseline',
    quality_rows: scenario === 'empty' ? [] : scenario === 'malformed' ? [{ filename: 'invalid' }] : baseline.quality,
  })
  return { configurationKey: qualityConfigurationKey, data }
}
export type QualitySnapshot = Awaited<ReturnType<typeof loadMockQuality>>
