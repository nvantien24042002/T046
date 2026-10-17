import { useEffect, useState } from 'react'
import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { config } from '../config'
import { VerifiedResultError } from '../api/verifiedResults'

export interface ImagePreviewProps {
  imagePath: string
  annotated?: boolean
  alt: string
  caption?: string
  className?: string
}

export function ImagePreview(props: ImagePreviewProps) {
  const verified = useVerifiedResults()
  return <PreviewContent key={`${verified?.revision}:${props.imagePath}:${props.annotated}`} {...props} />
}

function PreviewContent({
  imagePath,
  annotated = false,
  alt,
  caption,
  className = '',
}: ImagePreviewProps) {
  const verified = useVerifiedResults()
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const isMock = config.mode === 'mock'
  const isAuthorized = Boolean(config.mode === 'backend' && verified?.datasetId && verified?.activeResult)

  useEffect(() => {
    if (isMock || !isAuthorized || !imagePath || !verified) {
      return
    }

    let active = true
    let createdUrl: string | null = null
    const controller = new AbortController()

    // Start async fetch
    const doFetch = async () => {
      setLoading(true)
      setErrorMessage(null)
      try {
        const blob = await verified.fetchPreview(imagePath, annotated, controller.signal)
        if (!active) return
        createdUrl = URL.createObjectURL(blob)
        setObjectUrl(createdUrl)
        setLoading(false)
      } catch (err: unknown) {
        if (!active) return
        setLoading(false)
        if (err instanceof VerifiedResultError) {
          if (err.code === 'IMAGE_NOT_FOUND' || err.status === 404) {
            setErrorMessage('Không tìm thấy ảnh xem trước (404)')
          } else if (err.code === 'INVALID_PATH' || err.status === 422) {
            setErrorMessage('Đường dẫn ảnh không hợp lệ (422)')
          } else if (err.code === 'IMAGE_SIZE_LIMIT' || err.status === 413) {
            setErrorMessage('Ảnh lớn hơn 50 MB, không preview.')
          } else if (err.code === 'IMAGE_DECODE_FAILED' || err.status === 415) {
            setErrorMessage('Không đọc được ảnh: Định dạng không hỗ trợ')
          } else if (err.status === 401 || err.code === 'ACCESS_GRANT_INVALID') {
            setErrorMessage('Phiên ủy quyền hết hạn hoặc không hợp lệ')
          } else {
            setErrorMessage(`Lỗi tải ảnh: ${err.code ?? err.status ?? 'Network error'}`)
          }
        } else {
          setErrorMessage('Không thể tải ảnh xem trước')
        }
      }
    }

    void doFetch()

    return () => {
      active = false
      controller.abort()
      if (createdUrl) {
        URL.revokeObjectURL(createdUrl)
      }
    }
  }, [imagePath, annotated, isMock, isAuthorized, verified])

  if (isMock) {
    return (
      <div className={`preview-placeholder ${className}`}>
        <p className="preview-note">Preview ảnh yêu cầu Backend mode với dataset được cấp quyền.</p>
      </div>
    )
  }

  if (!isAuthorized) {
    return (
      <div className={`preview-placeholder ${className}`}>
        <p className="preview-note">Chưa có phiên dataset được ủy quyền để xem trước ảnh.</p>
      </div>
    )
  }

  if (!imagePath) {
    return (
      <div className={`preview-placeholder ${className}`}>
        <p className="preview-note">Không có đường dẫn ảnh.</p>
      </div>
    )
  }

  return (
    <div className={`image-preview-wrapper ${className}`}>
      {loading && (
        <div className="preview-loading" role="status" aria-live="polite">
          <span>Đang tải ảnh...</span>
        </div>
      )}
      {errorMessage && (
        <div className="preview-error" role="alert">
          <p>{errorMessage}</p>
        </div>
      )}
      {!loading && !errorMessage && objectUrl && (
        <figure className="preview-figure">
          <img src={objectUrl} alt={alt} className="preview-img" />
          {caption && <figcaption className="preview-caption">{caption}</figcaption>}
        </figure>
      )}
    </div>
  )
}
