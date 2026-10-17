# CVAT Dashboard — Modern UI

Phiên bản thay đổi **giao diện**, không thay đổi thuật toán tính Image Quality, Label Check A/B, Distribution, Coverage & Slice.

## Cài đặt

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Điểm mới
- Header dạng hero, nền gradient tối, sidebar và tabs đồng bộ.
- KPI cards, data tables, upload, button và expander được tinh chỉnh.
- Màu biểu đồ Plotly thống nhất qua theme.
- Theme được tách trong `ui_theme.py` để có thể sửa độc lập.

## Lưu ý
- Đây là CSS tùy chỉnh Streamlit; selector dựa trên `data-testid` có thể cần cập nhật khi Streamlit thay đổi phiên bản.
- Font Inter tải qua Google Fonts; nếu offline, hệ thống sẽ dùng sans-serif.
- 4 file gốc không bị chỉnh sửa.
