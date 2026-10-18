import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { EmptyState, ErrorState, LoadingState } from './States'

export function ResultStatus() {
  const result = useVerifiedResults()
  if (result?.loading) return <LoadingState />
  if (result?.error) return <ErrorState message={result.error} />
  return (
    <EmptyState>
      Chưa có kết quả phân tích. Hãy tải lên hoặc chọn bộ dữ liệu để bắt đầu.
    </EmptyState>
  )
}

export function ResultIdentity() {
  const state = useVerifiedResults()
  const result = state?.activeResult
  if (!result) return null

  return (
    <div className="result-identity-container">
      <span className="identity-chip status-chip verified">
        <span className="chip-dot" aria-hidden="true" />
        Đã tải kết quả phân tích
      </span>
    </div>
  )
}
