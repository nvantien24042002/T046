import { render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ImageQuality } from '../pages/ImageQuality'
vi.mock('../config', () => ({ config: { mode: 'backend', baseUrl: 'http://localhost:8011' } }))
afterEach(() => vi.unstubAllGlobals())
it('blocks unapproved quality integration without requests or fixture fallback', () => {
  const transport = vi.fn()
  const load = vi.fn()
  vi.stubGlobal('fetch', transport)
  render(<ImageQuality />)
  expect(screen.getByText(/Chưa có phiên dataset được cấp quyền/)).toBeVisible()
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  expect(screen.queryByRole('meter')).not.toBeInTheDocument()
  expect(load).not.toHaveBeenCalled()
  expect(transport).not.toHaveBeenCalled()
})
