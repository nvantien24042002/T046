import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { EmptyState, ErrorState, LoadingState } from './States'

export function ResultStatus() {
  const result = useVerifiedResults()
  if (result?.loading) return <LoadingState />
  if (result?.error) return <ErrorState message={result.error} />
  return <EmptyState>Chưa có phiên dataset được cấp quyền hoặc chưa chọn kết quả phân tích. Không tải fixtures trong Backend mode.</EmptyState>
}

export function ResultIdentity() {
  const state = useVerifiedResults()
  const result = state?.activeResult
  if (!result) return null
  return <p>Dataset: <code>{result.payload.dataset_id}</code> · {result.descriptor
    ? <>Result do backend cung cấp: <code>{result.descriptor.result_id}</code></>
    : 'Kết quả analyze trực tiếp; chưa có metadata xác thực nguồn dữ liệu.'}</p>
}
