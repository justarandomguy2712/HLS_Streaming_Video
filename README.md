# HLS Binary Cloud Streaming

Dự án xử lý MP4 trên Windows, phân mảnh HLS fMP4 và lưu media lâu dài trên R2 private. Một website Vercel cung cấp cùng trang đăng nhập cho Admin và User. Worker kiểm tra quyền và truy xuất D1/R2. Các tài khoản và video đã công bố vẫn hoạt động khi companion Windows dừng.

- [Tự triển khai GitHub, Cloudflare D1/Worker/R2 và Vercel](docs/DEPLOYMENT.md)
- [Đăng nhập, tạo User và phân quyền](docs/AUTHENTICATION.md)
- [Companion Windows, upload, thử lại và tùy chọn MP4 nguồn](docs/LOCAL_PROCESSING.md)
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

Nếu PowerShell chưa tìm được `py`, dùng đường dẫn tuyệt đối tới Python đã cài để tạo venv. Giữ credential R2 và khóa dịch vụ trong `.env` riêng. Worker có binding D1/R2 và lưu khóa dịch vụ trong Workers Secrets; endpoint HTTPS là cấu hình duy nhất của frontend.

## Xử lý video

Sau khi người quản trị triển khai Worker và bootstrap D1:

```powershell
.\.venv\Scripts\python.exe -m backend.local_processing add 'D:\Videos\sample.mp4'
```

Companion xác thực Admin, đọc MP4 theo khối trên Windows và dùng pipeline Phase 07. R2 được xác minh trước khi Worker công bố READY trong D1. Nếu lỗi publication, dùng `resume VIDEO-UUID`. Tệp MP4 nguồn giữ nguyên theo mặc định. Xem tùy chọn lưu thêm source hoặc Recycle Bin trong [hướng dẫn companion](docs/LOCAL_PROCESSING.md). Có thể chạy `import-ready` để chuyển video đã có trên cùng bucket sang danh mục D1.

Cấu hình mặc định dùng AUTO, ưu tiên Stream Copy, giữ kích thước và FPS, giới hạn upload 50 GiB và để trống đĩa 10 GiB. Các giá trị có thể thay đổi trong `.env`. HLS segment mục tiêu 4 giây. NVENC có CPU fallback. Trình phát dùng H.264 fMP4 và AAC-LC khi có audio; khả năng giải mã phụ thuộc trình duyệt.

## Kiểm thử

Thực hiện [bộ kiểm thử cục bộ](docs/DEPLOYMENT.md#kiểm-thử-trước-khi-deploy). FastAPI và UI admin cũ chỉ được giữ cho hồi quy Phase 01–07, tắt mặc định. Để chạy riêng bài cũ, đặt biến PowerShell `$env:ENABLE_LEGACY_LAB_HTTP = 'true'` trong môi trường lab chưa cấu hình `WORKER_ENDPOINT`, rồi dùng Uvicorn với `--host 127.0.0.1`. Deployment dùng duy nhất site Vercel. Companion production không mở cổng.

