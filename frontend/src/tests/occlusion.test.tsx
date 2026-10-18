import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { OcclusionChart } from '../components/Charts'
import { aggregateOcclusion, normalizeOcclusion, type OcclusionRow } from '../components/occlusion'

describe('Occlusion values from CVAT and defensive inputs', () => {
  it.each([
    ['0', 'unoccluded'], ['1', 'occluded'],
    ['true', 'occluded'], ['false', 'unoccluded'],
    ['yes', 'occluded'], ['no', 'unoccluded'],
    [' TRUE ', 'occluded'], [' No ', 'unoccluded'],
    [true, 'occluded'], [false, 'unoccluded'], [1, 'occluded'], [0, 'unoccluded'],
    [null, 'unknown'], [undefined, 'unknown'], ['', 'unknown'], ['  ', 'unknown'],
    ['unrecognized', 'unknown'], ['unknown', 'unknown'], [2, 'unknown'], [NaN, 'unknown'],
  ] as const)('classifies %s as %s', (value, expected) => {
    expect(normalizeOcclusion(value)).toBe(expected)
  })

  it('keeps unknown separate and conserves counts without mutating source rows', () => {
    const rows: OcclusionRow[] = [
      { label: 'car', occluded: '1', boxes: 2 },
      { label: 'car', occluded: '0', boxes: 3 },
      { label: 'car', occluded: null, boxes: 4 },
      { label: 'car', occluded: '', boxes: 5 },
      { label: 'car', occluded: 'invalid', boxes: 6 },
      { label: 'car', boxes: 1 },
      { label: 'bus', occluded: 'yes', boxes: 1 },
    ]
    const original = structuredClone(rows)
    expect(aggregateOcclusion(rows)).toEqual([
      { label: 'car', occluded: 2, unoccluded: 3, unknown: 16, total: 21, rate: 9.5 },
      { label: 'bus', occluded: 1, unoccluded: 0, unknown: 0, total: 1, rate: 100 },
    ])
    expect(rows).toEqual(original)
  })

  it('preserves totals and percentages for previously supported known values', () => {
    expect(aggregateOcclusion([
      { label: 'car', occluded: 'true', boxes: 2 },
      { label: 'car', occluded: 'false', boxes: 6 },
    ])).toEqual([{ label: 'car', occluded: 2, unoccluded: 6, unknown: 0, total: 8, rate: 25 }])
    expect(aggregateOcclusion([])).toEqual([])
  })

  it('renders real chart summary counts including unknown, not extra clear boxes', () => {
    render(<OcclusionChart rows={[
      { label: 'car', occluded: '1', boxes: 2 },
      { label: 'car', occluded: '0', boxes: 3 },
      { label: 'car', occluded: null, boxes: 5 },
    ]} />)
    expect(screen.getByText('2 box bị che (20%)')).toBeVisible()
    expect(screen.getByText('3 box rõ ràng')).toBeVisible()
    expect(screen.getByText('5 box chưa xác định')).toBeVisible()
  })

  it('does not add an unknown count for ordinary CVAT rows', () => {
    render(<OcclusionChart rows={[
      { label: 'car', occluded: '1', boxes: 1 },
      { label: 'car', occluded: '0', boxes: 3 },
    ]} />)
    expect(screen.getByText('1 box bị che (25%)')).toBeVisible()
    expect(screen.getByText('3 box rõ ràng')).toBeVisible()
    expect(screen.queryByText(/box chưa xác định/)).not.toBeInTheDocument()
  })
})
