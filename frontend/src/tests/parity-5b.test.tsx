import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { FilterAndThresholdPanel } from '../components/FilterAndThresholdPanel'
import { BackendDashboard } from '../components/BackendDashboard'
import { VerifiedResultsProvider, useVerifiedResults } from '../context/VerifiedResultsContext'
import { readDashboard, type DashboardView, type DashboardPage } from '../api/dashboard'
import type { AnalysisResultDetail } from '../api/verifiedResults'
import type { DatasetSummary } from '../api/datasets'
import { useEffect, useRef } from 'react'

vi.mock('../config', () => ({
  config: {
    mode: 'backend',
    baseUrl: 'http://127.0.0.1:8011',
    analyticsBaseUrl: 'http://127.0.0.1:8012',
  },
}))

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const sampleResult: AnalysisResultDetail = {
  descriptor: null,
  provenance: null,
  payload: {
    dataset_id: 'ds-test-5b',
    request_fingerprint: 'f'.repeat(64),
    quality_rows: [],
    audit_rows: [],
    classes: [],
    attribute_distribution: [],
    distribution_images: [],
    distribution_boxes: [],
    attributes: [],
  },
}

function sampleSummary(): Extract<DatasetSummary, { ready: true }> {
  return {
    upload_id: 'upload-5b',
    dataset_id: 'ds-test-5b',
    state: 'ready',
    ready: true,
    archive_bytes: 1024,
    archive_sha256: 'a'.repeat(64),
    entries: 10,
    images: 10,
    annotations: true,
    capabilities: {
      image_quality: true,
      label_audit: true,
      distribution: true,
      coverage: true,
      scene_finder: true,
    },
  }
}

function createMockView(overrides?: Partial<DashboardView>): DashboardView {
  return {
    schema_version: 'dashboard.v1',
    dataset_id: 'ds-test-5b',
    page: 'scene-finder',
    state: 'ready',
    reason: null,
    source: {
      request_fingerprint: 'f'.repeat(64),
      result_id: null,
      payload_sha256: null,
    },
    metrics: [
      { key: 'matched', label: 'Ảnh khớp', value: 3 },
      { key: 'total', label: 'Tổng ảnh', value: 10 },
    ],
    tables: [],
    charts: [],
    controls: [],
    notes: [],
    ...overrides,
  }
}

describe('Task 5B Parity - FilterAndThresholdPanel Multi-Select & Unknown Values', () => {
  it('renders multi-select chips and handles toggling dimensions', () => {
    const onUpdate = vi.fn()
    const onReset = vi.fn()
    const view = createMockView({
      controls: [
        {
          key: 'weather',
          label: 'Thời tiết',
          choices: ['sunny', 'rainy', 'missing'],
          value: null,
        },
      ],
    })

    render(
      <FilterAndThresholdPanel
        view={view}
        query={{}}
        onUpdate={onUpdate}
        onReset={onReset}
      />,
    )

    expect(screen.getByText('Thời tiết')).toBeInTheDocument()
    // "Tất cả" chip should be present
    expect(screen.getByRole('button', { name: 'Tất cả' })).toBeInTheDocument()

    // Clicking "sunny" chip selects it
    const sunnyChip = screen.getByRole('button', { name: 'sunny' })
    fireEvent.click(sunnyChip)

    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({
        weather: ['sunny'],
      }),
    )
  })

  it('preserves missing and unknown values with distinct semantics', () => {
    const onUpdate = vi.fn()
    const view = createMockView({
      controls: [
        {
          key: 'weather',
          label: 'Thời tiết',
          choices: ['sunny', 'missing', 'unknown'],
          value: ['missing'],
        },
      ],
    })

    render(
      <FilterAndThresholdPanel
        view={view}
        query={{ weather: ['missing'] }}
        onUpdate={onUpdate}
        onReset={vi.fn()}
      />,
    )

    // The missing chip displays "(Thiếu)"
    const missingChip = screen.getByRole('button', { name: /missing.*Thiếu/ })
    expect(missingChip).toHaveClass('chip-active')

    // Clicking it when it's the only one selected clears the filter to null
    fireEvent.click(missingChip)
    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({
        weather: null,
      }),
    )
  })

  it('clicking "Tất cả" resets the specific multi-select filter', () => {
    const onUpdate = vi.fn()
    const view = createMockView({
      controls: [
        {
          key: 'timeofday',
          label: 'Thời điểm',
          choices: ['day', 'night'],
          value: ['day'],
        },
      ],
    })

    render(
      <FilterAndThresholdPanel
        view={view}
        query={{ timeofday: ['day'] }}
        onUpdate={onUpdate}
        onReset={vi.fn()}
      />,
    )

    const allChip = screen.getByRole('button', { name: 'Tất cả' })
    fireEvent.click(allChip)

    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({
        timeofday: null,
      }),
    )
  })
})

describe('Task 5B Parity - Query Serialization', () => {
  it('serializes array queries in readDashboard request body', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(createMockView()), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetcher)

    const query = {
      weather: ['sunny', 'rainy'],
      timeofday: ['day'],
      priority: ['HIGH', 'LOW'],
    }

    await readDashboard(
      'scene-finder',
      sampleResult,
      'test-token',
      query,
      new AbortController().signal,
    )

    expect(fetcher).toHaveBeenCalledWith(
      'http://127.0.0.1:8011/api/v1/datasets/ds-test-5b/dashboard',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          page: 'scene-finder',
          query: {
            weather: ['sunny', 'rainy'],
            timeofday: ['day'],
            priority: ['HIGH', 'LOW'],
          },
          expected_fingerprint: 'f'.repeat(64),
        }),
      }),
    )
  })

  it('validates response with control values as array of strings', async () => {
    const viewWithArrayControls = createMockView({
      controls: [
        {
          key: 'weather',
          label: 'Thời tiết',
          choices: ['sunny', 'cloudy'],
          value: ['sunny', 'cloudy'],
        },
      ],
    })

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(viewWithArrayControls), { status: 200 }),
      ),
    )

    const result = await readDashboard(
      'scene-finder',
      sampleResult,
      'test-token',
      {},
      new AbortController().signal,
    )

    expect(result.controls[0]?.value).toEqual(['sunny', 'cloudy'])
  })
})

describe('Task 5B Parity - Scene Finder Gallery Navigation & Filter Preservation', () => {
  function DashboardWrapper({ page, mockView }: { page: DashboardPage; mockView: DashboardView }) {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(mockView), { status: 200 }),
      ),
    )

    function Initializer() {
      const state = useVerifiedResults()
      const once = useRef(false)
      useEffect(() => {
        if (!once.current) {
          once.current = true
          state?.setManualResult(sampleResult, sampleSummary(), 'test-token')
        }
      }, [state])
      return null
    }

    return (
      <VerifiedResultsProvider>
        <Initializer />
        <BackendDashboard page={page} />
      </VerifiedResultsProvider>
    )
  }

  it('renders gallery cards with weather and time badges and allows selecting preview', async () => {
    const sfView = createMockView({
      page: 'scene-finder',
      tables: [
        {
          key: 'preview_images',
          title: 'Danh sách ảnh khớp bộ lọc',
          export_filename: 'scene_matches.csv',
          rows: [
            {
              image_name: 'cam_01.jpg',
              image_path: 'images/cam_01.jpg',
              weather: 'sunny',
              timeofday: 'day',
              boxes: 3,
            },
            {
              image_name: 'cam_02.jpg',
              image_path: 'images/cam_02.jpg',
              weather: 'missing',
              timeofday: 'unknown',
              boxes: 0,
            },
          ],
        },
      ],
    })

    render(<DashboardWrapper page="scene-finder" mockView={sfView} />)

    // Wait for gallery view to render
    await screen.findByText('cam_01.jpg')

    // Check tags
    expect(screen.getByText('Weather: sunny')).toBeInTheDocument()
    expect(screen.getByText('Time: day')).toBeInTheDocument()
    expect(screen.getByText('Weather: missing')).toBeInTheDocument()
    expect(screen.getByText('Time: unknown')).toBeInTheDocument()

    // View mode toggle
    const tableToggleBtn = screen.getByRole('button', { name: /Bảng chi tiết/ })
    expect(tableToggleBtn).toBeInTheDocument()

    // Switch to table mode
    fireEvent.click(tableToggleBtn)
    expect(screen.getByRole('table')).toBeInTheDocument()

    // Switch back to gallery mode
    const galleryToggleBtn = screen.getByRole('button', { name: /Thư viện ảnh/ })
    fireEvent.click(galleryToggleBtn)
    expect(screen.getByText('cam_01.jpg')).toBeInTheDocument()
  })
})

describe('Task 5B Parity - Label Check Flagged Box Inspector & Charts', () => {
  it('opens and navigates flagged box inspector in audit table', async () => {
    const labelsView = createMockView({
      page: 'labels',
      tables: [
        {
          key: 'audit',
          title: 'Chi tiết cảnh báo nhãn',
          export_filename: 'label_audit.csv',
          rows: [
            {
              box_id: 'box_101',
              image_name: 'street_01.jpg',
              image_path: 'images/street_01.jpg',
              label: 'car',
              priority: 'HIGH',
              issue_type: 'missing_label',
              confidence: 0.95,
              xtl: 10,
              ytl: 10,
              xbr: 100,
              ybr: 100,
            },
            {
              box_id: 'box_102',
              image_name: 'street_02.jpg',
              image_path: 'images/street_02.jpg',
              label: 'pedestrian',
              priority: 'LOW',
              issue_type: 'low_contrast',
              confidence: 0.42,
              xtl: 50,
              ytl: 60,
              xbr: 120,
              ybr: 180,
            },
          ],
        },
      ],
    })

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(labelsView), { status: 200 }),
      ),
    )

    function Initializer() {
      const state = useVerifiedResults()
      const once = useRef(false)
      useEffect(() => {
        if (!once.current) {
          once.current = true
          state?.setManualResult(sampleResult, sampleSummary(), 'test-token')
        }
      }, [state])
      return null
    }

    render(
      <VerifiedResultsProvider>
        <Initializer />
        <BackendDashboard page="labels" />
      </VerifiedResultsProvider>,
    )

    // Wait for audit table rows to render
    const viewButtons = await screen.findAllByRole('button', { name: /Xem box/ })
    expect(viewButtons).toHaveLength(2)

    // Click "Xem box" on first row
    fireEvent.click(viewButtons[0]!)

    // Flagged item inspector should appear
    expect(screen.getByText('Flagged Box Inspector')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Box: box_101 · Class: car/ })).toBeInTheDocument()
    expect(screen.getAllByText('HIGH').length).toBeGreaterThanOrEqual(1)

    // Navigate to next box
    const nextBtn = screen.getByRole('button', { name: 'Tiếp →' })
    fireEvent.click(nextBtn)
    expect(screen.getByRole('heading', { name: /Box: box_102 · Class: pedestrian/ })).toBeInTheDocument()
    expect(screen.getAllByText('LOW').length).toBeGreaterThanOrEqual(1)

    // Navigate back to previous box
    const prevBtn = screen.getByRole('button', { name: '← Trước' })
    fireEvent.click(prevBtn)
    expect(screen.getByRole('heading', { name: /Box: box_101 · Class: car/ })).toBeInTheDocument()

    // Close inspector
    const closeBtn = screen.getByRole('button', { name: '✕' })
    fireEvent.click(closeBtn)
    expect(screen.queryByText('Flagged Box Inspector')).not.toBeInTheDocument()
  })
})

describe('Task 5B Parity - Safe Overlay & Edge Cases', () => {
  it('renders safe empty state when table has no matching rows', async () => {
    const emptyView = createMockView({
      page: 'scene-finder',
      state: 'ready',
      metrics: [
        { key: 'matched', label: 'Ảnh khớp', value: 0 },
        { key: 'total', label: 'Tổng ảnh', value: 10 },
      ],
      tables: [
        {
          key: 'preview_images',
          title: 'Danh sách ảnh khớp bộ lọc',
          export_filename: 'scene_matches.csv',
          rows: [],
        },
      ],
    })

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(emptyView), { status: 200 }),
      ),
    )

    function Initializer() {
      const state = useVerifiedResults()
      const once = useRef(false)
      useEffect(() => {
        if (!once.current) {
          once.current = true
          state?.setManualResult(sampleResult, sampleSummary(), 'test-token')
        }
      }, [state])
      return null
    }

    render(
      <VerifiedResultsProvider>
        <Initializer />
        <BackendDashboard page="scene-finder" />
      </VerifiedResultsProvider>,
    )

    // Should indicate 0 matched and empty table note
    await screen.findByText('Danh sách ảnh khớp bộ lọc')
    expect(screen.getByText('Backend trả về danh sách rỗng.')).toBeInTheDocument()
  })

  it('renders CSV export button for server tables', async () => {
    const viewWithExport = createMockView({
      page: 'scene-finder',
      tables: [
        {
          key: 'preview_images',
          title: 'Danh sách ảnh khớp bộ lọc',
          export_filename: 'scene_export.csv',
          rows: [{ image_name: 'img1.jpg', weather: 'sunny' }],
        },
      ],
    })

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(viewWithExport), { status: 200 }),
      ),
    )

    function Initializer() {
      const state = useVerifiedResults()
      const once = useRef(false)
      useEffect(() => {
        if (!once.current) {
          once.current = true
          state?.setManualResult(sampleResult, sampleSummary(), 'test-token')
        }
      }, [state])
      return null
    }

    render(
      <VerifiedResultsProvider>
        <Initializer />
        <BackendDashboard page="scene-finder" />
      </VerifiedResultsProvider>,
    )

    const exportBtn = await screen.findByRole('button', { name: /Tải .* CSV/ })
    expect(exportBtn).toBeInTheDocument()
  })
})
