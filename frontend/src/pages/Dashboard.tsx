import { useEffect, useState } from 'react'
import { config } from '../config'
import { readHealth, type Health } from '../api/health'
import { ApiError } from '../api/client'
import { ErrorState, LoadingState } from '../components/States'
import { BrowserDatasetUploader as DatasetUploader } from '../components/BrowserDatasetUploader'

type Connection = { kind: 'idle' | 'loading' } | { kind: 'success'; health: Health } | { kind: 'error'; message: string }
export function Dashboard() {
  const [connection, setConnection] = useState<Connection>({ kind: 'idle' })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (attempt === 0) return
    const controller = new AbortController()
    let active = true
    readHealth(config.baseUrl, { signal: controller.signal }).then(
      (health) => { if (active) setConnection({ kind: 'success', health }) },
      (error: unknown) => { if (active) setConnection({ kind: 'error', message: error instanceof ApiError ? error.message : 'Kiểm tra cấu hình Backend URL.' }) },
    )
    return () => { active = false; controller.abort() }
  }, [attempt])
  return <>
    <p className="intro">Không gian kiểm tra dữ liệu</p><h1>Tổng quan</h1>
    <p className="lead">Khám phá phân bố và tín hiệu chất lượng của dữ liệu CVAT.</p>
    
    <DatasetUploader />

    <section aria-labelledby="connection-heading"><h2 id="connection-heading">Kết nối Backend</h2><p>Chỉ kiểm tra dịch vụ qua GET /health. Không tải dataset hoặc gửi credentials.</p><button disabled={connection.kind === 'loading'} onClick={() => { setConnection({ kind: 'loading' }); setAttempt((value) => value + 1) }}>Kiểm tra kết nối</button>{connection.kind === 'loading' && <LoadingState />}{connection.kind === 'error' && <ErrorState message={connection.message} />}{connection.kind === 'success' && <p role="status">Backend sẵn sàng · Stage {connection.health.stage}. Chưa có dataset được chọn.</p>}</section>
  </>
}
