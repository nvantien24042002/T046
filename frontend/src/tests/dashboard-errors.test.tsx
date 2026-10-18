import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Dashboard } from '../pages/Dashboard'
import { readHealth } from '../api/health'

const settings = vi.hoisted(() => ({ mode: 'backend', baseUrl: 'http://localhost:8011' }))
vi.mock('../config', () => ({ config: settings }))
// readHealth and createApiClient remain real; only the network transport is stubbed.
beforeEach(() => { settings.baseUrl = 'http://localhost:8011' })
afterEach(() => vi.unstubAllGlobals())

function mount() { render(<MemoryRouter><Dashboard /></MemoryRouter>) }

describe('dashboard real API boundary', () => {
  it.each(['not-a-url', 'file:///private', 'http://owner:private@localhost', 'http://localhost?token=private', 'http://localhost/#private'])(
    'shows configuration errors without crashing or sending a request: %s', async (url) => {
      settings.baseUrl = url
      const transport = vi.fn<typeof fetch>()
      vi.stubGlobal('fetch', transport)
      mount()
      await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra kết nối' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('Kiểm tra cấu hình Backend URL.')
      expect(screen.getByRole('heading', { name: 'Tổng quan' })).toBeVisible()
      expect(screen.getByRole('button', { name: 'Kiểm tra kết nối' })).toBeEnabled()
      expect(screen.queryByText('Khám phá giao diện mẫu')).not.toBeInTheDocument()
      expect(screen.getByRole('alert')).not.toHaveTextContent('private')
      expect(transport).not.toHaveBeenCalled()
    },
  )

  it('shows schema rejection from real readHealth and can retry successfully', async () => {
    const transport = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response('{"status":"ok","stage":"6A","ready_datasets":"yes"}'))
      .mockResolvedValueOnce(new Response('{"status":"ok","stage":"6A","ready_datasets":true}'))
    vi.stubGlobal('fetch', transport)
    mount()
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra kết nối' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Backend returned an invalid response.')
    expect(screen.queryByText('Khám phá giao diện mẫu')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra kết nối' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Dịch vụ sẵn sàng')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(transport).toHaveBeenCalledTimes(2)
  })

  it.each([
    { status: 'wrong', stage: '6A', ready_datasets: true },
    { status: 'ok', stage: 6, ready_datasets: true },
    { status: 'ok', stage: '6A' },
  ])('rejects invalid health fields through the real schema', async (body) => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(body))))
    await expect(readHealth(settings.baseUrl)).rejects.toMatchObject({ kind: 'response', status: undefined })
  })
})
