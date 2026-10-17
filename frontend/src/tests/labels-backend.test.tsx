import { render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LabelCheck } from '../pages/LabelCheck'
vi.mock('../config', () => ({ config: { mode: 'backend', baseUrl: 'http://localhost:8011' } }))
afterEach(() => vi.unstubAllGlobals())
it('blocks unapproved Label Check integration without requests or fixture fallback', () => {
  const transport = vi.fn(), load = vi.fn()
  vi.stubGlobal('fetch', transport)
  render(<LabelCheck />)
  expect(screen.getByText(/Chưa có phiên dataset được cấp quyền/)).toBeVisible()
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  expect(load).not.toHaveBeenCalled()
  expect(transport).not.toHaveBeenCalled()
})
