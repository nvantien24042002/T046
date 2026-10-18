import { useState, type FormEvent } from 'react'
import type { DashboardQuery, DashboardView } from '../api/dashboard'

interface FilterAndThresholdPanelProps {
  view: DashboardView
  query: DashboardQuery
  onUpdate: (next: DashboardQuery) => void
  onReset: () => void
}

export function FilterAndThresholdPanel({
  view,
  query,
  onUpdate,
  onReset,
}: FilterAndThresholdPanelProps) {
  const hasParameters = Boolean(view.parameters && view.parameters.length > 0)
  const hasControls = Boolean(view.controls && view.controls.length > 0)
  const hasKeyword = Boolean(view.keyword_search)

  const [paramDraft, setParamDraft] = useState(() =>
    Object.fromEntries((view.parameters ?? []).map((p) => [p.key, String(p.value)]))
  )
  const [keywordDraft, setKeywordDraft] = useState(() =>
    typeof query.keyword === 'string' ? query.keyword : ''
  )
  const [isExpanded, setIsExpanded] = useState(true)

  if (!hasParameters && !hasControls && !hasKeyword) {
    return null
  }

  const handleParamSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!view.parameters) return
    const numericValues: Record<string, number> = {}
    for (const p of view.parameters) {
      const parsed = Number(paramDraft[p.key])
      if (!Number.isNaN(parsed)) {
        numericValues[p.key] = parsed
      }
    }
    onUpdate({ ...query, ...numericValues })
  }

  const handleKeywordSubmit = (event: FormEvent) => {
    event.preventDefault()
    onUpdate({ ...query, keyword: keywordDraft.trim() || null })
  }

  const hasActiveFilters = Object.keys(query).length > 0
  const activeCount = Object.keys(query).length


  return (
    <section className="filter-threshold-panel" aria-labelledby="filter-threshold-heading">
      <div className="filter-threshold-header">
        <div className="panel-title-cluster">
          <button
            type="button"
            className="btn-toggle-panel"
            onClick={() => setIsExpanded(!isExpanded)}
            aria-expanded={isExpanded}
            title={isExpanded ? 'Thu gọn bộ lọc' : 'Mở rộng bộ lọc'}
          >
            <span className="panel-icon-badge" aria-hidden="true">⚙️</span>
            <h2 id="filter-threshold-heading">Ngưỡng kiểm định & Bộ lọc</h2>
            <span className="panel-toggle-chevron" aria-hidden="true">{isExpanded ? '▲' : '▼'}</span>
          </button>
          {activeCount > 0 && (
            <span className="active-filters-badge">
              {activeCount} bộ lọc đang áp dụng
            </span>
          )}
        </div>
        <div className="panel-header-actions">
          {hasActiveFilters && (
            <button type="button" className="btn-reset-filters" onClick={onReset}>
              ✕ Xóa bộ lọc và tham số
            </button>
          )}
        </div>
      </div>

      {isExpanded && (
        <div className="filter-threshold-body">

        {/* Threshold parameters form */}
        {hasParameters && view.parameters && (
          <form className="threshold-form-block" onSubmit={handleParamSubmit}>
            <h3 className="sub-panel-title">Ngưỡng kiểm định Backend</h3>
            <div className="threshold-inputs-grid">
              {view.parameters.map((parameter) => (
                <div key={parameter.key} className="form-field-group">
                  <label htmlFor={`param-${parameter.key}`} className="field-label">
                    {parameter.label}
                    {parameter.key.includes('share') ? ' (%)' : parameter.key.includes('count') ? ' (box)' : ''}
                  </label>
                  <div className="input-with-affix">
                    <input
                      id={`param-${parameter.key}`}
                      type="number"
                      step="any"
                      required
                      className="field-input"
                      value={paramDraft[parameter.key] ?? ''}
                      onChange={(event) =>
                        setParamDraft({ ...paramDraft, [parameter.key]: event.target.value })
                      }
                    />
                    {parameter.key.includes('share') && <span className="input-unit">%</span>}
                    {parameter.key.includes('count') && <span className="input-unit">box</span>}
                  </div>
                </div>
              ))}
            </div>
            <div className="form-action-row">
              <button type="submit" className="btn-primary">
                Áp dụng tham số tại backend
              </button>
            </div>
          </form>
        )}

        {/* Dimension & Category controls */}
        {hasControls && (
          <div className="controls-form-block">
            <h3 className="sub-panel-title">Bộ lọc thuộc tính</h3>
            <div className="controls-grid">
              {view.controls.map((control) => {
                if (control.key === 'review_scope') {
                  const isChecked = query['review_scope'] === 'review' || control.value === 'review'
                  return (
                    <div key={control.key} className="form-field-group full-width-field">
                      <label htmlFor={`ctrl-${control.key}`} className="checkbox-toggle-label">
                        <input
                          id={`ctrl-${control.key}`}
                          type="checkbox"
                          checked={isChecked}
                          onChange={(event) =>
                            onUpdate({ ...query, review_scope: event.target.checked ? 'review' : null })
                          }
                        />
                        <span>{control.label} (chỉ hiển thị box có tín hiệu cảnh báo REVIEW)</span>
                      </label>
                    </div>
                  )
                }

                const rawVal = query[control.key] !== undefined ? query[control.key] : control.value
                const selectedList: string[] = Array.isArray(rawVal)
                  ? (rawVal as unknown[]).map(String)
                  : typeof rawVal === 'string' && rawVal.length > 0
                  ? [rawVal]
                  : []

                const handleToggleChoice = (choice: string) => {
                  let next: string[]
                  if (selectedList.includes(choice)) {
                    next = selectedList.filter((c) => c !== choice)
                  } else {
                    next = [...selectedList, choice]
                  }
                  onUpdate({
                    ...query,
                    [control.key]: next.length === 0 ? null : next,
                  })
                }

                const isAllSelected = selectedList.length === 0 || selectedList.length === control.choices.length

                return (
                  <div key={control.key} className="form-field-group filter-dimension-card">
                    <div className="filter-dimension-header">
                      <label htmlFor={`ctrl-${control.key}`} className="field-label">
                        {control.label}
                      </label>
                      <span className="filter-count-badge">
                        {isAllSelected ? 'Tất cả' : `${selectedList.length}/${control.choices.length}`}
                      </span>
                    </div>

                    <div className="multi-select-chip-cluster" role="group" aria-label={`Bộ lọc ${control.label}`}>
                      <button
                        type="button"
                        className={`filter-chip ${isAllSelected ? 'chip-active' : ''}`}
                        onClick={() => onUpdate({ ...query, [control.key]: null })}
                      >
                        Tất cả
                      </button>
                      {control.choices.map((choice) => {
                        const active = !isAllSelected && selectedList.includes(choice)
                        const isMissing = choice === 'missing'
                        const isUnknown = choice === 'unknown'
                        const isSpecial = isMissing || isUnknown
                        return (
                          <button
                            key={choice}
                            type="button"
                            className={`filter-chip ${active ? 'chip-active' : ''} ${isSpecial ? 'chip-special' : ''} ${
                              ['HIGH', 'MEDIUM', 'LOW'].includes(choice) ? `chip-${choice.toLowerCase()}` : ''
                            }`}
                            onClick={() => handleToggleChoice(choice)}
                            title={
                              isMissing
                                ? 'Chưa có metadata'
                                : isUnknown
                                ? 'Đã gán nhưng chưa xác định'
                                : choice
                            }
                          >
                            <span>{choice}</span>
                            {isMissing && <span className="chip-hint"> (Thiếu)</span>}
                            {isUnknown && <span className="chip-hint"> (Chưa rõ)</span>}
                          </button>
                        )
                      })}
                    </div>

                    {/* Accessible hidden fallback select for form compatibility */}
                    <select
                      id={`ctrl-${control.key}`}
                      className="field-select sr-select"
                      value={typeof rawVal === 'string' ? rawVal : ''}
                      onChange={(event) =>
                        onUpdate({ ...query, [control.key]: event.target.value || null })
                      }
                      aria-hidden="true"
                      tabIndex={-1}
                    >
                      <option value="">Tất cả</option>
                      {control.choices.map((choice) => (
                        <option key={choice} value={choice}>
                          {choice}
                        </option>
                      ))}
                    </select>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Keyword Search */}
        {hasKeyword && (
          <form className="keyword-search-block" onSubmit={handleKeywordSubmit}>
            <div className="form-field-group">
              <label htmlFor="search-keyword" className="field-label">Tìm tên ảnh</label>
              <div className="search-input-group">
                <input
                  id="search-keyword"
                  className="field-input"
                  placeholder="Nhập tên ảnh..."
                  value={keywordDraft}
                  onChange={(event) => setKeywordDraft(event.target.value)}
                />
                <button type="submit" className="btn-primary">
                  Tìm qua backend
                </button>
              </div>
            </div>
          </form>
        )}
      </div>
      )}
    </section>
  )
}

