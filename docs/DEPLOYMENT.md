# Tự triển khai website Vercel, Worker, D1 và R2

Yêu cầu của Phase 08 Addition dùng một website. D1 là danh mục và nguồn xác thực online. R2 chứa media private. Windows dùng companion PowerShell để xử lý. Phần chuẩn bị này không tạo tài nguyên, đăng nhập, push hoặc deploy thay Admin.

## 1. Chuẩn bị và push GitHub

Từ thư mục repository trên Windows:

```powershell
git status --short
git rev-list --all --count
git ls-files --cached --others --exclude-standard
git check-ignore .env cloudflare/.dev.vars admin.bootstrap.sql video.mp4 init.m4s data/database/videos.sqlite3
```

Tạo repository rỗng trên GitHub. Kiểm tra danh sách tệp dự kiến commit, gồm mã nguồn, cấu hình mẫu và migration. Nếu khóa từng xuất hiện ở commit trong clone của bạn, thu hồi và tạo lại khóa trước khi push.

```powershell
git add --all
git diff --cached --name-only
git commit -m "Prepare unified video site"
git branch -M main
git remote add origin https://github.com/YOUR-ACCOUNT/YOUR-REPOSITORY.git
git push -u origin main
```

Nếu đã có `origin`, kiểm tra URL và dùng remote hiện có.

## 2. Chuẩn bị R2 private và D1

Chọn bucket R2. Tắt URL `r2.dev` và các custom domain gắn trực tiếp công khai cho bucket dùng phát riêng tư. Nếu dùng bucket Phase 08 trước, giữ object media hiện có. Tạo R2 API token đọc/ghi đúng bucket cho Windows. [Cloudflare R2 private/public access](https://developers.cloudflare.com/r2/data-access/public-buckets/).

```powershell
npx.cmd wrangler login
npx.cmd wrangler d1 create hls-library
```

Sửa `cloudflare/wrangler.toml` với `database_name`, `database_id` thật cho binding `DB` và `bucket_name` thật cho binding `VIDEOS`. Sau đó áp dụng migration. Migration tạo bảng và chỉ mục nếu thiếu. Với D1 bên ngoài từng có schema khác, kiểm tra schema và sao lưu trước khi chạy. Phase 08 trước của repo này dùng JSON trên R2 và chưa có bảng tài khoản D1.

```powershell
npx.cmd wrangler d1 migrations apply hls-library --remote --config cloudflare/wrangler.toml
```

## 3. Bootstrap Admin đầu tiên

Tạo venv và cài dependency Python theo README. Chương trình hỏi mật khẩu ẩn, tạo SQL có hash và không thay thế tài khoản Admin đã tồn tại.

```powershell
$bootstrap = Join-Path $env:TEMP ('hls-' + [guid]::NewGuid().ToString('N') + '.bootstrap.sql')
.\.venv\Scripts\python.exe -m backend.bootstrap_admin --username your-admin --out $bootstrap
npx.cmd wrangler d1 execute hls-library --remote --config cloudflare/wrangler.toml --file $bootstrap
```

Giữ SQL bootstrap ngoài Git. Tạo các User còn lại trong website sau khi đăng nhập bằng Admin.

## 4. Deploy Worker và cấu hình service secret

Điền origin production Vercel vào `ALLOWED_ORIGINS` khi đã có. Nếu dùng placeholder lần đầu, login từ Vercel chỉ hoạt động sau khi điền đúng origin và deploy lại Worker. Sau khi Worker ra URL HTTPS, tạo key ngẫu nhiên và cho Worker dùng qua secret. Ghi cùng giá trị `$processingKey` vào `PROCESSING_SERVICE_TOKEN` trong `.env` Windows, giữ ngoài Git.

```powershell
npx.cmd wrangler deploy --config cloudflare/wrangler.toml
$processingKey = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
$processingKey | npx.cmd wrangler secret put PROCESSING_SERVICE_TOKEN --config cloudflare/wrangler.toml
# Trong cửa sổ quản trị riêng, lấy giá trị để điền .env Windows.
$processingKey
```

Credential companion chỉ phục vụ API publication và xác minh trạng thái. Không đưa credential này hoặc các khóa R2 xuống trang tĩnh. Kiểm thử tốc độ scrypt trong môi trường/gói Cloudflare bạn chọn. Nếu gói hiện tại không đủ CPU, xem thông báo trước khi quyết định đổi gói. [Cloudflare D1 migrations](https://developers.cloudflare.com/d1/reference/migrations/) · [Workers secrets](https://developers.cloudflare.com/workers/configuration/secrets/).

## 5. Deploy website tĩnh Vercel

Đặt origin Worker HTTPS tại `frontend/js/config.js`, commit và push cập nhật này. Trong Vercel import GitHub repository, chọn **Root Directory** là `frontend`, **Framework Preset** là **Other**, để trống build và install command. Sao chép origin `https://…vercel.app` được cấp rồi điền chính xác vào `ALLOWED_ORIGINS` và chạy lại lệnh deploy Worker. Trang tĩnh không có upload MP4 qua Vercel. [Cấu hình build Vercel](https://vercel.com/docs/builds/configure-a-build).

## 6. Chạy Companion trên Windows

Tạo `.env` từ mẫu và điền R2 credentials như Phase 07. Đặt các biến sau:

```ini
STORAGE_PROVIDER=r2
WORKER_ENDPOINT=https://your-worker.your-subdomain.workers.dev
PROCESSING_SERVICE_TOKEN=
ENABLE_LEGACY_LAB_HTTP=false
ONLINE_LIBRARY_ENABLED=false
DELETE_LOCAL_AFTER_VERIFICATION=true
KEEP_ORIGINAL_VIDEO=false
DELETE_SOURCE_FILE_AFTER_SUCCESS=false
```

```powershell
.\.venv\Scripts\python.exe -m backend.local_processing add 'D:\Videos\sample.mp4'
```

Companion hỏi mật khẩu Admin. Khi thấy `D1 READY`, thử đăng nhập bằng cả Admin lẫn User từ một thiết bị khác. Kiểm tra phát/tua, quyền User và việc chặn request media chưa đăng nhập. Tắt Windows rồi mở lại website. Browser phải chỉ gọi Vercel/Worker, và D1/R2 vẫn cung cấp tài khoản, danh mục cùng media.

Để đưa các video đã lưu từ Phase 08 cũ vào D1, giữ `.env`, SQLite, bucket và endpoint khớp với lúc xuất bản, rồi chạy `python -m backend.local_processing import-ready` một lần. R2 `library.json` cũ giữ nguyên như dữ liệu lịch sử. API production mới không đọc danh mục đó.

## Kiểm thử trước khi deploy

Tạo media thử bằng FFmpeg và S3 mô phỏng. Kiểm tra gói Worker và dùng Miniflare/Edge với binding cục bộ. Các lệnh dưới đây không deploy:

```powershell
$testRoot = Join-Path $env:TEMP ('hls-unified-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m pip install 'moto[server]'
.\.venv\Scripts\python.exe -m tests.companion_smoke --scratch-dir $testRoot --export
npm.cmd install --prefix $testRoot --no-save miniflare@4 playwright
$env:PHASE08_MINIFLARE = Join-Path $testRoot 'node_modules\miniflare'
$env:PHASE05_PLAYWRIGHT = Join-Path $testRoot 'node_modules\playwright'
$bundle = Join-Path $testRoot 'worker-bundle'
npx.cmd wrangler deploy --dry-run --config cloudflare/wrangler.toml --outdir $bundle
$env:PHASE08_WORKER_BUNDLE = Join-Path $bundle 'worker.js'
$receipt = Get-ChildItem $testRoot -Filter receipt.json -Recurse | Select-Object -First 1
node .\tests\worker_smoke.mjs $receipt.FullName
```

Bản local không xác nhận TLS, DNS, ngân sách CPU production, thiết bị thật khác hoặc quá trình Windows tắt/mở. Thực hiện bước 6 sau khi tự triển khai production.

