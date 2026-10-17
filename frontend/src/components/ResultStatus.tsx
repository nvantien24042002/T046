import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { EmptyState, ErrorState, LoadingState } from './States'

export function ResultStatus() {
  const result = useVerifiedResults()
  if (result?.loading) return <LoadingState />
  if (result?.error) return <ErrorState message={result.error} />
  return (
    <EmptyState>
      Chưa có phiên dataset được cấp quyền hoặc chưa chọn kết quả phân tích. Không tải fixtures trong Backend mode.
    </EmptyState>
  )
}

export function ResultIdentity() {
  const state = useVerifiedResults()
  const result = state?.activeResult
  if (!result) return null

  const datasetId = result.payload.dataset_id
  const isVerified = Boolean(result.descriptor)

  return (
    <div className="result-identity-container">
      <div className="result-identity-summary-bar">
        <p className="result-identity-line">
          Dataset: <code className="dataset-id-tag">{datasetId}</code> ·{' '}
          {result.descriptor ? (
            <>Result do backend cung cấp: <code>{result.descriptor.result_id}</code></>
          ) : (
            'Kết quả analyze trực tiếp; chưa có metadata xác thực nguồn dữ liệu.'
          )}
          <span className={`dataset-status-pill ${isVerified ? 'verified' : 'session'}`}>
            {isVerified ? '✓ Backend Verified' : '● Phiên trực tiếp'}
          </span>
        </p>
      </div>

      {(result.payload.request_fingerprint || result.descriptor?.payload_sha256) && (
        <details className="diagnostic-disclosure">
          <summary>Thông tin chẩn đoán kỹ thuật & Chữ ký dữ liệu</summary>
          <div className="diagnostic-body">
            {result.payload.request_fingerprint && (
              <p className="diagnostic-item">
                <span>Fingerprint:</span> <samp>{result.payload.request_fingerprint}</samp>
              </p>
            )}
            {result.descriptor?.payload_sha256 && (
              <p className="diagnostic-item">
                <span>Payload SHA256:</span> <samp>{result.descriptor.payload_sha256}</samp>
              </p>
            )}
          </div>
        </details>
      )}
    </div>
  )
}
