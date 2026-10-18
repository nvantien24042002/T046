import { useEffect, useRef, useState } from 'react'
import { ImagePreview } from './ImagePreview'
import { publicCell } from './presentation'

export function ImageGallery({ rows }: { rows: Record<string, unknown>[] }) {
  const opener = useRef<HTMLButtonElement | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  useEffect(() => { if (!selected) opener.current?.focus() }, [selected])
  return <><div className="image-gallery">{rows.map((row, index) => <article key={index} className="image-card">
    {typeof row.image_path === 'string' && row.image_path ? <button className="image-open" aria-label="Mở ảnh xem trước" onClick={(event) => { opener.current = event.currentTarget; setSelected(row.image_path as string) }}>
      <ImagePreview imagePath={row.image_path} alt="Ảnh trong bộ dữ liệu CVAT" />
    </button> : <p>Ảnh xem trước chưa được cung cấp.</p>}
    {row.status !== undefined && <p>Trạng thái: {publicCell(row.status)}</p>}
    {row.score !== undefined && <p>Điểm: {publicCell(row.score)}</p>}
  </article>)}</div>{selected && <ImageViewer path={selected} onClose={() => setSelected(null)} />}</>
}
function ImageViewer({ path, onClose }: { path: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [annotated, setAnnotated] = useState(false)
  useEffect(() => {
    const element = dialog.current
    element?.showModal()
    return () => element?.close()
  }, [])
  return <dialog ref={dialog} className="image-viewer" aria-label="Xem ảnh dữ liệu" onCancel={(event) => { event.preventDefault(); onClose() }}>
    <div className="viewer-toolbar"><h2>Xem ảnh</h2><button onClick={onClose}>Đóng ảnh</button></div>
    <label><input type="checkbox" checked={annotated} onChange={(event) => setAnnotated(event.target.checked)} />Hiển thị box CVAT</label>
    <ImagePreview imagePath={path} annotated={annotated} alt={annotated ? 'Ảnh với bounding box CVAT' : 'Ảnh trong bộ dữ liệu CVAT'} />
  </dialog>
}
