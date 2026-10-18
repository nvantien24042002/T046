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

const destinationIcons: Record<string, string> = {
  '/distribution': '📈',
  '/quality': '✨',
  '/labels': '🏷️',
  '/review': '👁️',
  '/reports': '📑',
  '/coverage': '🎯',
  '/scene-finder': '🔍',
}

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
          <NavLink onClick={() => setMenuOpen(false)} end to="/">
            <span className="nav-icon" aria-hidden="true">📊</span>
            <span className="nav-label">Tổng quan</span>
          </NavLink>
          <NavLink onClick={() => setMenuOpen(false)} to="/upload">
            <span className="nav-icon" aria-hidden="true">☁️</span>
            <span className="nav-label">Tải lên dữ liệu (Upload)</span>
          </NavLink>
          <NavLink onClick={() => setMenuOpen(false)} to="/overview">
            <span className="nav-icon" aria-hidden="true">📋</span>
            <span className="nav-label">Tổng quan dữ liệu</span>
          </NavLink>
          {destinations.map(([path, title]) => (
            <NavLink onClick={() => setMenuOpen(false)} key={path} to={path}>
              <span className="nav-icon" aria-hidden="true">{destinationIcons[path] ?? '📌'}</span>
              <span className="nav-label">{title}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer-card">
          <div className="sidebar-live-pill">
            <span className="live-dot" aria-hidden="true" />
            <span className="live-text">Backend Connected</span>
          </div>
        </div>
      </aside>
      <div className="content">
        <header>
          <span>Scene Finder</span>
          <strong className="mode">Dữ liệu từ Backend · Không dùng dữ liệu mẫu</strong>
        </header>
        <main id="main" tabIndex={-1} ref={main}>
          {config.mode === 'backend' && <BrowserDatasetPanel />}
          <Outlet />
        </main>
        <footer className="app-shell-footer">
          <span>CVAT QA/QC Analytics Platform · Version N2-05B</span>
        </footer>
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
