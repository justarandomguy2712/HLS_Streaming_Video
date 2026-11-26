# Đo hiệu năng trên Windows

Dùng PowerShell tại repository. Cài Moto trong môi trường kiểm thử để chạy emulator S3 trên loopback. Mặc định suite dùng video tổng hợp, SQLite độc lập và S3 mô phỏng. Tham số `--real` gửi video tổng hợp lên bucket đang cấu hình. Dùng thư mục có đủ chỗ cho tệp thử lẫn bản upload tạm khi bật `--ladder`.

```powershell
.\.venv\Scripts\python.exe -m pip install 'moto[server]'
$scratch = Join-Path $env:TEMP 'hls-phase07'
.\.venv\Scripts\python.exe -m tests.cloud_first_smoke --ffmpeg ffmpeg --ffprobe ffprobe --scratch-dir $scratch --compare
.\.venv\Scripts\python.exe -m tests.cloud_first_smoke --ffmpeg ffmpeg --ffprobe ffprobe --scratch-dir $scratch --real --ladder
```

Chạy A với batch và B với streaming trên cùng đầu vào, encoder, thời lượng segment và kết nối R2. Log Phase07 ghi thời gian của job, số đoạn, byte nguồn/đích và đỉnh số byte spool media. Giá trị spool chưa gồm bản MP4 và file metadata nhỏ. Cộng kích thước MP4 để ước tính peak của media do ứng dụng tạo. Thời gian tổng cộng của thao tác upload trên các thread có thể lớn hơn thời gian thực do chạy song song. Suite ghi CPU time của tiến trình Python đang chạy test/HTTP backend và peak working set tích lũy của nó. Đo riêng FFmpeg bằng Task Manager hoặc Performance Monitor. Chỉ số working set dùng bộ đếm tiến trình Windows. [Microsoft Win32 PROCESS_MEMORY_COUNTERS](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters). Kiểm GPU Video Encode bằng Task Manager hoặc `nvidia-smi`. Theo dõi peak, tốc độ và độ trễ riêng với video đại diện thực tế.

Để kiểm trên Edge qua CDN, chạy FastAPI với `HLS_DATA_DIR` trỏ tới thư mục `streaming` dưới đường dẫn Scratch mà suite đã in. Mở server tại origin được CORS cho phép rồi chạy `node .\tests\cloud_first_browser.mjs <scratch>\result.jsonl` sau khi cài Playwright theo [MSE](MSE.md). Suite đo lúc mở đến frame sẵn sàng và độ trễ lúc gán vị trí đến khi seek hoàn thành. Kết quả thu trong lần thử hiện tại ở [TEST_RESULTS.md](TEST_RESULTS.md).
