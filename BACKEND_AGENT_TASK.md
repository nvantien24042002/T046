# Task Brief: Tách Backend cho CVAT Quality Dashboard

## 1. Bối cảnh repo

Repo hiện tại là `CVAT_Dashboard_Scene_Finder` (Python/Streamlit). Đây là phiên bản dashboard có nhiều chức năng QA/QC cho dữ liệu CVAT:

- Tổng quan dataset.
- Phân bố class, bounding box, thuộc tính annotation; cảnh báo class imbalance.
- Image Quality: blur, brightness, contrast, noise, resolution và trạng thái PASS/REVIEW/ERROR.
- Label Check A+B: kiểm tra geometry và các visual signals trên bounding box.
- Review ảnh gốc/ảnh có bounding box.
- Xuất báo cáo CSV.
- Coverage & Slice theo class/weather/timeofday/occluded.
- Scene Finder lọc theo weather, time of day, tên/đường dẫn ảnh.

Hiện chưa có backend API riêng. `app.py` đang trộn Streamlit UI và logic đọc ZIP, parse CVAT XML, phân tích ảnh, audit label. Một số logic đã tách ra ở `core/distribution.py` và `core/coverage_dynamic.py`; các module `core/coverage_ui.py` và `core/scene_finder.py` vẫn phụ thuộc Streamlit nên không được import trực tiếp vào backend API.

## 2. Mục tiêu

Xây dựng backend FastAPI để frontend React có thể gọi qua HTTP trong giai đoạn tiếp theo. Đây là nhiệm vụ **tách và đóng gói logic hiện có thành API**, không phải viết lại thuật toán QA/QC từ đầu và không phải xây frontend mới.

## 3. Quy tắc bắt buộc

1. Trước khi sửa, đọc toàn bộ repo và tạo `docs/BACKEND_PLAN.md` ghi kiến trúc, luồng dữ liệu, API contract và kế hoạch kiểm thử. Sau đó tiếp tục triển khai theo kế hoạch, không dừng lại để hỏi lại những chi tiết có thể quyết định hợp lý cho MVP.
2. Giữ nguyên hành vi/ngưỡng/ý nghĩa nghiệp vụ hiện có nếu không có lỗi rõ ràng. Không tự đổi thuật toán hoặc tên trạng thái như `PASS`, `REVIEW`, `ERROR`, `NO_SIGNAL`, `A_ONLY`.
3. Tách pure business logic khỏi Streamlit: backend service không được `import streamlit`, không gọi `st.*`, không phụ thuộc session state hay UI progress widgets. Nếu cần progress, dùng callback tùy chọn hoặc bỏ progress khỏi service.
4. Không import `app.py` từ backend vì file đó khởi tạo Streamlit UI khi import.
5. Giữ dashboard cũ chạy được bằng `streamlit run app.py`. Không xóa/gộp lại UI cũ khi backend chưa hoàn tất và được kiểm thử.
6. Không lưu ảnh/ZIP upload trong thư mục public/static. Không giải nén ZIP trực tiếp vào đường dẫn do người dùng kiểm soát.
7. Không làm lộ full filesystem paths trong response/log/API. Sanitize `NaN`/`Infinity` thành JSON `null` hoặc một biểu diễn được schema quy định.
8. Không thêm database, authentication hoặc hàng đợi tác vụ phức tạp vào MVP nếu chưa cần. Dùng lưu ZIP theo `dataset_id` trong thư mục cấu hình được, có giới hạn upload và hướng dẫn dọn dữ liệu; ghi rõ giới hạn này trong README.
9. Không commit/push tự động. Không xóa dữ liệu hoặc file người dùng. Không thay đổi frontend.
10. Viết code có type hints, xử lý lỗi rõ ràng, logging không chứa dữ liệu ảnh nhạy cảm, và tests tự động.

## 4. Kiến trúc mục tiêu gợi ý

Có thể điều chỉnh cấu trúc nếu giải thích rõ trong `docs/BACKEND_PLAN.md`, nhưng hãy giữ ranh giới UI / domain / HTTP rõ ràng:

```text
CVAT_Dashboard_Scene_Finder/
├── app.py                         # Streamlit cũ, vẫn chạy
├── core/
│   ├── image_quality.py           # logic image quality thuần
│   ├── cvat_parser.py              # đọc ZIP + parse CVAT XML
│   ├── label_audit.py              # geometry/visual audit, không phụ thuộc Streamlit
│   ├── distribution.py             # logic hiện có, chỉnh nếu cần
│   └── coverage_dynamic.py          # logic coverage hiện có, chỉnh nếu cần
├── backend/
│   ├── main.py                     # FastAPI app / lifespan / health
│   ├── api/routes/                 # route handlers
│   ├── schemas/                    # Pydantic request/response schemas
│   ├── services/                   # điều phối nghiệp vụ, không chứa HTTP/UI
│   ├── storage/                    # lưu và truy xuất ZIP theo dataset_id
│   └── requirements.txt            # dependency backend riêng
├── tests/
│   ├── core/
│   └── api/
├── docs/BACKEND_PLAN.md
└── README_BACKEND.md
```

Không bắt buộc phải tạo tất cả file trên nếu không cần thiết; tránh tạo abstraction chỉ để cho đủ cấu trúc. Giữ `core` có thể được gọi cả từ Streamlit và backend, không có dependency ngược từ `core` sang `backend`/Streamlit.

## 5. API contract MVP

Dùng prefix `/api/v1`; OpenAPI tự sinh của FastAPI phải truy cập được tại `/docs`.

- `GET /health` — health check, trả về trạng thái service.
- `POST /api/v1/datasets` — nhận một file ZIP qua multipart upload; kiểm tra định dạng/kích thước, cấp `dataset_id`, lưu file an toàn và trả summary/khả năng dữ liệu.
- `GET /api/v1/datasets/{dataset_id}/summary` — tổng số ảnh, ảnh đọc được/lỗi, tổng box, số class, cảnh báo/bỏ qua và khả năng thực hiện từng nhóm phân tích.
- `GET /api/v1/datasets/{dataset_id}/distributions` — phân bố class, box, attributes, bbox area/occlusion, weather/timeofday và cảnh báo imbalance; các ngưỡng cảnh báo có schema rõ ràng.
- `GET /api/v1/datasets/{dataset_id}/scenes` — lọc danh sách ảnh theo `weather`, `timeofday`, `keyword`, có `page`/`page_size` và tổng số kết quả.
- `POST /api/v1/datasets/{dataset_id}/image-quality/analyze` — chạy Image Quality với threshold config tùy chọn; trả summary + rows, không trả raw image bytes trong JSON.
- `POST /api/v1/datasets/{dataset_id}/label-audit/analyze` — chạy Label Check A+B với threshold config tùy chọn; trả summary + rows.
- `GET /api/v1/datasets/{dataset_id}/images/{image_id}/preview` — trả ảnh preview phù hợp; không nhận filesystem path từ client.
- `GET /api/v1/datasets/{dataset_id}/reports/{report_name}.csv` — tải CSV cho các report hỗ trợ (ít nhất image quality, label audit, class distribution, attributes/box metrics); từ chối report name không nằm trong allowlist.
- `POST /api/v1/datasets/{dataset_id}/coverage/evaluate` — nhận targets/slices theo schema, trả support, target, gap, attainment, confidence/metadata unresolved và recommendations.

Nếu phát hiện endpoint nào nên dùng POST thay vì GET vì request lớn hoặc cấu hình phức tạp, có thể thay đổi, nhưng phải ghi rõ lý do trong OpenAPI/README. Mọi response phải có schema ổn định và ví dụ JSON trong docs. API phải phân biệt rõ lỗi file không hợp lệ, dataset không tồn tại, request sai và lỗi phân tích nội bộ.

## 6. An toàn upload và lỗi

- Chỉ chấp nhận ZIP; đặt giới hạn tổng kích thước upload qua config/environment variable.
- Xác thực ZIP trước khi đọc; kiểm tra số lượng entry, kích thước giải nén tổng, kích thước XML, kích thước ảnh riêng lẻ; chặn zip-bomb / ảnh quá lớn hợp lý.
- Không extract ZIP theo path gốc của archive; chỉ đọc entry từ archive và lưu ZIP gốc bằng UUID do server sinh.
- Phân biệt ZIP chỉ có ảnh (Image Quality vẫn chạy) với ZIP có `annotations.xml` (Label Check/Distribution/Coverage). Dataset thiếu annotation không nên bị từ chối toàn bộ nếu Image Quality còn sử dụng được.
- Parse XML có giới hạn kích thước; dùng parser an toàn phù hợp.
- Trả lỗi HTTP có cấu trúc, không trả stack trace nội bộ cho client.
- CORS cấu hình bằng environment variable; mặc định phát triển local cho React (`http://localhost:5173`), không bật wildcard kèm credentials.

## 7. Kiểm thử và tiêu chí nghiệm thu

Tạo dữ liệu test nhỏ bằng synthetic image + synthetic CVAT XML đóng gói thành ZIP; không phụ thuộc dataset private hay Internet.

Tối thiểu phải có tests cho:

- Health endpoint.
- Upload ZIP hợp lệ, ZIP hỏng, file không phải ZIP, ZIP không có ảnh, ZIP chỉ có ảnh và ZIP có CVAT XML.
- Parse ảnh/box, metadata weather/timeofday, attribute và geometry không hợp lệ.
- Image Quality và Label Audit trả schema đúng, JSON không chứa NaN/Infinity.
- Bộ lọc Scene Finder, phân trang và trường hợp không có kết quả.
- CSV download có header đúng và tên file an toàn.
- Coverage evaluate: slice đủ mục tiêu, thiếu mục tiêu, metadata unresolved, request sai.
- Dataset ID không tồn tại và request sai trả HTTP status phù hợp.
- Giới hạn upload và ZIP entry/size được thực thi.
- Smoke test xác nhận `streamlit run app.py` vẫn import/run được.

Cài dependency backend và chạy tests bằng lệnh được ghi trong README. Không được tuyên bố tests pass nếu chưa chạy thực tế. Nếu môi trường thiếu dependency hoặc không thể chạy một phần tests, báo rõ phần nào và lý do.

## 8. Definition of Done

- Có FastAPI backend chạy local bằng một lệnh rõ ràng trong `README_BACKEND.md`.
- API contract và ví dụ request/response được tài liệu hóa.
- Logic nghiệp vụ chính không phụ thuộc Streamlit.
- Có tests tự động cho parser/services/API.
- Dashboard Streamlit cũ không bị phá vỡ.
- Có `.env.example` hoặc bảng environment variables cho upload directory, max upload size và CORS origins; không đưa secret thật vào repo.
- Báo cáo cuối gồm: kiến trúc đã chọn, endpoints, file thay đổi, lệnh chạy, kết quả tests, giới hạn MVP/risk còn lại và các bước frontend cần dùng.

## 9. Thứ tự triển khai mong muốn

1. Audit repo, ghi `docs/BACKEND_PLAN.md`, xác định các hàm cần tách khỏi `app.py`.
2. Refactor core logic sang module thuần Python; sửa `app.py` để import lại và giữ UI tương đương; thêm tests core.
3. Xây FastAPI, schemas, upload/storage, health và dataset summary.
4. Xây từng nhóm endpoint phân bố/scene finder, image quality, label audit, preview/CSV, coverage.
5. Chạy toàn bộ tests và smoke test Streamlit; sửa regressions.
6. Cập nhật README, API examples và báo cáo bàn giao.

Ưu tiên tính đúng và bảo toàn nghiệp vụ trước khi tối ưu hiệu năng. Nếu một phép phân tích quá nặng với synchronous HTTP, ghi lại giới hạn và đề xuất job queue cho giai đoạn sau; không tự âm thầm thêm hệ thống phức tạp vào MVP.
