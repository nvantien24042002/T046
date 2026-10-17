import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { useEffect, useRef } from 'react'
import { ImagePreview } from '../components/ImagePreview'
import { VerifiedResultsProvider, useVerifiedResults } from '../context/VerifiedResultsContext'
import { config } from '../config'
import { type VerifiedResultsTransport, VerifiedResultError, type VerifiedResultDetail } from '../api/verifiedResults'

const sampleDescriptor = {
  result_id: 'res_001',
  dataset_id: 'ds_test_preview',
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
    dataset_id: 'ds_test_preview',
    quality_rows: [],
    audit_rows: [],
    classes: [],
    attribute_distribution: [],
    distribution_images: [],
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

const mutableConfig = config as { mode: 'mock' | 'backend'; analyticsBaseUrl: string; baseUrl: string }

describe('ImagePreview component', () => {
  let originalMode: typeof mutableConfig.mode

  beforeEach(() => {
    originalMode = mutableConfig.mode
    window.URL.createObjectURL = vi.fn().mockReturnValue('blob:http://localhost:5173/synthetic-123')
    window.URL.revokeObjectURL = vi.fn()
  })

  afterEach(() => {
    mutableConfig.mode = originalMode
    vi.restoreAllMocks()
  })

  it('renders placeholder in mock mode without fetching', () => {
    mutableConfig.mode = 'mock'
    render(
      <VerifiedResultsProvider>
        <ImagePreview imagePath="img_01.png" alt="Test image" />
      </VerifiedResultsProvider>,
    )

    expect(screen.getByText(/Preview ảnh yêu cầu Backend mode/)).toBeInTheDocument()
    expect(window.URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('renders placeholder when backend mode has no active dataset or token', () => {
    mutableConfig.mode = 'backend'
    render(
      <VerifiedResultsProvider>
        <ImagePreview imagePath="img_01.png" alt="Test image" />
      </VerifiedResultsProvider>,
    )

    expect(
      screen.getByText(/Chưa có phiên dataset được ủy quyền để xem trước ảnh/),
    ).toBeInTheDocument()
    expect(window.URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('renders loaded image blob and caption when authorized in backend mode', async () => {
    mutableConfig.mode = 'backend'
    const syntheticBlob = new Blob(['PNG_DATA'], { type: 'image/png' })
    const fetchPreviewMock = vi.fn().mockResolvedValue(syntheticBlob)

    const fakeTransport: VerifiedResultsTransport = {
      listResults: vi.fn(),
      readResult: vi.fn(),
      fetchPreview: fetchPreviewMock,
    }

    render(
      <VerifiedResultsProvider transport={fakeTransport}>
        <Initializer detail={sampleDetail} token="grant_tok_abc">
          <ImagePreview
            imagePath="frame_001.png"
            annotated={false}
            alt="Frame 1 preview"
            caption="Ảnh gốc"
          />
        </Initializer>
      </VerifiedResultsProvider>,
    )

    await waitFor(() => {
      expect(fetchPreviewMock).toHaveBeenCalledWith('ds_test_preview', 'frame_001.png', false, 'grant_tok_abc', expect.any(AbortSignal))
      const img = screen.getByRole('img', { name: 'Frame 1 preview' })
      expect(img).toBeInTheDocument()
      expect(img).toHaveAttribute('src', 'blob:http://localhost:5173/synthetic-123')
      expect(screen.getByText('Ảnh gốc')).toBeInTheDocument()
    })
  })

  it('calls fetchPreview with annotated=true for labeled previews', async () => {
    mutableConfig.mode = 'backend'
    const syntheticBlob = new Blob(['PNG_DATA'], { type: 'image/png' })
    const fetchPreviewMock = vi.fn().mockResolvedValue(syntheticBlob)

    const fakeTransport: VerifiedResultsTransport = {
      listResults: vi.fn(),
      readResult: vi.fn(),
      fetchPreview: fetchPreviewMock,
    }

    render(
      <VerifiedResultsProvider transport={fakeTransport}>
        <Initializer detail={sampleDetail} token="grant_tok_abc">
          <ImagePreview
            imagePath="frame_002.png"
            annotated={true}
            alt="Annotated box preview"
            caption="Đỏ: HIGH — Cam: MEDIUM — Xanh: LOW"
          />
        </Initializer>
      </VerifiedResultsProvider>,
    )

    await waitFor(() => {
      expect(fetchPreviewMock).toHaveBeenCalledWith('ds_test_preview', 'frame_002.png', true, 'grant_tok_abc', expect.any(AbortSignal))
      expect(screen.getByText('Đỏ: HIGH — Cam: MEDIUM — Xanh: LOW')).toBeInTheDocument()
    })
  })

  it('displays 404 image not found error message', async () => {
    mutableConfig.mode = 'backend'
    const fetchPreviewMock = vi.fn().mockRejectedValue(new VerifiedResultError('http', 404, 'IMAGE_NOT_FOUND'))

    const fakeTransport: VerifiedResultsTransport = {
      listResults: vi.fn(),
      readResult: vi.fn(),
      fetchPreview: fetchPreviewMock,
    }

    render(
      <VerifiedResultsProvider transport={fakeTransport}>
        <Initializer detail={sampleDetail} token="grant_tok_abc">
          <ImagePreview imagePath="missing.png" alt="Missing" />
        </Initializer>
      </VerifiedResultsProvider>,
    )

    await waitFor(() => {
      expect(screen.getByText(/Không tìm thấy ảnh xem trước \(404\)/)).toBeInTheDocument()
    })
  })

  it('displays 422 invalid path error message', async () => {
    mutableConfig.mode = 'backend'
    const fetchPreviewMock = vi.fn().mockRejectedValue(new VerifiedResultError('http', 422, 'INVALID_PATH'))

    const fakeTransport: VerifiedResultsTransport = {
      listResults: vi.fn(),
      readResult: vi.fn(),
      fetchPreview: fetchPreviewMock,
    }

    render(
      <VerifiedResultsProvider transport={fakeTransport}>
        <Initializer detail={sampleDetail} token="grant_tok_abc">
          <ImagePreview imagePath="../secret.png" alt="Bad path" />
        </Initializer>
      </VerifiedResultsProvider>,
    )

    await waitFor(() => {
      expect(screen.getByText(/Đường dẫn ảnh không hợp lệ \(422\)/)).toBeInTheDocument()
    })
  })

  it('displays size limit warning when image > 50 MB', async () => {
    mutableConfig.mode = 'backend'
    const fetchPreviewMock = vi.fn().mockRejectedValue(new VerifiedResultError('http', 413, 'IMAGE_SIZE_LIMIT'))

    const fakeTransport: VerifiedResultsTransport = {
      listResults: vi.fn(),
      readResult: vi.fn(),
      fetchPreview: fetchPreviewMock,
    }

    render(
      <VerifiedResultsProvider transport={fakeTransport}>
        <Initializer detail={sampleDetail} token="grant_tok_abc">
          <ImagePreview imagePath="huge.png" alt="Huge" />
        </Initializer>
      </VerifiedResultsProvider>,
    )

    await waitFor(() => {
      expect(screen.getByText(/Ảnh lớn hơn 50 MB, không preview/)).toBeInTheDocument()
    })
  })

  it('displays decode failure message for corrupted or unsupported images', async () => {
    mutableConfig.mode = 'backend'
    const fetchPreviewMock = vi.fn().mockRejectedValue(new VerifiedResultError('http', 415, 'IMAGE_DECODE_FAILED'))

    const fakeTransport: VerifiedResultsTransport = {
      listResults: vi.fn(),
      readResult: vi.fn(),
      fetchPreview: fetchPreviewMock,
    }

    render(
      <VerifiedResultsProvider transport={fakeTransport}>
        <Initializer detail={sampleDetail} token="grant_tok_abc">
          <ImagePreview imagePath="corrupt.xyz" alt="Corrupt" />
        </Initializer>
      </VerifiedResultsProvider>,
    )

    await waitFor(() => {
      expect(screen.getByText(/Không đọc được ảnh: Định dạng không hỗ trợ/)).toBeInTheDocument()
    })
  })

  it('cleans up objectUrl on unmount', async () => {
    mutableConfig.mode = 'backend'
    const syntheticBlob = new Blob(['PNG_DATA'], { type: 'image/png' })
    const fetchPreviewMock = vi.fn().mockResolvedValue(syntheticBlob)

    const fakeTransport: VerifiedResultsTransport = {
      listResults: vi.fn(),
      readResult: vi.fn(),
      fetchPreview: fetchPreviewMock,
    }

    const { unmount } = render(
      <VerifiedResultsProvider transport={fakeTransport}>
        <Initializer detail={sampleDetail} token="grant_tok_abc">
          <ImagePreview imagePath="frame_cleanup.png" alt="Cleanup test" />
        </Initializer>
      </VerifiedResultsProvider>,
    )

    await waitFor(() => {
      expect(screen.getByRole('img', { name: 'Cleanup test' })).toBeInTheDocument()
    })

    unmount()
    expect(window.URL.revokeObjectURL).toHaveBeenCalledWith('blob:http://localhost:5173/synthetic-123')
  })
})
