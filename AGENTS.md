# Quy định dự án HLS Binary Cloud Streaming

- Đọc `README.md`, `AGENTS.md` và `PROJECT_STATE.md` trước mỗi phase. Chỉ thực hiện phase được giao.
- Dự án chạy trực tiếp trên Windows 10/11 với PowerShell. Dùng Python, FastAPI, SQLite, FFmpeg, FFprobe, Boto3, JavaScript thuần, HTML5 MSE, Cloudflare R2 và CDN theo phạm vi phase.
- Phase 08 Addition dùng một website Vercel, D1 cho xác thực/danh mục và R2 private cho media. Companion chạy bằng PowerShell trên Windows, không mở HTTP production, không có queue online. UI Windows cũ chỉ giữ cho kiểm thử hồi quy có bật cờ lab.
- Không dùng Ubuntu, WSL, Docker, React, Vue, Redis, Celery hoặc PostgreSQL. Giao diện HTML chỉ đủ để kiểm thử upload, danh sách và phát video. Không xây dựng dashboard hay tính năng ngoài phạm vi.
- Đọc và ghi media theo luồng hoặc theo khối. Dung lượng upload phải cấu hình được. Không đặt giới hạn cứng về thời lượng hoặc số phân đoạn.
- Nghiên cứu chữ ký JPEG với dữ liệu tự sở hữu trên máy lab nội bộ. Giữ tệp thí nghiệm tại lab. Khi phân phối qua CDN, dùng HLS chuẩn, khai báo đúng loại media và tuân thủ điều khoản Cloudflare.
- Nạp thông tin xác thực từ môi trường khi tích hợp R2. Giữ bí mật, cơ sở dữ liệu và media sinh ra ngoài Git.
- Chia mã thành module theo trách nhiệm. Triển khai mã chạy được cho chức năng thuộc phase. Viết tài liệu kiến trúc và giải thích mã bằng tiếng Việt.
- Sau mỗi phase, kiểm thử chức năng và cập nhật `PROJECT_STATE.md` với danh sách tệp, vai trò module, lệnh PowerShell, kết quả thực tế và công việc được chuyển giao.
