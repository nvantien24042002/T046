import { Coverage } from './pages/Coverage'
import { SceneFinder } from './pages/SceneFinder'
import { BrowserDatasetPanel } from './components/BrowserDatasetPanel'
import { NavLink, Outlet, Route, Routes, Link, useLocation } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'
import { config } from './config'
import { Dashboard } from './pages/Dashboard'
import { Overview } from './pages/Overview'
import { ImageQuality } from './pages/ImageQuality'
import { LabelCheck } from './pages/LabelCheck'
import { Distribution } from './pages/Distribution'
import { Review } from './pages/Review'
import { Reports } from './pages/Reports'
import { BrowserSessionProvider } from './context/BrowserSessionProvider'

import { Upload } from './pages/Upload'

export const destinations = [
  ['/distribution', 'Phân bố & thuộc tính'],
  ['/quality', 'Image Quality'],
  ['/labels', 'Label Check A+B'],
  ['/review', 'Review ảnh'],
  ['/reports', 'Xuất báo cáo'],
  ['/coverage', 'Coverage & Slice'],
  ['/scene-finder', 'Scene Finder'],
] as const

function Layout() {
  const location = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)
  const main = useRef<HTMLElement>(null)
  const firstRender = useRef(true)

  useEffect(() => {
    if (firstRender.current) { firstRender.current = false; return }
    main.current?.focus()
  }, [location.pathname])

  return (
    <div className="workspace">
      <a className="skip-link" href="#main">Đến nội dung chính</a>
      <aside className="sidebar">
        <Link className="brand" to="/">CVAT<span>Dataset workspace</span></Link>
        <button className="nav-toggle" aria-expanded={menuOpen} aria-controls="main-navigation" onClick={() => setMenuOpen(!menuOpen)}>Menu điều hướng</button>
        <nav id="main-navigation" className={menuOpen ? 'is-open' : ''} aria-label="Điều hướng chính">
          <NavLink onClick={() => setMenuOpen(false)} end to="/">Tổng quan</NavLink>
          <NavLink onClick={() => setMenuOpen(false)} to="/upload">Tải lên dữ liệu (Upload)</NavLink>
          <NavLink onClick={() => setMenuOpen(false)} to="/overview">Tổng quan dữ liệu</NavLink>
          {destinations.map(([path, title]) => <NavLink onClick={() => setMenuOpen(false)} key={path} to={path}>{title}</NavLink>)}
        </nav>
        <p className="sidebar-note">Tín hiệu hỗ trợ review, không phải kết luận annotation đúng hoặc sai.</p>
      </aside>
      <div className="content">
        <header>
          <span>Scene Finder</span>
          <strong className="mode">{config.mode === 'backend' ? 'Dữ liệu từ Backend · Không dùng dữ liệu mẫu' : 'Chế độ phát triển · Không gửi upload'} </strong>
        </header>
        <main id="main" tabIndex={-1} ref={main}>
          {config.mode === 'backend' && <BrowserDatasetPanel />}
          <Outlet />
        </main>
        <footer>Thông số từ backend; QA/QC là tín hiệu hỗ trợ review.</footer>
      </div>
    </div>
  )
}


export function App() {
  return (
    <BrowserSessionProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/distribution" element={<Distribution />} />
          <Route path="/quality" element={<ImageQuality />} />
          <Route path="/labels" element={<LabelCheck />} />
          <Route path="/review" element={<Review />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/coverage" element={<Coverage />} />
          <Route path="/scene-finder" element={<SceneFinder />} />
          <Route path="*" element={<><h1>Không tìm thấy trang</h1><p>Địa chỉ này không thuộc dashboard.</p><Link to="/">Về tổng quan</Link></>} />
        </Route>
      </Routes>
    </BrowserSessionProvider>
  )
}
