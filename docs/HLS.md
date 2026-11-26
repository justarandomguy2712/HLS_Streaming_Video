# Upload MP4 và phân mảnh HLS

## MP4, fMP4 và MPEG-TS

| Dạng tệp | Cấu trúc và vai trò |
| --- | --- |
| MP4 thông thường | Container ISO BMFF. Box `moov` mô tả track và bảng sample. Box `mdat` giữ dữ liệu âm thanh và video đã mã hóa. Dự án nhận MP4 làm đầu vào. |
| MP4 phân mảnh (fMP4) | Cùng họ ISO BMFF. Init segment có `ftyp` và `moov` để thiết lập track và codec. Mỗi đoạn media có `moof` mô tả fragment và `mdat` chứa sample tương ứng. Dự án chọn fMP4 để dùng với MSE ở phase phát. |
| MPEG-TS | Transport stream đóng gói dữ liệu thành packet 188 byte. Đây là một lựa chọn khác cho HLS. Phase 02 tạo fMP4. |

Tham khảo [định dạng byte ISO BMFF cho MSE](https://www.w3.org/TR/mse-byte-stream-format-isobmff/) và [các định dạng segment HLS](https://datatracker.ietf.org/doc/html/rfc8216#section-3).

## Playlist, init segment và keyframe

HLS chia video thành các đoạn media để máy khách lấy tuần tự theo yêu cầu HTTP. Playlist `playlist.m3u8` liệt kê các URI tương đối và thời lượng thực của chúng theo thứ tự phát. `#EXTINF` báo thời lượng từng đoạn, `#EXT-X-MAP` trỏ đến `init.mp4`, còn `#EXT-X-ENDLIST` đánh dấu VOD đã đủ đoạn. Init segment chỉ chứa thông tin khởi tạo cho bộ giải mã. Ứng dụng MSE sẽ nối dữ liệu init trước các media segment của track tương ứng. [Mô tả media playlist HLS](https://datatracker.ietf.org/doc/html/rfc8216#section-4).

Keyframe cho phép bộ giải mã bắt đầu hình ảnh mà không cần khung hình video trước đó. FFmpeg cắt HLS ở keyframe phù hợp sau khi đạt thời lượng mục tiêu. Phase 02 mã hóa video thành H.264 8-bit với GOP đóng và chèn keyframe gần mỗi mốc cấu hình để các segment bắt đầu tại điểm giải mã độc lập. Segment cuối thường ngắn hơn. Vị trí khung hình thực, đặc biệt với video có tốc độ khung hình biến thiên, có thể khiến segment dài hoặc ngắn hơn mục tiêu. Dùng `EXTINF` làm thời lượng thực. [Tài liệu HLS muxer của FFmpeg](https://ffmpeg.org/ffmpeg-formats.html#hls-2).

## Luồng xử lý cục bộ

1. API đọc body MP4 theo luồng và ghi vào tệp UUID có đuôi `.part`. Nó kiểm tra giới hạn cấu hình với từng khối và kiểm tra dung lượng trống khi ghi. Sau khi nhận đủ dữ liệu, API đổi tên thành `<uuid>.mp4`, thêm hàng `uploaded` trong SQLite rồi trả HTTP `202`.
2. Worker lấy tác vụ từ SQLite và đổi trạng thái thành `processing`. Khóa tệp Windows giữ một worker xử lý cho mỗi cơ sở dữ liệu. Worker kế tiếp có thể lấy lại công việc đang dở khi tiến trình trước dừng.
3. Worker kiểm tra ổ xử lý có đủ phần dự trữ cộng dung lượng ước tính bằng kích thước nguồn. FFprobe kiểm tra container và đọc codec, độ phân giải, thời lượng, bitrate. Metadata giữ `null` cho duration hay bitrate nếu FFprobe không cung cấp. Worker chọn một luồng video và, nếu có, luồng audio đầu tiên. [Tài liệu FFprobe](https://ffmpeg.org/ffprobe.html).
4. FFmpeg chạy trong tiến trình con do worker quản lý. Pipeline mã hóa lại video để chủ động đặt keyframe theo `HLS_SEGMENT_SECONDS`. Audio AAC-LC mono hoặc stereo được sao chép. Audio khác được chuyển sang AAC-LC. Đầu vào chỉ có video tạo HLS chỉ có video. Worker tiếp tục kiểm tra dung lượng dự trữ khi FFmpeg đang ghi.
5. FFmpeg tạo `init.mp4`, `playlist.m3u8` và các tệp `seg_000000.m4s` kế tiếp. Backend đọc từng dòng playlist để ghi `metadata.json`. Mỗi mục chứa số thứ tự, tên tệp, thời điểm bắt đầu tính từ đầu playlist, thời lượng, kích thước và SHA-256. Init segment cũng có kích thước và SHA-256. Cả video đầu vào lẫn phân đoạn được đọc theo luồng khi xử lý hoặc băm.
6. Worker chỉ đổi tên thư mục tạm thành `data/processing/<uuid>/` khi playlist và metadata đã hoàn tất, sau đó ghi `ready` trong SQLite. Lỗi xử lý chuyển sang `failed`. Tệp MP4 nguồn được giữ lại cho những phase sau.
