import type { ReactNode } from 'react'
export function LoadingState() { return <div className="state" role="status" aria-live="polite"><span className="loading-mark" aria-hidden="true" />Đang tải dữ liệu…</div> }
export function EmptyState({ children }: { children: ReactNode }) { return <div className="state"><h2>Chưa có dữ liệu</h2><p>{children}</p></div> }
export function ErrorState({ message }: { message: string }) { return <div className="state error" role="alert"><h2>Không thể tải dữ liệu</h2><p>{message}</p></div> }
