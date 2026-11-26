# Xử lý video dài trên Windows

FastAPI ghi một bản MP4 tạm và tính SHA-256 trong lúc nhận. FFprobe đọc codec, profile, pixel format, kích thước, tỷ lệ khung hình, thông tin màu, bitrate, FPS và timestamp. Khi bật `ALLOW_STREAM_COPY`, worker quét từng packet video H.264, kiểm tra DTS, IDR thực và khoảng cách giữa các điểm cắt. Payload media chỉ được đọc theo luồng. Đoạn copy có thể dài hơn thời lượng HLS mục tiêu vì điểm cắt phụ thuộc IDR. Giới hạn độ dài một đoạn copy tự động là giá trị lớn hơn giữa 30 giây và hai lần mục tiêu HLS. Đặt `MAX_COPY_SEGMENT_SECONDS` bằng một số giây dương nếu cần giới hạn phù hợp với bitrate và bộ nhớ.

| Chế độ | Video | Audio chính nếu có |
| --- | --- | --- |
| `ORIGINAL` | Chỉ copy H.264 phù hợp. Job thất bại nếu cần mã hóa hoặc FFmpeg không thể remux. | Chỉ copy AAC-LC. |
| `AUTO` | Copy nếu phù hợp. Khi cần mã hóa, dùng libx264 CRF 19 hoặc NVENC CQ 19. | Copy AAC-LC. Audio khác được chuyển sang AAC-LC VBR. |
| `HIGH_QUALITY` | Mã hóa bằng libx264 CRF 17 preset slow hoặc NVENC CQ 17 preset p6. | Mã hóa AAC-LC VBR ưu tiên chất lượng. |

API trạng thái và trang upload hiển thị lý do cần chuyển mã. Phạm vi MSE hiện tại là H.264 8-bit 4:2:0 với một luồng audio chính nếu nguồn có audio. Pipeline giữ số kênh mà AAC-LC hỗ trợ và bỏ qua các audio track bổ sung. Chuyển mã có thể thay đổi bitrate, độ chính xác màu và chất lượng hình/tiếng. Stream copy giữ dữ liệu media đã mã hóa trong container mới.

Với `VIDEO_ENCODER=auto` hoặc `h264_nvenc` và `ENABLE_NVENC=true`, hệ thống thử encode hai frame trước khi chọn NVIDIA. Bài thử bao gồm khả năng mở encoder và driver, thay vì chỉ dựa vào danh sách encoder trong FFmpeg. Khi probe hoặc encode thực tế thất bại, worker dùng `libx264`. Tắt stream copy để kiểm thử riêng đường chuyển mã. FFmpeg giữ nguyên kích thước, SAR và nhịp frame mặc định. Chế độ chuyển mã giữ timestamp theo demuxer và dùng IDR theo lưới HLS. Nếu kích thước lẻ gây xung đột với H.264 yuv420p và `PRESERVE_RESOLUTION=true`, ứng dụng báo lỗi rõ ràng. Khi chủ động tắt giữ kích thước, ứng dụng có thể pad một pixel và bù SAR. Nếu đồng thời bật `ALLOW_DOWNSCALING` và kích thước cho phép, ứng dụng có thể hiệu chỉnh kích thước lẻ xuống số chẵn gần nhất. Cấu hình dự án không có preset hạ mọi video xuống cùng một kích thước hoặc số FPS.

`HLS_SEGMENT_DURATION` là thời lượng mục tiêu. FFmpeg cắt ở keyframe phù hợp. 4 giây phù hợp với kiểm thử mặc định. Segment dài giảm số lượt PUT/HEAD/GET và làm mỗi lần tua có thể phải tải nhiều byte hơn. Trình phát đọc thời lượng thực từ `EXTINF`.

FFmpeg dùng muxer HLS fMP4. Trong chế độ R2 streaming, muxer ghi init, từng fragment và playlist VOD bằng HTTP PUT qua loopback. Phương án batch giữ chức năng cũ khi cần đối chiếu hoặc xử lý HLS cục bộ. Xem [HLS và M3U8](HLS.md), [định dạng cloud-first](CLOUD_FIRST_STORAGE.md) và [mã nguồn FFmpeg](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/hlsenc.c).

Ba mức toàn vẹn phục vụ ba mục đích. SHA-256 của toàn MP4 xác định đúng tệp nguồn đã nhận. SHA-256 của init và mỗi segment xác minh byte lưu trên R2. Hash payload từng packet trong bài thử stream copy kiểm dữ liệu media đã mã hóa trước và sau remux. HLS và MP4 có cấu trúc container khác nhau nên checksum toàn tệp của chúng khác nhau. Chất lượng hình và tiếng sau chuyển mã là thuộc tính của encoder, độc lập với việc SHA-256 của object lưu trữ có khớp. Lab HLSLAB vẫn so sánh hai bản byte nguồn và khôi phục trong môi trường cục bộ.

Tài liệu về ý nghĩa stream copy và các tùy chọn timestamp. [FFmpeg Documentation](https://ffmpeg.org/ffmpeg.html).
