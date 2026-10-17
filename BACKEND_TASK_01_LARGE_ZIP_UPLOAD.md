# Agent Task 01 — Hỗ trợ upload ZIP dung lượng lớn (vài chục GB)

## Bối cảnh

Repository hiện tại là `CVAT_Dashboard_Scene_Finder`, một ứng dụng Python/Streamlit phân tích dataset CVAT. Đây là cải tiến backend đầu tiên và cần được ưu tiên trước các API phân tích khác.

Các vấn đề hiện tại đã thấy trong repo:

- `app.py` dùng `st.file_uploader(...)` và sau đó gọi `uploaded.getvalue()`, tức lấy toàn bộ ZIP thành một đối tượng bytes.
- ZIP được bọc qua `io.BytesIO(...)` ở nhiều hàm.
- `read_images_from_zip()` đọc từng ảnh bằng `archive.read(info)` và giữ bytes của tất cả ảnh trong list `ZipImage`.
- Hàm này hiện có giới hạn tổng kích thước ảnh giải nén 1 GB.
- Các bước Image Quality / Label Audit / Distribution / Scene Finder dùng lại `zip_data` hoặc dữ liệu ảnh đã nạp sẵn; cách này không phù hợp với ZIP có dung lượng nén hàng chục GB.
- Tăng `server.maxUploadSize` của Streamlit đơn thuần không giải quyết được vấn đề tiêu thụ RAM, thời gian chờ, tiếp tục upload khi mất kết nối, lưu trữ và đọc dữ liệu lớn.

## Mục tiêu

Thiết kế và triển khai nền tảng upload ZIP lớn cho backend FastAPI, hướng tới file ZIP cỡ vài chục GB (giá trị tối đa phải cấu hình được), với các yêu cầu:

1. Upload theo chunk, hiển thị được tiến độ từ API contract và có thể tiếp tục từ phần đã nhận sau khi client mất kết nối hoặc tải lại trang.
2. Không giữ toàn bộ ZIP hoặc toàn bộ ảnh trong RAM.
3. Lưu file vào thư mục dữ liệu riêng trên server, dùng ID do server sinh; không lưu trong `static/public`.
4. Kiểm tra toàn vẹn, định dạng ZIP, kích thước và giới hạn bảo vệ tài nguyên.
5. Thiết kế API để frontend React có thể triển khai upload progress/resume trong giai đoạn sau.
6. Bảo toàn khả năng dùng dashboard Streamlit hiện có cho các dataset nhỏ hơn trong thời gian chuyển đổi; không tự tuyên bố rằng widget Streamlit hiện tại đã hỗ trợ hàng chục GB nếu chưa triển khai và kiểm thử luồng upload tương ứng.

## Quy tắc bắt buộc

- **Không chỉ tăng `server.maxUploadSize` rồi coi như hoàn thành.** Giải pháp chính cần có upload theo chunk/resumable và luồng xử lý file disk-backed.
- Không gọi `uploaded.getvalue()` hoặc `read()` không giới hạn với ZIP lớn.
- Không tạo một `bytes` object chứa toàn bộ ZIP.
- Không đọc tất cả ảnh trong archive vào một list để giữ lại trong RAM. Chỉ đọc từng entry/ảnh cần xử lý rồi giải phóng tài nguyên.
- Không giải nén toàn bộ archive trực tiếp theo đường dẫn có trong ZIP; cần tránh Zip Slip, Zip Bomb và path traversal.
- Không thay đổi thuật toán/threshold Image Quality, Label Check A+B, Distribution, Coverage hoặc Scene Finder trong task này, ngoại trừ refactor tối thiểu để đọc file từ disk/stream.
- Không import `app.py` từ backend; không để service nghiệp vụ phụ thuộc `streamlit` hoặc `st.session_state`.
- Không tự ý xóa file hoặc dữ liệu người dùng, không commit/push tự động.
- Không bắt buộc thêm database hay queue phức tạp vào task này. Nếu upload session cần metadata, có thể bắt đầu bằng metadata file/SQLite nhẹ hoặc cơ chế đơn giản tương đương; phải giải thích lý do lựa chọn và giới hạn.

## Yêu cầu thiết kế trước khi code

Đọc repo và cập nhật `docs/BACKEND_PLAN.md` trước khi refactor. Tạo thêm `docs/LARGE_UPLOAD_DESIGN.md` ghi:

- Luồng upload, lưu trữ, resume, validation và xử lý sau upload.
- Sơ đồ trạng thái upload: `created` → `uploading` → `uploaded` → `validating` → `ready` hoặc `failed`.
- Giới hạn cấu hình: dung lượng ZIP tối đa, chunk size, tổng dung lượng giải nén tối đa, số entry, kích thước `annotations.xml`, kích thước ảnh đơn, thời gian sống của upload session và chính sách cleanup.
- Dự toán ổ đĩa/RAM và các điểm nghẽn còn lại.
- Những giới hạn cần cấu hình ở reverse proxy/deployment (nếu có), timeout và giới hạn request body.
- Phân biệt rõ: backend có thể nhận ZIP vài chục GB không đồng nghĩa việc phân tích toàn bộ dataset sẽ chạy nhanh trong một HTTP request.

Nếu repo đã có cấu trúc phù hợp, ưu tiên dùng lại thay vì tạo nhiều lớp abstraction không cần thiết.

## API upload mong muốn

Có thể điều chỉnh tên/chi tiết endpoint nếu có lý do kỹ thuật, nhưng API contract phải hỗ trợ resume, retry và báo tiến độ. Một thiết kế tham khảo:

1. `POST /api/v1/uploads`
   - Request JSON gồm `filename`, `size_bytes`, và `sha256` nếu client có thể cung cấp.
   - Tạo `upload_id`, trả `chunk_size_bytes`, `received_bytes`, `next_offset`/trạng thái và giới hạn upload đã áp dụng.
   - Từ chối ngay nếu tên/đuôi file không hợp lệ, dung lượng vượt giới hạn hoặc thiếu dung lượng ổ đĩa an toàn.

2. `PUT /api/v1/uploads/{upload_id}/chunks/{chunk_index}` (hoặc API theo `Content-Range`)
   - Nhận một chunk nhị phân giới hạn kích thước.
   - Ghi chunk xuống đĩa; không tích lũy toàn bộ file trong RAM.
   - Kiểm tra offset/chunk index, chunk size và trạng thái upload.
   - Quy định rõ việc retry cùng chunk: idempotent hoặc trả lỗi có cấu trúc; không làm hỏng file nếu request bị ngắt giữa chừng.

3. `GET /api/v1/uploads/{upload_id}`
   - Trả trạng thái, kích thước dự kiến/đã nhận, tiến độ phần trăm, chunk/offset tiếp theo và thông tin resume cần thiết.

4. `POST /api/v1/uploads/{upload_id}/complete`
   - Chỉ hoàn tất nếu đã nhận đủ bytes.
   - Xác minh kích thước cuối cùng và checksum nếu được cung cấp.
   - Mở/kiểm tra ZIP từ file trên đĩa, không đọc toàn bộ ZIP vào RAM.
   - Đăng ký `dataset_id` và trả trạng thái `validating`/`ready` phù hợp; không chạy một tác vụ phân tích nặng kéo dài bên trong request upload.

5. `DELETE /api/v1/uploads/{upload_id}`
   - Hủy upload tạm và dọn file do upload đó tạo ra; chỉ xóa đúng thư mục/file do server quản lý.

6. `GET /api/v1/datasets/{dataset_id}/summary`
   - Có thể thêm hoặc sửa tối thiểu để trả số entry/ảnh ước tính, có annotation hay không, kích thước file và trạng thái validation.

Mỗi endpoint cần có schema Pydantic ổn định, ví dụ request/response trong tài liệu, status code và lỗi JSON có cấu trúc. Không trả stack trace hay đường dẫn filesystem tuyệt đối cho client.

## Lưu trữ và xử lý ZIP dung lượng lớn

- Lưu ZIP gốc theo ID server sinh trong `UPLOAD_DIR`/`DATASET_STORAGE_DIR` cấu hình bằng environment variable. Dùng file tạm khi upload; chỉ đổi tên nguyên tử sang trạng thái hoàn chỉnh khi việc ghi đã thành công.
- Giữ dữ liệu upload tạm tách biệt với dataset đã hoàn tất.
- Kiểm tra dung lượng đĩa khả dụng trước khi bắt đầu và trong lúc nhận chunk; xử lý rõ trường hợp hết dung lượng.
- Thiết kế mở archive trực tiếp từ filesystem path (`zipfile.ZipFile(path)` hoặc cơ chế tương đương), không phải `ZipFile(io.BytesIO(zip_bytes))` cho dataset lớn.
- Duyệt ZIP theo entry; đọc từng ảnh qua file-like stream khi có thể (`archive.open(info)`), xử lý xong thì giải phóng bộ nhớ.
- Với các tác vụ cần nhiều lần truy cập archive, mở/đóng ZIP an toàn theo phạm vi xử lý; không cache toàn bộ nội dung ảnh trong session.
- Phân biệt giới hạn **kích thước ZIP nén** với **tổng kích thước sau giải nén**. Cả hai phải cấu hình được; không bỏ giới hạn bảo vệ chỉ để chấp nhận file lớn.
- Với ZIP không có `annotations.xml`, cho phép đánh dấu dataset có thể chạy Image Quality nhưng không thể chạy Label Audit/Distribution/Coverage phụ thuộc annotation.
- Không yêu cầu giải nén tất cả ảnh ra disk nếu các phép tính có thể đọc trực tiếp từ ZIP; nếu có lý do cần extract, phải dùng tên/path do server kiểm soát và kiểm tra path traversal.
- Có cleanup cho upload dở dang/hết hạn và hướng dẫn người vận hành cách dọn dataset; không âm thầm xóa dataset đang dùng.

## Hiệu năng và UX

- API phải giúp frontend hiển thị tiến độ upload và khôi phục phiên upload sau khi refresh/reconnect.
- Trả lỗi có thể hành động: vượt dung lượng tối đa, upload hết hạn, chunk sai thứ tự/offset, checksum sai, ZIP hỏng, ZIP không có ảnh, thiếu dung lượng đĩa.
- Không hứa thời gian hoàn tất phân tích trước khi đo benchmark.
- Thêm logging có `upload_id`/`dataset_id`, trạng thái và bytes xử lý; không log ảnh, token hoặc nội dung nhạy cảm.
- Tài liệu rõ rằng để thật sự tải file vài chục GB qua mạng, giới hạn ở proxy/load balancer, server timeout, reverse proxy body-size và ổ đĩa phải phù hợp. Những config này cần được liệt kê dù không nằm trong repo.

## Kiểm thử tối thiểu

Dùng ZIP synthetic nhỏ và chunk size giảm trong test để mô phỏng protocol; không cần commit file test vài chục GB.

- Tạo upload session và từ chối file vượt giới hạn.
- Upload tuần tự nhiều chunk, kiểm tra progress/next offset.
- Retry chunk; chunk sai index/offset; chunk vượt max size.
- Mất kết nối giữa chừng/mô phỏng upload chưa hoàn chỉnh rồi resume.
- Complete thành công, sai tổng bytes, checksum không đúng.
- ZIP hợp lệ; ZIP hỏng; file không phải ZIP; ZIP chỉ có ảnh; ZIP có `annotations.xml`; ZIP không có ảnh.
- Dọn/hủy upload; session hết hạn; dataset/upload ID không tồn tại.
- ZIP có entry path độc hại, entry count quá lớn, tổng uncompressed size vượt ngưỡng và ảnh đơn quá lớn.
- Chứng minh code path xử lý không gọi `getvalue()` và không nạp toàn bộ ZIP/ảnh vào RAM.
- Test parser/service mở archive từ filesystem path và trả cùng kết quả nghiệp vụ với fixture nhỏ hiện tại.
- Kiểm thử giới hạn đĩa/lỗi ghi file ở mức có thể mô phỏng.
- Chạy regression/smoke test để xác nhận Streamlit hiện tại vẫn chạy với dataset nhỏ, nếu môi trường cho phép.

Không cần chạy benchmark thực sự trên một file 20–50 GB nếu không có file và máy đủ tài nguyên. Thay vào đó, báo rõ điều gì được kiểm chứng bằng test và điều gì vẫn cần benchmark trên môi trường triển khai.

## Tiêu chí nghiệm thu

- Có tài liệu kiến trúc upload lớn và cấu hình vận hành.
- API hỗ trợ upload chia chunk, kiểm tra tiến độ, resume/retry và hủy upload.
- ZIP được lưu disk-backed; không có đường đi nào yêu cầu nạp toàn bộ ZIP vào RAM.
- Việc đọc/parse ảnh được sửa để có thể xử lý theo từng entry/ảnh, không giữ bytes của toàn bộ dataset cùng lúc.
- Giới hạn file nén, giải nén, entry count, XML và ảnh riêng được cấu hình và kiểm thử.
- Dataset được xác minh trước khi đánh dấu hoàn tất; lỗi có schema ổn định.
- Tests tự động được chạy thật và có báo cáo chính xác; không tuyên bố pass với test chưa chạy.
- Dashboard Streamlit cũ vẫn hoạt động cho workflow cũ hoặc những giới hạn của nó được ghi rõ. Không nói rằng upload vài chục GB hoạt động trên giao diện Streamlit cũ nếu chưa có kiểm chứng end-to-end.
- Không thêm frontend React trong task này; chỉ thiết kế API và contract để React dễ triển khai sau.

## Thứ tự thực hiện

1. Audit code liên quan đến upload/ZIP, lập `docs/LARGE_UPLOAD_DESIGN.md` và cập nhật `docs/BACKEND_PLAN.md`.
2. Tạo config và storage layer disk-backed.
3. Triển khai upload API theo chunk/resume/retry và validation.
4. Tách các hàm đọc ZIP trong `app.py` sang service/core có thể đọc từ filesystem path và xử lý ảnh theo luồng; giữ thuật toán nghiệp vụ hiện có.
5. Bổ sung tests protocol, storage, archive validation và regression tests.
6. Cập nhật `.env.example`, README và ví dụ `curl`/client flow thể hiện init → upload chunks → status/resume → complete.
7. Báo cáo file đã sửa, cách chạy, tests đã thực thi, giới hạn còn lại và bước frontend React sẽ cần làm tiếp theo.

## Báo cáo cuối agent phải trả

- Kiến trúc upload được chọn và vì sao.
- Danh sách endpoint với request/response ví dụ.
- Các config liên quan cùng giá trị mặc định.
- File thay đổi.
- Kết quả test thực tế.
- Những phần chưa kiểm chứng trên file thật vài chục GB.
- Checklist triển khai môi trường thật (ổ đĩa, RAM, proxy, timeout, cleanup, quyền truy cập).
