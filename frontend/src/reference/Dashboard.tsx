import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { config } from '../config'
import { readHealth, type Health } from '../api/health'
import { ApiError } from '../api/client'
import { EmptyState, ErrorState, LoadingState } from '../components/States'
import { sampleRows, type Scenario } from '../mocks/scenarios'
import { DatasetUploader } from '../components/DatasetUploader'

type Connection = { kind: 'idle' | 'loading' } | { kind: 'success'; health: Health } | { kind: 'error'; message: string }
export function Dashboard() {
  const [scenario, setScenario] = useState<Scenario>('success')
  const [connection, setConnection] = useState<Connection>({ kind: 'idle' })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (config.mode !== 'backend' || attempt === 0) return
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

    {config.mode === 'mock' ? <section aria-labelledby="sample-heading">
      <div className="section-heading"><h2 id="sample-heading">Khám phá giao diện mẫu</h2><label>Trạng thái mẫu <select value={scenario} onChange={(event) => setScenario(event.target.value as Scenario)}><option value="success">Dữ liệu mẫu</option><option value="loading">Đang tải</option><option value="empty">Trống</option><option value="error">Lỗi</option></select></label></div>
      {scenario === 'loading' && <LoadingState />}
      {scenario === 'empty' && <EmptyState>Chưa chọn dataset. Đây là tình huống mẫu, không có upload thực.</EmptyState>}
      {scenario === 'error' && <ErrorState message="Lỗi mẫu có chủ đích. Chọn trạng thái khác để tiếp tục khám phá." />}
      {scenario === 'success' && <ul className="feature-list">{sampleRows.map((row) => <li key={row.destination}><div><h3><Link to={row.destination}>{row.area}</Link></h3><p>{row.description}</p></div><span className="tag">Chưa triển khai</span></li>)}</ul>}
    </section> : <section aria-labelledby="connection-heading"><h2 id="connection-heading">Kết nối Backend</h2><p>Chỉ kiểm tra dịch vụ qua GET /health. Không tải dataset hoặc gửi credentials.</p><button disabled={connection.kind === 'loading'} onClick={() => { setConnection({ kind: 'loading' }); setAttempt((value) => value + 1) }}>Kiểm tra kết nối</button>{connection.kind === 'loading' && <LoadingState />}{connection.kind === 'error' && <ErrorState message={connection.message} />}{connection.kind === 'success' && <p role="status">Backend sẵn sàng · Stage {connection.health.stage}. Chưa có dataset được chọn.</p>}</section>}
  </>
}
