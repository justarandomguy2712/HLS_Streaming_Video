# Vòng đời dữ liệu cloud-first

```mermaid
flowchart LR
    U[MP4 do người dùng sở hữu] -->|HTTP| T[MP4 tạm của ứng dụng]
    T --> F[FFmpeg HLS fMP4]
    F --> S[Spool có giới hạn trên Windows]
    S --> V[SHA-256 theo khối]
    V --> R[(Cloudflare R2)]
    R -->|GET đối chiếu SHA-256| V
    V -->|Đúng| D[Xóa segment tạm]
    V -->|Đủ init và mọi segment| P[Metadata và playlist cuối]
    P --> R
    P --> Q[(SQLite ready và object keys)]
    Q -->|Cần phát| C[Cloudflare CDN hoặc proxy]
    R --> C
    C --> M[MSE chỉ nạp cửa sổ phát]
```

Trình nhận dùng cổng loopback ngẫu nhiên và một token URL của job. HTTP chunk cuối báo file đã được FFmpeg gửi đủ. File đóng trước khi được đưa cho uploader. Journal ghi kết quả xác minh theo thứ tự phân đoạn. Worker đọc nối tiếp journal và playlist, đối chiếu toàn bộ tên phân đoạn, xác nhận init và ENDLIST, tạo metadata rồi upload playlist cuối cùng. SQLite chỉ chuyển ready khi xác minh playlist xong. Với bucket công khai, một client biết chính xác UUID lần xử lý có thể truy cập object riêng lẻ đã gửi trước thời điểm playlist được công bố.

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as FastAPI mới khởi động
    participant D as SQLite
    participant C as CDN hoặc proxy R2
    B->>A: GET /api/videos/ID/playback
    A->>D: Đọc trạng thái và key đã commit
    A-->>B: URL hiện tại, codec và mục tiêu buffer
    B->>C: GET playlist
    B->>C: GET init và segment cần thiết
    C-->>B: fMP4
```

Ở mặc định cloud-first, media tồn tại lâu dài trên R2. Mỗi tệp do ứng dụng nhận hoặc sinh ra trên Windows có thể dọn qua luồng xác minh và commit. SQLite giữ object key, kích thước nguồn, kích thước bộ HLS trên R2, thời lượng, số đoạn, init key và trạng thái. Bản ghi cũ và đường phát tương ứng được giữ tương thích.
