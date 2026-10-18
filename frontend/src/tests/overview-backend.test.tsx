import { render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { Overview } from '../pages/Overview'
vi.mock('../config', () => ({ config: { mode: 'backend', baseUrl: 'http://localhost:8011' } }))
afterEach(() => vi.unstubAllGlobals())
it('stops unapproved dataset integration without calling endpoints or substituting fixtures', () => {
  const load = vi.fn()
  const transport = vi.fn()
  vi.stubGlobal('fetch', transport)
  render(<Overview />)
  expect(screen.getByText(/Chưa có kết quả phân tích/)).toBeVisible()
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  expect(screen.queryByRole('group', { name: 'Tổng ảnh' })).not.toBeInTheDocument()
  expect(load).not.toHaveBeenCalled()
  expect(transport).not.toHaveBeenCalled()
})
