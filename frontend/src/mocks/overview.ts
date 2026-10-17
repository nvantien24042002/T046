import baseline from './overview-baseline.json'
import { decodeOverview } from '../api/overview'
export type OverviewScenario = 'annotated' | 'images-only' | 'empty' | 'error'
// Local UI fixture identity. No real dataset/session identifiers or credentials.
const fixtureId = 'mock-baseline'
export async function loadMockOverview(scenario: OverviewScenario) {
  if (scenario === 'error') throw new Error('Lỗi mẫu có chủ đích. Hãy thử lại hoặc chọn dataset mẫu khác.')
  const annotated = scenario === 'annotated'
  const empty = scenario === 'empty'
  return decodeOverview(
    { dataset_id: fixtureId, state: 'ready', ready: true, images: empty ? 0 : 2, annotations: annotated },
    {
      dataset_id: fixtureId,
      quality_rows: empty ? [] : baseline.quality,
      audit_rows: annotated ? baseline.audit : [],
      classes: annotated ? baseline.classes : [],
    },
  )
}
