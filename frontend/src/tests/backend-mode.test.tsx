import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { Dashboard } from '../pages/Dashboard'
import { readHealth } from '../api/health'
import { ApiError } from '../api/client'
vi.mock('../config', () => ({ config: { mode: 'backend', baseUrl: 'http://localhost:8011' } }))
vi.mock('../api/health', () => ({ readHealth: vi.fn() }))
describe('backend mode', () => {
 it('does not make automatic requests or show mock controls', () => { render(<MemoryRouter><Dashboard /></MemoryRouter>); expect(readHealth).not.toHaveBeenCalled(); expect(screen.queryByRole('combobox')).not.toBeInTheDocument() })
 it('keeps real failures visible without sample fallback', async () => { vi.mocked(readHealth).mockRejectedValueOnce(new ApiError('network', 'Cannot reach the backend.')); render(<MemoryRouter><Dashboard /></MemoryRouter>); await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra kết nối' })); expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach'); expect(screen.queryByText('Khám phá giao diện mẫu')).not.toBeInTheDocument() })
 it('renders a validated health success without pretending a dataset exists', async () => { vi.mocked(readHealth).mockResolvedValueOnce({ status: 'ok', stage: '6A', ready_datasets: true }); render(<MemoryRouter><Dashboard /></MemoryRouter>); await userEvent.click(screen.getByRole('button', { name: 'Kiểm tra kết nối' })); expect(await screen.findByRole('status')).toHaveTextContent('Chưa có dataset được chọn') })
})