# Đăng nhập và quyền

Cùng một website Vercel phục vụ Admin và User. Worker tìm tài khoản trong D1, kiểm tra mật khẩu bằng scrypt có salt ngẫu nhiên và tạo token ngẫu nhiên 256 bit. D1 giữ hash SHA-256 của token cùng thời hạn và mốc thu hồi. Trang giữ token trong bộ nhớ tab và gửi qua `Authorization: Bearer` tới đúng HTTPS origin của Worker.

- `ADMIN` được xem video, quản lý User, đổi tiêu đề, xóa video và xác nhận phát/tua. Tài khoản đầu tiên do người triển khai tạo bằng SQL bootstrap có hash.
- `USER` được xem, phát, pause, tua và đăng xuất. Mọi endpoint quản trị kiểm tra role tại Worker. Dữ liệu tạo User luôn dùng role USER.
- Khóa, đặt lại mật khẩu hoặc xóa một User sẽ vô hiệu các session của tài khoản đó. Đăng xuất thu hồi session tại máy chủ. Trang yêu cầu đăng nhập lại khi nhận 401 hoặc hết thời hạn.
- Worker giới hạn lượt đăng nhập theo username và nguồn request trong cửa sổ 15 phút. Địa chỉ mạng chỉ tham gia giới hạn đăng nhập. Role lấy từ D1.
- Token companion khác token phiên. Token này chỉ xác thực `/api/local/*`, nằm trong secret Worker và `.env` Windows. CLI cũng yêu cầu đăng nhập Admin trước khi cho phép thao tác. Token User luôn bị từ chối ở API companion.

Không tạo tài khoản công khai. Admin dùng form **Tạo User**, rồi gửi mật khẩu ban đầu qua kênh riêng. Khi tạo hoặc đặt lại mật khẩu User, Worker chấp nhận từ 1 ký tự đến tối đa 256 byte UTF-8. API giữ hash mật khẩu ở máy chủ. Mật khẩu Admin khi bootstrap cần ít nhất 12 ký tự.

Cấu hình `SESSION_TTL_SECONDS` từ 60 đến 86400 giây. Giá trị mặc định là 28800. Phiên giữ trong bộ nhớ nên tải lại toàn bộ trang cần đăng nhập lại. Endpoint metadata, playlist, init và media kiểm tra phiên đang hiệu lực, trạng thái tài khoản và video READY ở từng request. CORS chỉ giới hạn origin trong trình duyệt.

Scrypt chạy trong `node:crypto` của Worker với N=16384, r=8, p=5. Cần kiểm tra ngân sách CPU của gói Cloudflare khi triển khai thật. Mã triển khai và bộ test không kích hoạt gói trả phí. [Cloudflare Node.js crypto](https://developers.cloudflare.com/workers/runtime-apis/nodejs/crypto/) · [Giới hạn Workers](https://developers.cloudflare.com/workers/platform/limits/).

