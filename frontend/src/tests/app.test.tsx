import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { App, destinations } from '../App'
import { Dashboard as ReferenceDashboard } from '../reference/Dashboard'
import { readConfig } from '../config'
function mount(path = '/') { return render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>) }
describe('M1 application', () => {
 it('labels backend data and provides semantic navigation', () => { mount(); expect(screen.getByText('Chế độ phát triển · Không gửi upload')).toBeVisible(); expect(screen.getByRole('navigation', { name: 'Điều hướng chính' })).toBeVisible(); expect(screen.getByRole('main')).toBeVisible() })
 it.each(destinations)('requires a backend dataset at %s', (path, title) => { mount(path); expect(screen.getByRole('heading', { level: 1, name: title })).toBeVisible(); expect(screen.getByText(/Chưa có phiên dataset được cấp quyền/)).toBeVisible() })
 it('navigates using the shell', async () => { mount(); await userEvent.click(screen.getByRole('link', { name: 'Coverage & Slice' })); expect(screen.getByRole('heading', { name: 'Coverage & Slice' })).toBeVisible(); expect(screen.getByRole('main')).toHaveFocus() })
 it('renders Not Found and a recovery link', () => { mount('/missing'); expect(screen.getByRole('heading', { name: 'Không tìm thấy trang' })).toBeVisible(); expect(screen.getByRole('link', { name: 'Về tổng quan' })).toHaveAttribute('href', '/') })
 it('preserves deterministic states in the test-only historical harness', async () => { render(<MemoryRouter><ReferenceDashboard /></MemoryRouter>); const select = screen.getByRole('combobox', { name: 'Trạng thái mẫu' }); await userEvent.selectOptions(select, 'loading'); expect(screen.getByRole('status')).toHaveTextContent('Đang tải'); await userEvent.selectOptions(select, 'empty'); expect(screen.getByRole('heading', { name: 'Chưa có dữ liệu' })).toBeVisible(); await userEvent.selectOptions(select, 'error'); expect(screen.getByRole('alert')).toHaveTextContent('Lỗi mẫu'); await userEvent.selectOptions(select, 'success'); expect(screen.getByRole('list')).toBeVisible() })
 it('requires an explicit valid data mode', () => { expect(readConfig({}).mode).toBe('backend'); expect(readConfig({ VITE_DATA_MODE: 'backend' }).mode).toBe('backend'); expect(() => readConfig({ VITE_DATA_MODE: 'auto' })).toThrow() })
})
