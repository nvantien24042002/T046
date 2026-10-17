export interface PaginationState {
  page: number
  pageSize: number
}

export function paginate<T>(items: T[], page: number, pageSize: number): T[] {
  const start = page * pageSize
  return items.slice(start, start + pageSize)
}

export function Pagination({ total, page, pageSize, onPageChange, onPageSizeChange }: {
  total: number
  page: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
}) {
  const totalPages = Math.ceil(total / pageSize)
  if (totalPages <= 1) return null

  return (
    <div className="pagination" role="navigation" aria-label="Phân trang">
      <button onClick={() => onPageChange(0)} disabled={page === 0} aria-label="Trang đầu">««</button>
      <button onClick={() => onPageChange(page - 1)} disabled={page === 0} aria-label="Trang trước">‹</button>
      <span>Trang {page + 1} / {totalPages} (Tổng: {total})</span>
      <button onClick={() => onPageChange(page + 1)} disabled={page >= totalPages - 1} aria-label="Trang sau">›</button>
      <button onClick={() => onPageChange(totalPages - 1)} disabled={page >= totalPages - 1} aria-label="Trang cuối">»»</button>
      <label>
        Kích thước trang:
        <select value={pageSize} onChange={(e) => { onPageSizeChange(Number(e.target.value)); onPageChange(0) }}>
          <option value="5">5</option>
          <option value="8">8</option>
          <option value="10">10</option>
          <option value="16">16</option>
          <option value="25">25</option>
          <option value="50">50</option>
        </select>
      </label>
    </div>
  )
}
