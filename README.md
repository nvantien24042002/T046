# T046 — N2-05B Dashboard phân bố class và thuộc tính dữ liệu

## 1. Giới thiệu

N2-05B là dashboard hỗ trợ QA/QC dữ liệu annotation, giúp theo dõi phân bố class, thuộc tính dữ liệu và nhận diện các trường hợp cần kiểm tra thêm.

- **Demo:** https://t046.onrender.com
- **GitHub:** https://github.com/nvantien24042002/T046
- **Branch:** `main`
- **Commit triển khai:** `c50d606`

## 2. Chức năng chính

- **Overview:** Tổng quan dataset.
- **Distribution & Attributes:** Phân bố class và thuộc tính dữ liệu.
- **Image Quality:** Thống kê và cấu hình kiểm tra chất lượng ảnh.
- **Label Check A+B:** Hỗ trợ rà soát các trường hợp nhãn đáng ngờ.
- **Coverage Targets:** Theo dõi mục tiêu độ phủ dữ liệu.

## 3. Hướng dẫn demo

1. Mở [Dashboard Demo](https://t046.onrender.com).
2. Tải lên file ZIP dataset được phép sử dụng.
3. Chờ hệ thống xử lý dữ liệu.
4. Khám phá các mục chức năng trên dashboard.

## 4. Lưu ý khi kiểm thử

**Trạng thái hiện tại:** Bản MVP đang trong giai đoạn kiểm thử.

- Hệ thống từng gặp lỗi phiên upload `429 ONE_ACTIVE_UPLOAD`. Nếu upload thất bại, không bấm thử liên tục; hãy ghi nhận lỗi và báo người phụ trách.
- Các file JavaScript/CSS từng trả về `403 Forbidden` trên Render. Nếu giao diện không tải đầy đủ, thử tải lại trang và báo lỗi nếu vẫn tái hiện.
- Khả năng upload đồng thời và lưu dữ liệu qua các lần restart chưa được xác nhận đầy đủ.
