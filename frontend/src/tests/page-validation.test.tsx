import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Overview } from '../reference/Overview'
import { ImageQuality } from '../reference/ImageQuality'
import { LabelCheck } from '../reference/LabelCheck'
import { qualityConfigurationKey } from '../mocks/quality'
import { labelConfigurationKey } from '../mocks/labels'
vi.mock('../config', () => ({ config: { mode: 'mock' } }))

describe('runtime validation immediately before rendering', () => {
  it('keeps malformed Overview loader data out of the view', async () => {
    const load = vi.fn().mockResolvedValue({ summary: { dataset_id: 'same' }, results: { dataset_id: 'same', quality_rows: 'invalid' } })
    render(<Overview load={load} />)
    expect(await screen.findByRole('alert')).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
  it('does not trust a matching fixture config key when Quality metrics are malformed', async () => {
    const load = vi.fn().mockResolvedValue({ configurationKey: qualityConfigurationKey, data: { dataset_id: 'mock-baseline', quality_rows: [{ score: 'private invalid' }] } })
    render(<ImageQuality load={load} />)
    expect(await screen.findByRole('alert')).not.toHaveTextContent('private invalid')
    expect(screen.queryByRole('group', { name: 'Điểm trung bình' })).not.toBeInTheDocument()
  })
  it('does not trust a matching fixture config key when Label fields are malformed', async () => {
    const load = vi.fn().mockResolvedValue({ configurationKey: labelConfigurationKey, data: { summary: { dataset_id: 'mock-baseline', annotations: true }, results: { dataset_id: 'mock-baseline', audit_rows: [{ status: 'invalid' }] } } })
    render(<LabelCheck load={load} />)
    expect(await screen.findByRole('alert')).toBeVisible()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})