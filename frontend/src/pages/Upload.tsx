import { BrowserDatasetUploader as DatasetUploader } from '../components/BrowserDatasetUploader'

export function Upload() {
  return (
    <>
      <p className="intro">Nhập dữ liệu CVAT</p>
      <h1>Tải lên & Khởi tạo Dataset</h1>
      <p className="lead">
        Tải lên tệp ZIP chứa ảnh và annotations.xml của CVAT để thực hiện kiểm tra chất lượng ảnh, kiểm tra nhãn và phân tích phân bố dữ liệu.
      </p>
      <DatasetUploader />
    </>
  )
}
