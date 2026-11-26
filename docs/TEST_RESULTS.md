# Kết quả kiểm thử Phase 07


## Bổ sung · Chất lượng nguồn và thư viện lớn

Kiểm thử ngày 26/11/2026 với FFmpeg/FFprobe 9.1 và Edge trên Windows. Các ca thực được tạo riêng, xuất bản lên bucket R2 hiện có và dùng SQLite kiểm thử độc lập. Từng init và segment được GET và xác minh SHA-256. Sau commit ready, suite xác nhận bản MP4 mà backend đã nhận và thư mục HLS tạm đã được dọn, tệp nguồn thử vẫn có cùng SHA-256. Một tiến trình FastAPI khác mở lại playback URL đã lưu trong SQLite.

| Mẫu R2 thật | Chế độ | Kết quả |
| --- | --- | --- |
| 1920×1080, 24 fps, AAC-LC, 1,5 giây | ORIGINAL | Video và audio copy. Hash packet phù hợp với nguồn. Kiểm tra giữ SAR 4:3. |
| 2560×1440, 30 fps, AC-3, 1,5 giây | AUTO | Video copy với SHA-256 từng packet trùng nguồn. Audio chuyển AAC-LC. Edge phát đủ 45 frame, bỏ 0 frame. |
| 3840×2160, 60 fps, khoảng 204 Mb/s, 1,5 giây | AUTO | Video copy nguyên packet, 2 đoạn mục tiêu 1 giây. Edge báo 90 frame và bỏ 2 frame khi phát trực tiếp từ CDN. |
| 1920×1080, 60 fps, MPEG-4 Part 2 + AAC-LC, 1,5 giây | AUTO | libx264 giữ kích thước, SAR 4:3 và 60 fps. Audio vẫn copy. |
| 1920×1080, 24 fps, 1,5 giây | HIGH_QUALITY | libx264 CRF 17 và AAC-LC VBR, giữ kích thước, SAR 4:3, 24 fps. |
| 10 giây, IDR đầu và tại giây thứ 9, mục tiêu 4 giây | ORIGINAL | FFmpeg chọn 2 đoạn theo IDR, payload video giữ nguyên. |
| 3 giờ, 96×64, 2 fps | ORIGINAL | R2 có 180 đoạn mục tiêu 60 giây. Hash từng packet video sau remux vẫn khớp. Edge tua đến mốc 7.200 giây với hai đoạn 119 và 120 sau khi mở lại SQLite. |

ORIGINAL từ chối mẫu AC-3 có lý do rõ ràng và trạng thái failed trước khi công bố playlist. S3 mô phỏng lặp lại các ca ngắn, kiểm R2 retry, multipart và từ chối dữ liệu sai hash. Kiểm lab JPEG/HLSLAB xác minh bằng SHA-256 và so từng byte.

Dữ liệu thư viện gồm **10.025 hàng metadata tổng hợp** có cùng thời điểm tạo, UUID riêng và không tạo object media trên R2 cho các hàng đó. Trong lượt chạy với cấu hình R2 thật, API đọc trang 20 hàng trong khoảng 8,6 ms và duyệt đủ thư viện theo cursor trong 1,06 giây, gồm khởi động lại server thử. Sau khi chèn thêm hàng mới đầu thư viện giữa hai lượt, cursor cũ vẫn đọc chính xác các hàng còn lại, không trùng hoặc bỏ sót. SQLite dùng index `videos_created` cho truy vấn cursor. API từ chối limit 201 và cursor sai, đồng thời giữ phân trang offset. Edge hiển thị 20 hàng lúc mở và 40 hàng với UUID phân biệt sau một lần bấm Xem thêm.

Kết quả 4K60 áp dụng cho clip thử ngắn với H.264 và đoạn 1 giây. Mẫu 3 giờ là video nhẹ. Chưa kiểm thử media 4K60 kéo dài 3 giờ hoặc chuyển mã 4K bằng NVENC. Encoded bitrate có thể thay đổi khi worker chuyển mã. Đường phát hiện tại dùng H.264 với tối đa một audio track chính và phụ thuộc khả năng giải mã MSE của trình duyệt.


Thực hiện ngày 26/11/2026 trên Windows với Python 3.12, FFmpeg/FFprobe 9.1, RTX 3050 Laptop 6 GiB và Edge 158.0.4370.33. Các ca bên dưới dùng dữ liệu tổng hợp. R2 và custom domain thật đã phục vụ các ca TC01–TC09. SQLite kiểm thử dùng thư mục riêng, sau đó được mở lại bằng backend mới. Sự cố kết nối, lỗi công cụ, tiến trình tắt đột ngột, multipart 9 MiB và dữ liệu bị sửa được kiểm qua S3 mô phỏng.

## Upload và phân mảnh trên R2 thật

Thời gian pipeline đo từ lúc bắt đầu gửi HTTP đến khi API báo ready, gồm lần chờ polling tối đa 0,25 giây. Peak spool đo số byte media/playlist do trình nhận theo dõi, chưa cộng bản MP4 nhận qua HTTP, metadata và journal của phiên đo.

| Ca | Video | MP4 nguồn (byte) | Số đoạn | Trung bình mỗi đoạn (byte) | Tổng trên R2 (byte) | Pipeline (giây) | Peak spool (byte) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| TC01 | 10 giây, có AAC | 203457 | 3 | 67149 | 204470 | 3,891 | 203065 |
| TC02 | 1 phút | 671410 | 15 | 44888 | 678385 | 4,547 | 361215 |
| TC03 | 10 phút | 2197685 | 10 | 219865 | 2202607 | 5,641 | 1759406 |
| TC04 | 30 phút | 6614904 | 30 | 220650 | 6628128 | 9,046 | 1772631 |
| TC05 | 1 giờ | 13223198 | 60 | 220554 | 13248891 | 18,594 | 1772512 |
| TC06 | 2 giờ | 26491333 | 120 | 220935 | 26541930 | 27,985 | 1776210 |
| TC07 | 3 giờ | 39802878 | 180 | 221304 | 39878485 | 42,891 | 1778781 |
| TC08 | 3 giờ 5 phút | 40910856 | 185 | 221316 | 40988547 | 46,250 | 1778781 |
| TC09 | 10 giây và box MP4 `free` | 2416029670 | 1 | 109904 | 111784 | 25,828 | 110932 |

TC01–TC02 dùng segment mục tiêu 4 giây. TC03–TC09 dùng 60 giây để giới hạn số request của lần thử. Video dài là 96×64, 2 fps, H.264 với IDR theo lưới. Cả chín ca xác minh SHA-256 từng object khi xuất bản, giữ tệp nguồn thử nguyên vẹn và còn 0 byte media tạm do ứng dụng quản lý sau dọn. TC08 mất 42,103 giây trong giai đoạn FFmpeg có backpressure, và 164,884 giây cộng dồn tác vụ xác minh R2 qua các thread. TC09 truyền 2,25 GiB thật qua HTTP trong 21,594 giây. Box `free` kiểm tra I/O và dung lượng đầu vào. Khối media nén cần giải mã của ca này khoảng 110 KB.

## Sự cố, phát lại và hồi quy

| Ca | Kết quả đã quan sát |
| --- | --- |
| TC10 và TC20 | Giả lập mất kết nối khi GET xác minh segment. Job failed, giữ MP4 tạm, không có playlist trên S3. Retry cùng UUID phát hành attempt mới và xóa object của attempt trước. Kiểm thêm checksum sai cùng kích thước và metadata đúng trên S3 mô phỏng. |
| TC11 | FFmpeg trả lỗi khác 0. Job failed, giữ nguồn, chưa công bố playlist. |
| TC12 và TC14 | Tiến trình Uvicorn thoát bằng `os._exit` giữa lần xác minh segment và trong ca khác sau khi playlist đã xác minh. Khởi động lại tự phân mảnh từ nguồn hoặc tiếp tục từ receipt thích hợp. Cả hai job trở lại ready, media tạm được dọn. |
| TC13 | Mô phỏng dung lượng dự trữ lớn hơn đĩa trống. Upload bị từ chối HTTP 507. |
| TC15 | Mở lại các bản ghi R2 trong backend mới với cùng SQLite. Edge phát trực tiếp custom domain khi thư mục xử lý và bản MP4 backend đã được xóa. |
| TC16 | TC08 khởi tạo frame đầu 1.054 ms. Tua đến giờ thứ hai trong 764 ms, chỉ tải segment 119 và 120. Tua giữa video mất 420 ms, gần cuối 139 ms, trở về giây đầu 30 ms. |
| TC17 | Edge phát liên tục qua các biên segment cho 149 giây media, đo 37,274 giây ở tốc độ 4×. Chưa ngâm phát trong nhiều giờ liên tục. |
| TC18 và TC19 | Đổi video và thu hồi object URL cũ. Mô phỏng một HTTP 403 và một QuotaExceededError. Trình phát nạp lại URL và tiếp tục phát. Fixture 48 giây kiểm tra cửa sổ đệm cuộn, tua ngược và phát lại sau eviction. |
| Hồi quy | Health, API phiên bản Phase 07, route lưu trữ cũ, multipart 9 MiB, CDN thật, pause, play, seek, hết video, phát lại và lab JPEG/HLSLAB đều đã chạy. Lab round-trip init và 3 segment khớp SHA-256, từ chối 6 container lỗi. |

## So sánh trên cùng video và encoder

TC02, libx264, 15 segment, R2 thật. A dùng đường batch tuần tự hiện có. B dùng pipeline với bốn thread upload và tám slot spool trong lần thử. Kích thước R2 chênh 4 byte do trường encoder được bổ sung trong metadata của B.

| Số đo | A · Batch | B · Streaming |
| --- | ---: | ---: |
| Thời gian HTTP đến ready | 14,187 giây | 6,516 giây |
| HLS/Spool đỉnh trong ứng dụng | 331854 byte | 174886 byte |
| MP4 tạm cộng HLS/Spool | 1003264 byte | 846296 byte |
| Xử lý HLS | 0,679 giây giai đoạn xử lý cục bộ | 2,176 giây FFmpeg có backpressure |
| Upload và xác minh | 13,312 giây tuần tự | 12,716 giây cộng dồn nhiều thread |
| Python CPU time đến ready | 0,984 giây | 2,016 giây |
| Python peak working set tích lũy của suite | 98,45 MiB | 100,17 MiB |

B giảm 47% số byte HLS chờ trên đĩa và 54% thời gian đến ready trong lần đo này. Mức dùng CPU Python trung bình trong khoảng đo tương đương 6,9% một lõi ở A và 30,9% ở B. Tính cả mã chạy bài thử và HTTP backend trong cùng tiến trình. Trong lượt lấy mẫu A/B riêng bằng PowerShell, FFmpeg đạt working set 33,2 MiB và CPU time 0,844 giây ở mẫu cao nhất quan sát được. `nvidia-smi` báo GPU toàn adapter tối đa 57%, bộ nhớ 743 MiB và engine encode 0% trong lượt đó. Các mẫu của adapter gồm cả chương trình khác đang chạy. Tổng CPU/RAM và độ trễ thực tế phụ thuộc độ phân giải, encoder, độ dài segment và tốc độ kết nối R2. Khả năng phục hồi của A dựa vào bộ HLS cục bộ. B tạo attempt mới từ bản MP4 nếu dừng trước receipt và dùng lại receipt nếu dừng sau xác minh media.

## Giới hạn kiểm chứng

NVENC probe chạy trên RTX 3050 Laptop với driver 591.74. Bản FFmpeg yêu cầu NVENC API 13.1 và driver đang cung cấp 13.0. Đã chạy đường chuyển mã libx264, còn chuyển mã phần cứng thành công chưa kiểm chứng. TC08 kiểm chứng timeline lớn với nội dung nhẹ và 60 giây mỗi đoạn. Chưa đo media thực tế 3 giờ ở 1080p/4K, hàng nghìn phân đoạn 4 giây trên R2 thật hoặc tắt nguồn Windows. HLS phát trực tiếp dùng custom domain hiện có. Bài kiểm tra URL hết hạn dùng HTTP 403 giả lập vì URL CDN công khai hiện tại không hết hạn.

Xem [lệnh tái lập](PERFORMANCE_TESTING.md).
