# Security & Code-Quality Audit — desi-order (2026-06-07)

> Báo cáo kiểm tra toàn diện backend (Django 6 / DRF / Celery / Channels), frontend
> (React 18 / Vite / TS) và hạ tầng (docker-compose / nginx / CI). Mọi phát hiện được
> verify trực tiếp trên code thật, không suy đoán.
>
> **Quy ước trạng thái:** `[ ]` chưa làm · `[~]` đang làm · `[x]` xong (ghi commit/MR).
> Mỗi mục có: vị trí · mô tả · cách fix. Cập nhật tiến độ ngay tại file này sau mỗi sprint.

## Tổng quan mức độ

| Mức | Số lượng | Phạm vi |
|-----|----------|---------|
| 🔴 CRITICAL | 4 | Rò rỉ PII/credential, mật khẩu plaintext, DB phơi ra internet |
| 🟠 HIGH | 10 | Auth fail-open, thiếu object-level authz, JWT đời quá dài, token lưu sai chỗ, Docker chạy root |
| 🟡 MEDIUM | 8 | Rate-limit login, race condition fetch, CI thiếu backend, alerting rỗng |
| 🟢 LOW | 7 | Logging thừa, key theo index, file rác, header thiếu |

---

## 🔴 SPRINT 0 — "Stop the bleeding" (ưu tiên: NGAY, 1–2 ngày)

> Đây là **incident an ninh đang phơi dữ liệu**, không phải cải tiến. Làm trước tất cả.

- [ ] **C1 — Dump DB production (PII + credential) bị commit vào git**
  - Vị trí: `backup/*.dump`, `backup/postgres/desi_order.sql`, `backup/redis*.rdb` (đang `git ls-files`-tracked)
  - Rủi ro: `desi_order.sql` chứa **cleartext** họ tên / SĐT / địa chỉ khách, hash mật khẩu Django,
    session active, `kiotviet.client_secret`, `channelapitoken.token_hash`. RDB cache PII tương tự.
  - Fix:
    1. `git rm -r --cached backup/` và thêm `backup/` vào `.gitignore`
    2. Purge khỏi history: `git filter-repo --path backup/ --invert-paths` (hoặc BFG)
    3. Force-push + thông báo team re-clone
    4. **Rotate**: KiotViet `client_secret`, `JWT_SIGNING_KEY`, `ENCRYPTION_KEY`, session key
    5. Chuyển backup sang object storage mã hóa (R2/S3 — script `scripts/desi-backup.sh:30` đã có sẵn dòng push S3 đang comment)
    6. Đánh giá nghĩa vụ thông báo data breach (PII khách hàng)

- [ ] **C2 — Mật khẩu plaintext ghi vào audit log**
  - Vị trí: `backend/apps/audit/middleware_impl.py:171-181` (call site `:89`, `SKIP_PATHS :24`)
  - Rủi ro: `_create_curl_command` serialize body JSON của mọi POST/PUT/PATCH vào `AuditLog.curl_command`;
    không loại trừ auth → `POST /api/login`, `change_password`, admin create user lưu `password` cleartext vào DB + forward sang Go audit-writer.
  - Fix: tạo redaction set (`password`, `current_password`, `new_password`, `token`, `secret`, `access`, `refresh`),
    mask trước khi serialize; thêm path auth (`/api/login`, `/api/users/.../password`) vào `SKIP_PATHS`.

- [ ] **C3 — Postgres bind `0.0.0.0` ở production**
  - Vị trí: `docker-compose.prod.yml:128-129` (`${POSTGRES_BIND_IP:-0.0.0.0}:5432:5432`)
  - Rủi ro: DB ~130GB phơi ra mọi interface, chỉ dựa firewall ngoài luồng; mật khẩu `Desi@2026` yếu, dùng chung.
  - Fix: đổi mặc định `POSTGRES_BIND_IP=127.0.0.1` (service nội bộ đi qua docker network `desi-net`);
    bỏ host port nếu không cần external; đặt mật khẩu mạnh **riêng** từng service.

- [ ] **C4 — Secret hardcode trong compose + Redis không persistence**
  - Vị trí: `docker-compose.yml:7,26,32,41` (`POSTGRES_PASSWORD: Desi@2026`, `--requirepass Desi@2026`); Redis `--appendonly no`
  - Rủi ro: secret nằm vĩnh viễn trong history; Redis là broker Celery + Channels + cache → restart mất sạch task đang chạy.
  - Fix: thay literal bằng `${POSTGRES_PASSWORD:?}` / `${REDIS_PASSWORD:?}` + rotate;
    bật AOF (`--appendonly yes --appendfsync everysec`) trên volume bền; cân nhắc tách cache (ephemeral) khỏi broker (durable).

- [ ] **C5 (đi kèm) — KiotViet webhook secret thật commit trong `.env.example`**
  - Vị trí: `.env.example:24-25` (`KIOTVIET_WEBHOOK_SECRET=@@@Desi2026@@@` + bản b64)
  - Fix: thay bằng placeholder `change-me`; rotate webhook secret thật.

---

## 🟠 SPRINT 1 — Hardening Auth & App Security (3–5 ngày)

### Backend
- [ ] **H1 — Webhook/ingest fail-open khi secret trống** — `apps/zalo_bridge/views.py:267`,
  `apps/orders/.../order_ingest_product_dashboard_view.py:126,263`
  → khi secret/token rỗng thì bỏ qua xác thực HMAC/bearer → ai cũng tạo order / push event.
  Fix: reject (401/500) khi secret chưa cấu hình, ít nhất khi `IS_PRODUCTION` (theo mẫu KiotViet `webhook_impl.py:315-322` fail-closed).

- [ ] **H2 — Resale endpoints thiếu object-level (channel) authz** — `apps/customers/views.py:171,199,228,287` (+ `customer_orders :329`)
  → user section `resale-tracking` sửa được order/customer của **mọi channel**.
  Fix: thêm `OrderService.can_edit_channel(...)` như `order_action_view.py:39`.

- [ ] **H3 — JWT access token sống 1 năm** — `config/settings_parts/base.py:417-418`
  (`JWT_ACCESS_MINUTES=525600`, `JWT_REFRESH_DAYS=365`); access token không bị blacklist.
  Fix: access ≤ 15–60 phút, refresh ≤ 7–30 ngày; dựa rotation + blacklist (đã bật) cho refresh.

- [ ] **H4 — `/api/orders/metrics` public** — `apps/orders/.../metrics_view.py:14-16` (AllowAny), nginx `default.conf:32`
  → lộ business metrics (order count, ingest rate) ra internet.
  Fix: chặn ở nginx (`allow` dải nội bộ / `deny all`) hoặc yêu cầu token nội bộ; scrape qua docker network riêng.

### Frontend
- [ ] **H5 — JWT bị log ra console qua URL WebSocket** — `frontend/src/services/websocket.ts:69,76`
  Fix: không log `wsUrl` (chứa `?token=`); chỉ log path; cân nhắc đưa token qua subprotocol.

- [ ] **H6 — JWT access token lưu `localStorage`** — `frontend/src/services/http.ts:3,34` (`desi_access_token`)
  → mọi XSS = đánh cắp token. (Refresh token đã đúng là httpOnly cookie.)
  Fix: giữ access token **in-memory**, dựa refresh-cookie để re-mint khi reload.

- [ ] **H7 — WebSocket connection/timer leak** — `frontend/src/App.tsx:869-1131` (cleanup `:1128`)
  → effect `connect()` nhưng cleanup chỉ `removeEventListener`, không `disconnect()`; dep theo cả object `user`.
  Fix: cleanup gọi `disconnectCRM()`; đổi dep sang `user?.id`.

### Edge / nginx
- [ ] **H8 — Thiếu security headers** — `nginx/conf.d/default.conf`, `nginx/customer.delax.vn.conf.prod-template`
  Fix: thêm `Strict-Transport-Security`, `X-Content-Type-Options nosniff`, `X-Frame-Options DENY`/CSP `frame-ancestors`,
  `Referrer-Policy`, `server_tokens off`; scope CORS ingest (`Access-Control-Allow-Origin: *`) về origin biết trước.

### Docker
- [ ] **H9 — Backend Dockerfile single-stage, chạy root, không `.dockerignore`** — `backend/Dockerfile`
  → `COPY . /app` lùa `.env`/dumps/`staticfiles` vào image; container chạy root.
  Fix: thêm `backend/.dockerignore` (`.env*`, `__pycache__`, `*.dump`, `staticfiles/`, `.venv`, tests);
  thêm `USER appuser`; multi-stage tách build deps.

- [ ] **H10 — Prod compose không định nghĩa Redis nhưng backend phụ thuộc** — `docker-compose.prod.yml` (ref `:39,:297`)
  → dựa container `redis` tạo tay trên network ngoài → SPOF không codify.
  Fix: định nghĩa `redis` trong prod compose (persistence + resource limit) hoặc document chính xác run-spec.

---

## 🟡 SPRINT 2 — CI/CD, Reliability & Quality (≈1 tuần)

- [ ] **M1 — Không rate-limit/lockout login** — `apps/users/views.py:48-65`
  Fix: DRF `AnonRateThrottle`/`ScopedRateThrottle` trên login.

- [ ] **M2 — Race condition fetch danh sách** — `pages/ResaleTrackingPage.tsx:277-300`, `pages/LoyalCustomersPage.tsx:177-199`
  → không AbortController → response stale ghi đè. Mẫu đúng: `AdsReportPage.tsx:199-203`.
  Fix: dùng AbortController hoặc chuyển sang `@tanstack/react-query` (đã có sẵn).

- [ ] **M3 — TOCTOU check trùng SĐT vs claim** — `apps/orders/.../lead_filter_service.py` (`evaluate :91` vs `register_claim :185`)
  Fix: thực hiện claim trong cùng transaction tạo order.

- [ ] **M4 — Audit visibility dùng `is_superuser` thay vì `role==admin`** — `apps/audit/views.py:37-39`
  Fix: dùng helper `_is_admin` / `UserRole.ADMIN`.

- [ ] **M5 — Dependency không pin + bleeding-edge** — `pyproject.toml:7-19` (`>=`), `frontend/package.json` (caret)
  Fix: pin exact (dựa `uv.lock`/`package-lock.json`); thêm `pip-audit` + `npm audit` + Dependabot.

- [ ] **M6 — CI chỉ có frontend, backend không có CI** — `.github/workflows/` (chỉ `frontend-ci.yml`)
  → `test:ci` exclude test đang fail (websocket, OrderFormDialog, mobile) = che lỗi.
  Fix: thêm workflow backend (`ruff`, `pytest`, `migrate --check`, docker build); unmask test FE.

- [ ] **M7 — Alerting rỗng, không Alertmanager, không monitor Celery** — `monitoring/alerts.yml` (`groups: []`), `prometheus.full.yml`
  Fix: viết alert rules (instance down, pg connections, disk, redis mem, Celery queue depth) + wire Alertmanager + celery-exporter/Flower.

- [ ] **M8 — Type safety yếu trên data path** — `features/orders/use-orders-data.ts:19` (`Promise<any>`), ~35 chỗ `as any` trong order-detail
  Fix: `fetchJson: Promise<unknown>` + narrow; định nghĩa DTO; bỏ `catch (err: any)`.

---

## 🟢 SPRINT 3 — Dọn dẹp / Polish (backlog)

- [ ] **L1** — WS token qua subprotocol thay query string — `apps/channels_core/jwt_middleware.py:38-45`
- [ ] **L2** — Bỏ `except Exception: pass` nuốt lỗi (log `debug`) — `audit/middleware_impl.py:206,231,108`
- [ ] **L3** — Key theo `id` thay vì index trong list editable — `OrdersPage.tsx:2364`, `BlockProducts.tsx:338`, `BlockCrmRating.tsx:612`
- [ ] **L4** — Logger gated `import.meta.env.DEV` / esbuild `drop:['console']` (65 chỗ console) — FE
- [ ] **L5** — Top-level ErrorBoundary quanh `<App/>` — `frontend/src/main.tsx`
- [ ] **L6** — Backup script: bật offsite upload + restore-verify + RPO/RTO — `scripts/desi-backup.sh`
- [ ] **L7** — Xóa file rác commit ở root (`order_id`, `main.py`, `_map_t4.py`, root `package.json`); pin `:latest` image monitoring; thêm healthcheck worker/beat

---

## Ghi chú điểm tốt (đã verify-good, không cần sửa)
- JWT có `token_version` để revoke; permission theo section nhất quán; webhook KiotViet ký HMAC fail-closed đúng.
- Không phát hiện SQL injection, command injection, `eval/exec/pickle`, insecure deserialization, `verify=False`, mutable default args, user-controlled SSRF.
- Secret handling trong `settings_parts/base.py` solid (guard prod cho `SECRET_KEY`/`POSTGRES_PASSWORD`, JWT key ≥32 byte, `CORS_ALLOW_CREDENTIALS=False`, không `CORS_ALLOW_ALL`).
- Frontend không có `dangerouslySetInnerHTML`/`eval`; có code-splitting, AbortController (một số nơi), ErrorBoundary.
- `.env` **không** bị git-track (chỉ `.env.example`).

---

## Tiến độ
| Sprint | Trạng thái | Ngày | Ghi chú |
|--------|-----------|------|---------|
| Sprint 0 | ⏳ chưa bắt đầu | — | 4 mục CRITICAL |
| Sprint 1 | ⏳ chưa bắt đầu | — | 10 mục HIGH |
| Sprint 2 | ⏳ chưa bắt đầu | — | 8 mục MEDIUM |
| Sprint 3 | ⏳ chưa bắt đầu | — | 7 mục LOW |
