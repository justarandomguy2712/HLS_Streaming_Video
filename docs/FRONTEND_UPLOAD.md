# Upload từ website và xử lý trên Windows

## Dùng website Vercel

1. Trong `.env` trên máy Windows xử lý video, giữ cấu hình R2, `WORKER_ENDPOINT` và `PROCESSING_SERVICE_TOKEN` đã thiết lập. Điền `BROWSER_ADMIN_ORIGIN=https://ten-site.vercel.app` bằng đúng origin production. Nếu có custom domain, điền tên miền Admin thực sự dùng.
2. Chạy companion một lần từ PowerShell trong thư mục dự án.

   ```powershell
   .\.venv\Scripts\python.exe -m backend.browser_companion
   ```

3. Trên chính máy đó, mở website Vercel và đăng nhập **Admin**. Bấm **Kết nối companion Windows** và chọn **Cho phép** nếu Chrome hoặc Edge hỏi quyền mạng cục bộ. Chọn hoặc thả một tệp MP4, nhập tiêu đề và bấm **Upload & Process**.
4. Khi trang báo Windows đã tiếp nhận MP4, bạn có thể đóng tab. Companion tiếp tục chạy trên Windows. Để xem lại tiến độ, đăng nhập lại và kết nối companion. Danh sách video cập nhật khi tác vụ đạt **READY**.
5. Nếu bước xác minh hoặc công bố lỗi, bấm **Thử lại** trong tác vụ. Tác vụ đã xác minh HLS có thể tiếp tục công bố từ dữ liệu sẵn có. Chỗ báo lỗi dọn tệp có nút thử lại riêng cho tác vụ đã công bố.

Companion chỉ lắng nghe tại `127.0.0.1:8765`. CORS cho phép đúng origin HTTPS cấu hình và origin loopback của companion. Mỗi API kiểm Bearer session qua `/api/auth/me` và yêu cầu role Admin. Trạng thái upload chỉ cấp cho đúng chủ tác vụ. MP4 đi từ trình duyệt tới đĩa Windows theo luồng. R2 nhận bộ HLS qua Boto3, còn Vercel phục vụ HTML/JS tĩnh. [Quyền truy cập mạng cục bộ của Chrome](https://developer.chrome.com/docs/modern-web-guidance/guides/security/local-network-access)

## Mở companion cục bộ

Nếu trình duyệt chặn kết nối từ website, mở **Mở companion cục bộ** hoặc nhập `http://127.0.0.1:8765/` trên máy chạy companion. Trang này phục vụ cùng UI đã có và gửi MP4 về cùng origin. Để đăng nhập và phát thông qua Worker từ địa chỉ đó, thêm `http://127.0.0.1:8765` vào danh sách `ALLOWED_ORIGINS` của Worker, kèm origin Vercel hiện tại, rồi triển khai lại Worker.

```powershell
npx.cmd wrangler deploy --config cloudflare/wrangler.toml
```

Browser companion giữ khóa SQLite dùng chung với CLI. Dừng tiến trình companion trước khi chạy một lệnh CLI xử lý video trên cùng thư mục dữ liệu. Hàng đợi được lưu trên Windows. Khi Windows khởi động lại, mở lại companion để tiếp tục tác vụ đã nhận đủ MP4.

## Đọc tiến độ

| Chỉ số | Nguồn |
| --- | --- |
| Truyền MP4 đến Windows | Số byte từ `XMLHttpRequest.upload`. HTTP 202 xác nhận đã ghi xong tệp tạm và đăng ký tác vụ trong SQLite. |
| FFmpeg | `out_time_us` và `speed` từ `-progress pipe:1`. Phần trăm là thời lượng đã xử lý chia thời lượng FFprobe. Tốc độ hiển thị theo bội số thời gian thực. |
| Upload R2 | Boto3 Transfer Callback đo byte gửi. Phần trăm dùng số byte của object đã được R2 xác nhận. Bộ đếm segment tăng sau khi object được đọc lại và xác minh. Tốc độ là trung bình từ lúc bắt đầu gửi byte. |
| Tổng và ETA | Total R2 chỉ có sau khi biết kích thước toàn bộ HLS, gồm playlist và metadata. Trước đó hiển thị số byte và số đoạn đã xử lý. ETA FFmpeg dùng thời lượng còn lại và tốc độ FFmpeg. ETA upload chỉ hiện khi có mẫu số và tốc độ. |
| Xác minh, công bố và dọn tệp | Trạng thái lần lượt gồm `ANALYZING`, `PROCESSING`, `UPLOADING`, `VERIFYING`, `PUBLISHING`, `CLEANING_UP`, `READY` hoặc `FAILED`. Hai thanh FFmpeg và R2 có thể cùng chạy. READY đợi xác minh và ghi danh mục D1. Lỗi dọn tệp có trạng thái riêng. |
| Tài nguyên | `nvidia-smi` đo tên GPU, encoder/decode utilization và VRAM. Windows đo CPU và RAM toàn hệ thống. Chỉ số thiếu được bỏ qua. |

Frontend thăm dò khoảng một giây một lần trong lúc có tác vụ. Trạng thái cuối được lưu trong SQLite. Danh sách tiến độ hiển thị tối đa 30 tác vụ gần đây và ưu tiên tác vụ đang chờ hoặc đang chạy. Giới hạn hiển thị này áp dụng cho bảng tiến độ cục bộ. [Boto3 Transfer Callback](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/bucket/upload_file.html)

## GPU NVIDIA

```ini
VIDEO_PROCESSING_MODE=auto
VIDEO_ENCODER=auto
ENABLE_NVENC=true
ENABLE_HW_DECODE=auto
ALLOW_CPU_FALLBACK=true
NVENC_PRESET=p5
ENABLE_GPU_MONITORING=true
PROGRESS_UPDATE_INTERVAL_MS=1000
```

AUTO và GPU ưu tiên giữ nguyên stream tương thích, sau đó dùng NVENC khi cần chuyển mã và CPU nếu được phép. COPY chỉ chấp nhận stream copy. CPU chủ động mã hóa video bằng libx264. Kiểm tra Windows bằng PowerShell:

```powershell
nvidia-smi
ffmpeg -hide_banner -hwaccels
ffmpeg -hide_banner -encoders | Select-String h264_nvenc
ffmpeg -hide_banner -v error -f lavfi -i 'color=s=1280x720:r=30' -frames:v 8 -c:v h264_nvenc -preset p5 -f null -
```

Trên RTX 3050 6 GB, driver 591.74 đã chạy NVENC và giải mã CUDA bằng Gyan FFmpeg 8.0.1. Build FFmpeg 9.1 đã thử yêu cầu NVENC API 13.1 và báo cần driver Windows từ 610.00. Để dùng bản đã kiểm chứng, chỉ định đường dẫn đầy đủ tới cả `ffmpeg.exe` và `ffprobe.exe` trong `.env`. [Gyan FFmpeg 8.0.1](https://github.com/GyanD/codexffmpeg/releases/tag/8.0.1)

## Kiểm thử trên Windows

Một cửa sổ PowerShell chạy các lệnh sau từ thư mục dự án. Smoke test dùng cổng loopback 8765, vì vậy dừng companion đang chạy trên cổng này trước khi thử.

```powershell
$phase9Test = Join-Path $env:TEMP ('hls-phase09-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m pip install 'moto[server]'
npm.cmd install --prefix $phase9Test --no-save playwright
$env:PHASE05_PLAYWRIGHT = Join-Path $phase9Test 'node_modules\playwright'
.\.venv\Scripts\python.exe -m tests.browser_companion_smoke --scratch-dir $phase9Test
.\.venv\Scripts\python.exe -m tests.phase09_benchmark 'D:\Videos\input.mp4' --scratch-dir $phase9Test
```

Thêm `--ffmpeg 'C:\Tools\ffmpeg\bin\ffmpeg.exe' --ffprobe 'C:\Tools\ffmpeg\bin\ffprobe.exe'` với đường dẫn bản muốn kiểm thử. Smoke test cũng nhận `--source` và `--segment-seconds` để kiểm tệp lớn hoặc video dài. Benchmark dùng cùng MP4 cho COPY, CPU và GPU. Cấu hình chuyển mã dùng CRF 19 hoặc CQ 19 và báo SSIM so với nguồn để đối chiếu, vì hai thang chất lượng của encoder khác nhau.
