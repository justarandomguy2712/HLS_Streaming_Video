# HLS Binary Cloud Streaming

Dự án xử lý MP4 trên Windows, phân mảnh HLS fMP4 và lưu media lâu dài trên R2 private. Một website Vercel cung cấp cùng trang đăng nhập cho Admin và User. Worker kiểm tra quyền và truy xuất D1/R2. Các tài khoản và video đã công bố vẫn hoạt động khi companion Windows dừng.

- [Tự triển khai GitHub, Cloudflare D1/Worker/R2 và Vercel](docs/DEPLOYMENT.md)
- [Đăng nhập, tạo User và phân quyền](docs/AUTHENTICATION.md)
- [Upload từ website, tiến độ và GPU trên Windows](docs/FRONTEND_UPLOAD.md)
- [Companion CLI, thử lại và tùy chọn MP4 nguồn](docs/LOCAL_PROCESSING.md)
- [Kiến trúc](docs/ARCHITECTURE.md), [quản lý tệp tạm](docs/LOCAL_CLEANUP.md), [chất lượng](docs/VIDEO_PROCESSING.md), [lab JPEG/HLSLAB](docs/BINARY_PROCESSING.md)
- [Bàn giao và kết quả kiểm thử](PROJECT_STATE.md)

## Chuẩn bị trên Windows

Cài Python 3.12+ và FFmpeg có cả FFprobe, libx264 và AAC. Có thể thêm thư mục FFmpeg vào PATH hoặc điền đường dẫn `HLS_FFMPEG_BIN` và `HLS_FFPROBE_BIN` trong `.env`.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
ffmpeg -version
ffprobe -version
```

Nếu PowerShell chưa tìm được `py`, dùng đường dẫn tuyệt đối tới Python đã cài để tạo venv. Giữ credential R2 và khóa dịch vụ trong `.env` riêng. Worker có binding D1/R2 và lưu khóa dịch vụ trong Workers Secrets. Cấu hình `frontend/js/config.js` chỉ chứa origin HTTPS của Worker.

## Xử lý video

Sau khi triển khai Worker và bootstrap D1, điền `BROWSER_ADMIN_ORIGIN=https://ten-site.vercel.app` trong `.env`. Chạy companion một lần cho phiên làm việc:

```powershell
.\.venv\Scripts\python.exe -m backend.browser_companion
```

Trên chính máy Windows đó, mở website Vercel, đăng nhập Admin, bấm **Kết nối companion Windows**, cho phép truy cập mạng cục bộ khi trình duyệt hỏi, rồi dùng **Upload & Process**. Nếu trình duyệt chặn kết nối, dùng **Mở companion cục bộ** theo [hướng dẫn upload](docs/FRONTEND_UPLOAD.md). Bạn có thể đóng tab sau khi Windows báo đã tiếp nhận MP4. Giữ tiến trình companion chạy để hoàn tất công bố và dọn tệp tạm. Công cụ CLI vẫn hỗ trợ thao tác từ PowerShell, lưu thêm nguồn và nhập các video cũ. [Hướng dẫn CLI](docs/LOCAL_PROCESSING.md)

Cấu hình mặc định dùng AUTO, ưu tiên Stream Copy, giữ kích thước và FPS, giới hạn upload 50 GiB và để trống đĩa 10 GiB. Các giá trị có thể thay đổi trong `.env`. HLS segment mục tiêu 4 giây. NVENC có CPU fallback. Trình phát dùng H.264 fMP4 và AAC-LC khi có audio; khả năng giải mã phụ thuộc trình duyệt.

## Kiểm thử

Thực hiện [bộ kiểm thử Worker và trình phát](docs/DEPLOYMENT.md#kiểm-thử-trước-khi-deploy) và [smoke test upload trình duyệt](docs/FRONTEND_UPLOAD.md#kiểm-thử-trên-windows). FastAPI và UI Admin cũ chỉ bật để hồi quy Phase 01–07 bằng `ENABLE_LEGACY_LAB_HTTP=true` khi chưa cấu hình Worker. Companion Phase 09 lắng nghe tại `127.0.0.1:8765` trên máy xử lý.

