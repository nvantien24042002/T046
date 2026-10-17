import { z } from 'zod'
import {
  type AnalysisResultDetail,
  type VerifiedPayload,
  payloadSchema,
} from './verifiedResults'
import { decodeDatasetSummary, decodeLifecycle, type DatasetSummary } from './datasets'

export type UploadPhase =
  | 'idle'
  | 'validating_file'
  | 'creating_session'
  | 'uploading'
  | 'completing'
  | 'validating_dataset'
  | 'analyzing'
  | 'ready'
  | 'error'

export interface UploadProgress {
  phase: UploadPhase
  message: string
  percent: number
  transferredBytes: number
  totalBytes: number
  chunkIndex?: number
  totalChunks?: number
}

export interface FileMetadata {
  name: string
  size: number
  formattedSize: string
  format: string
  valid: boolean
  error?: string
}

export interface UploadResult {
  datasetId: string
  uploadId: string
  capability: string
  detail: AnalysisResultDetail
  summary: DatasetSummary
}

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024 * 1024 // 50 GB
export const DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024 // 8 MiB

export function formatBytes(bytes: number): string {
  if (bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  const formatted = (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 2)
  return `${formatted} ${units[i]}`
}

export function validateFile(file: File | null): FileMetadata {
  if (!file) {
    return {
      name: '',
      size: 0,
      formattedSize: '0 B',
      format: 'Unknown',
      valid: false,
      error: 'Vui lòng chọn tệp dữ liệu CVAT.',
    }
  }

  const name = file.name
  const isZip = name.toLowerCase().endsWith('.zip')
  const size = file.size
  const formattedSize = formatBytes(size)
  const format = isZip ? 'CVAT Dataset ZIP Archive (.zip)' : 'Định dạng không được hỗ trợ'

  if (!isZip) {
    return {
      name,
      size,
      formattedSize,
      format,
      valid: false,
      error: 'Định dạng tệp không được hỗ trợ. Vui lòng chọn tệp ZIP chứa ảnh và annotations.xml của CVAT.',
    }
  }

  if (size === 0) {
    return {
      name,
      size,
      formattedSize,
      format,
      valid: false,
      error: 'Tệp rỗng (0 bytes). Vui lòng chọn tệp ZIP hợp lệ.',
    }
  }

  if (size > MAX_UPLOAD_BYTES) {
    return {
      name,
      size,
      formattedSize,
      format,
      valid: false,
      error: `Dung lượng tệp vượt quá giới hạn tối đa cho phép (${formatBytes(MAX_UPLOAD_BYTES)}).`,
    }
  }

  return {
    name,
    size,
    formattedSize,
    format,
    valid: true,
  }
}

export function validateOwnerKey(key: string): { valid: boolean; error?: string } {
  const trimmed = key.trim()
  if (!trimmed) {
    return { valid: false, error: 'Vui lòng nhập Owner Key được cấp quyền.' }
  }
  if (trimmed.length < 32) {
    return { valid: false, error: 'Owner Key phải có độ dài tối thiểu 32 ký tự theo chuẩn bảo mật.' }
  }
  return { valid: true }
}

export async function hashBlob(blob: Blob): Promise<string> {
  let buffer: ArrayBuffer
  if (typeof blob.arrayBuffer === 'function') {
    buffer = await blob.arrayBuffer()
  } else {
    buffer = await new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result as ArrayBuffer)
      reader.onerror = () => reject(reader.error)
      reader.readAsArrayBuffer(blob)
    })
  }
  const digest = await crypto.subtle.digest('SHA-256', buffer)
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}

export const ERROR_MESSAGES: Record<string, string> = {
  ACCESS_DENIED: 'Owner Key không chính xác hoặc không có quyền tải lên.',
  SERVICE_UNHEALTHY: 'Hệ thống backend tạm thời không sẵn sàng.',
  FILE_TOO_LARGE: 'Dung lượng tệp vượt quá giới hạn của máy chủ.',
  METADATA_TOO_LARGE: 'Thông tin tệp quá lớn.',
  INVALID_METADATA: 'Tên tệp hoặc kích thước tệp không hợp lệ.',
  STORAGE_IO_FAILED: 'Không đủ dung lượng lưu trữ trên máy chủ hoặc lỗi I/O.',
  QUOTA_EXCEEDED: 'Vượt quá hạn ngạch dung lượng lưu trữ.',
  CHUNK_TIMEOUT: 'Quá thời gian truyền dữ liệu chunk. Vui lòng thử lại.',
  CHUNK_INTERRUPTED: 'Kết nối mạng bị gián đoạn khi truyền dữ liệu.',
  INVALID_OFFSET: 'Vị trí chunk (offset) không khớp với trạng thái máy chủ.',
  ARCHIVE_SIZE_LIMIT: 'Kích thước tệp nén vượt quá giới hạn cho phép.',
  UNSUPPORTED_ARCHIVE_FORMAT: 'Định dạng tệp nén không hợp lệ hoặc bị hỏng.',
  IMAGE_PIXEL_LIMIT: 'Độ phân giải của ảnh trong dataset vượt quá giới hạn cho phép.',
  IMAGE_BYTE_LIMIT: 'Dung lượng một ảnh trong archive vượt quá giới hạn cho phép.',
  UNSUPPORTED_IMAGE_FORMAT: 'Định dạng ảnh không được hỗ trợ (hỗ trợ JPG, PNG, WEBP, BMP).',
  IMAGE_DECODE_FAILED: 'Không thể giải mã dữ liệu ảnh trong archive.',
  XML_SIZE_OR_NAME_LIMIT: 'Tệp annotations.xml vượt quá kích thước cho phép hoặc sai tên.',
  AMBIGUOUS_ANNOTATIONS: 'Archive chỉ được chứa duy nhất 1 tệp annotations.xml.',
  NO_VALID_IMAGES: 'Không tìm thấy ảnh hợp lệ nào trong tệp ZIP.',
  NO_IMAGES_FOUND: 'Không có ảnh hợp lệ trong dataset.',
  CORRUPTED_ARCHIVE: 'Tệp ZIP bị hỏng hoặc thiếu dữ liệu.',
  XML_PARSE_ERROR: 'Lỗi phân tích cú pháp tệp XML annotations.',
  DATASET_NOT_READY: 'Dataset chưa hoàn tất quá trình lập chỉ mục.',
  ANALYSIS_NOT_RUN: 'Chưa có kết quả phân tích.',
  PIPELINE_EXECUTION_FAILED: 'Lỗi thực thi quy trình phân tích Stage 5 trên máy chủ.',
}

export function translateErrorCode(code: string | undefined): string {
  if (!code) return 'Đã xảy ra lỗi không xác định.'
  return ERROR_MESSAGES[code] || `Lỗi từ máy chủ: ${code}`
}

export async function uploadDatasetWorkflow({
  file,
  ownerKey,
  baseUrl,
  onProgress,
  signal,
}: {
  file: File
  ownerKey: string
  baseUrl: string
  onProgress: (progress: UploadProgress) => void
  signal?: AbortSignal
}): Promise<UploadResult> {
  // 1. Client-side file validation
  const fileCheck = validateFile(file)
  if (!fileCheck.valid) {
    throw new Error(fileCheck.error || 'Tệp không hợp lệ')
  }

  const keyCheck = validateOwnerKey(ownerKey)
  if (!keyCheck.valid) {
    throw new Error(keyCheck.error || 'Owner Key không hợp lệ')
  }

  const cleanBase = baseUrl.replace(/\/$/, '')

  onProgress({
    phase: 'creating_session',
    message: 'Khởi tạo phiên tải lên trên máy chủ...',
    percent: 0,
    transferredBytes: 0,
    totalBytes: file.size,
  })

  // 2. Create Upload Session
  const idempotencyKey = crypto.randomUUID()
  const createRes = await fetch(`${cleanBase}/api/v1/uploads`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Owner-Key': ownerKey.trim(),
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({
      filename: file.name,
      size_bytes: file.size,
    }),
    ...(signal ? { signal } : {}),
  })

  if (!createRes.ok) {
    let errorCode = `HTTP_${createRes.status}`
    try {
      const errJson = await createRes.json()
      errorCode = errJson.error?.code || errorCode
    } catch { /* empty */ }
    throw new Error(translateErrorCode(errorCode))
  }

  const session = z.object({
    upload_id: z.string().min(1),
    upload_capability: z.string().min(1),
    chunk_size_bytes: z.number().int().positive(),
    received_bytes: z.number().int().nonnegative(),
  }).parse(await createRes.json())

  const uploadId = session.upload_id
  const capability = session.upload_capability
  const chunkSize = Math.min(Math.max(session.chunk_size_bytes || DEFAULT_CHUNK_SIZE, 64 * 1024), 8 * 1024 * 1024)
  const totalChunks = Math.ceil(file.size / chunkSize)

  // 3. Upload Chunks
  let offset = 0
  let chunkIndex = 0

  while (offset < file.size) {
    if (signal?.aborted) {
      throw new Error('Quá trình tải lên đã bị hủy.')
    }

    const end = Math.min(offset + chunkSize, file.size)
    const blob = file.slice(offset, end)
    const chunkSha = await hashBlob(blob)

    onProgress({
      phase: 'uploading',
      message: `Đang truyền dữ liệu: chunk ${chunkIndex + 1}/${totalChunks} (${formatBytes(offset)} / ${formatBytes(file.size)})...`,
      percent: Math.floor((offset / file.size) * 80),
      transferredBytes: offset,
      totalBytes: file.size,
      chunkIndex,
      totalChunks,
    })

    // Retry loop for transient issues
    let success = false
    let lastError: Error | null = null

    for (let attempt = 0; attempt < 3; attempt++) {
      if (signal?.aborted) throw new Error('Quá trình tải lên đã bị hủy.')
      try {
        const chunkRes = await fetch(`${cleanBase}/api/v1/uploads/${uploadId}/chunks/${chunkIndex}`, {
          method: 'PUT',
          headers: {
            'Upload-Offset': String(offset),
            'X-Chunk-SHA256': chunkSha,
            'Upload-Capability': capability,
            'Content-Type': 'application/octet-stream',
          },
          body: blob,
          ...(signal ? { signal } : {}),
        })

        if (chunkRes.ok) {
          success = true
          break
        }

        let code = `HTTP_${chunkRes.status}`
        try {
          const errJson = await chunkRes.json()
          code = errJson.error?.code || code
        } catch { /* empty */ }

        if (chunkRes.status < 500 && chunkRes.status !== 408) {
          throw new Error(translateErrorCode(code))
        }
        lastError = new Error(translateErrorCode(code))
      } catch (err) {
        if (signal?.aborted) throw err
        lastError = err instanceof Error ? err : new Error('Lỗi kết nối khi gửi chunk')
      }

      await new Promise((r) => setTimeout(r, 300 * Math.pow(2, attempt)))
    }

    if (!success) {
      throw lastError || new Error(`Lỗi tải chunk ${chunkIndex} sau 3 lần thử.`)
    }

    offset = end
    chunkIndex++
  }

  // 4. Complete Upload
  onProgress({
    phase: 'completing',
    message: 'Niêm phong và kiểm tra toàn vẹn tệp đã tải lên...',
    percent: 80,
    transferredBytes: file.size,
    totalBytes: file.size,
  })

  const completeRes = await fetch(`${cleanBase}/api/v1/uploads/${uploadId}/complete`, {
    method: 'POST',
    headers: {
      'Upload-Capability': capability,
    },
    ...(signal ? { signal } : {}),
  })

  if (!completeRes.ok) {
    let code = `HTTP_${completeRes.status}`
    try {
      const errJson = await completeRes.json()
      code = errJson.error?.code || code
    } catch { /* empty */ }
    throw new Error(translateErrorCode(code))
  }

  // 5. Start and poll validation
  onProgress({
    phase: 'validating_dataset',
    message: 'Bắt đầu kiểm tra cấu trúc ZIP, giải nén an toàn và lập chỉ mục...',
    percent: 85,
    transferredBytes: file.size,
    totalBytes: file.size,
  })

  const validateRes = await fetch(`${cleanBase}/api/v1/uploads/${uploadId}/validate`, {
    method: 'POST',
    headers: {
      'Upload-Capability': capability,
    },
    ...(signal ? { signal } : {}),
  })

  if (!validateRes.ok) {
    let code = `HTTP_${validateRes.status}`
    try {
      const errJson = await validateRes.json()
      code = errJson.error?.code || code
    } catch { /* empty */ }
    throw new Error(translateErrorCode(code))
  }

  // Poll dataset lifecycle status
  let datasetId = ''
  let attempts = 0
  while (attempts < 120) {
    if (signal?.aborted) throw new Error('Quá trình kiểm tra đã bị hủy.')

    const statusRes = await fetch(`${cleanBase}/api/v1/uploads/${uploadId}/dataset`, {
      headers: {
        'Upload-Capability': capability,
      },
      ...(signal ? { signal } : {}),
    })

    if (!statusRes.ok) {
      let code = `HTTP_${statusRes.status}`
      try {
        const errJson = await statusRes.json()
        code = errJson.error?.code || code
      } catch { /* empty */ }
      throw new Error(translateErrorCode(code))
    }

    const st = decodeLifecycle(await statusRes.json())
    if (st.upload_id !== uploadId) throw new Error('UPLOAD_MISMATCH')

    if (st.state === 'ready' && st.dataset_id) {
      datasetId = st.dataset_id
      break
    } else if (st.state === 'failed') {
      throw new Error(translateErrorCode(st.error_code || 'VALIDATION_FAILED'))
    } else if (st.state === 'cancelled') {
      throw new Error('Quá trình kiểm tra dataset đã bị hủy.')
    }

    onProgress({
      phase: 'validating_dataset',
      message: `Đang lập chỉ mục dataset (trạng thái: ${st.state})...`,
      percent: 88,
      transferredBytes: file.size,
      totalBytes: file.size,
    })

    await new Promise((r) => setTimeout(r, 600))
    attempts++
  }

  if (!datasetId) {
    throw new Error('Quá thời gian chờ kiểm tra dataset trên máy chủ.')
  }

  // 6. Trigger Stage 5 QA/QC Pipeline Analysis
  onProgress({
    phase: 'analyzing',
    message: 'Thực thi phân tích chất lượng ảnh và nhãn CVAT (Stage 5 Pipeline)...',
    percent: 92,
    transferredBytes: file.size,
    totalBytes: file.size,
  })

  const analyzeRes = await fetch(`${cleanBase}/api/v1/datasets/${datasetId}/analyze`, {
    method: 'POST',
    headers: {
      'Upload-Capability': capability,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({}),
    ...(signal ? { signal } : {}),
  })

  if (!analyzeRes.ok) {
    let code = `HTTP_${analyzeRes.status}`
    try {
      const errJson = await analyzeRes.json()
      code = errJson.error?.code || code
    } catch { /* empty */ }
    throw new Error(translateErrorCode(code))
  }

  const rawAnalysis = await analyzeRes.json()
  const validatedPayload: VerifiedPayload = payloadSchema.parse(rawAnalysis)

  if (validatedPayload.dataset_id !== datasetId) throw new Error('DATASET_MISMATCH')

  // 7. Fetch Summary
  const summaryRes = await fetch(`${cleanBase}/api/v1/datasets/${datasetId}/summary`, {
    headers: {
      'Upload-Capability': capability,
    },
    ...(signal ? { signal } : {}),
  })

  if (!summaryRes.ok) {
    let code = `HTTP_${summaryRes.status}`
    try {
      const errJson = await summaryRes.json()
      code = errJson.error?.code || code
    } catch { /* empty */ }
    throw new Error(translateErrorCode(code))
  }

  const summary = decodeDatasetSummary(await summaryRes.json())
  if (summary.dataset_id !== datasetId || summary.upload_id !== uploadId) {
    throw new Error('DATASET_MISMATCH')
  }
  if (!summary.ready) throw new Error('DATASET_NOT_READY')

  const detail: AnalysisResultDetail = {
    payload: validatedPayload,
    descriptor: null,
    provenance: null,
  }

  onProgress({
    phase: 'ready',
    message: 'Tải lên và xử lý dataset thành công! Dữ liệu đã sẵn sàng trên toàn bộ dashboard.',
    percent: 100,
    transferredBytes: file.size,
    totalBytes: file.size,
  })

  return {
    datasetId,
    uploadId,
    capability,
    detail,
    summary,
  }
}
