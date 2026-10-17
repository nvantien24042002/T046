import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import {
  validateFile,
  validateOwnerKey,
  uploadDatasetWorkflow,
  formatBytes,
  MAX_UPLOAD_BYTES,
} from '../api/upload'
import { DatasetUploader } from '../components/DatasetUploader'
import { VerifiedResultsProvider } from '../context/VerifiedResultsContext'

describe('Upload unit validation functions', () => {
  it('formats byte numbers accurately', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1024)).toBe('1.00 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.00 MB')
    expect(formatBytes(2.5 * 1024 * 1024 * 1024)).toBe('2.50 GB')
  })

  it('validates CVAT ZIP files properly', () => {
    const validFile = new File(['dummy-content'], 'dataset.zip', { type: 'application/zip' })
    const result = validateFile(validFile)
    expect(result.valid).toBe(true)
    expect(result.name).toBe('dataset.zip')
    expect(result.format).toContain('CVAT Dataset ZIP')
  })

  it('rejects unsupported file formats', () => {
    const txtFile = new File(['dummy-content'], 'data.txt', { type: 'text/plain' })
    const result = validateFile(txtFile)
    expect(result.valid).toBe(false)
    expect(result.error).toContain('Định dạng tệp không được hỗ trợ')

    const tarFile = new File(['dummy-content'], 'archive.tar.gz', { type: 'application/gzip' })
    expect(validateFile(tarFile).valid).toBe(false)
  })

  it('rejects empty files (0 bytes)', () => {
    const emptyFile = new File([], 'empty.zip', { type: 'application/zip' })
    const result = validateFile(emptyFile)
    expect(result.valid).toBe(false)
    expect(result.error).toContain('Tệp rỗng (0 bytes)')
  })

  it('rejects oversized files exceeding 50 GB limit', () => {
    const fakeBigFile = {
      name: 'huge.zip',
      size: MAX_UPLOAD_BYTES + 1,
    } as unknown as File
    const result = validateFile(fakeBigFile)
    expect(result.valid).toBe(false)
    expect(result.error).toContain('vượt quá giới hạn tối đa')
  })

  it('validates owner keys against security length rules', () => {
    expect(validateOwnerKey('').valid).toBe(false)
    expect(validateOwnerKey('short-key').valid).toBe(false)
    expect(validateOwnerKey('short-key').error).toContain('tối thiểu 32 ký tự')

    const validKey = 'a'.repeat(32)
    expect(validateOwnerKey(validKey).valid).toBe(true)
  })
})

describe('uploadDatasetWorkflow API orchestrator', () => {
  const originalFetch = global.fetch
  const validKey = '0123456789abcdef0123456789abcdef'

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    global.fetch = originalFetch
  })

  it('completes the entire upload, validation, and analysis pipeline', async () => {
    const file = new File(['chunk-content-for-testing'], 'test_dataset.zip', { type: 'application/zip' })
    const onProgress = vi.fn()

    const mockFetch = vi.fn<typeof fetch>()
      // 1. POST /api/v1/uploads
      .mockResolvedValueOnce(new Response(JSON.stringify({
        upload_id: 'up-123',
        upload_capability: 'cap-secret-123',
        chunk_size_bytes: 8 * 1024 * 1024,
        received_bytes: 0,
      }), { status: 201 }))
      // 2. PUT chunk
      .mockResolvedValueOnce(new Response(JSON.stringify({ received_bytes: file.size, durable: true }), { status: 200 }))
      // 3. POST /complete
      .mockResolvedValueOnce(new Response(JSON.stringify({ state: 'sealed' }), { status: 200 }))
      // 4. POST /validate
      .mockResolvedValueOnce(new Response(JSON.stringify({ state: 'validating' }), { status: 200 }))
      // 5. GET /dataset status -> ready
      .mockResolvedValueOnce(new Response(JSON.stringify({
        upload_id: 'up-123',
        state: 'ready',
        ready: true,
        dataset_id: 'ds-test-456',
      }), { status: 200 }))
      // 6. POST /analyze
      .mockResolvedValueOnce(new Response(JSON.stringify({
        dataset_id: 'ds-test-456',
        request_fingerprint: 'a'.repeat(64),
        quality_rows: [{
          filename: 'img1.jpg',
          status: 'PASS',
          score: 95,
          issues: [],
          width: 640,
          height: 480,
          size_kb: 45.2,
          blur_score: 120,
          brightness: 110,
          contrast: 35,
          noise_score: 5,
          black_pct: 0.1,
          white_pct: 0.2,
          error: '',
        }],
        audit_rows: [{
          image_id: '1',
          image_name: 'img1.jpg',
          box_id: 'b1',
          label: 'car',
          occluded: '0',
          xtl: 10, ytl: 10, xbr: 50, ybr: 50,
          image_available: true,
          image_error: null,
          area_pct: 1.2,
          aspect_ratio: 1.0,
          edge_density: 0.1,
          texture_variance: 50,
          laplacian_variance: 60,
          foreground_contrast: 20,
          boundary_support: 0.8,
          geometry_issues: '',
          visual_issues: '',
          issues: '',
          issue_count: 0,
          priority: 'LOW',
          status: 'NO_SIGNAL',
        }],
        classes: [{ class: 'car', boxes: 1, images: 1, share_pct: 100 }],
        attribute_distribution: [],
        distribution_images: [{ image_id: '1', image_name: 'img1.jpg', weather: 'sunny', timeofday: 'day' }],
        distribution_boxes: [{
          image_id: '1',
          image_name: 'img1.jpg',
          box_id: 'b1',
          label: 'car',
          occluded: '0',
          valid_geometry: true,
          area_pct: 1.2,
          attributes: {},
        }],
        attributes: [],
      }), { status: 200 }))
      // 7. GET /summary
      .mockResolvedValueOnce(new Response(JSON.stringify({
        dataset_id: 'ds-test-456',
        upload_id: 'up-123', state: 'ready', ready: true,
        archive_sha256: 'b'.repeat(64),
        archive_bytes: file.size,
        entries: 2,
        images: 1,
        annotations: true,
        capabilities: {
          image_quality: true,
          label_audit: true,
          distribution: true,
          coverage: true,
          scene_finder: true,
        },
      }), { status: 200 }))

    vi.stubGlobal('fetch', mockFetch)

    const outcome = await uploadDatasetWorkflow({
      file,
      ownerKey: validKey,
      baseUrl: 'http://127.0.0.1:8011',
      onProgress,
    })

    expect(outcome.datasetId).toBe('ds-test-456')
    expect(outcome.uploadId).toBe('up-123')
    expect(outcome.capability).toBe('cap-secret-123')
    expect(outcome.detail.payload.quality_rows.length).toBe(1)
    expect(outcome.detail.payload.classes[0]?.class).toBe('car')
    expect(outcome.summary.ready).toBe(true)
    if (!outcome.summary.ready) throw new Error('Expected a ready dataset summary')
    expect(outcome.summary.images).toBe(1)
    expect(onProgress).toHaveBeenCalledWith(expect.objectContaining({ phase: 'ready', percent: 100 }))
  })

  it('surfaces backend errors clearly on access denial', async () => {
    const file = new File(['content'], 'test.zip', { type: 'application/zip' })
    const mockFetch = vi.fn<typeof fetch>().mockResolvedValueOnce(
      new Response(JSON.stringify({ error: { code: 'ACCESS_DENIED' } }), { status: 403 })
    )
    vi.stubGlobal('fetch', mockFetch)

    await expect(uploadDatasetWorkflow({
      file,
      ownerKey: validKey,
      baseUrl: 'http://127.0.0.1:8011',
      onProgress: vi.fn(),
    })).rejects.toThrow('Owner Key không chính xác hoặc không có quyền tải lên.')
  })

  it('surfaces corrupted archive validation errors', async () => {
    const file = new File(['bad-content'], 'corrupt.zip', { type: 'application/zip' })
    const mockFetch = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        upload_id: 'up-1', upload_capability: 'cap-1', chunk_size_bytes: 8388608, received_bytes: 0,
      }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ received_bytes: file.size }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ state: 'sealed' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ state: 'validating' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        upload_id: 'up-1', dataset_id: 'failed-dataset', ready: false,
        state: 'failed',
        error_code: 'CORRUPTED_ARCHIVE',
      }), { status: 200 }))

    vi.stubGlobal('fetch', mockFetch)

    await expect(uploadDatasetWorkflow({
      file,
      ownerKey: validKey,
      baseUrl: 'http://127.0.0.1:8011',
      onProgress: vi.fn(),
    })).rejects.toThrow('Tệp ZIP bị hỏng hoặc thiếu dữ liệu.')
  })
})

describe('DatasetUploader UI Component', () => {
  it('renders upload area, drag-and-drop zone and owner key input', () => {
    render(
      <MemoryRouter>
        <VerifiedResultsProvider>
          <DatasetUploader />
        </VerifiedResultsProvider>
      </MemoryRouter>
    )

    expect(screen.getByRole('heading', { name: 'Tải lên & Khởi tạo Dataset CVAT' })).toBeVisible()
    expect(screen.getByLabelText(/Owner Key/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Bắt đầu tải lên & Phân tích' })).toBeDisabled()
  })

  function selectFile(input: HTMLInputElement, file: File) {
    Object.defineProperty(input, 'files', {
      value: [file],
      configurable: true,
    })
    fireEvent.change(input)
  }

  it('displays file metadata when a ZIP file is selected', async () => {
    const { container } = render(
      <MemoryRouter>
        <VerifiedResultsProvider>
          <DatasetUploader />
        </VerifiedResultsProvider>
      </MemoryRouter>
    )

    const file = new File(['zip-binary-bytes'], 'annotations_dataset.zip', { type: 'application/zip' })
    const fileInput = container.querySelector('#cvat-file-input') as HTMLInputElement
    expect(fileInput).not.toBeNull()
    selectFile(fileInput, file)

    expect(await screen.findByText('annotations_dataset.zip')).toBeVisible()
    expect(screen.getByText('✓ Hợp lệ')).toBeVisible()
    expect(screen.getByText('CVAT Dataset ZIP Archive (.zip)')).toBeVisible()
  })

  it('shows error banner when an invalid non-ZIP file is selected', async () => {
    const { container } = render(
      <MemoryRouter>
        <VerifiedResultsProvider>
          <DatasetUploader />
        </VerifiedResultsProvider>
      </MemoryRouter>
    )

    const invalidFile = new File(['text'], 'document.pdf', { type: 'application/pdf' })
    const fileInput = container.querySelector('#cvat-file-input') as HTMLInputElement
    selectFile(fileInput, invalidFile)

    expect(await screen.findByText(/Định dạng tệp không được hỗ trợ/)).toBeVisible()
  })

  it('warns about mock mode when trying to upload in mock mode', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <MemoryRouter>
        <VerifiedResultsProvider>
          <DatasetUploader />
        </VerifiedResultsProvider>
      </MemoryRouter>
    )

    const file = new File(['dummy'], 'sample.zip', { type: 'application/zip' })
    const fileInput = container.querySelector('#cvat-file-input') as HTMLInputElement
    selectFile(fileInput, file)

    const uploadBtn = await screen.findByRole('button', { name: 'Bắt đầu tải lên & Phân tích' })
    expect(uploadBtn).toBeEnabled()
    await user.click(uploadBtn)

    expect(await screen.findByText(/Đang ở chế độ Mock mode/)).toBeVisible()
  })

  it('runs live upload in backend mode and displays success summary card', async () => {
    const user = userEvent.setup()
    const validKey = '0123456789abcdef0123456789abcdef'

    const file = new File(['zip-bytes-test'], 'imported_cvat.zip', { type: 'application/zip' })
    const mockFetch = vi.fn<typeof fetch>()
      // 1. POST /uploads
      .mockResolvedValueOnce(new Response(JSON.stringify({
        upload_id: 'up-real-1',
        upload_capability: 'cap-real-1',
        chunk_size_bytes: 8 * 1024 * 1024,
        received_bytes: 0,
      }), { status: 201 }))
      // 2. PUT chunk
      .mockResolvedValueOnce(new Response(JSON.stringify({ received_bytes: file.size, durable: true }), { status: 200 }))
      // 3. POST /complete
      .mockResolvedValueOnce(new Response(JSON.stringify({ state: 'sealed' }), { status: 200 }))
      // 4. POST /validate
      .mockResolvedValueOnce(new Response(JSON.stringify({ state: 'validating' }), { status: 200 }))
      // 5. GET /dataset status
      .mockResolvedValueOnce(new Response(JSON.stringify({
        upload_id: 'up-real-1',
        state: 'ready',
        ready: true,
        dataset_id: 'ds-imported-999',
      }), { status: 200 }))
      // 6. POST /analyze
      .mockResolvedValueOnce(new Response(JSON.stringify({
        dataset_id: 'ds-imported-999',
        request_fingerprint: 'c'.repeat(64),
        quality_rows: [{
          filename: 'imported_img.jpg',
          status: 'PASS',
          score: 99,
          issues: [],
          width: 1920,
          height: 1080,
          size_kb: 120.5,
          blur_score: 150,
          brightness: 128,
          contrast: 40,
          noise_score: 2,
          black_pct: 0,
          white_pct: 0,
          error: '',
        }],
        audit_rows: [{
          image_id: '1',
          image_name: 'imported_img.jpg',
          box_id: 'b1',
          label: 'traffic_sign',
          occluded: '0',
          xtl: 100, ytl: 100, xbr: 200, ybr: 200,
          image_available: true,
          image_error: null,
          area_pct: 2.5,
          aspect_ratio: 1.0,
          edge_density: 0.2,
          texture_variance: 60,
          laplacian_variance: 70,
          foreground_contrast: 25,
          boundary_support: 0.9,
          geometry_issues: '',
          visual_issues: '',
          issues: '',
          issue_count: 0,
          priority: 'LOW',
          status: 'NO_SIGNAL',
        }],
        classes: [{ class: 'traffic_sign', boxes: 1, images: 1, share_pct: 100 }],
        attribute_distribution: [],
        distribution_images: [{ image_id: '1', image_name: 'imported_img.jpg', weather: 'clear', timeofday: 'noon' }],
        distribution_boxes: [{
          image_id: '1',
          image_name: 'imported_img.jpg',
          box_id: 'b1',
          label: 'traffic_sign',
          occluded: '0',
          valid_geometry: true,
          area_pct: 2.5,
          attributes: {},
        }],
        attributes: [],
      }), { status: 200 }))
      // 7. GET /summary
      .mockResolvedValueOnce(new Response(JSON.stringify({
        dataset_id: 'ds-imported-999',
        upload_id: 'up-real-1', state: 'ready', ready: true,
        archive_sha256: 'd'.repeat(64),
        archive_bytes: file.size,
        entries: 2,
        images: 1,
        annotations: true,
        capabilities: {
          image_quality: true,
          label_audit: true,
          distribution: true,
          coverage: true,
          scene_finder: true,
        },
      }), { status: 200 }))

    vi.stubGlobal('fetch', mockFetch)

    // Render with config mode mocked as backend for this test
    const { config } = await import('../config')
    const originalMode = config.mode
    // @ts-expect-error mutating readonly for test
    config.mode = 'backend'

    try {
      const { container } = render(
        <MemoryRouter>
          <VerifiedResultsProvider>
            <DatasetUploader />
          </VerifiedResultsProvider>
        </MemoryRouter>
      )

      const fileInput = container.querySelector('#cvat-file-input') as HTMLInputElement
      selectFile(fileInput, file)

      const keyInput = screen.getByLabelText(/Owner Key/i)
      await user.type(keyInput, validKey)

      const uploadBtn = await screen.findByRole('button', { name: 'Bắt đầu tải lên & Phân tích' })
      expect(uploadBtn).toBeEnabled()
      await user.click(uploadBtn)

      // Expect success card
      expect(await screen.findByText('✓ Tải lên và nạp dữ liệu thành công!')).toBeVisible()
      expect(screen.getByText('ID: ds-imported-999')).toBeVisible()
      expect(screen.getByText('Có (CVAT XML)')).toBeVisible()
      expect(screen.getByRole('link', { name: /Tổng quan dữ liệu/i })).toBeVisible()
      expect(screen.getByRole('link', { name: /Image Quality/i })).toBeVisible()
      expect(screen.getByRole('link', { name: /Label Check/i })).toBeVisible()
      expect(screen.getByRole('link', { name: /Scene Finder/i })).toBeVisible()
    } finally {
      // @ts-expect-error restoring
      config.mode = originalMode
    }
  })
})
