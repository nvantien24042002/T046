import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import {
  Coverage,
  evaluateTargets,
  getRecommendations,
  type TargetConfig,
  type EvaluatedSlice,
} from '../reference/Coverage'
import { VerifiedResultsProvider, useVerifiedResults } from '../context/VerifiedResultsContext'
import { config } from '../config'
import { type VerifiedResultDetail } from '../api/verifiedResults'
import { useEffect, useRef } from 'react'

const sampleDescriptor = {
  result_id: 'res_cov_001',
  dataset_id: 'ds_test_coverage',
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
    dataset_id: 'ds_test_coverage',
    quality_rows: [],
    audit_rows: [
      {
        image_id: 'img1',
        image_name: 'im1.jpg',
        box_id: 'b1',
        label: 'car',
        occluded: '0',
        xtl: 10,
        ytl: 10,
        xbr: 50,
        ybr: 50,
        image_available: true,
        image_error: null,
        area_pct: 5,
        aspect_ratio: 1.2,
        edge_density: 0.5,
        texture_variance: 10,
        laplacian_variance: 20,
        foreground_contrast: 0.8,
        boundary_support: 0.9,
        geometry_issues: '',
        visual_issues: '',
        issues: '',
        issue_count: 0,
        priority: 'LOW',
        status: 'A_ONLY',
      },
      {
        image_id: 'img2',
        image_name: 'im2.jpg',
        box_id: 'b2',
        label: 'bus',
        occluded: '1',
        xtl: 10,
        ytl: 10,
        xbr: 80,
        ybr: 80,
        image_available: true,
        image_error: null,
        area_pct: 15,
        aspect_ratio: 1.5,
        edge_density: 0.6,
        texture_variance: 12,
        laplacian_variance: 25,
        foreground_contrast: 0.85,
        boundary_support: 0.95,
        geometry_issues: '',
        visual_issues: '',
        issues: '',
        issue_count: 0,
        priority: 'LOW',
        status: 'A_ONLY',
      },
    ],
    classes: [
      { class: 'car', boxes: 1, images: 1, share_pct: 50 },
      { class: 'bus', boxes: 1, images: 1, share_pct: 50 },
    ],
    attribute_distribution: [],
    distribution_images: [
      { image_id: 'img1', image_name: 'im1.jpg', weather: 'sunny', timeofday: 'day' },
      { image_id: 'img2', image_name: 'im2.jpg', weather: 'unknown', timeofday: 'night' },
    ],
    distribution_boxes: [
      {
        image_id: 'img1',
        image_name: 'im1.jpg',
        box_id: 'b1',
        label: 'car',
        occluded: '0',
        valid_geometry: true,
        area_pct: 5,
        attributes: {},
      },
      {
        image_id: 'img2',
        image_name: 'im2.jpg',
        box_id: 'b2',
        label: 'bus',
        occluded: '1',
        valid_geometry: true,
        area_pct: 15,
        attributes: {},
      },
    ],
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

describe('Coverage & Slice unit calculations', () => {
  const dummyUniverse = [
    { image_id: '1', box_id: 'b1', class: 'car', weather: 'sunny', timeofday: 'day', occluded: 'no' },
    { image_id: '2', box_id: 'b2', class: 'car', weather: 'sunny', timeofday: 'day', occluded: 'no' },
    { image_id: '2', box_id: 'b3', class: 'pedestrian', weather: 'sunny', timeofday: 'day', occluded: 'yes' },
  ]
  const dummyImages = [
    { image_id: '1', image_name: '1.jpg', weather: 'sunny', timeofday: 'day' },
    { image_id: '2', image_name: '2.jpg', weather: 'sunny', timeofday: 'day' },
    { image_id: '3', image_name: '3.jpg', weather: 'unknown', timeofday: 'missing' },
  ]

  it('calculates support by images and objects correctly', () => {
    const targetImages: TargetConfig[] = [
      { filters: { class: 'car' }, unit: 'images', target: 5, weight: 1.0 },
      { filters: { class: 'car' }, unit: 'objects', target: 5, weight: 2.0 },
    ]

    const evalRes = evaluateTargets(dummyUniverse, dummyImages, targetImages)
    expect(evalRes).toHaveLength(2)

    // Images unit: distinct image_ids = 2 (id 1 and id 2)
    const imgSlice = evalRes[0]!
    expect(imgSlice.support).toBe(2)
    expect(imgSlice.target).toBe(5)
    expect(imgSlice.gap).toBe(3)
    expect(imgSlice.attainment_pct).toBe(40.0)
    expect(imgSlice.weighted_gap).toBe(0.6)
    expect(imgSlice.status).toBe('Thiếu')

    // Objects unit: total boxes = 2
    const objSlice = evalRes[1]!
    expect(objSlice.support).toBe(2)
    expect(objSlice.target).toBe(5)
    expect(objSlice.gap).toBe(3)
    expect(objSlice.weighted_gap).toBe(1.2)
  })

  it('flags metadata_unresolved and sets confidence to Tạm thời', () => {
    const targets: TargetConfig[] = [
      { filters: { class: 'car', weather: 'sunny' }, unit: 'images', target: 10, weight: 1.0 },
    ]
    const evalRes = evaluateTargets(dummyUniverse, dummyImages, targets)
    expect(evalRes[0]!.metadata_unresolved).toBe(1) // image 3 has 'unknown'
    expect(evalRes[0]!.confidence).toBe('Tạm thời')
  })

  it('sorts recommendations by weighted_gap descending', () => {
    const evaluated: EvaluatedSlice[] = [
      {
        slice: 'slice-A',
        filters: '{}',
        unit: 'images',
        support: 10,
        target: 20,
        gap: 10,
        attainment_pct: 50,
        weight: 1,
        weighted_gap: 0.5,
        status: 'Thiếu',
        metadata_unresolved: 0,
        confidence: 'Quan sát',
      },
      {
        slice: 'slice-B',
        filters: '{}',
        unit: 'images',
        support: 10,
        target: 20,
        gap: 10,
        attainment_pct: 50,
        weight: 2,
        weighted_gap: 1.0,
        status: 'Thiếu',
        metadata_unresolved: 2,
        confidence: 'Tạm thời',
      },
    ]

    const recs = getRecommendations(evaluated)
    expect(recs[0]!.slice).toBe('slice-B') // higher weighted_gap
    expect(recs[0]!.recommendation).toContain('Kiểm tra và bổ sung metadata')
    expect(recs[1]!.slice).toBe('slice-A')
    expect(recs[1]!.recommendation).toContain('Ưu tiên bổ sung/kiểm tra annotation')
  })
})

describe('Coverage page UI', () => {
  const mutableConfig = config as { mode: 'mock' | 'backend'; analyticsBaseUrl: string; baseUrl: string }
  let originalMode: typeof config.mode

  beforeEach(() => {
    originalMode = config.mode
  })

  afterEach(() => {
    mutableConfig.mode = originalMode
  })

  it('renders mock baseline data and allows adding a single slice', async () => {
    mutableConfig.mode = 'mock'
    render(
      <MemoryRouter>
        <VerifiedResultsProvider>
          <Coverage />
        </VerifiedResultsProvider>
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { level: 1, name: 'Coverage & Slice' })).toBeInTheDocument()
    expect(screen.getByText(/Chưa triển khai trong M1/)).toBeInTheDocument()
    expect(screen.getByText(/1. Phân bố quan sát theo class/)).toBeInTheDocument()

    // Add slice
    const addBtn = screen.getByRole('button', { name: 'Thêm slice' })
    fireEvent.click(addBtn)

    expect(screen.getByText(/3. Chỉnh mục tiêu theo từng slice/)).toBeInTheDocument()
    expect(screen.getByText(/4. So sánh thực tế với mục tiêu/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Tải coverage CSV' })).toBeInTheDocument()
  })

  it('renders backend mode empty state when unauthenticated', () => {
    mutableConfig.mode = 'backend'
    render(
      <MemoryRouter>
        <VerifiedResultsProvider>
          <Coverage />
        </VerifiedResultsProvider>
      </MemoryRouter>,
    )

    expect(screen.getByText(/Chưa có kết quả phân tích/)).toBeInTheDocument()
  })

  it('renders verified dataset data in backend mode', () => {
    mutableConfig.mode = 'backend'
    render(
      <MemoryRouter>
        <VerifiedResultsProvider>
          <Initializer detail={sampleDetail} token="tok123">
            <Coverage />
          </Initializer>
        </VerifiedResultsProvider>
      </MemoryRouter>,
    )

    expect(screen.getByText('Tỷ lệ quan sát theo từng class (2 class)')).toBeInTheDocument()
    expect(screen.getAllByText('car').length).toBeGreaterThan(0)
    expect(screen.getAllByText('bus').length).toBeGreaterThan(0)
  })
})
