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
        <div className="result-identity-chips">
          <span className="identity-chip dataset-chip">
            <span className="chip-icon" aria-hidden="true">📁</span>
            <span className="chip-label">Dataset:</span>
            <code className="dataset-id-tag">{datasetId}</code>
          </span>

          <span className={`identity-chip status-chip ${isVerified ? 'verified' : 'session'}`}>
            <span className="chip-dot" aria-hidden="true" />
            {isVerified ? 'Backend Verified' : 'Phiên trực tiếp'}
          </span>

          {result.descriptor && (
            <span className="identity-chip result-chip">
              <span className="chip-label">ID:</span>
              <span className="chip-val">{result.descriptor.result_id}</span>
            </span>
          )}
        </div>
      </div>

      {(result.payload.request_fingerprint || result.descriptor?.payload_sha256) && (
        <details className="diagnostic-disclosure">
          <summary>
            <span className="disclosure-icon" aria-hidden="true">🔒</span>
            <span>Chữ ký & Chẩn đoán kỹ thuật</span>
          </summary>
          <div className="diagnostic-body">
            {result.payload.request_fingerprint && (
              <p className="diagnostic-item">
                <span>Fingerprint:</span> <samp>{result.payload.request_fingerprint}</samp>
              </p>
            )}
            {result.descriptor?.payload_sha256 && (
              <p className="diagnostic-item">
                <span>SHA256:</span> <samp>{result.descriptor.payload_sha256}</samp>
              </p>
            )}
          </div>
        </details>
      )}
    </div>
  )
}

