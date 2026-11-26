# Xử lý nhị phân trong lab

Phase 03 dùng ảnh tổng hợp và bản sao phân đoạn HLS tự sở hữu. Công cụ chạy cục bộ bằng Python, đọc dữ liệu theo khối và ghi kết quả vào thư mục lab. Tệp nguồn Phase 02 được mở để đọc.

## JPEG ở mức byte

Byte offset là vị trí tính từ đầu tệp, bắt đầu bằng 0. Hai byte `FF D8` là marker **Start of Image** (SOI). Chúng là chữ ký nhận diện ban đầu. Phía sau SOI là các marker và trường của JPEG. Một số file dùng `APP0` chứa thông tin JFIF, hoặc dùng `APP1` cho metadata như Exif. Ở các marker có trường độ dài, hai byte độ dài dùng big-endian, tính cả chính trường độ dài và bắt đầu sau byte mã marker. SOI và **End of Image** (EOI, `FF D9`) đứng riêng, mỗi marker dài 2 byte. [ITU-T T.81, phụ lục B](https://www.itu.int/rec/T-REC-T.81-199209-I/en).

| Marker của `valid.jpg` | Byte offset | Vai trò |
| --- | ---: | --- |
| `FF D8` (SOI) | `0x0000` (0) | Bắt đầu ảnh JPEG. |
| `FF E0` (APP0) | `0x0002` (2) | Độ dài `00 10` ở `0x0004`. Payload 14 byte bắt đầu ở `0x0006` với `4A 46 49 46 00` (`JFIF\0`). |
| `FF FE` (COM) | `0x0014` (20) | Trường chú thích do bộ mã hóa tạo. |
| `FF DB` (DQT), `FF C4` (DHT) | `0x0026` (38), `0x006B` (107) | Bảng lượng tử và bảng Huffman. |
| `FF C0` (SOF0) | `0x011E` (286) | Khung baseline. Precision ở offset 290, chiều cao tại 291 và chiều rộng tại 293, mỗi chiều là số 2 byte big-endian. FFmpeg tạo ảnh 96 × 64 pixel. |
| `FF DA` (SOS) | `0x0131` (305) | Header của scan theo sau marker. Dữ liệu nén của scan bắt đầu ở offset 319. |
| `FF D9` (EOI) | `0x093C` (2364) | Kết thúc codestream. Offset ngay sau marker là cuối tệp mẫu. |

Các trường APP và phần dữ liệu nén có thể chứa byte `FF`. Parser bỏ qua payload của marker theo độ dài, rồi tìm marker trong vùng scan có xử lý `FF 00` (byte stuffing) và `FF D0` đến `FF D7` (restart marker). Nó đọc được nhiều scan và hỗ trợ cấu trúc Huffman SOF0, SOF1, SOF2 có kích thước khai báo trong frame. Kết quả phản ánh cấu trúc marker. FFmpeg được dùng riêng để kiểm tra khả năng giải mã ảnh mẫu. [ITU-T T.81, phụ lục B và F](https://www.itu.int/rec/T-REC-T.81-199209-I/en).

## fMP4 đầu ra Phase 02

Mỗi box có trường kích thước big-endian và tên bốn byte. Header thông thường dài 8 byte, gồm kích thước 32 bit ở offset tương đối 0 và tên loại ở offset tương đối 4. Giá trị kích thước `1` chỉ kích thước mở rộng 64 bit ngay sau tên. Giá trị `0` được đọc tới hết vùng cha. Box `uuid` có thêm 16 byte định danh trong header. Detector kiểm tra biên và đọc box con trong `moov`, `mvex`, `trak`, `moof` và `traf`. [W3C ISO BMFF byte stream](https://www.w3.org/TR/mse-byte-stream-format-isobmff/).

| Tệp và box quan sát được | Offset | Kích thước | Nội dung |
| --- | ---: | ---: | --- |
| `init.mp4` · `ftyp` | 0 | 28 | Thông tin brand của tệp. |
| `init.mp4` · `moov` | 28 | 1.326 | Mô tả track và cấu hình khởi tạo. Box con `mvex` báo media có fragment. |
| `seg_000000.m4s` · `styp` | 0 | 24 | Thông tin brand của segment. |
| `seg_000000.m4s` · `sidx` | 24 và 76 | 52 mỗi box | Chỉ mục fragment. |
| `seg_000000.m4s` · `moof` | 128 | 1.556 | Metadata fragment, gồm `mfhd` và hai `traf` ở offset 152 và 872. Các box con `tfhd`, `tfdt`, `trun` mô tả track, mốc giải mã và sample. |
| `seg_000000.m4s` · `mdat` | 1684 | 84.990 | Dữ liệu sample nén. Payload bắt đầu ở offset 1692. |

Cả ba tệp `.m4s` trong mẫu này có `moof` ở offset 128. `mdat` lần lượt bắt đầu ở 1684, 1700 và 744. JSON của detector ghi cả offset lẫn kích thước của từng box. Init segment cung cấp thông tin mà bộ giải mã cần trước khi đọc media segment.

## Chữ ký, phần mở rộng, MIME và polyglot

`signature` là dấu hiệu tìm được trong byte. `mime_by_extension` là tra cứu từ tên tệp. `declared_mime` là nhãn thử nghiệm do người chạy lệnh cung cấp, tương tự cách một bên gửi có thể khai báo Content-Type. `mime_by_structure` là kết quả dựa trên cấu trúc quan sát được. Đặt các trường cạnh nhau giúp thấy vì sao đổi tên tệp hoặc đặt nhãn MIME không đổi dữ liệu. [Danh mục media type IANA](https://www.iana.org/assignments/media-types).

| Tệp thử nghiệm | Chữ ký | Kết quả đọc cấu trúc |
| --- | --- | --- |
| `valid.jpg` | JPEG SOI | Có frame, scan và EOI. FFmpeg giải mã được. |
| `trailing-text.jpg` | JPEG SOI | Codestream JPEG kết thúc ở EOI, theo sau là 11 byte văn bản tổng hợp. |
| `missing-eoi.jpg` | JPEG SOI | Thiếu EOI. |
| `soi-only.jpg` | JPEG SOI | Có đúng hai byte SOI và thiếu frame, scan, EOI. |
| `segment-renamed.jpg` | ISO BMFF `styp` ở offset 4 | Byte trùng với `seg_000000.m4s`. Đuôi `.jpg` và nhãn thử `image/jpeg` lệch với cấu trúc fMP4 (`video/iso.segment`). |

Trong lab, *format masquerading* được quan sát qua chênh lệch giữa tên gọi, chữ ký và cấu trúc. *Polyglot* là cùng một chuỗi byte có thể được diễn giải hợp lệ theo nhiều định dạng. Để kết luận polyglot, cần kiểm tra từng cách diễn giải theo đặc tả và trình phân tích tương ứng. Mẫu đổi đuôi và mẫu văn bản sau EOI chỉ minh họa các trường hợp không đồng nhất, với ảnh gốc và media giữ riêng trong bộ mẫu.

## Container HLSLAB v1

Định dạng thử nghiệm dùng phần mở rộng `.hlslab` và magic riêng. Header dài 52 byte (`0x34`). Các trường số đều dùng big-endian.

| Offset hex (thập phân) | Số byte | Trường | Ý nghĩa |
| --- | ---: | --- | --- |
| `0x00` (0) | 8 | Magic | `48 4C 53 4C 41 42 1A 0A`. |
| `0x08` (8) | 2 | Version | `00 01` cho v1. |
| `0x0A` (10) | 2 | Flags | `00 00` trong v1. |
| `0x0C` (12) | 8 | Payload length | Số byte payload dưới dạng số nguyên không dấu 64 bit. |
| `0x14` (20) | 32 | SHA-256 | Digest nhị phân của payload. Container lưu 32 byte thay vì chuỗi hiển thị 64 ký tự hex. |
| `0x34` (52) | Theo payload length | Payload | Các byte tệp nguồn theo đúng thứ tự, kể cả byte `00`. |

`pack` ghi payload theo khối 1 MiB, tính SHA-256 trong lúc sao chép rồi ghi header. `unpack` kiểm tra magic, version, flags và độ dài trước khi ghi vào tệp tạm, sau đó tính lại SHA-256 và chỉ chuyển sang tên đích khi dữ liệu khớp. `verify` tính SHA-256 độc lập của hai tệp và so sánh từng byte. SHA-256 ở đây phát hiện sai lệch nội dung. Tính xác thực của tác giả đòi hỏi cơ chế ký hoặc xác minh khác.

## Byte trong trình duyệt

`ArrayBuffer` giữ một vùng byte. `Uint8Array` là view để đọc từng byte từ 0 đến 255. `ArrayBuffer.slice(start, end)` và `Uint8Array.slice(start, end)` tạo bản sao và bỏ qua vị trí `end`. `Uint8Array.subarray(start, end)` tạo view trên cùng buffer. `Blob.slice()` giới hạn phạm vi cần đọc ngay từ tệp. `DataView.getBigUint64(offset, false)` đọc trường 64 bit big-endian mà không làm mất độ chính xác. [ECMAScript ArrayBuffer và DataView](https://tc39.es/ecma262/2025/multipage/structured-data.html) · [ECMAScript TypedArray](https://tc39.es/ecma262/2025/multipage/indexed-collections.html) · [W3C File API](https://www.w3.org/TR/FileAPI/#dfn-slice).

```javascript
// file là File/Blob người dùng đã chọn trong lab.
const header = await file.slice(0, 52).arrayBuffer();
if (header.byteLength !== 52) throw new Error("Header bị cắt");
const bytes = new Uint8Array(header);
const magic = bytes.subarray(0, 8);
const payloadBytes = new DataView(header).getBigUint64(12, false);
const digestCopy = new Uint8Array(header.slice(20, 52));
```

Ví dụ chỉ đọc 52 byte đầu. Việc xác thực header và băm payload theo khối được thực hiện bằng CLI Python.

## Quan sát bằng ImHex trên Windows

1. Mở [ImHex](https://imhex.werwolv.net/) và chọn **File → Open File…**. Từ repository mở `data\binary-lab\phase03\valid.jpg`. Giữ base address bằng 0 và đọc offset ở cột trái. [Hướng dẫn mở tệp và đọc Hex Editor](https://docs.werwolv.net/imhex/common/providers) · [ImHex Hex Editor](https://docs.werwolv.net/imhex/views/hex-editor).
2. Xem `FF D8` ở đầu. Dùng **Go to address…** (mặc định `Ctrl+G`) và nhập `0x093C` để xem EOI `FF D9`. Mở `trailing-text.jpg`, tới `0x093E` và xem vùng ASCII hiển thị `LAB_SAMPLE`. [Hướng dẫn cấu hình phím tắt ImHex](https://docs.werwolv.net/imhex/misc/settings).
3. Mở `segment-renamed.jpg`. Ở `0x04` có `73 74 79 70` (`styp`). Tới `0x80` xem kích thước box `moof` và ở `0x84` xem tên box dạng ASCII.
4. Mở `seg_000000.m4s.hlslab`. Tại `0x14` xem digest bắt đầu. Tới `0x34` xem payload. Đối chiếu 8 byte đầu payload với offset `0x00` của `restored-seg_000000.m4s`. Sử dụng các tệp trong thư mục lab để quan sát.

## Lệnh PowerShell

Chạy từ thư mục gốc repository. Thay UUID hoặc đường dẫn dữ liệu nếu video ở vị trí khác. FFmpeg trong `PATH` dùng để tạo ảnh JPEG tổng hợp và kiểm tra giải mã ảnh mẫu.

```powershell
$py = '.\.venv\Scripts\python.exe'
$hls = '.\data\processing\ee3e7935-4b66-492d-a7f5-f937fb4be8f1'
$lab = ".\data\binary-lab\phase03-$(Get-Date -Format yyyyMMdd-HHmmss)"
& $py -m tests.binary_lab_smoke --hls-dir $hls --output-dir $lab
& $py -m backend.binary.signature_detector (Join-Path $lab 'segment-renamed.jpg') --declared-mime image/jpeg
& $py -m backend.binary.lab_container verify (Join-Path $hls 'seg_000000.m4s') (Join-Path $lab 'restored-seg_000000.m4s')
```

Đóng gói và mở riêng một tệp vào hai tên đích mới.

```powershell
& $py -m backend.binary.lab_container pack (Join-Path $hls 'seg_000000.m4s') (Join-Path $lab 'segment.hlslab')
& $py -m backend.binary.lab_container unpack (Join-Path $lab 'segment.hlslab') (Join-Path $lab 'segment.m4s')
```

Kết quả chạy thực tế nằm trong `data/binary-lab/phase03/report.json`. Báo cáo liệt kê phép đối chiếu marker JPEG, cây box fMP4, SHA-256 trước và sau, kết quả so sánh byte và các ca hỏng bị từ chối.
