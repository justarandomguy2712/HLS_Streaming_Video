# Companion Windows

Trang này mô tả companion CLI chạy theo lệnh Admin. Để chọn tệp ngay trong trình duyệt và xem tiến độ, dùng [companion loopback Phase 09](FRONTEND_UPLOAD.md).

```powershell
.\.venv\Scripts\python.exe -m backend.local_processing add 'D:\Video\sample.mp4'
```

Bỏ đường dẫn để được hỏi trên console. CLI hỏi username và mật khẩu Admin, kiểm tra role từ Worker, rồi kiểm tra credential dịch vụ. Nó tạo đúng một bản MP4 tạm theo luồng trong thư mục dữ liệu ứng dụng, lưu hash và định danh tệp chính xác. FFprobe/FFmpeg dùng pipeline Phase 07. Các đoạn đã đóng được gửi lên R2, đọc lại theo khối và đối chiếu SHA-256. Playlist đã xác minh xong thì companion công bố lên D1. Chỉ sau khi D1 xác nhận READY, ứng dụng dọn bản MP4 tạm và thư mục xử lý theo cấu hình.

```powershell
.\.venv\Scripts\python.exe -m backend.local_processing resume VIDEO-UUID
.\.venv\Scripts\python.exe -m backend.local_processing import-ready
```

Nếu Worker hoặc D1 lỗi, SQLite giữ công việc và bản MP4 tạm cần thiết. Khi chạy lại, companion thu dọn tệp upload mồ côi mang UUID trong thư mục ứng dụng nếu đã cũ hơn 24 giờ. Bản MP4 đã có hàng trong SQLite được giữ để tiếp tục tác vụ. `resume` dùng lại bộ media đã xác minh nếu local đã ready. `import-ready` đăng danh mục cho các video R2-ready cũ có cùng bucket và endpoint, đọc SQLite theo lô. Nó không ghi đè media và bỏ qua tombstone video đã xóa. Giữ bucket/media cũ để di trú. Xem thêm [dọn tệp](LOCAL_CLEANUP.md).

## Tùy chọn tệp nguồn

- Mặc định R2 chỉ chứa HLS. `add ... --archive-source` in số byte phát sinh và lưu thêm MP4 sau khi HLS xác minh thành công. MP4 lưu thêm không được cung cấp qua API phát. Xóa video từ Admin cũng xóa bản lưu thêm cùng prefix.
- Mặc định giữ nguyên MP4 nguồn. `add ... --request-recycle` hoặc `DELETE_SOURCE_FILE_AFTER_SUCCESS=true` chỉ đánh dấu cho phép xác nhận ở bước riêng.
- Phát video từ website, tua đến vị trí khác và bấm **Xác nhận đã phát và tua video hiện tại**. Website chỉ bật nút sau khi nhận được sự kiện phát và tua trong phiên Admin.
- Chạy `python -m backend.local_processing recycle-source VIDEO-UUID` rồi gõ đúng câu xác nhận. Companion kiểm D1 READY, playlist key, mốc kiểm tra online, đường dẫn, định danh tệp và SHA-256 trước khi yêu cầu Windows chuyển vào Recycle Bin. Nó chỉ thao tác với đúng đường dẫn nguồn đã chọn trên ổ cục bộ, ngoài thư mục ứng dụng. Khi không đồng ý, giữ nguyên tệp. HLS không đảm bảo phục hồi lại MP4 gốc từng byte.

FastAPI HTTP Phase 01–07 phục vụ lab hồi quy qua `ENABLE_LEGACY_LAB_HTTP=true` khi chưa cấu hình Worker. Companion Phase 09 dùng khóa xử lý chung với CLI. Dừng companion trình duyệt trước khi chạy lệnh CLI cần khóa. Các endpoint online vẫn hoạt động qua D1 và R2 khi Windows dừng.

