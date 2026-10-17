// UI-only fixtures, deliberately not shaped as an unconfirmed backend contract.
export const sampleRows = [
  { area: 'Phân bố & thuộc tính', description: 'Class, bounding box, thời tiết và thời điểm', destination: '/distribution' },
  { area: 'Image Quality', description: 'Độ nét, độ sáng và các tín hiệu cần review', destination: '/quality' },
  { area: 'Label Check A+B', description: 'Geometry và visual signals', destination: '/labels' },
] as const
export type Scenario = 'success' | 'loading' | 'empty' | 'error'
