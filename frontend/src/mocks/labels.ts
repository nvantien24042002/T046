import baseline from './overview-baseline.json'
import { decodeLabels } from '../api/labels'
// Pinned to DEFAULT_LABEL_CFG and tests/baseline/capture.py LABEL_CFG.
export const labelDefaults = Object.freeze({ min_area_pct: 0.01, max_area_pct: 80, min_aspect: 0.15, max_aspect: 7,
  duplicate_iou: 0.90, min_edge: 0.02, min_texture: 100, min_sharpness: 50, min_contrast: 12, min_boundary: 0.02 })
// Local fixture key, not a server request_fingerprint.
export const labelConfigurationKey = JSON.stringify(labelDefaults)
export type LabelScenario = 'baseline' | 'no-annotations' | 'no-boxes' | 'empty' | 'malformed' | 'error'
export async function loadMockLabels(scenario: LabelScenario) {
  if (scenario === 'error') throw Error('Deliberate fixture failure')
  const data = decodeLabels(
    { dataset_id: 'mock-baseline', annotations: scenario !== 'no-annotations' },
    { dataset_id: 'mock-baseline', audit_rows: scenario === 'baseline' ? baseline.audit : scenario === 'malformed' ? [{ box_id: 'invalid' }] : [] },
  )
  return { configurationKey: labelConfigurationKey, data }
}
export type LabelSnapshot = Awaited<ReturnType<typeof loadMockLabels>>
