import { useVerifiedResults } from '../context/VerifiedResultsContext'
import { config } from '../config'

export interface ExportScope { datasetId: string; resultId: string | null }

export function scopedExport(data: Record<string, unknown>[], scope?: ExportScope): Record<string, unknown>[] {
  return scope ? data.map((row) => {
    const scoped: Record<string, unknown> = { ...row, dataset_id: scope.datasetId }
    delete scoped.result_id
    return { ...scoped, ...(scope.resultId ? { result_id: scope.resultId } : {}) }
  }) : data
}

function escapeCSV(value: unknown): string {
  if (value === null || value === undefined || value === '') return ''
  const str = String(value)
  if (str.includes(',') || str.includes('"') || str.includes('\n')) {
    return `"${str.replace(/"/g, '""')}"`
  }
  return str
}

export function downloadCSV(data: Record<string, unknown>[], filename: string, scope?: ExportScope) {
  data = scopedExport(data, scope)
  if (!data.length) return

  const first = data[0]
  if (!first) return
  const headers = Object.keys(first)
  const csv = [
    headers.map(escapeCSV).join(','),
    ...data.map((row) => headers.map((header) => escapeCSV(row[header])).join(','))
  ].join('\n')

  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' }) // BOM for Excel
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export function ExportButton({ data, filename, label = 'Xuất CSV', disabled = false }: {
  data: Record<string, unknown>[]
  filename: string
  label?: string
  disabled?: boolean
}) {
  const state = useVerifiedResults()
  const result = config.mode === 'backend' ? state?.activeResult : null
  const unavailable = config.mode === 'backend' && (!result || state?.loading || !!state?.error)
  return (
    <button
      onClick={() => downloadCSV(data, filename, result ? {
        datasetId: result.payload.dataset_id, resultId: result.descriptor?.result_id ?? null,
      } : undefined)}
      disabled={disabled || unavailable || !data.length}
      aria-label={label}
    >
      {label} ({data.length} hàng)
    </button>
  )
}
