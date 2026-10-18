import { useState, useRef, useEffect, type ChangeEvent, type DragEvent } from 'react'
import { Link } from 'react-router-dom'
import { config } from '../config'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import {
  validateFile,
  type FileMetadata,
  type UploadProgress,
} from '../api/upload'
import { browserJson, uploadBrowserDataset } from '../api/browserSession'
import { ErrorState, LoadingState } from './States'


export function BrowserDatasetUploader() {
  const verifiedResults = useVerifiedResults()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const activeUpload = useRef<string | null>(null)
  const requestRef = useRef<number | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)

  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [fileMetadata, setFileMetadata] = useState<FileMetadata | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<UploadProgress | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<Awaited<ReturnType<typeof uploadBrowserDataset>> | null>(null)
  const [resultRevision, setResultRevision] = useState<number | null>(null)


  const [cachedUploadId, setCachedUploadId] = useState<string | null>(() => {
    try { return sessionStorage.getItem('cvat_active_upload_id') } catch { return null }
  })

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    setError(null)
    setResult(null)
    const file = event.target.files?.[0] || null
    setSelectedFile(file)
    if (file) {
      const meta = validateFile(file)
      setFileMetadata(meta)
    } else {
      setFileMetadata(null)
    }
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(true)
  }

  function handleDragLeave(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    e.stopPropagation()
    if (busy) return
    setIsDragging(false)
    setError(null)
    setResult(null)
    const file = e.dataTransfer.files?.[0] || null
    if (file) {
      setSelectedFile(file)
      const meta = validateFile(file)
      setFileMetadata(meta)
      if (!meta.valid && meta.error) {
        setError(meta.error)
      }
    }
  }

  const failRequest = verifiedResults?.failRequest
  useEffect(() => () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      if (requestRef.current !== null) failRequest?.(requestRef.current, 'Upload đã bị hủy khi rời trang.')
    }
  }, [failRequest])

  async function handleStartUpload() {
    const request = config.mode === 'backend' ? verifiedResults?.beginRequest() ?? null : null
    requestRef.current = request
    if (!selectedFile || !fileMetadata?.valid) {
      if (request !== null) verifiedResults?.failRequest(request, 'Tệp ZIP không hợp lệ.')
      setError('Vui lòng chọn một tệp ZIP hợp lệ của CVAT.')
      return
    }

    if (config.mode === 'mock') {
      setError('Đang ở chế độ Mock mode (VITE_DATA_MODE=mock). Để gửi file tới backend thực sự, vui lòng khởi động với VITE_DATA_MODE=backend và chạy backend tại ' + config.baseUrl + '.')
      return
    }

    setError(null)
    setResult(null)
    setBusy(true)

    const controller = new AbortController()
    abortControllerRef.current = controller

    try {
      const uploadResult = await uploadBrowserDataset({
        file: selectedFile,
        onUpload: (id) => {
          activeUpload.current = id
          setCachedUploadId(id)
          try { sessionStorage.setItem('cvat_active_upload_id', id) } catch { /* ignore */ }
        },
        onProgress: (p) => { if (!controller.signal.aborted && (request === null || verifiedResults?.isCurrentRequest(request))) setProgress(p) },
        signal: controller.signal,
      })

      if (request !== null && !verifiedResults?.isCurrentRequest(request)) return
      if (controller.signal.aborted) return
      try { sessionStorage.removeItem('cvat_active_upload_id') } catch { /* ignore */ }
      setCachedUploadId(null)
      setResultRevision(request)
      setResult(uploadResult)
      // Publish raw API analysis only for the still-current upload request
      verifiedResults?.setManualResult(
        uploadResult.detail,
        uploadResult.summary,
        undefined,
        request ?? undefined,
      )
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Lỗi trong quá trình upload và xử lý dữ liệu.'
      if (request !== null && !verifiedResults?.isCurrentRequest(request)) return
      verifiedResults?.failRequest(request ?? -1, msg)
      setError(msg)
      setProgress(null)
    } finally {
      if (abortControllerRef.current === controller) {
        setBusy(false)
        abortControllerRef.current = null
      }
    }
  }

  async function handleCancel(targetUploadId?: string) {
    const id = targetUploadId || activeUpload.current || cachedUploadId || (() => {
      try { return sessionStorage.getItem('cvat_active_upload_id') } catch { return null }
    })()
    const phase = progress?.phase
    if (requestRef.current !== null) verifiedResults?.failRequest(requestRef.current, 'Đã dừng chờ ở trình duyệt; kiểm tra trạng thái server trước retry.')
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    setBusy(false)
    setProgress(null)
    if (id) {
      try { sessionStorage.removeItem('cvat_active_upload_id') } catch { /* ignore */ }
      setCachedUploadId(null)
    }
    if (id && phase === 'validating_dataset') {
      try {
        await browserJson(`/uploads/${encodeURIComponent(id)}/dataset/cancel`, { method: 'POST' })
        setError('Server đã nhận yêu cầu hủy validation; worker có thể còn đang dừng.')
      } catch {
        setError('Chưa xác nhận cancellation; không tự retry validation.')
      }
    } else if (id) {
      try {
        await browserJson(`/uploads/${encodeURIComponent(id)}`, { method: 'DELETE' })
        setError('Đã hủy phiên upload bị gián đoạn thành công. Bây giờ bạn có thể chọn tệp và tải lên lại.')
      } catch {
        setError('Chưa xác nhận hủy upload trên server; kiểm tra trạng thái trước thao tác tiếp.')
      }
    } else {
      setError('Chỉ dừng chờ ở trình duyệt. Server có thể tiếp tục; không tự hủy hoặc tạo job mới.')
    }
  }

  function handleReset() {
    if (busy && abortControllerRef.current) {
      abortControllerRef.current.abort()
      if (requestRef.current !== null) verifiedResults?.failRequest(requestRef.current, 'Upload đã bị hủy.')
    }
    setSelectedFile(null)
    setFileMetadata(null)
    setProgress(null)
    setError(null)
    setResult(null)
    setBusy(false)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  return (
    <section className="dataset-uploader-card" aria-labelledby="upload-section-title">
      <div className="uploader-header">
        <h2 id="upload-section-title">Tải lên & Khởi tạo Dataset CVAT</h2>
        <span className={`upload-badge ${config.mode}`}>
          {config.mode === 'backend' ? 'Backend Mode · Upload thực tế' : 'Mock Mode · Xem trước'}
        </span>
      </div>

      <p className="uploader-desc">
        Hỗ trợ định dạng file nén <strong>.ZIP</strong> chuẩn CVAT chứa thư mục ảnh (<code>.jpg</code>, <code>.png</code>, <code>.webp</code>, <code>.bmp</code>) và tệp chú thích tùy chọn <code>annotations.xml</code> (CVAT for images).
      </p>

      {config.mode === 'mock' && (
        <div className="notice notice-warning" role="note">
          <strong>Lưu ý chế độ Mock:</strong>
          <p>
            Bạn đang xem giao diện ở chế độ Mock (<code>VITE_DATA_MODE=mock</code>). Kiểm tra định dạng và kích thước hoạt động trực tiếp trên trình duyệt. Để gửi file tới FastAPI Backend và chạy pipeline QA/QC thực tế, hãy đặt <code>$env:VITE_DATA_MODE = &quot;backend&quot;</code> và chạy lại frontend.
          </p>
        </div>
      )}

      {/* Drag & Drop File Zone */}
      <div
        className={`drop-zone ${isDragging ? 'dragging' : ''} ${selectedFile ? 'has-file' : ''}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <input
          ref={fileInputRef}
          id="cvat-file-input"
          type="file"
          accept=".zip"
          onChange={handleFileChange}
          disabled={busy}
          className="file-input-hidden"
          aria-describedby="file-requirements"
        />

        <div className="drop-zone-content">
          <div className="drop-icon" aria-hidden="true">📁</div>
          <p className="drop-prompt">
            Kéo thả tệp ZIP vào đây hoặc{' '}
            <label htmlFor="cvat-file-input" className="file-browse-btn">
              chọn tệp từ máy tính
            </label>
          </p>
          <span id="file-requirements" className="drop-hint">
            Chọn tệp ZIP CVAT. Giới hạn dung lượng thực tế do dịch vụ xác nhận khi tạo phiên.
          </span>
        </div>
      </div>

      {/* File Metadata Card */}
      {fileMetadata && (
        <div className={`file-info-card ${fileMetadata.valid ? 'valid' : 'invalid'}`}>
          <div className="file-info-header">
            <strong>Tệp dữ liệu đã chọn</strong>
            <span className={`file-tag ${fileMetadata.valid ? 'tag-valid' : 'tag-invalid'}`}>
              {fileMetadata.valid ? '✓ Hợp lệ' : '✗ Không hợp lệ'}
            </span>
          </div>
          <div className="file-info-grid">
            <div>
              <span className="info-label">Dung lượng:</span>
              <span className="info-val">{fileMetadata.formattedSize}</span>
            </div>
            <div>
              <span className="info-label">Định dạng phát hiện:</span>
              <span className="info-val">{fileMetadata.format}</span>
            </div>
          </div>
          {fileMetadata.error && <p className="file-error-text" role="alert">{fileMetadata.error}</p>}
        </div>
      )}

      {/* Progress & Status */}
      {progress && (
        <div className="upload-progress-box" role="status" aria-live="polite">
          <div className="progress-labels">
            <span className="progress-phase-title">
              {progress.phase === 'creating_session' && '1/5. Khởi tạo phiên upload...'}
              {progress.phase === 'uploading' && '2/5. Đang tải lên theo chunk...'}
              {progress.phase === 'completing' && '3/5. Niêm phong tệp trên server...'}
              {progress.phase === 'validating_dataset' && '4/5. Giải nén an toàn & Lập chỉ mục...'}
              {progress.phase === 'analyzing' && '5/5. Phân tích chất lượng & Nhãn (Stage 5)...'}
              {progress.phase === 'ready' && '✓ Hoàn tất thành công!'}
            </span>
            <span className="progress-pct">{progress.percent}%</span>
          </div>

          <progress
            className="progress-bar-element"
            value={progress.percent}
            max={100}
            aria-label="Tiến trình tải lên và phân tích"
          />

          <p className="progress-status-msg">{progress.message}</p>
        </div>
      )}

      {/* Error display */}
      {error && (
        <div className="upload-error-container">
          <ErrorState message={error} />
          {error.includes('ONE_ACTIVE_UPLOAD') && (
            <div className="upload-active-recovery" style={{ marginTop: '0.75rem', padding: '0.75rem', background: 'rgba(239, 68, 68, 0.08)', borderRadius: '8px', border: '1px solid rgba(239, 68, 68, 0.2)' }}>
              <p style={{ margin: '0 0 0.5rem 0', fontSize: '0.875rem', color: 'var(--text-secondary, #94a3b8)' }}>
                Hệ thống giới hạn 1 phiên tải lên đồng thời. Phiên trước có thể bị gián đoạn do tải lại trang hoặc rớt mạng.
              </p>
              <button
                type="button"
                className="btn-secondary"
                style={{ fontSize: '0.85rem', padding: '0.4rem 0.8rem' }}
                onClick={() => void handleCancel()}
              >
                Hủy phiên đang gián đoạn & Thử lại
              </button>
            </div>
          )}
        </div>
      )}

      {/* Action Buttons */}
      <div className="uploader-actions">
        <button
          type="button"
          className="btn-primary"
          onClick={handleStartUpload}
          disabled={busy || !selectedFile || !fileMetadata?.valid}
        >
          {busy ? 'Đang xử lý...' : 'Bắt đầu tải lên & Phân tích'}
        </button>

        {busy && (
          <button type="button" className="btn-cancel" onClick={() => void handleCancel()}>
            Hủy thao tác
          </button>
        )}

        {selectedFile && !busy && (
          <button type="button" className="btn-secondary" onClick={handleReset}>
            Chọn tệp khác
          </button>
        )}
      </div>

      {busy && <LoadingState />}

      {/* Success Result Summary */}
      {result && verifiedResults?.datasetId === result.datasetId && verifiedResults?.revision === resultRevision && verifiedResults?.activeResult && (
        <div className="upload-result-card" role="region" aria-label="Kết quả tải lên">
          <div className="result-header">
            <h3>✓ Tải lên và nạp dữ liệu thành công!</h3>
            <span className="dataset-tag">Kết quả đã sẵn sàng</span>
          </div>

          <div className="result-metrics">
            {result.summary.ready && (
              <>
                <div className="metric-item">
                  <span className="metric-name">Tổng số ảnh</span>
                  <span className="metric-val">{result.summary.images}</span>
                </div>
                <div className="metric-item">
                  <span className="metric-name">Annotations</span>
                  <span className="metric-val">{result.summary.annotations ? 'Có (CVAT XML)' : 'Chỉ có ảnh'}</span>
                </div>
              </>
            )}
            <p>Xem các KPI do backend cung cấp tại trang Tổng quan dữ liệu.</p>
          </div>

          <div className="result-nav-grid">
            <p className="result-nav-title">Dataset đã được đồng bộ vào phiên làm việc. Khám phá các trang phân tích:</p>
            <div className="nav-buttons-cluster">
              <Link to="/overview" className="nav-shortcut-btn">📊 Tổng quan dữ liệu</Link>
              <Link to="/distribution" className="nav-shortcut-btn">📈 Phân bố & thuộc tính</Link>
              <Link to="/quality" className="nav-shortcut-btn">🔍 Image Quality</Link>
              <Link to="/labels" className="nav-shortcut-btn">🏷️ Label Check A+B</Link>
              <Link to="/review" className="nav-shortcut-btn">🖼️ Review ảnh</Link>
              <Link to="/scene-finder" className="nav-shortcut-btn">🎯 Scene Finder</Link>
              <Link to="/coverage" className="nav-shortcut-btn">🌐 Coverage & Slice</Link>
              <Link to="/reports" className="nav-shortcut-btn">📑 Xuất báo cáo</Link>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
