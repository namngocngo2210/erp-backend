# CRM Quản Lý Đơn Hàng và Khách Hàng - Thiết Kế Hệ Thống (MVP 1.5 Tuần)

## 1) Tóm tắt hiểu bài toán
- Xây dựng CRM nội bộ cho CSKH để quản lý đơn hàng và khách hàng.
- Đơn hàng đổ về từ nhiều landing page qua API, có gắn theo kênh.
- CSKH gọi xác nhận và chỉnh sửa đơn ngay trên danh sách (inline edit).
- Hệ thống cần realtime trong CRM: đơn mới và cập nhật trạng thái hiển thị ngay.
- Navbar có icon thông báo khi có đơn mới.
- Phân quyền: `admin` xem toàn bộ; `nhân viên` xem toàn bộ nhưng chỉ sửa đơn thuộc kênh phụ trách.
- Quy mô hiện tại: >2000 đơn/tháng, khoảng 10 người dùng.
- Hạ tầng: Docker trên 1 VPS, cơ sở dữ liệu PostgreSQL.

## 2) Phạm vi và phần chưa làm
### MVP trong 1.5 tuần (bắt buộc)
- Login, logout, RBAC (`admin`, `staff`).
- Danh sách đơn hàng/khách hàng, tìm kiếm, lọc.
- Inline edit trên từng dòng đơn hàng.
- API nhận đơn từ landing page, validate/chuyển đổi dữ liệu, lưu DB.
- Realtime update trong CRM + icon thông báo đơn mới.
- Dashboard cơ bản (tổng đơn theo trạng thái/kênh/ngày).

### Chưa làm trong MVP
- Tích hợp KiotViet thật (tạo hóa đơn/cập nhật vận đơn/giá ship).
- Luồng đợi phức tạp nhiều tầng, báo cáo BI nâng cao.

## 3) Giả định
- Backend: Django + Django REST Framework + Django Channels.
- Realtime: Redis channel layer cho WebSocket.
- RabbitMQ để phase sau; MVP ưu tiên đơn giản.
- Mỗi kênh landing page có token API riêng để xác thực nguồn gửi.
- Số điện thoại là khóa để gộp lịch sử mua hàng của khách.

## 4) Kiến trúc đề xuất
### Option A (Khuyến nghị cho 1.5 tuần): Modular Monolith + Redis Realtime
- Một codebase Django, tách app theo module (`auth`, `orders`, `customers`, `channels`, `dashboard`, `integrations`).
- REST API cho CRUD và ingest đơn.
- WebSocket (Channels) để đẩy sự kiện đến React client.
- PostgreSQL cho dữ liệu nghiệp vụ, Redis cho pub/sub channel layer.
- Docker Compose cho local/prod nhỏ.

### Option B: Monolith + Celery + Redis ngay từ đầu
- Thêm Celery worker để xử lý async ingest và đồng bộ.
- Linh hoạt hơn cho tác vụ nền, nhưng tăng độ phức tạp vận hành.

### Option C: Tách microservice sớm
- Tách service ingest/order/customer/realtime.
- Không phù hợp timebox 1.5 tuần và quy mô hiện tại.

## Lựa chọn
- Chọn **Option A** để đạt tốc độ ra MVP, giữ khả năng mở rộng sang Option B mà không phá vỡ kiến trúc.

## 5) Decision Log (tóm tắt ADR)
### ADR-001: Chọn Modular Monolith
- Alternatives: Microservices, Monolith thuần.
- Why: Timebox rất ngắn, team nhỏ, cần ship nhanh.
- Trade-off: Scale theo service chưa tối ưu.
- Revisit khi: >20 người dùng nội bộ hoặc >30k đơn/tháng.

### ADR-002: PostgreSQL làm DB chính
- Alternatives: MySQL, SQLite.
- Why: Quan hệ dữ liệu rõ, cần filter/search/history theo SĐT.
- Trade-off: Cần quản trị backup/tuning trên VPS.
- Revisit khi: Multi-region hoặc tải đọc/ghi rất lớn.

### ADR-003: Redis + WebSocket cho realtime
- Alternatives: Polling theo chu kỳ.
- Why: UI cần cập nhật ngay khi có đơn mới/trạng thái thay đổi.
- Trade-off: Tăng thêm 1 thành phần hạ tầng.
- Revisit khi: Load realtime vượt khả năng 1 node Redis.

## 6) Mô hình dữ liệu (PostgreSQL)
### Bảng người dùng và phân quyền
- `users`: id, username, password_hash, role (`admin`,`staff`), is_active, created_at, updated_at.
- `user_channels`: user_id, channel_id (staff có thể phụ trách nhiều kênh).

### Bảng kênh
- `channels`: id, code, name, landing_source, is_active, created_at.
- `channel_api_tokens`: id, channel_id, token_hash, status, created_at, rotated_at.

### Bảng khách hàng
- `customers`: id, full_name, phone_normalized, phone_raw, address, note, first_order_at, last_order_at, created_at, updated_at.
- Unique de-dup: `phone_normalized`.

### Bảng đơn hàng
- `orders`: id, external_order_id, channel_id, customer_id, order_status, confirm_status, total_amount, shipping_fee, shipping_code, kiotviet_invoice_id, raw_payload_jsonb, created_at, updated_at.
- `order_items`: id, order_id, sku, product_name, quantity, unit_price, line_total.
- `order_status_logs`: id, order_id, from_status, to_status, changed_by, changed_at, reason.

### Bảng thông báo
- `notifications`: id, user_id, type, title, payload_jsonb, is_read, created_at.

### Indexing bắt buộc
- `orders(created_at desc)`.
- `orders(channel_id, order_status, created_at desc)`.
- `customers(phone_normalized) unique`.
- `orders(customer_id, created_at desc)`.
- `order_status_logs(order_id, changed_at desc)`.

## 7) API Contract (MVP)
### Auth
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`

### Ingest đơn từ landing page
- `POST /api/integrations/landing/orders`
- Header: `X-Channel-Code`, `X-Api-Token`
- Server xử lý:
  - validate payload
  - normalize phone
  - upsert customer theo phone
  - create order + items + initial status log
  - phát event realtime `order.created`

### CRM APIs
- `GET /api/orders` (search/filters/pagination)
- `PATCH /api/orders/{id}` (inline edit với permission check)
- `GET /api/customers`
- `GET /api/customers/{id}/orders`
- `GET /api/dashboard/summary`

## 8) Thiết kế realtime
- React mở kết nối WebSocket `/ws/crm`.
- Event cần đẩy:
  - `order.created`
  - `order.updated`
  - `notification.created`
- Hành vi UI:
  - thêm đơn mới lên đầu danh sách nếu đúng bộ lọc.
  - cập nhật row nếu đơn đã hiển thị.
  - navbar badge tăng khi có đơn mới/chưa đọc.

## 9) Thiết kế frontend (ReactTS)
### Hướng thẩm mỹ
- `Industrial utilitarian CRM`.
- Mục tiêu: rõ ràng, thao tác nhanh, nhận diện trạng thái tốt.

### Design system snapshot
- Typography: một sans để đọc nhanh (body), một weight đậm cho heading/status.
- Màu:
  - neutral xám nhạt cho nền
  - accent xanh cho trạng thái thành công
  - accent đỏ/cam cho cảnh báo và đơn mới
- Layout:
  - table-first, sticky filter bar, sticky action column
  - navbar có noti icon + badge.
- Motion:
  - chỉ animate nhẹ khi có row mới và badge tăng.

### Tránh generic UI bằng cách
- Ưu tiên "operation-first table UX" thay vì card dashboard chung chung.
- Inline edit theo từng cell với state rõ (editing/saving/saved/error).

## 10) Docker Deployment (1 VPS)
### Services
- `web`: Django + DRF + Channels (gunicorn/uvicorn).
- `postgres`: PostgreSQL.
- `redis`: channel layer + cache.
- `nginx`: reverse proxy + static.

### Biến môi trường tối thiểu
- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS`
- `DATABASE_URL`
- `REDIS_URL`
- `CORS_ALLOWED_ORIGINS`

## 11) Security và Reliability
- Password hash bằng Argon2/Bcrypt.
- JWT hoặc session cookie HttpOnly + CSRF.
- RBAC check ở API layer và object-level permission (channel ownership cho edit).
- Audit log cho thay đổi trạng thái đơn.
- Backup Postgres hằng ngày + retention 7-14 ngày.
- Healthcheck container + restart policy.

## 12) Lộ trình thực thi 1.5 tuần
### Ngày 1-2
- Scaffold Django + ReactTS + Docker Compose.
- Auth + RBAC + base layout CRM.

### Ngày 3-5
- Schema DB + migration + seed.
- Orders/Customers list + search/filter/pagination.
- Inline edit + permission check.

### Ngày 6-8
- API ingest landing page + validate/normalize/upsert.
- WebSocket realtime + navbar notification badge.

### Ngày 9-10
- Dashboard cơ bản.
- Hardening: logging, audit trail, backup script.
- UAT nội bộ + fix bug + đóng gói bản MVP.

## 13) Mở rộng sau MVP (Phase C - KiotViet)
- Tạo app `integrations_kiotviet` riêng.
- Outbox pattern để đảm bảo gửi sự kiện tin cậy.
- Có retry/backoff + dead-letter queue.
- Đồng bộ 2 chiều: trạng thái, mã vận đơn, phí ship.

## 14) Cập nhật triển khai thực tế (2026-03-18)
### 14.1 Trạng thái đã làm
- Đã có luồng ingest landing page tạo đơn hàng, parse địa chỉ và realtime đẩy về CRM.
- Đã tách cột địa chỉ thành `address` (street), `ward`, `district`, `province`.
- Đã thêm mã hành chính: `ward_code`, `district_code`, `province_code` trong bảng `orders`.
- Đã thêm API catalog địa giới để FE dùng dropdown phụ thuộc:
  - `GET /api/orders/address-catalog`
- FE đã hỗ trợ chọn địa giới theo cascade:
  - Chọn `Tỉnh/TP` -> lọc `Quận/Huyện`
  - Chọn `Quận/Huyện` -> lọc `Phường/Xã`
- Đã bổ sung `staff_id` cho đơn hàng, validate server-side staff phải thuộc đúng channel đã assign.
- Đã thêm `owner` cho channel và hiển thị tách biệt giữa `chủ kênh` và `nhân viên xử lý`.

### 14.2 Parse địa chỉ và mapping địa giới
- Nguồn parse: `vietnamadminunits` (mode `LEGACY`, level `3`).
- Dữ liệu chuẩn hóa địa giới: `backend/apps/orders/data/sorted.json` (local file).
- Khi ingest:
  - Ưu tiên lưu text địa giới từ `short_province`, `short_district`, `short_ward`.
  - Ưu tiên lưu code từ `province_code`, `district_code`, `ward_code` parser trả về.
  - Mapping catalog dùng để chuẩn hóa và fallback khi thiếu dữ liệu parser.

### 14.3 Xác thực (đã chuyển sang JWT)
- REST API đang dùng JWT:
  - Access token + Refresh token (SimpleJWT).
  - Access mặc định 15 phút.
  - Refresh mặc định 3 ngày cho production, 14 ngày cho non-production.
  - Refresh rotation + blacklist enabled.
- Endpoint auth hiện tại:
  - `POST /api/users/login`
  - `POST /api/users/token/refresh`
  - `POST /api/users/logout`
  - `GET /api/users/me`

### 14.4 WebSocket (JWT)
- Kênh realtime: `ws://<host>/ws/crm/?token=<access_token>` (hoặc `wss://`).
- Backend dùng middleware JWT cho Channels và từ chối kết nối nếu token không hợp lệ (`4401`).

### 14.5 Quy ước URL
- Hệ thống đang chuẩn hóa API theo dạng không có slash cuối endpoint.
- `APPEND_SLASH=False` trong Django settings.
