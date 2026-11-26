# Cloudflare R2 và phân phối HLS

Phase 04 dùng Cloudflare R2 làm nơi lưu chính cho bộ HLS khi cấu hình R2 đã được điền. SQLite giữ UUID, trạng thái, bucket, endpoint và object key của playlist. Các biến R2 cùng để trống cho phép chạy và kiểm thử HLS cục bộ.

## Các khái niệm

| Thuật ngữ | Ý nghĩa trong dự án |
| --- | --- |
| Cloudflare R2 | Dịch vụ lưu trữ đối tượng của Cloudflare. Backend ghi HLS vào R2 qua Boto3. |
| Object Storage | Lưu từng object gồm nội dung và metadata HTTP. Object được đọc bằng key. |
| Bucket | Phạm vi chứa object và áp dụng quyền truy cập. Dùng bucket dành cho bộ HLS cần phân phối. |
| Object Key | Tên đầy đủ trong bucket, chẳng hạn `videos/<UUID>/init.mp4`. Dấu `/` dùng để tổ chức theo tiền tố. |
| S3 API | Giao diện tương thích S3 cho các thao tác như `HeadBucket`, `HeadObject`, ghi object và `GetObject`. |
| CDN | Mạng điểm biên phục vụ object từ custom domain và có thể lưu bản cache gần trình duyệt. |
| Cache HIT | Cloudflare phục vụ từ cache, nhận biết qua `CF-Cache-Status: HIT`. |
| Cache MISS | Cache chưa có bản phù hợp và Cloudflare lấy từ nguồn. Có thể thấy `HIT` trong lượt sau khi object đủ điều kiện. |
| CORS | Quy tắc cho phép JavaScript từ một origin đọc HLS trên origin khác. Origin bao gồm scheme, host và cổng. |
| Cache-Control | Header HTTP xác định cách cache lưu và thời gian object được coi là mới. |

[Cloudflare R2 và S3 API](https://developers.cloudflare.com/r2/get-started/s3/) · [Cache responses](https://developers.cloudflare.com/cache/concepts/cache-responses/) · [Origin Cache Control](https://developers.cloudflare.com/cache/concepts/cache-control/).

## Tạo bucket và thông tin xác thực

1. Trong Cloudflare Dashboard, mở **Storage & databases → R2 → Overview → Create bucket**. Chọn tên và vùng hoặc jurisdiction phù hợp. [Tạo bucket và cấu hình S3](https://developers.cloudflare.com/r2/get-started/s3/).
2. Trong **R2 → Overview**, chọn **Manage** ở phần **API Tokens**. Tạo token có quyền **Object Read & Write** giới hạn trong bucket vừa tạo. Lưu Access Key ID, Secret Access Key và endpoint S3 do Dashboard hiển thị. Endpoint của bucket có jurisdiction riêng cần lấy đúng từ Dashboard. [R2 API Tokens](https://developers.cloudflare.com/r2/api/tokens/).
3. Từ thư mục gốc trên PowerShell, cài Boto3 cùng phụ thuộc dự án. Tạo `.env` từ tệp mẫu nếu chưa có và điền giá trị trực tiếp trên máy.

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
   if (-not (Test-Path -LiteralPath '.env')) {
       Copy-Item -LiteralPath '.env.example' -Destination '.env'
   }
   ```

   ```ini
   R2_ACCOUNT_ID=
   R2_ACCESS_KEY_ID=
   R2_SECRET_ACCESS_KEY=
   R2_BUCKET_NAME=
   R2_ENDPOINT_URL=
   R2_PUBLIC_BASE_URL=
   HLS_SEGMENT_DURATION=4
   DELETE_LOCAL_AFTER_UPLOAD=true
   ```

   Điền endpoint S3 nếu Cloudflare cấp endpoint riêng. Với endpoint chuẩn, backend có thể lấy `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` từ Account ID. Để trống `R2_PUBLIC_BASE_URL` lúc thử bucket riêng. `HLS_SEGMENT_SECONDS` vẫn là tên tương thích với cấu hình cũ.

4. Kiểm tra quyền đến bucket rồi khởi động ứng dụng.

   ```powershell
   .\.venv\Scripts\python.exe -m backend.storage.r2_storage check-bucket
   .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
   ```

## Chọn đường phân phối

**Bucket riêng trên localhost.** Để trống `R2_PUBLIC_BASE_URL`. Sau khi upload, API trạng thái trả `playback_url` dạng `/api/uploads/<UUID>/media/playlist.m3u8`. Mở URL trên cùng origin FastAPI. API đọc playlist, init và từng segment trực tiếp từ R2 theo luồng. Playlist giữ URI tương đối nên các tệp kế tiếp đi qua cùng đường dẫn. Chế độ này dùng cho máy chủ phát triển bind loopback. Bucket riêng là quyền truy cập ở R2, còn bản thân route FastAPI hiện có thể được gọi bởi mọi client truy cập được backend.

**Custom domain cho nội dung được phép phân phối.** Xác nhận gói, dịch vụ và điều khoản áp dụng cho cấu hình video của tài khoản. Dùng nội dung thuộc quyền phân phối và định dạng HLS chuẩn. [Điều khoản CDN của Cloudflare](https://www.cloudflare.com/service-specific-terms-application-services/) · [Hướng dẫn phân phối video](https://developers.cloudflare.com/fundamentals/reference/policies-compliances/delivering-videos-with-cloudflare/) · [Giải thích điều khoản với nội dung lưu ở R2](https://blog.cloudflare.com/updated-tos/).

1. Thêm zone vào cùng tài khoản Cloudflare. Trong bucket, chọn **Settings → Custom Domains → Add**, nhập hostname ví dụ `media.example.com`, xác nhận bản ghi DNS rồi chờ trạng thái **Active**. Custom domain cho phép truy cập các object trong bucket qua Internet. Dùng bucket dành cho nội dung được phép công khai. [R2 custom domains](https://developers.cloudflare.com/r2/buckets/public-buckets/#connect-a-bucket-to-a-custom-domain).
2. Trong **Settings → CORS Policy**, cho phép các origin thực tế của trình duyệt. Với hai URL thử nghiệm Windows có thể dùng chính sách sau. Thêm origin ứng dụng HTTPS khi triển khai. [Cấu hình R2 CORS](https://developers.cloudflare.com/r2/buckets/cors/).

   ```json
   [
     {
       "AllowedOrigins": ["http://localhost:8000", "http://127.0.0.1:8000"],
       "AllowedMethods": ["GET", "HEAD"],
       "AllowedHeaders": ["Range"],
       "ExposeHeaders": ["ETag", "Accept-Ranges", "Content-Range", "CF-Cache-Status"],
       "MaxAgeSeconds": 3600
     }
   ]
   ```

3. Trong zone, mở **Caching → Cache Rules**. Tạo rule khớp hostname media và tiền tố `/videos/`. Chọn **Eligible for cache**. Với **Edge TTL**, dùng tùy chọn tuân theo `Cache-Control` nếu có và bỏ qua cache khi vắng header. Thêm **Browser TTL → Respect origin TTL** để playlist giữ thời gian 60 giây từ R2. Việc chỉ áp dụng rule cho hostname và đường dẫn HLS giúp giới hạn phạm vi cache. Nếu bucket đã trả nội dung cũ trước khi đổi CORS, xem hướng dẫn purge cho hostname đó. [Cache Rules](https://developers.cloudflare.com/cache/how-to/cache-rules/settings/) · [CORS và cache](https://developers.cloudflare.com/r2/buckets/cors/).
4. Đặt `R2_PUBLIC_BASE_URL=https://media.example.com` rồi khởi động lại FastAPI. API trả URL đầy đủ như `https://media.example.com/videos/<UUID>/playlist.m3u8`. Endpoint S3 vẫn dùng khi upload và xác minh.

## Luồng upload và khôi phục

```text
videos/<UUID>/
├── init.mp4
├── seg_000000.m4s
├── seg_000001.m4s
├── seg_000002.m4s
├── metadata.json
└── playlist.m3u8
```

Worker xác minh dữ liệu local theo metadata Phase 02, upload init, từng segment và metadata trước playlist. Boto3 dùng multipart khi cần. Mỗi object có metadata `sha256`. Backend kiểm tra kích thước, SHA-256 khai báo và metadata HTTP bằng `HEAD`, sau đó đọc lại nội dung bằng `GET` theo khối để tự tính SHA-256. ETag có thể khác tùy cách upload, nên ứng dụng xác minh trực tiếp nội dung. Lỗi mạng hoặc giới hạn tạm thời được retry có tăng thời gian chờ. Worker xác minh object trùng đã có khi chạy lại. Nếu object tại key cũ khác nội dung dự kiến, tác vụ dừng để tránh thay đổi nội dung đã cache. [R2 S3 API](https://developers.cloudflare.com/r2/api/s3/api/) · [R2 consistency](https://developers.cloudflare.com/r2/reference/consistency/).

| Tệp | Content-Type | Cache-Control |
| --- | --- | --- |
| `playlist.m3u8` | `application/vnd.apple.mpegurl` | `public, max-age=60` |
| `init.mp4` | `video/mp4` | `public, max-age=31536000, immutable` |
| `seg_*.m4s` | `video/iso.segment` | `public, max-age=31536000, immutable` |
| `metadata.json` | `application/json` | `public, max-age=60` |

SQLite ghi `ready`, object key và nơi lưu sau khi xác minh toàn bộ HLS. Khi `DELETE_LOCAL_AFTER_UPLOAD=true`, worker dọn các tệp HLS đệm trong `data/processing/<UUID>/` sau khi SQLite đã commit. Tệp MP4 đầu vào vẫn được giữ trong `data/uploads/`. Đường phát R2 hoạt động độc lập với thư mục media cục bộ. Nếu lần xuất bản thất bại, các tệp cục bộ được giữ lại.

## Kiểm tra trên Windows

Upload video qua trang gốc hoặc Swagger. Với video Phase 02 đã có HLS cục bộ, có thể đưa lại vào hàng đợi sau khi backend đã được khởi động với cùng cấu hình R2. Lệnh `queue` cũng dùng để retry một lần xuất bản đã hết lượt retry tự động.

```powershell
$py = '.\.venv\Scripts\python.exe'
$id = 'ee3e7935-4b66-492d-a7f5-f937fb4be8f1'
& $py -m backend.storage.r2_storage queue $id
$video = Invoke-RestMethod "http://localhost:8000/api/uploads/$id"
$video | Select-Object status, storage, playlist_key, playback_url, error
```

Khi status là `ready` và storage là `r2`, kiểm tra object và tải một bản mới bằng CLI.

```powershell
& $py -m backend.storage.r2_storage head "videos/$id/seg_000000.m4s"
& $py -m backend.storage.r2_storage download "videos/$id/seg_000000.m4s" '.\r2-seg_000000.m4s'
```

Với custom domain, gửi hai lượt GET tới cùng segment với một origin trong CORS. Kiểm tra HTTP 200, `Content-Type`, `Access-Control-Allow-Origin`, `Cache-Control` và `CF-Cache-Status`. Cloudflare có thể báo MISS ở lần đầu và HIT khi object đã vào cache ở cùng điểm biên. [Cloudflare cache responses](https://developers.cloudflare.com/cache/concepts/cache-responses/).

```powershell
$segment = $video.playback_url -replace 'playlist\.m3u8$', 'seg_000000.m4s'
1..2 | ForEach-Object {
    curl.exe -sS -D - -o NUL -H 'Origin: http://localhost:8000' $segment
}
```

Kiểm thử tích hợp cục bộ với Moto dùng endpoint loopback và khóa giả. Cài phụ thuộc kiểm thử trong môi trường ảo, rồi chạy với FFmpeg và FFprobe trong `PATH`.

```powershell
.\.venv\Scripts\python.exe -m pip install 'moto[server]==5.2.6'
.\.venv\Scripts\python.exe -m tests.r2_smoke
```
