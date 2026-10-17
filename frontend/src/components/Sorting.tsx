export type SortDirection = 'asc' | 'desc' | null

export interface SortState<K extends string> {
  column: K | null
  direction: SortDirection
}

export function sortData<T, K extends keyof T>(data: T[], column: K | null, direction: SortDirection): T[] {
  if (!column || !direction) return data

  return [...data].sort((a, b) => {
    const aVal = a[column]
    const bVal = b[column]

    // Handle null/undefined
    if (aVal === null || aVal === undefined) return direction === 'asc' ? 1 : -1
    if (bVal === null || bVal === undefined) return direction === 'asc' ? -1 : 1

    // Compare values
    if (typeof aVal === 'number' && typeof bVal === 'number') {
      return direction === 'asc' ? aVal - bVal : bVal - aVal
    }

    const aStr = String(aVal)
    const bStr = String(bVal)
    const comparison = aStr.localeCompare(bStr)
    return direction === 'asc' ? comparison : -comparison
  })
}

export function SortableHeader<K extends string>({ column, currentSort, onSort, children }: {
  column: K
  currentSort: SortState<K>
  onSort: (column: K) => void
  children: React.ReactNode
}) {
  const isActive = currentSort.column === column
  const arrow = isActive ? (currentSort.direction === 'asc' ? ' ▲' : ' ▼') : ''

  return (
    <th aria-sort={isActive ? (currentSort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button
        type="button"
        onClick={() => onSort(column)}
        className={isActive ? 'sort-active' : ''}
      >
        {children}{arrow}
      </button>
    </th>
  )
}
