import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { CoverageTargetsPanel } from '../components/CoverageTargetsPanel'
import { coverageTargetsSchema, mergeCoverageTargets, targetsJSON, type CoverageTarget } from '../api/coverageTargets'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
const target: CoverageTarget = { name: 'rain-night', filters: { weather: 'rain', timeofday: 'night' }, unit: 'images', target: 7, weight: 2.5 }
function mount(onGenerate = vi.fn().mockResolvedValue([target]), applied: unknown = []) {
  const onApply = vi.fn()
  render(<CoverageTargetsPanel applied={applied} busy={false} choices={{ weather: ['rain', 'unknown', 'missing'] }} onApply={onApply} onGenerate={onGenerate} />)
  fireEvent.click(screen.getByText('Sinh tổ hợp từ dữ liệu máy chủ'))
  fireEvent.click(screen.getByText('Nhập targets JSON'))
  return { onApply, onGenerate }
}

it('validates then adds, edits and removes a target without changing the applied set prematurely', async () => {
  const user = userEvent.setup(); const { onApply } = mount()
  await user.click(screen.getByRole('button', { name: 'Thêm target vào bản soạn' }))
  expect(screen.getByRole('alert')).toHaveTextContent('target là số nguyên > 0')
  await user.click(screen.getByLabelText('Dimension weather'))
  await user.type(screen.getByLabelText('Giá trị weather'), 'missing')
  fireEvent.change(screen.getByLabelText('Weight ưu tiên'), { target: { value: '0' } })
  await user.click(screen.getByRole('button', { name: 'Thêm target vào bản soạn' }))
  expect(onApply).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Weight ưu tiên'), { target: { value: '2' } })
  await user.click(screen.getByRole('button', { name: 'Thêm target vào bản soạn' }))
  expect(screen.getByText('Bản soạn targets (1/250)')).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Sửa target 1' }))
  fireEvent.change(screen.getByLabelText('Target tối thiểu'), { target: { value: '12' } })
  await user.click(screen.getByRole('button', { name: 'Lưu sửa target' }))
  await user.click(screen.getByRole('button', { name: 'Đánh giá targets tại backend' }))
  expect(onApply).toHaveBeenLastCalledWith([{ filters: { weather: 'missing' }, unit: 'images', target: 12, weight: 2 }])
  await user.click(screen.getByRole('button', { name: 'Xóa target 1' }))
  await user.click(screen.getByRole('button', { name: 'Đánh giá targets tại backend' }))
  expect(onApply).toHaveBeenLastCalledWith([])
})

it.each([
  [{ ...target, target: 0 }], [{ ...target, target: 1.5 }], [{ ...target, weight: 0 }],
  [{ ...target, filters: {} }], [{ ...target, filters: { invalid: 'x' } }], [target, target],
  Array.from({ length: 251 }, (_, i) => ({ ...target, filters: { class: `class-${i}` } })),
].map((items) => ({ items })))('rejects invalid JSON configuration without sending it', ({ items }) => {
  const { onApply } = mount()
  fireEvent.change(screen.getByLabelText('Targets do người dùng cung cấp (JSON)'), { target: { value: JSON.stringify(items) } })
  fireEvent.click(screen.getByText('Áp dụng JSON targets'))
  expect(screen.getByRole('alert')).toHaveTextContent('JSON không hợp lệ')
  expect(onApply).not.toHaveBeenCalled()
})

it('previews server-generated targets and requires confirmation before applying', async () => {
  let finish!: (value: CoverageTarget[]) => void
  const generate = vi.fn(() => new Promise<CoverageTarget[]>((resolve) => { finish = resolve }))
  const { onApply } = mount(generate)
  fireEvent.click(screen.getByLabelText('Sinh theo weather'))
  fireEvent.click(screen.getByLabelText('Sinh theo timeofday'))
  fireEvent.click(screen.getByLabelText('Bao gồm tổ hợp chưa xuất hiện (Cartesian product)'))
  fireEvent.click(screen.getByText('Xem trước tổ hợp từ backend'))
  expect(screen.getByText('Đang xem trước tổ hợp từ máy chủ…')).toBeVisible()
  expect(generate).toHaveBeenCalledWith({ dimensions: ['weather', 'timeofday'], unit: 'images', target: 100, weight: 1, include_unobserved: true }, expect.any(AbortSignal))
  finish([target])
  expect(await screen.findByText('Máy chủ trả 1 target dự kiến; đang chọn 1')).toBeVisible()
  expect(onApply).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('Xác nhận thay thế targets'))
  expect(onApply).toHaveBeenCalledWith([target])
})

it('supports filtering only the actual generated targets by returned values', async () => {
  const unknown = { ...target, filters: { weather: 'unknown' } }
  const { onApply } = mount(vi.fn().mockResolvedValue([target, unknown]))
  fireEvent.click(screen.getByLabelText('Sinh theo weather'))
  fireEvent.click(screen.getByText('Xem trước tổ hợp từ backend'))
  await screen.findByText('Máy chủ trả 2 target dự kiến; đang chọn 2')
  await userEvent.selectOptions(screen.getByLabelText(/Giá trị sinh weather/), 'unknown')
  expect(screen.getByText('Máy chủ trả 2 target dự kiến; đang chọn 1')).toBeVisible()
  fireEvent.click(screen.getByText('Xác nhận thêm targets'))
  expect(onApply).toHaveBeenCalledWith([unknown])
})

it('keeps the 250 cap when appending and allows confirmed replacement', async () => {
  const existing = Array.from({ length: 250 }, (_, i) => ({ ...target, filters: { class: `class-${i}` } }))
  const { onApply } = mount(vi.fn().mockResolvedValue([target]))
  fireEvent.change(screen.getByLabelText('Targets do người dùng cung cấp (JSON)'), { target: { value: JSON.stringify(existing) } })
  fireEvent.click(screen.getByText('Áp dụng JSON targets')); onApply.mockClear()
  fireEvent.click(screen.getByLabelText('Sinh theo weather'))
  fireEvent.click(screen.getByText('Xem trước tổ hợp từ backend'))
  await screen.findByText('Máy chủ trả 1 target dự kiến; đang chọn 1')
  fireEvent.click(screen.getByText('Xác nhận thêm targets'))
  expect(screen.getByRole('alert')).toHaveTextContent('vượt giới hạn 250')
  expect(onApply).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('Xác nhận thay thế targets'))
  expect(onApply).toHaveBeenCalledWith([target])
})

it('warns when server output reaches cap and does not invent additional combinations', async () => {
  const generated = Array.from({ length: 250 }, (_, i) => ({ ...target, filters: { class: `class-${i}` } }))
  const { onApply } = mount(vi.fn().mockResolvedValue(generated))
  fireEvent.click(screen.getByLabelText('Sinh theo class')); fireEvent.click(screen.getByText('Xem trước tổ hợp từ backend'))
  expect(await screen.findByText(/Đã chạm cap 250/)).toBeVisible()
  expect(onApply).not.toHaveBeenCalled()
})

it('preserves targets on generation errors and shows empty server output', async () => {
  const generate = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([])
  const { onApply } = mount(generate)
  fireEvent.click(screen.getByLabelText('Sinh theo weather')); fireEvent.click(screen.getByText('Xem trước tổ hợp từ backend'))
  expect(await screen.findByRole('alert')).toHaveTextContent('targets hiện tại vẫn được giữ')
  fireEvent.click(screen.getByText('Xem trước tổ hợp từ backend'))
  expect(await screen.findByText('Không có tổ hợp phù hợp hoặc thiếu dữ liệu. Targets hiện tại không đổi.')).toBeVisible()
  expect(screen.getByText('Xác nhận thêm targets')).toBeDisabled()
  expect(onApply).not.toHaveBeenCalled()
})

it('deduplicates by slice and unit while preserving existing values and dimension order', () => {
  const reversed = { ...target, filters: { timeofday: 'night', weather: 'rain' }, target: 99 }
  expect(mergeCoverageTargets([target], [reversed])).toEqual({ targets: [target], duplicates: 1 })
  const parsed = JSON.parse(targetsJSON([target])) as CoverageTarget[]
  expect(parsed).toEqual([target])
  expect(Object.keys(parsed[0]!.filters)).toEqual(['weather', 'timeofday'])
  expect(coverageTargetsSchema.safeParse([{ ...target, weight: Infinity }]).success).toBe(false)
})

it('exports applied server configuration rather than an unsaved draft', async () => {
  let exported!: Blob
  vi.stubGlobal('URL', { createObjectURL: vi.fn((blob: Blob) => { exported = blob; return 'blob:targets' }), revokeObjectURL: vi.fn() })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  mount(undefined, [target])
  fireEvent.click(screen.getByText('Xuất targets JSON đang áp dụng'))
  const reader = new FileReader()
  const text = new Promise<string>((resolve) => { reader.onload = () => resolve(String(reader.result)) })
  reader.readAsText(exported)
  expect(JSON.parse(await text)).toEqual([target])
  await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:targets'))
})
