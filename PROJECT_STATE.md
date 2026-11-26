# Trạng thái dự án

## Phát video trên iPhone

Ngày 26/11/2026. Trình phát ưu tiên `MediaSource` khi hỗ trợ codec của video và dùng `ManagedMediaSource` khi trình duyệt cung cấp API này. Nhánh Managed tắt phát từ xa khi gắn nguồn để đáp ứng yêu cầu WebKit. Playlist và các phân đoạn vẫn được tải qua Worker với phiên đã đăng nhập.

| Tệp | Thay đổi |
| --- | --- |
| `frontend/js/mse-player.js` | Chọn API phát, thiết lập phát từ xa và phân biệt lỗi thiếu API với lỗi codec. |
| `frontend/js/request-signal.js` | Kết hợp tín hiệu hủy và timeout bằng AbortController để chạy trên Safari có ManagedMediaSource nhưng thiếu AbortSignal.any(). |
| `frontend/js/segment-loader.js`, `frontend/js/online.js` | Dùng tín hiệu tương thích cho media, đăng nhập và API trực tuyến. |

Đã chạy `git diff --check` và smoke test Phase 08 từ PowerShell theo [hướng dẫn kiểm thử](docs/DEPLOYMENT.md#kiểm-thử-trước-khi-deploy). Edge thật đăng nhập, phát và tua với Worker/D1/R2 mô phỏng. Kiểm thử Node mô phỏng riêng cả hai API phát, tải playlist và fMP4 có xác thực, hủy và timeout khi thiếu AbortSignal.any(). Chưa phát thử trên iPhone thật và chưa triển khai bản sửa lên Vercel.


## Phase 08 Addition · Một website, D1 và companion Windows

Ngày 26/11/2026. Kiến trúc hiện hành là một website Vercel, Worker với D1/R2 private và companion PowerShell không mở cổng. Không còn dùng mã viewer chung hoặc `library.json` để xác thực hay cung cấp danh mục production. FastAPI/UI localhost cũ tắt mặc định và được giữ riêng để kiểm thử hồi quy. Phiên Admin localhost Phase 08 trước đã dừng. Không tạo/push GitHub, không đăng nhập, tạo tài nguyên trả phí hoặc deploy Vercel/Cloudflare thay người dùng.

| Tệp | Thay đổi |
| --- | --- |
| `cloudflare/migrations/0001_unified.sql` | Tạo D1 users, sessions, login_attempts, videos và index phân trang. |
| `cloudflare/auth.js`, `cloudflare/http.js` | Scrypt, session, rate limit, kiểm role, API User và kiểm tra request. |
| `cloudflare/catalog.js`, `cloudflare/worker.js`, `cloudflare/wrangler.toml` | Publication, danh mục D1, playback/R2 Range, CORS, video delete có tombstone và cấu hình binding. |
| `backend/bootstrap_admin.py`, `backend/worker_client.py`, `backend/local_processing.py` | Bootstrap Admin bằng hash, HTTPS client không theo redirect, companion add/resume/import-ready/recycle-source. |
| `backend/config.py`, `backend/database.py`, `backend/services/upload_service.py`, `backend/main.py` | Trạng thái local, định danh nguồn, trì hoãn dọn tới D1 READY, khóa HTTP cũ mặc định. |
| `frontend/index.html`, `frontend/js/online.js`, `frontend/js/online-admin.js`, `frontend/js/mse-player.js` | Login chung, logout, quản lý User/video và xử lý mất phiên khi phát. |
| `tests/companion_smoke.py`, `tests/worker_smoke.mjs`, `tests/r2_smoke.py` | Companion, xác minh S3, D1/Worker/Edge cục bộ và hồi quy Phase trước. |
| `.env.example`, `.gitignore`, `README.md`, `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/DEPLOYMENT.md`, `docs/AUTHENTICATION.md`, `docs/LOCAL_PROCESSING.md`, `docs/LOCAL_CLEANUP.md` | Cấu hình, triển khai và ghi chú vận hành. |

Giữ pipeline HLS/FFmpeg, lựa chọn Stream Copy/NVENC/CPU, R2 uploader/xác minh SHA-256, lưu SQLite cục bộ, parser/MSE và lab JPEG/HLSLAB. Không có upload nguồn trước xử lý, Multipart Presigned, polling job từ xa, lease, heartbeat, Tunnel hoặc port forwarding. R2 catalog JSON cũ và các video R2/SQLite hiện hữu không bị xóa; sau khi deploy dùng `import-ready` cho cùng bucket/endpoint.

### Chạy từ PowerShell

Sau khi tự tạo D1, Worker và Admin theo [DEPLOYMENT.md](docs/DEPLOYMENT.md), bố trí FFmpeg/FFprobe trên PATH hoặc trong `.env`:

```powershell
.\.venv\Scripts\python.exe -m backend.local_processing add 'D:\Videos\sample.mp4'
.\.venv\Scripts\python.exe -m backend.local_processing resume VIDEO-UUID
```

### Kiểm thử thực tế

- FFmpeg thật và S3 Moto kiểm hai lần xử lý MP4, SHA-256 của media, SHA-256 từng packet video khớp khi Stream Copy, lỗi publication có chủ đích, giữ bản MP4 tạm khi chưa D1 READY, resume và dọn tệp. Ca mặc định giữ nguồn, ca opt-in xác minh thêm bản MP4 archive. Nhánh Recycle Bin kiểm soát điều kiện và xác nhận bằng mock; chưa thực hiện xóa tệp nguồn bằng Explorer/Recycle Bin thật.
- Miniflare nạp các object và payload do companion thử nghiệm xuất. Worker kiểm migration D1, đăng nhập Admin/User, quyền và secret publication, CORS/Range, 401 khi chưa có phiên, 403 User gọi API Admin, khóa/mở khóa/reset/xóa User, thu hồi phiên, hết hạn, rate limit và xóa đúng prefix video.
- Edge dùng website tĩnh và Worker mô phỏng, phát/tua và ghi mốc xác nhận Admin khi FastAPI cũ không chạy. Ba tài khoản User thử đồng thời tải Range. Cursor duyệt đủ 1.003 hàng D1 cục bộ, gồm 1.001 hàng tổng hợp chỉ metadata.
- Wrangler 4.168.0 dry-run đã đóng gói Worker và nhận binding DB/VIDEOS. Git có 0 commit; rà 88 tệp ứng viên không thấy mẫu credential hoặc tệp media/dữ liệu bí mật trong danh sách. Hồi quy S3 retry, multipart 9 MiB, kiểm checksum và lab JPEG/HLSLAB đã qua. Chưa chạy lại bài 3 giờ và 4K60 qua Worker D1 mới; số đo Phase 07 được ghi riêng ở dưới.
- Chưa kiểm chứng thiết bị vật lý thứ hai, Windows tắt/khởi động lại thật, CORS/TLS production hoặc deployment thực tế. Cần tự kiểm trên origin Vercel và Worker thật sau khi tạo bucket private, D1 và secrets.

## Phase 08 · Chuẩn bị triển khai production

Ngày kiểm thử 26/11/2026. Local Admin chạy FastAPI 0.8.0 tại localhost:8000. Trình phát tĩnh trong `frontend/` chỉ dùng Worker và R2. Tác vụ cập nhật danh mục dùng `ONLINE_LIBRARY_ENABLED`, tắt theo mặc định để admin chọn bucket private và chuẩn bị deployment trước. Chưa tạo deployment từ xa, đăng nhập hoặc push GitHub.

### Tệp đã tạo hoặc cập nhật

| Tệp | Vai trò |
| --- | --- |
| `admin/index.html`, `admin/js/library.js`, `admin/js/upload.js`, `admin/js/player.js` | Local Admin giữ upload, thư viện SQLite, trạng thái đồng bộ và trình phát cục bộ. |
| `admin/js/binary-lab.js` | Di chuyển module lab ra khỏi thư mục deploy Vercel, giữ nguyên nội dung. |
| `frontend/index.html`, `frontend/js/config.js`, `frontend/js/online.js`, `frontend/vercel.json` | Player tĩnh, cấu hình origin Worker, viewer code chỉ trong bộ nhớ tab, phân trang và header Vercel. |
| `frontend/js/mse-player.js`, `frontend/js/segment-loader.js` | Dùng chung MSE, cho player online truyền Authorization chỉ tới đúng Worker origin. |
| `cloudflare/worker.js`, `cloudflare/wrangler.toml` | R2 binding, xác thực viewer, CORS, list/detail/playlist/media và streaming Range. |
| `backend/storage/online_library.py` | Ghi `library.json` và trang bất biến bằng conditional write, retry chống trùng theo UUID. |
| `backend/database.py`, `backend/services/upload_service.py`, `backend/config.py`, `backend/api/uploads.py`, `backend/main.py` | Worker đồng bộ nền sau ready, retry bền vững, trạng thái online và phục vụ trang Admin. |
| `.gitignore`, `.env.example` | Loại secrets, DB, media và cache khỏi danh sách Git. Cung cấp biến config mẫu cho admin. |
| `tests/online_library_smoke.py`, `tests/worker_smoke.mjs`, `tests/mse_smoke.mjs` | Kiểm hồi quy admin, đồng bộ S3, binding Worker và Edge khi FastAPI kiểm thử đã dừng. |
| `README.md`, `docs/MSE.md`, `docs/DEPLOYMENT.md`, `PROJECT_STATE.md` | Bàn giao luồng triển khai, cấu hình và lệnh tái lập trên Windows. |

### Chạy Admin trên PowerShell

Từ thư mục repo, với FFmpeg và FFprobe trong PATH:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Người quản trị thực hiện các bước GitHub, Worker và Vercel trong [DEPLOYMENT.md](docs/DEPLOYMENT.md). Dùng `online_status=published` trước khi tắt máy admin.

### Kết quả thực tế

- S3 mô phỏng và FFmpeg kiểm hai video, gồm tự đồng bộ video ready từ lần chạy trước, bảo toàn 50 hàng trên trang lưu trữ, tranh chấp ETag, retry cùng UUID và dọn media tạm.
- Wrangler 4.168.0 đóng gói Worker thành công bằng dry-run. Miniflare kiểm auth, CORS, pagination, MIME, playlist, streaming và Range 206/416. Edge mở trang tĩnh, phát và tua bằng Worker mô phỏng sau khi FastAPI kiểm thử dừng. Admin 0.8.0 vẫn đọc 7 video gốc. Health, Swagger, phát CDN cũ, seek, lỗi mạng, bộ đệm, multipart và lab JPEG/HLSLAB đều qua hồi quy.
- Git có 0 commit và 0 file index. Kiểm tra 77 file chuẩn bị đưa lên Git không tìm thấy file dữ liệu nhạy cảm hoặc mẫu literal credential đã rà soát. `.env` thật vẫn nằm ngoài Git.
- Cần điền bucket/origin thật, tạo secret và cấu hình Vercel theo hướng dẫn. Kiểm tra URL production và thử từ thiết bị khác khi Windows tắt sau khi người quản trị deploy.

## Phase 07 bổ sung · Chất lượng nguồn và thư viện lớn

Ngày kiểm thử 26/11/2026 trên Windows. FastAPI 0.7.1 đang chạy tại localhost:8000 với thư viện gốc 7 video, Health và SQLite ok, Swagger HTTP 200. Chế độ mặc định là AUTO. ORIGINAL chỉ cho phép copy, AUTO lựa chọn độc lập trên video và audio chính, HIGH_QUALITY chủ động chọn encoder chất lượng cao. Giới hạn 20 hàng mặc định cho mỗi trang. API nhận tối đa 200 hàng mỗi phản hồi. SQLite không có giới hạn số video do code đặt ra.

| Tệp | Vai trò |
| --- | --- |
| `.env.example`, `backend/config.py` | Chế độ mã hóa, cấu hình bảo toàn kích thước và timestamp, độ dài đoạn stream copy và kích thước trang. |
| `backend/processing/ffprobe.py`, `ffmpeg.py` | FFprobe đọc profile, FPS, SAR, DTS và IDR. FFmpeg lập kế hoạch từng luồng, chọn NVENC/libx264 và giữ thông số nguồn theo cấu hình. |
| `backend/processing/cloud_pipeline.py`, `hls_generator.py` | Áp dụng kế hoạch cho cả pipeline R2 và batch, ghi lý do và encoder vào metadata. |
| `backend/services/upload_service.py`, `backend/database.py` | Băm MP4 theo luồng, lưu ghi chú và checksum, truy vấn cursor qua index, backfill/dọn theo lô. |
| `backend/api/uploads.py`, `backend/api/videos.py`, `backend/main.py` | Xuất ghi chú và checksum ở trạng thái, trả next_cursor/next_offset và tăng phiên bản API. |
| `frontend/js/library.js`, `upload.js` | Tải từng trang theo cursor và hiển thị lý do chuyển mã. |
| `tests/quality_library_smoke.py`, `quality_browser.mjs` | So profile trước/sau, packet SHA-256, các chế độ, SQLite 10.025 bản ghi tổng hợp, phát lại bằng Edge. |
| `tests/upload_smoke.py`, `library_smoke.py`, `r2_smoke.py` | Chuẩn bị fixture có IDR phù hợp hoặc chọn encode rõ ràng trong các ca cần kiểm số phân đoạn cũ. |
| `README.md`, `docs/ARCHITECTURE.md`, `docs/VIDEO_PROCESSING.md`, `docs/TEST_RESULTS.md`, `PROJECT_STATE.md` | Cấu hình, sơ đồ và luồng dữ liệu, kết quả kiểm thử, giới hạn và bàn giao. |

### Chạy lại trên PowerShell

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Trong cửa sổ khác với FFmpeg và FFprobe trên PATH:

```powershell
.\.venv\Scripts\python.exe -m pip install 'moto[server]'
$scratch = Join-Path $env:TEMP 'hls-quality07'
.\.venv\Scripts\python.exe -m tests.quality_library_smoke --ffmpeg ffmpeg --ffprobe ffprobe --scratch-dir $scratch
```

Thêm `--real --long` để kiểm thử nhóm video ngắn độ phân giải cao và một mẫu 3 giờ trên R2 thật. 10.025 hàng SQLite thuần metadata được tạo trong thư mục kiểm thử độc lập.

- R2 thật và FFprobe xác nhận 1080p24, 1440p30, 4K60 khoảng 204 Mb/s và 1080p60 chuyển mã giữ kích thước và FPS. Các luồng video copy khớp SHA-256 từng packet. ORIGINAL từ chối AC-3 với lý do rõ ràng. AUTO tự chuyển AC-3 sang AAC-LC, giữ video theo stream copy.
- Edge phát trực tiếp 1440p và 4K60 từ CDN sau dọn tệp và restart. Mẫu 4K ngắn báo 90 frame và bỏ 2 frame. Video ORIGINAL dài 3 giờ, 96×64 ở 2 fps, tua tới mốc 2 giờ chỉ tải segment 119 và 120.
- Duyệt đủ 10.025 hàng tổng hợp sau restart theo cursor. Trang mặc định 20 hàng, Edge dùng cursor để tải thêm. Hồi quy Phase 02 (upload tới 16 MiB và HLS), R2 mô phỏng và lab JPEG/HLSLAB đều qua.
- Chưa kiểm thử video 4K60 kéo dài nhiều giờ hoặc NVENC thành công với driver hiện tại. Cấu hình giữ mặc định một audio track chính. Xem [kết quả chi tiết và giới hạn](docs/TEST_RESULTS.md).

## Phase 07 · Video lớn và storage cloud-first

Ngày kiểm thử 26/11/2026. FastAPI 0.7.0 chạy trên Windows tại localhost:8000 với thư viện gốc gồm 7 video. Health và SQLite báo ok, Swagger trả 200, HTTP khai báo vượt 50 GiB nhận 413. Bộ dữ liệu kiểm thử cloud-first dùng SQLite riêng và R2 thật. HLS mới xác minh từng object trước khi dọn segment, phát hành playlist cuối, commit ready và dọn MP4 tạm theo cấu hình.

### Tệp đã tạo hoặc cập nhật

| Tệp | Vai trò |
| --- | --- |
| `backend/processing/cloud_pipeline.py` | Bộ nhận PUT loopback có backpressure, spool theo byte, journal xác minh, receipt và playlist cuối. |
| `backend/processing/ffmpeg.py`, `backend/processing/ffprobe.py`, `backend/processing/hls_generator.py` | Probe MP4, kiểm DTS/IDR để chọn stream copy, kiểm driver NVENC, chọn encoder, giữ luồng batch và parser/metadata HLS. |
| `backend/services/upload_service.py` | Ghi MP4 theo khối, nhặt job SQLite, dùng chung pool upload, commit và dọn tệp có kiểm tra đường dẫn. |
| `backend/storage/r2_client.py`, `r2_uploader.py`, `r2_storage.py` | Pool kết nối, timeout/retry, khóa theo attempt, kiểm HEAD và SHA-256 qua GET, dọn attempt lỗi và CLI retry. |
| `backend/database.py`, `backend/api/videos.py`, `backend/api/uploads.py`, `backend/main.py` | Migration kích thước và init key, cờ dọn, API playback với cấu hình đệm, proxy theo prefix, API 0.7.0. |
| `backend/config.py`, `.env.example` | Tùy chọn giới hạn đĩa/upload, encoder, provider, concurrency, retry và cửa sổ MSE. |
| `.env` cục bộ | Thay hai biến giới hạn kế thừa bằng giới hạn MP4 50 GiB và mức đĩa trống 10 GiB. |
| `frontend/js/mse-player.js`, `buffer-manager.js`, `segment-loader.js` | Điều chỉnh đệm, tìm đoạn khi tua, khôi phục 401/403, giảm bộ đệm khi gặp quota, hủy và giải phóng nguồn khi đổi video. |
| `tests/cloud_first_smoke.py`, `cloud_first_browser.mjs`, `r2_smoke.py`, `library_smoke.py`, `mse_smoke.mjs` | Kiểm R2 thật hoặc mô phỏng, nhiều thời lượng, ngắt tiến trình, đo A/B và Edge, giữ kiểm thử tương thích. |
| `README.md`, `docs/ARCHITECTURE.md`, `docs/MSE.md`, `PROJECT_STATE.md` | Cấu hình, API, sơ đồ tổng quan và bàn giao. |
| `docs/VIDEO_PROCESSING.md`, `docs/CLOUDFLARE_R2.md`, `docs/LARGE_FILE_OPTIMIZATION.md`, `docs/CLOUD_FIRST_STORAGE.md`, `docs/LOCAL_CLEANUP.md`, `docs/PERFORMANCE_TESTING.md`, `docs/TEST_RESULTS.md` | Codec, vòng đời R2, backpressure, phát sau restart, phục hồi, lệnh kiểm thử và số đo thực tế. |

### Chạy từ Windows PowerShell

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\python.exe -m backend.storage.r2_storage check-bucket
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Dùng tệp .env hiện có. Thêm biến mới theo .env.example khi cần ghi đè mặc định. FFmpeg và FFprobe cần có trong PATH hoặc qua HLS_FFMPEG_BIN và HLS_FFPROBE_BIN. Lệnh kiểm thử đầy đủ có trong [PERFORMANCE_TESTING.md](docs/PERFORMANCE_TESTING.md). Để thử lại job R2 failed đã giữ MP4, dùng `.\.venv\Scripts\python.exe -m backend.storage.r2_storage retry <video-uuid>`.

### Kết quả và giới hạn

- R2 thật qua 9 ca đầu vào, từ 10 giây tới 3 giờ 5 phút và một ca 2,25 GiB qua HTTP. Job ready, từng object được đọc đối chiếu SHA-256, giữ SQLite, sau dọn còn 0 byte media tạm của ứng dụng và mở lại bằng backend mới.
- Edge phát video 3 giờ 5 phút qua CDN, tua tới giờ thứ hai chỉ lấy segment 119 và 120, tua thêm giữa/gần cuối/đầu, phát liên tục thêm 149 giây media ở tốc độ 4×. Ca giả lập 403, quota và đổi video đã qua.
- S3 mô phỏng kiểm retry, multipart 9 MiB, checksum sai, lỗi FFmpeg, HTTP 507 và tiến trình bị tắt trước/sau khi công bố playlist. Lab JPEG/HLSLAB vẫn khớp từng byte.
- Driver NVIDIA 591.74 cung cấp API NVENC 13.0, build FFmpeg 9.1 yêu cầu 13.1. Probe thất bại trên máy này và đường libx264 hoạt động. Chưa đo nội dung 3 giờ bitrate cao, NVENC thành công hoặc tắt nguồn Windows. Ca dài dùng segment 60 giây và nội dung tổng hợp nhẹ. Ca 2,25 GiB dùng box MP4 free để kiểm HTTP và đĩa.
- HTTP upload gián đoạn trước khi nhận UUID cần gửi lại. Retry từ MP4 tạm áp dụng cho job đã nhận xong. Tệp media của bản ghi legacy được giữ theo chính sách cũ trừ các job đã ghi cờ dọn hoặc được xử lý lại.
- Đọc [kết quả đầy đủ](docs/TEST_RESULTS.md) và [kiến trúc](docs/ARCHITECTURE.md).


## Phase 06 · Thư viện video và kiểm thử tích hợp

Ngày kiểm thử 26/11/2026. Pipeline cốt lõi đã hoàn tất trên Windows. [Thư viện tại localhost](http://localhost:8000/) hiển thị video từ SQLite và mở video R2 qua CDN bằng MSE. FastAPI phiên bản `0.6.0` cung cấp đủ năm API `/api/videos`. Các route Phase 02 đến Phase 05 tiếp tục phục vụ client cũ.

### Tệp đã tạo hoặc cập nhật

| Tệp | Vai trò |
| --- | --- |
| `backend/api/videos.py` | API upload mới, danh sách phân trang, metadata, trạng thái và URL phát theo UUID. |
| `backend/database.py` | Migration thư viện, lưu metadata và khóa R2, khôi phục trường còn thiếu và ghi `ready` trước khi dọn HLS. |
| `backend/api/uploads.py`, `backend/main.py` | Dùng chung xử lý upload theo luồng, giữ API cũ và đăng ký router thư viện. |
| `backend/services/upload_service.py`, `backend/processing/hls_generator.py` | Đếm byte MP4 thực nhận, lấy thời lượng và số đoạn từ manifest, bổ sung metadata cũ và điều phối xác minh trước khi dọn. |
| `backend/storage/r2_storage.py` | Đọc manifest R2 kiểm SHA-256 và tạo URL phát từ object key đã lưu. |
| `frontend/index.html`, `frontend/js/library.js`, `frontend/js/upload.js`, `frontend/js/mse-player.js` | Trang HTML tối thiểu có upload, trạng thái, thư viện, nút Play và thẻ video. Trình phát gọi API playback mới. |
| `tests/library_smoke.py`, `tests/library_browser.mjs`, `tests/mse_smoke.mjs` | Kiểm thử API và R2 thật, tự động kiểm tra Edge trước và sau khởi động lại, cập nhật bài kiểm thử MSE theo API mới. |
| `README.md`, `docs/ARCHITECTURE.md`, `docs/MSE.md`, `PROJECT_STATE.md` | Hướng dẫn chạy Windows, mô tả thư viện, API, kiểm thử và bàn giao. |

### Chạy bằng Windows PowerShell

Chạy từ thư mục gốc với FFmpeg và FFprobe trong `PATH`. Điền `.env` theo [hướng dẫn R2/CDN](docs/R2_CDN.md).

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\python.exe -m backend.storage.r2_storage check-bucket
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Mở [trang thư viện](http://localhost:8000/) hoặc làm theo [lệnh kiểm thử và kiểm tra lại sau restart](README.md#chạy-lại-kiểm-thử).

### Kết quả kiểm thử thực tế

| Kiểm thử | Kết quả |
| --- | --- |
| Video ngắn | `Phase 06 - Ngan.mp4`, 65.363 byte, 2,4 giây, 1 segment. Gửi MP4 với Content-Length. |
| Video dài hơn | `Phase 06 - Dai.mp4`, 4.631.560 byte, 29,2 giây, 8 segment. Gửi MP4 theo HTTP chunked. |
| Tạo HLS và R2 thật | FFmpeg 9.1 phân mảnh cả hai video. Boto3 đọc lại và xác minh 15 object gồm init, segment, playlist và metadata của hai video. |
| SQLite và dọn tệp | Cả hai video có metadata, trạng thái `ready`, object key và tiền tố R2. Thư mục HLS tạm của hai video đã được dọn sau commit. MP4 nguồn còn trong thư mục uploads. |
| Migration | Cả 5 bản ghi cũ giữ UUID và trạng thái, SQLite có 7 bản ghi sau hai lần upload mới. Kiểm tra toàn vẹn SQLite trả `ok`. Trên bản sao trước migration với thư mục media trống, backend bổ sung metadata video Phase 04 từ manifest R2 đã xác minh mà vẫn giữ object key. |
| Giao diện và phát | Edge 158.0.4370.33 hiển thị cả hai video. Nút Play phát qua CDN, tua được tới 23,5 giây ở video dài và phát được video ngắn khi chuyển sang bản ghi khác. Biểu mẫu upload gửi đúng endpoint và nhận UUID trong ca kiểm thử trình duyệt. |
| Khởi động lại thực tế | FastAPI đã dừng, cổng 8000 không phản hồi, sau đó khởi động lại với cùng SQLite. Hai video vẫn xuất hiện khi đọc danh sách phân trang, API playback trả URL CDN và Edge tiếp tục phát mà không cần bộ HLS cục bộ. |
| Hồi quy | Smoke test trang gốc, health, Swagger, OpenAPI và lỗi 404 cho UUID không tồn tại đều đạt. Kiểm thử R2 mô phỏng và kiểm thử MSE Phase 05 qua API mới đạt. Lab Phase 03 khôi phục cả bốn tệp HLS khớp byte và SHA-256, từ chối sáu container lỗi. |

### Phạm vi và giới hạn thực tế

- Kết quả trên dùng hai video tổng hợp tới 29,2 giây và khoảng 4,63 MB. Chưa đo tải nhiều người dùng, danh mục lớn hoặc video gần giới hạn upload cấu hình.
- Kiểm thử giao diện với Edge trên Windows và origin localhost cổng 8000 đã cấu hình CORS. Các trình duyệt khác chưa được kiểm thử ở Phase 06.
- Player hỗ trợ HLS VOD fMP4 do pipeline dự án tạo. Kho lưu trữ cho playback là R2. Bản ghi local cũ có thể được xuất bản lại bằng CLI `queue`. Metadata cũ chỉ được bổ sung khi còn manifest hợp lệ.
- SQLite lưu danh mục và khóa đối tượng. Bộ HLS trên R2 có thể phát không cần bản sao HLS trên đĩa. Giữ cùng `HLS_DATA_DIR` để mở lại danh mục sau khi khởi động. MP4 nguồn được giữ cục bộ để bảo toàn bản gốc.

## Phase 05 · JavaScript và HTML5 Media Source Extensions

Ngày kiểm thử 26/11/2026. Trang gốc FastAPI đã có trình phát MSE dùng JavaScript thuần. Với Video ID sẵn sàng, trình phát lấy codec và URL từ API, đọc playlist rồi tải init và các đoạn fMP4 theo thứ tự. [Mở video Phase 04 tại localhost](http://localhost:8000/?video=ee3e7935-4b66-492d-a7f5-f937fb4be8f1).

### Tệp đã tạo hoặc cập nhật

| Tệp | Vai trò |
| --- | --- |
| `frontend/js/playlist-parser.js` | Đọc media playlist VOD, phân giải URI init và segment, lập chỉ mục thời gian để tua. |
| `frontend/js/segment-loader.js` | Fetch, kiểm tra header box bằng Uint8Array, đọc ArrayBuffer mỗi segment, hủy yêu cầu cũ và thử lại lỗi tạm thời. |
| `frontend/js/mse-player.js` | Lấy metadata bằng Video ID, gắn MediaSource, nạp theo thứ tự, xử lý tua, kết thúc và phát lại. |
| `frontend/js/buffer-manager.js` | Điều phối append và remove qua `updateend`, dọn theo biên segment, thử giải phóng thêm khi hết quota. |
| `frontend/js/binary-lab.js` | Đọc header HLSLAB và một cửa sổ byte từ Blob cục bộ để thực nghiệm offset và slice. |
| `frontend/index.html`, `frontend/js/upload.js` | Thêm thẻ video, ô nhập Video ID và điền ID khi upload đạt `ready`. |
| `tests/mse_smoke.mjs` | Kiểm thử Playwright trên Edge với CDN thật và fixture 48 giây. |
| `docs/MSE.md`, `docs/ARCHITECTURE.md`, `README.md`, `PROJECT_STATE.md` | Giải thích API trình duyệt, sơ đồ Mermaid, lệnh PowerShell và bàn giao. |

### Chạy bằng PowerShell

Từ thư mục gốc repository, khởi động FastAPI. Nếu đang chạy, tải lại trang để thấy trình phát mới.

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Từ cửa sổ PowerShell khác, mở video thử nghiệm.

```powershell
Start-Process 'http://localhost:8000/?video=ee3e7935-4b66-492d-a7f5-f937fb4be8f1'
```

Xem [các lệnh tạo fixture và chạy bộ kiểm thử](docs/MSE.md#chạy-trên-powershell).

### Kết quả kiểm thử thực tế

| Kiểm thử | Kết quả |
| --- | --- |
| CDN thật | Edge 158.0.4370.33 phát fMP4 H.264/AAC trực tiếp từ `media.nguyen-ha.studio` với CORS. Trước Play chỉ lấy init và segment đầu. Kiểm thử play, pause, tua, kết thúc và phát lại đạt. |
| Tua video dài | Fixture fMP4 48 giây không audio. Tua trong lúc pause tới 29,5 giây tải segment thứ 7 theo chỉ số từ 0, bỏ qua các segment trung gian. |
| Bộ đệm và phát lại | Vùng đệm phía sau được dọn, vùng phía trước giữ trong mục tiêu 12 giây cộng tối đa một segment 4 giây của fixture. Tua ngược và phát lại sau khi kết thúc đều tải lại segment đầu đã bị dọn. |
| Lỗi mạng | Một HTTP 503 ở segment khi tua được retry thành công. Khi HTTP 503 liên tiếp, UI báo lỗi sau ba lần tải. Bấm Mở video sau khi khôi phục kết nối phát được. |
| Lab và playlist | Kiểm tra timeline, playlist thiếu ENDLIST, URI không hợp lệ, đọc header HLSLAB, slice khu vực payload và từ chối vùng vượt biên. |
| Tương thích | HTML và cả năm module trả HTTP 200. Smoke test trang gốc, health, OpenAPI và Swagger đạt. Sơ đồ Mermaid được render thành SVG trong Edge. |

### Bàn giao

Đọc `README.md`, `AGENTS.md` và tệp này trước phase kế tiếp. Trình phát hỗ trợ một media playlist VOD fMP4 theo định dạng pipeline của dự án. Dùng `GET /api/uploads/<uuid>` để lấy `playback_url` và codec. Lab HLSLAB dùng Blob cục bộ và độc lập với trình phát.

## Phase 04 · Cloudflare R2 và CDN

Ngày kiểm thử 26/11/2026. FastAPI `0.4.0` lưu bộ HLS chuẩn trên R2 khi có cấu hình. Worker ghi theo `videos/<UUID>/`, xác minh từng object bằng S3 rồi ghi `ready` vào SQLite. **Bucket R2 `file` và CDN `media.nguyen-ha.studio` đã được cấu hình và xác minh trên Cloudflare thật.** `R2_PUBLIC_BASE_URL` trong `.env` đã được đặt thành `https://media.nguyen-ha.studio`. API trạng thái hiện trả URL playlist ở hostname này.

### Tệp đã tạo hoặc cập nhật

| Tệp | Vai trò |
| --- | --- |
| `backend/storage/r2_client.py` | Tạo Boto3 S3 client cho R2, kiểm tra bucket và truy vấn tồn tại, kích thước, SHA-256 metadata của object. |
| `backend/storage/r2_uploader.py` | Upload một object với Content-Type và Cache-Control. Hỗ trợ multipart, retry, và đọc lại để xác minh nội dung. |
| `backend/storage/r2_downloader.py` | Tải theo khối, đối chiếu kích thước và SHA-256 trước khi hoàn tất tệp đích. |
| `backend/storage/r2_storage.py` | Điều phối xuất bản HLS, đặt key theo UUID, tạo URL playlist, dọn tệp HLS đã xác minh và CLI. |
| `backend/config.py`, `.env.example`, `requirements.txt` | Cấu hình R2 từ môi trường, tương thích tên cấu hình thời lượng cũ, cài Boto3. |
| `backend/database.py` | Migration giữ dữ liệu trước đó, lưu bucket và endpoint, hàng đợi xuất bản lại và dấu dọn tệp sau commit. |
| `backend/services/upload_service.py`, `backend/main.py`, `backend/api/uploads.py` | Tích hợp lifecycle, xử lý xuất bản qua worker, trả trạng thái và đọc object không cần HLS local. |
| `tests/r2_smoke.py` | Kiểm thử HTTP, FFmpeg, Boto3 và lưu trữ S3 mô phỏng trên loopback bằng Moto. |
| `docs/R2_CDN.md`, `docs/ARCHITECTURE.md`, `README.md`, `PROJECT_STATE.md` | Hướng dẫn Cloudflare, CORS, cache, lệnh PowerShell, minh họa luồng và bàn giao. |

### Lệnh PowerShell

Thực hiện từ thư mục gốc. Tạo token bucket và điền `.env` theo [hướng dẫn R2/CDN](docs/R2_CDN.md).

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\python.exe -m backend.storage.r2_storage check-bucket
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Để thử lại với S3 mô phỏng, cài Moto trong môi trường kiểm thử rồi chạy lệnh.

```powershell
.\.venv\Scripts\python.exe -m pip install 'moto[server]==5.2.6'
.\.venv\Scripts\python.exe -m tests.r2_smoke
```

### Kết quả kiểm thử thực tế trên Windows

| Kiểm thử | Kết quả |
| --- | --- |
| S3 mô phỏng | Boto3 1.44.12 và Moto 5.2.6 thực hiện được HeadBucket, HEAD tồn tại và kích thước, PUT, GET và multipart 9 MiB. Lỗi kết nối giả lập lần đầu kích hoạt retry. |
| Integrity | GET theo khối khớp SHA-256 sau upload và khi tải ra tệp. Object sai payload nhưng có cùng kích thước bị từ chối, tệp đích không được tạo. |
| Tích hợp HLS | FFmpeg 9.1 tạo init, 3 segment, metadata và playlist. Thứ tự ghi quan sát được là playlist sau cùng. Đối chiếu Content-Type, Content-Length và SHA-256 của cả 6 object. |
| Đọc lại và dọn tệp | Backend dọn thư mục HLS cục bộ sau khi xác minh. Bài kiểm thử bỏ cả bản MP4 tổng hợp trong sandbox trước khi khởi động lại. FFmpeg vẫn giải mã được playlist qua FastAPI và S3 mô phỏng. Object không tồn tại trả HTTP 404. |
| Giữ dữ liệu nguồn | MP4 đầu vào được giữ trong `data/uploads/`. Bucket không tồn tại đưa tác vụ về `failed` và giữ HLS cục bộ. `DELETE_LOCAL_AFTER_UPLOAD=false` giữ HLS sau khi xuất bản thành công. |
| Tương thích | Chế độ để trống R2 tiếp tục tạo HLS cục bộ. CLI `queue` xuất bản được video cục bộ đã tồn tại sang S3 mô phỏng. Có kiểm tra tạo URL custom domain theo cấu hình. |
| Cloudflare R2 thật | `check-bucket` trả `ok` cho bucket `file`. Video `ee3e7935-4b66-492d-a7f5-f937fb4be8f1` đã xuất bản đủ 6 object, có `status: ready` và `storage: r2`. Sáu lượt đọc lại qua FastAPI khớp kích thước, SHA-256 và Content-Type. FFmpeg giải mã toàn bộ playlist. Phiên FastAPI dùng `DELETE_LOCAL_AFTER_UPLOAD=false` để giữ bộ HLS cục bộ khi đối chiếu. |
| Cloudflare CDN thật | Custom domain `media.nguyen-ha.studio` được gắn vào bucket `file`. Cache Rule `R2 HLS media CDN` hoạt động trên hostname này và tiền tố `/videos/`, Edge TTL và Browser TTL theo header từ R2. Playlist trả HTTP 200 với `max-age=60`. Phân đoạn đầu lần lượt trả `MISS` và `HIT`, và HTTP Range trả 206 với đúng 8 byte yêu cầu. Cả 6 object đọc qua HTTPS có SHA-256 và Content-Type khớp. FFmpeg giải mã trực tiếp playlist CDN. |
| CORS thật | GET từ origin `http://localhost:8000` trả đúng `Access-Control-Allow-Origin`. OPTIONS từ `http://127.0.0.1:8000` với header `Range` trả 204 và cho phép header đó. |

### Bàn giao

Đọc `README.md`, `AGENTS.md` và tệp này trước phase tiếp theo. Video Phase 02 đã xuất bản qua Cloudflare CDN. Các object hiện tại và được ghi thêm vào bucket phân phối có thể truy cập công khai qua custom domain nếu biết URL. CORS hiện cho phép hai origin phát triển trên cổng 8000. Lab JPEG và HLSLAB tiếp tục là công cụ cục bộ. Trình phát MSE thuộc phase tương ứng.

## Phase 03 · Lab JPEG và xử lý nhị phân

Ngày kiểm thử 26/11/2026. Phase 03 dùng init segment và ba media segment từ video `ee3e7935-4b66-492d-a7f5-f937fb4be8f1`. Báo cáo và các mẫu nằm trong `data/binary-lab/phase03/`. Tệp HLS nguồn được dùng để đọc, còn lab chạy qua CLI cục bộ.

### Tệp đã tạo hoặc cập nhật

| Tệp | Vai trò |
| --- | --- |
| `backend/binary/jpeg_parser.py` | Đọc marker JPEG theo offset, nhận biết SOI, SOF, SOS, EOI, byte stuffing và restart marker. |
| `backend/binary/signature_detector.py` | Đối chiếu chữ ký, phần mở rộng, MIME và cấu trúc. Liệt kê offset, kích thước và các box con fMP4. |
| `backend/binary/lab_container.py` | Container HLSLAB v1 với header 52 byte. CLI đóng gói, khôi phục có kiểm tra integrity và so sánh. |
| `backend/binary/integrity.py` | Sao chép và băm theo khối, tính SHA-256 độc lập và so sánh byte trực tiếp. |
| `tests/binary_lab_smoke.py` | Sinh ảnh JPEG và các mẫu đối chiếu, tái tạo toàn bộ phép kiểm thử trên một bộ HLS Phase 02. |
| `docs/BINARY_PROCESSING.md`, `docs/ARCHITECTURE.md`, `README.md`, `PROJECT_STATE.md` | Bố cục byte, bảng offset thực nghiệm, ví dụ JavaScript, hướng dẫn ImHex và bàn giao. |
| `.gitignore` | Loại trừ thư mục dữ liệu lab sinh ra khỏi Git. |
| `data/binary-lab/phase03/` | Tạo `report.json`, năm mẫu quan sát, bốn tệp `.hlslab` và bốn tệp đã khôi phục. |

### Chạy lại bằng PowerShell

Chạy từ thư mục gốc với Python 3.12 trong venv và FFmpeg trên `PATH`. Lệnh tạo một thư mục lab mới.

```powershell
$hls = '.\data\processing\ee3e7935-4b66-492d-a7f5-f937fb4be8f1'
$lab = ".\data\binary-lab\phase03-$(Get-Date -Format yyyyMMdd-HHmmss)"
.\.venv\Scripts\python.exe -m tests.binary_lab_smoke --hls-dir $hls --output-dir $lab
```

### Kết quả kiểm thử thực tế

| Kiểm thử | Kết quả |
| --- | --- |
| JPEG | FFmpeg 9.1 giải mã ảnh baseline 96 × 64. Parser thấy EOI tại offset 2364. Mẫu có trailer báo 11 byte dư, hai mẫu thiếu cấu trúc bị báo bất thường. Mẫu progressive phụ do Pillow 13.0.0 tạo có 10 scan và FFmpeg giải mã được. |
| Chữ ký và MIME | Tệp `segment-renamed.jpg` giữ nguyên byte fMP4, detector thấy `styp` tại offset 4 và nhận cấu trúc `fmp4_media` dù nhãn thử là `image/jpeg`. |
| fMP4 | Đọc `ftyp/moov` trong init và `styp/sidx/moof/mdat` trong ba segment. Kiểm tra thêm size 64 bit, size 0 và từ chối box vượt biên. |
| Khôi phục HLS | 4/4 tệp khớp từng byte và khớp SHA-256 Phase 02. FFmpeg giải mã được playlist đầy đủ với các tệp khôi phục. |
| Kiểm tra biên | Kiểm tra byte stuffing và restart marker tại ranh giới khối. Container round-trip thành công với payload rỗng và 3.145.745 byte. Sáu ca hỏng bị từ chối. Tệp đích có sẵn được bảo toàn. |
| JavaScript | Chạy ví dụ xử lý header với Node.js và header thực của phân đoạn đầu. Kết quả độ dài là 86.674 byte, trường digest đã cắt giữ bản sao độc lập. |
| Tái lập và tương thích | Lần chạy lại tạo báo cáo JSON giống từng byte. Kiểm tra hex bằng PowerShell xác nhận EOI tại `0x093C` và payload HLSLAB bắt đầu tại `0x34`. Trang gốc, health, OpenAPI và Swagger Phase 02 tiếp tục trả HTTP 200. |

### Bàn giao

Đọc `README.md`, `AGENTS.md` và tệp này trước phase tiếp theo. Các định dạng phát và phân phối tiếp tục dựa trên HLS chuẩn của Phase 02. HLSLAB và các mẫu đối chiếu là dữ liệu thí nghiệm cục bộ.

## Phase 02 · Upload MP4 và tạo HLS fMP4

Ngày kiểm thử 26/11/2026. FastAPI `0.2.0` nhận MP4 dạng raw binary, ghi theo khối, lưu trạng thái trong SQLite và tạo HLS VOD trên đĩa bằng worker. Trang upload tối thiểu ở `http://localhost:8000/`. [docs/HLS.md](docs/HLS.md) mô tả playlist, init segment, keyframe và luồng xử lý.

### Tệp đã tạo hoặc cập nhật

| Tệp | Vai trò |
| --- | --- |
| `backend/api/uploads.py` | Nhận MP4 ở `POST /api/uploads` và tra cứu trạng thái ở `GET /api/uploads/{video_id}`. |
| `backend/services/upload_service.py` | Ghi upload theo luồng, kiểm tra kích thước và dung lượng trống, điều phối worker lấy tác vụ từ SQLite. |
| `backend/processing/ffprobe.py` | Kiểm tra MP4, đọc codec, độ phân giải, duration, bitrate và chuỗi codec HLS. |
| `backend/processing/ffmpeg.py` | Chạy FFmpeg tạo HLS VOD fMP4, đặt keyframe H.264, xử lý AAC và theo dõi dung lượng ổ đĩa. |
| `backend/processing/hls_generator.py` | Quản lý thư mục staging, tạo metadata từng segment và SHA-256, chuyển kết quả hoàn chỉnh sang thư mục UUID. |
| `backend/main.py`, `backend/config.py`, `backend/database.py` | Đăng ký router và vòng đời worker, cấu hình phân mảnh và dung lượng dự trữ, bổ sung trường lỗi bằng migration SQLite. |
| `frontend/index.html`, `frontend/js/upload.js` | Chọn và gửi MP4, hiển thị trạng thái theo UUID. |
| `tests/upload_smoke.py` | Sinh MP4 bằng FFmpeg, gọi API qua HTTP rồi kiểm tra playlist, codec, SHA-256 và giải mã đầu ra. |
| `.env.example`, `.gitignore` | Cấu hình mặc định cho Phase 02 và loại trừ tệp khóa worker. |
| `README.md`, `docs/ARCHITECTURE.md`, `docs/HLS.md`, `PROJECT_STATE.md` | Lệnh chạy, cập nhật kiến trúc, giải thích HLS và bàn giao. |

Tệp mẫu `phase02-sample.mp4` nằm cạnh thư mục repository. Đây là MP4 tổng hợp H.264/AAC dài 9,2 giây được tạo bằng FFmpeg để thử biểu mẫu upload.

### Khởi chạy và kiểm thử lại

Từ thư mục gốc, bảo đảm thư mục `bin` của FFmpeg có trong `PATH`. Có thể đặt `HLS_FFMPEG_BIN` và `HLS_FFPROBE_BIN` bằng đường dẫn tuyệt đối để khởi chạy backend.

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Trong cửa sổ PowerShell khác, mở trang gốc để thử bằng trình duyệt hoặc chạy các lệnh sau. Dùng đường dẫn tương ứng nếu đã cấu hình `HLS_DATA_DIR`.

```powershell
.\tests\smoke.ps1 -BaseUrl http://localhost:8000
.\.venv\Scripts\python.exe .\tests\upload_smoke.py --base-url http://localhost:8000 --data-dir .\data
```

### Kết quả kiểm thử thực tế

| Kiểm thử | Kết quả |
| --- | --- |
| Môi trường | Windows, Python 3.12.10, FFmpeg và FFprobe 9.1 Essentials. |
| H.264 và AAC sẵn có | Video tổng hợp 9,2 giây tạo 3 đoạn có thời lượng 4, 4 và 1,2 giây. Playlist giải mã được với H.264 và AAC. |
| MP4 chỉ có video | Upload HTTP chunked 16.977.751 byte gồm MP4 MPEG-4 và box `free` để tăng kích thước kiểm thử. HLS H.264 giải mã được với các đoạn 4 và 1,25 giây. |
| Chuyển mã audio | MP4 có video MPEG-4 và audio ALAC được phân mảnh thành HLS chứa H.264 và AAC. |
| Cấu hình 2,5 giây | Video 9,2 giây tạo các đoạn 2,5, 2,5, 2,5 và 1,7 giây. Quan sát được trạng thái `uploaded`, `processing`, `ready`. |
| Metadata | Đối chiếu thứ tự và thời lượng với playlist, kích thước và SHA-256 của init cùng từng segment. FFprobe xác nhận codec và FFmpeg giải mã cả playlist. |
| Lỗi và giới hạn | MP4 lỗi chuyển `failed` và không sinh bộ HLS. HTTP trả 400 với body rỗng, 404 với UUID chưa tồn tại, 415 với sai loại nội dung, 413 khi vượt giới hạn kiểm thử 20 MiB với cả hai cách upload. Khi client ngắt kết nối giữa upload, backend dọn tệp `.part` đã ghi. |
| Dung lượng ổ đĩa | Cấu hình phần dự trữ cao hơn dung lượng trống. Upload trả 507, còn job đã có trong hàng đợi chuyển `failed` trước khi tạo đầu ra. |
| SQLite và khởi động lại | Migration giữ bản ghi Phase 01. Worker khôi phục bản ghi `processing` có thư mục tạm dở dang. Trạng thái `ready` và khóa playlist còn sau khi khởi động Uvicorn lại. |
| Trình duyệt | Edge 158.0.4370.33 tải MP4 từ form, nhận HTTP 202 và hiển thị `ready`. |
| Tương thích Phase 01 | Trang gốc, health check, Swagger và OpenAPI trả HTTP 200. |

Bộ kiểm thử media dùng SQLite và thư mục riêng trong `work/`. Tệp nguồn lưu ở `uploads/<uuid>.mp4`. Tệp hoàn chỉnh nằm ở `processing/<uuid>/` với `init.mp4`, `playlist.m3u8`, `seg_*.m4s` và `metadata.json`. `ready` nghĩa là bộ HLS cục bộ đã hoàn tất. Trạng thái phân phối cloud sẽ được xử lý ở phase tương ứng. Nếu tiến trình bị buộc dừng giữa lúc nhận MP4, tệp `.part` có thể còn trên đĩa và có thể dọn khi backend đã dừng.

### Bàn giao

Đọc `README.md`, `AGENTS.md` và tệp này trước phase kế tiếp. Hiện đã có HLS chuẩn và metadata cục bộ để tiếp tục công việc được giao. Lab JPEG, R2/CDN, API danh mục và trình phát MSE thuộc các phase sau.

## Phase 01 · Khởi tạo kiến trúc và backend

Ngày kiểm thử 26/11/2026. Repository Git đã được khởi tạo. FastAPI chạy bằng Uvicorn trực tiếp trên Windows và tự tạo SQLite cùng bảng `videos`. Swagger truy cập tại `http://localhost:8000/docs`. Kiến trúc đích và sơ đồ luồng dữ liệu nằm trong [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tệp đã tạo

| Tệp | Vai trò |
| --- | --- |
| `backend/__init__.py`, `backend/api/__init__.py` | Khai báo package Python cho backend và các router. |
| `backend/main.py` | Điểm vào ASGI, khởi tạo qua lifespan, phục vụ HTML, Swagger và OpenAPI. |
| `backend/config.py` | Đọc cấu hình từ môi trường và `.env`. Chuẩn hóa thư mục dữ liệu và kiểm tra dung lượng cấu hình. |
| `backend/database.py` | Tạo bảng danh mục SQLite và kiểm tra khả năng đọc bằng kết nối chỉ đọc. |
| `backend/api/health.py` | Cung cấp `GET /health`, mô tả OpenAPI cho cả HTTP 200 và 503. |
| `backend/services/__init__.py` | Package dự kiến điều phối nghiệp vụ video. |
| `backend/processing/__init__.py` | Package dự kiến xử lý FFprobe và FFmpeg. |
| `backend/binary/__init__.py` | Package dự kiến dành cho nghiên cứu chữ ký JPEG trong lab. |
| `backend/storage/__init__.py` | Package dự kiến truy cập R2 bằng Boto3. |
| `frontend/index.html`, `frontend/js/.gitkeep` | Trang tối thiểu liên kết đến Swagger và health check, giữ thư mục JavaScript cho các phase sau. |
| `data/uploads/.gitkeep`, `data/processing/.gitkeep`, `data/database/.gitkeep` | Giữ thư mục dữ liệu trong cấu trúc repository. |
| `tests/smoke.ps1` | Kiểm tra bốn endpoint qua HTTP. |
| `requirements.txt`, `.env.example`, `.gitignore` | Phụ thuộc Python, cấu hình mẫu và mẫu loại trừ môi trường ảo, bí mật và dữ liệu sinh ra khỏi Git. |
| `docs/ARCHITECTURE.md`, `README.md`, `AGENTS.md`, `PROJECT_STATE.md` | Kiến trúc và Mermaid, hướng dẫn PowerShell, quy định dự án và trạng thái bàn giao. |

## Khởi chạy và kiểm thử lại

Chạy từ thư mục gốc. Xem [README.md](README.md) để gọi Python qua đường dẫn tuyệt đối nếu launcher không nhận bản cài.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Chạy trong cửa sổ PowerShell thứ hai khi Uvicorn đã sẵn sàng.

```powershell
.\tests\smoke.ps1 -BaseUrl http://localhost:8000
```

## Kết quả kiểm thử thực tế

| Kiểm thử | Kết quả |
| --- | --- |
| Môi trường | Windows 11 build 26300, PowerShell 7.6.9, Python 3.12.10 gọi trực tiếp, SQLite 3.49.1. |
| Phụ thuộc | Phiên bản trong `requirements.txt` khớp môi trường ảo. `pip check` báo không có phụ thuộc lỗi. |
| Khởi động | Uvicorn lắng nghe `127.0.0.1:8000`. Lifespan tạo thư mục dữ liệu và SQLite trên đĩa. |
| HTTP | `/`, `/health`, `/openapi.json` và `/docs` đều trả 200. Smoke test chạy đạt trên PowerShell 7 và Windows PowerShell. |
| Swagger trong Edge | Edge headless 158.0.4370.33 hiển thị Swagger. **Try it out → Execute** gọi `GET /health` và nhận HTTP 200. |
| Sơ đồ Mermaid | Mermaid 11 phân tích sơ đồ luồng dữ liệu và render thành SVG trong Edge. |
| SQLite | Khởi tạo lặp giữ được hàng kiểm thử. Tạm di chuyển tệp SQLite trong thư mục thử nghiệm cho kết quả 503, đưa tệp trở lại cho kết quả 200. Health check không tạo lại tệp đã vắng mặt. |
| Cấu hình | Dùng đường dẫn dữ liệu chứa dấu cách, `#` và `%`. `HLS_MAX_UPLOAD_BYTES=2048` được nạp, các giá trị `0`, `-1` và `invalid` bị từ chối. |
| FFmpeg và FFprobe | Bản Windows 9.1 Essentials của Gyan đã được kiểm tra SHA-256. FFmpeg tạo được MP4 tổng hợp. FFprobe xác nhận H.264, 160×90, 0,5 giây. |

Kiểm thử dùng môi trường ảo và thư mục dữ liệu riêng trong `work/` của workspace. Trên máy kiểm thử, launcher `py` không nhận bản cài và lệnh `python` trỏ tới Windows Store. Python 3.12 được xác minh bằng lệnh gọi trực tiếp tệp thực thi. FFmpeg và FFprobe được giải nén trong `work/` và đưa vào `PATH` của tiến trình kiểm thử. Swagger dùng thư viện mặc định qua jsDelivr.

## Bàn giao

Đọc `README.md`, `AGENTS.md` và tệp này trước phase tiếp theo. Upload MP4, phân mảnh HLS, lab JPEG, upload R2, phân phối CDN, API danh mục và trình phát MSE đang ở mức thiết kế. Phase tiếp theo chỉ triển khai phần được giao.
