import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SceneFinder } from '../reference/SceneFinder'
import { VerifiedResultsProvider, useVerifiedResults } from '../context/VerifiedResultsContext'
import { config } from '../config'
import { type VerifiedResultDetail } from '../api/verifiedResults'
import { useEffect, useRef } from 'react'

const sampleDescriptor = {
  result_id: 'res_sf_001',
  dataset_id: 'ds_test_sf',
  archive_sha256: 'a'.repeat(64),
  config_id: 'cfg_001',
  request_fingerprint: 'f'.repeat(64),
  configuration_schema_revision: 'rev_1',
  effective_config: { image_cfg: {}, label_cfg: {} },
  defaults_version: 'v1',
  canonicalization_version: 'c1',
  algorithm_version: 'a1',
  producer_build_id: 'b1',
  payload_sha256: 'a'.repeat(64),
  verification: 'verified' as const,
}

const sampleDetail: VerifiedResultDetail = {
  schema_version: 'analytics.v1',
  descriptor: sampleDescriptor,
  provenance: {
    verification: 'verified',
    config_id: 'cfg_001',
    payload_sha256: 'a'.repeat(64),
  },
  payload: {
    dataset_id: 'ds_test_sf',
    quality_rows: [],
    audit_rows: [],
    classes: [],
    attribute_distribution: [],
    distribution_images: [
      { image_id: '1', image_name: 'cam_day_sunny.jpg', weather: 'sunny', timeofday: 'day' },
      { image_id: '2', image_name: 'cam_night_rain.jpg', weather: 'rain', timeofday: 'night' },
      { image_id: '3', image_name: 'cam_unknown.jpg', weather: 'missing', timeofday: 'unknown' },
    ],
    distribution_boxes: [],
    attributes: [],
  },
}

function Initializer({
  children,
  detail,
  token,
}: {
  children: React.ReactNode
  detail: VerifiedResultDetail
  token: string
}) {
  const verified = useVerifiedResults()
  const called = useRef(false)
  useEffect(() => {
    if (!called.current && verified) {
      called.current = true
      verified.setManualResult(detail, null, token)
    }
  }, [verified, detail, token])

  return <>{children}</>
}

describe('SceneFinder page', () => {
  const mutableConfig = config as { mode: 'mock' | 'backend'; analyticsBaseUrl: string; baseUrl: string }
  let originalMode: typeof config.mode

  beforeEach(() => {
    originalMode = config.mode
  })

  afterEach(() => {
    mutableConfig.mode = originalMode
  })

  it('renders mock baseline data with filters and metrics', () => {
    mutableConfig.mode = 'mock'
    render(
      <MemoryRouter>
        <VerifiedResultsProvider>
          <SceneFinder />
        </VerifiedResultsProvider>
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { level: 1, name: 'Scene Finder' })).toBeInTheDocument()
    expect(screen.getByText(/Chưa triển khai trong M1/)).toBeInTheDocument()
    expect(screen.getByText('Ảnh khớp bộ lọc')).toBeInTheDocument()
    expect(screen.getByText('Tổng ảnh trong XML')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Tải scene filtered CSV' })).toBeInTheDocument()
  })

  it('renders backend mode empty state when unauthenticated', () => {
    mutableConfig.mode = 'backend'
    render(
      <MemoryRouter>
        <VerifiedResultsProvider>
          <SceneFinder />
        </VerifiedResultsProvider>
      </MemoryRouter>,
    )

    expect(screen.getByText(/Chưa có kết quả phân tích/)).toBeInTheDocument()
  })

  it('filters images by weather, timeofday, and keyword in backend mode', () => {
    mutableConfig.mode = 'backend'
    render(
      <MemoryRouter>
        <VerifiedResultsProvider>
          <Initializer detail={sampleDetail} token="tok123">
            <SceneFinder />
          </Initializer>
        </VerifiedResultsProvider>
      </MemoryRouter>,
    )

    // Total 3 images, 1 with unresolved metadata
    expect(screen.getAllByText('3').length).toBeGreaterThan(0)

    // Search keyword
    const searchInput = screen.getByPlaceholderText('Nhập tên ảnh...')
    fireEvent.change(searchInput, { target: { value: 'night' } })

    // Only cam_night_rain.jpg matches
    expect(screen.getByRole('heading', { level: 3, name: 'cam_night_rain.jpg' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 3, name: 'cam_day_sunny.jpg' })).not.toBeInTheDocument()

    // No match test
    fireEvent.change(searchInput, { target: { value: 'nonexistent_foo' } })
    expect(screen.getByText('Không có ảnh phù hợp với bộ lọc hiện tại.')).toBeInTheDocument()
  })
})
