# Audit bảo mật desi-order — 2026-09-23

> Chỉ đọc và đo. Không sửa code, không commit, không in giá trị secret (chỉ tên biến/file).
> Máy chạy audit chính là host prod (`docker compose ls`: project `desi-order` từ
> `docker-compose.prod.yml`), nên một số bằng chứng là đo trực tiếp (nginx host, `ss`,
> `docker logs`, `curl` public). Đường dẫn backend tính từ `backend/`, frontend từ `frontend/`.
> Script quét: `scratchpad/scan_write_perms.py` (AST, phân loại gate của MỌI view có method ghi,
> function-based lẫn class-based), `scan_isauth_inner.py`, `scan_pagination.py`.
> Kết quả thô: `scratchpad/scan_out.txt`, `npm-audit.json`, `pip-audit.json`.

## 0. Kết luận nhanh

Các lỗ hổng tầng phân quyền hành động (N2) của audit 2026-08-19 đã đóng đúng như mô tả, kèm test
kiến trúc. Rủi ro lớn nhất hiện nay KHÔNG còn ở authZ mà ở vòng đời token và nơi token bị ghi ra:
prod đang đặt access token JWT sống 1 NĂM (ghi đè mặc định 60 phút), trong khi access token đi qua
query string WebSocket và đang được ghi vào log container → Loki → Grafana (112 dòng/24h). Cộng lại:
ai đọc được log (Grafana, docker, nginx access.log) có phiên đăng nhập dùng được 12 tháng, không thu
hồi được trừ khi người dùng đổi mật khẩu.

## 1. Xác minh các mục audit trước

### 1.1 N2 — endpoint ghi gác bằng `require_section` (= `:view`), 13 nhóm

Quét lại toàn bộ `apps/**` bằng AST: 194 view có method ghi. Phân loại: 72 `require_permission`
action thật · 51 `require_method_permission` · 40 chỉ `IsAuthenticated` (đều có gate trong thân —
xem M2) · 14 `require_section` trần (11 đã whitelist "POST-là-đọc", 3 chưa) · 6
`require_permission("x:view")` trên method ghi (đều có gate `:edit` bên trong) · 11 `AllowAny`
(webhook/ingest/portal, có token/throttle).

| Nhóm cũ | Trạng thái | Bằng chứng (gate hiện tại) |
|---|---|---|
| Kho PATCH/DELETE | ĐÓNG | `warehouses/transport/views/warehouse_views.py:116` PATCH=`warehouses:edit`/DELETE=`:delete`; `:52` POST=`:create` |
| Sản phẩm/danh mục/nhóm | ĐÓNG | `orders/.../product_catalog_views.py:263,339,376,419,447,482` method-gate view/create/edit/delete |
| Bảng giá | ĐÓNG | `orders/.../price_book_views.py:73,102` method-gate |
| Blocklist SĐT | ĐÓNG | `orders/.../phone_blocklist_view.py:80,150` method-gate |
| DELETE đơn | ĐÓNG | `orders/.../order_action_view.py:156` DELETE=`ORDER_DELETE_CODES`; `:356 bulk_delete_orders` |
| Sổ kho | ĐÓNG | `inventory/.../attachment_views.py:52,121` `stock-documents:edit`; `document_views.py:68,163,226,259,295,329` create/edit/approve; `return_views.py:50,95,122`; `stock_views.py:239` `stock:edit`; `flow_pending_views.py:177` |
| Bảo hành journey/claim | ĐÓNG | `warranty/journey_views.py:26,46` `warranty:edit`; `claim_views.py:40` POST=`warranty:create`, `:83` PATCH/DELETE, `:117` |
| Khách hàng / resale | ĐÓNG | `customers/.../customer_crud_view.py:86` PATCH=`customers:edit`/DELETE=`:delete`, `:131`; `resale_tracking_view.py:137,189,236,272` `resale-tracking:edit` |
| Contact-flow config | ĐÓNG | `contact_flow/.../config_views.py:94,102,115,123,138,149,180` `contact_flow:edit` |
| Config ads FB/Google | ĐÓNG | `facebook_ads/.../app_config_views.py:36,62`; `google_ads/.../config_views.py:34,63` |
| KShip preset / sender | ĐÓNG | `orders/.../kship_note_preset_view.py:69,110`; `kship_sender_address_view.py:103,129` (`kiotviet:edit`) |

`POST /api/warranty/claims` (mục 7 rbac-scope-review) đã chuyển sang `warranty:create` (`warranty/claim_views.py:40`).

Test kiến trúc: CÓ — `apps/users/tests/test_write_permission_coverage.py` (AST, cùng kỹ thuật
`test_record_scope_coverage.py`) + migration grandfather `users/0039` + `test_grandfather_write_permissions.py`.
NHƯNG test này hiện ĐỎ trên HEAD master (M1). Điểm mù của lưới: M2.

### 1.2 T2 — token ingest dùng chung + CORS `*`
- Token vẫn dùng chung 3 cửa (quyết định bỏ qua 2026-08-19): `config/settings_parts/base.py:641`
  `ORDERS_INGEST_API_TOKEN`; `orders/.../order_ingest_views.py:60,95`; `warranty/ingest_views.py:39`.
  compare_digest + fail-closed khi rỗng (`:66,100`).
- CORS đã THU HẸP: nginx host thật `/etc/nginx/sites-enabled/customer.delax.vn:32`
  `Access-Control-Allow-Origin "https://www.delax.click"` (không còn `*`). `CORS_ALLOW_CREDENTIALS=False` (`base.py:1142`).

### 1.3 rbac-scope-review — 3 việc "còn lại"
| Việc | Trạng thái |
|---|---|
| `can_edit_order` fail-open own/all | CHƯA sửa — `apps/users/services.py:120-121` vẫn `can_edit=True` khi scope ∈ {ALL, OWN}; lưới `test_record_scope_coverage.py` là phòng thủ chính (chỉ phủ `apps.orders/warranty/customers`, `_APP_PREFIXES` :56). |
| `GET /api/orders/search` trả SĐT thô | ĐÃ che — `orders/transport/views/order_search_view.py` áp `PhoneVisibility`. |
| `POST /api/warranty/claims` gate `:view` | ĐÃ sửa → `warranty:create`. |

### 1.4 customer-phone-masking (chốt 2026-09-22)
Đã áp đúng các luồng trong doc (serializer đơn, search, export `orders/exports_definitions.py`,
WS `channels_core/consumers.py:145-166` che per-connection + miễn sàn TMĐT, contact list,
`CustomerPhoneMasker`, `shipping_lookup_view`). Audit middleware mask key `phone`
(`audit/middleware_impl.py:41,48,346`). LỘ qua kênh khác: in hoá đơn HTML (M3). Analytics
`customer_search_view` không trả SĐT (`analytics/infrastructure/mart_queries.py:150-158`), OK.

### 1.5 security-dependency-audit-2026-07-24
- `cryptography` vẫn 46.0.7 (nay thêm 3 CVE mới) — L3. FE `npm audit --omit=dev` không còn 0: 2 high `react-router` — L3.
- `backend:pip-audit` vẫn `allow_failure: true` (`.gitlab-ci.yml:210`) như thiết kế.

### 1.6 Mục #8 audit cũ — `/admin`
CHƯA đóng. `config/urls.py:7`; vhost host `customer.delax.vn` `location /` proxy thẳng backend;
đo thật `curl https://customer.delax.vn/admin/login/` → 302 (reachable); không `allow/deny` IP.

## 2. Phát hiện mới (xếp theo mức)

### HIGH

H1. Prod ghi đè vòng đời JWT thành 1 năm (access) / 1 năm (refresh)
- `.env:34` `JWT_ACCESS_MINUTES=525600`, `.env:35` `JWT_REFRESH_DAYS=365` (cả `.env.bak-20260907-193337`).
  Compose truyền thẳng `docker-compose.prod.yml:122-123`. Mặc định code an toàn 60'/14d
  (`config/settings_parts/base.py:1112-1113`, comment "prod nên đặt access <= 60'"), `.env.example:6-7` = 60/14,
  commit `6d22bea2 security(H1,H2,H3)... shorter JWT` từng rút ngắn — env prod chưa cập nhật.
- Hệ quả: access token stateless, không blacklist; thu hồi duy nhất qua `token_version` khi đổi mật khẩu.
  Token rò (log, máy nhiễm, proxy) dùng được 12 tháng.
- Sửa: `JWT_ACCESS_MINUTES=60`, `JWT_REFRESH_DAYS=14` trong `.env`, restart web; FE tự refresh im lặng
  (`services/http.ts:15-30`). Cân nhắc fail-fast khi `JWT_ACCESS_MINUTES > 1440` ở production.

H2. Access token WebSocket nằm trong query string và đang được ghi vào log → Loki/Grafana
- FE `frontend/src/services/websocket.ts:74` `${safeUrl}?token=${token}`. Uvicorn access log ghi nguyên
  path+query: `docker logs desi-web --since 24h | grep -c "token="` = 112 dòng
  `"WebSocket /ws/crm/?token=<...>" [accepted]`. Alloy thu log mọi container (`monitoring/alloy-config.alloy:1-30`)
  → Loki retention 14 ngày (`monitoring/loki-config.yml:36`) → Grafana `/grafana`. Nginx host `access_log`
  format mặc định (`/etc/nginx/nginx.conf:40`) gần chắc cũng ghi `$request` (file 640, chưa đọc được).
- Kết hợp H1: mọi tài khoản Grafana / ai có `docker logs` / đọc được `/var/log/nginx` lấy được token ~1 năm
  của bất kỳ user, kể cả admin.
- Sửa: (1) không đưa JWT vào URL — gửi token trong message đầu sau connect, hoặc `Sec-WebSocket-Protocol`,
  hoặc WS-ticket một lần sống 30s; (2) tạm thời scrub `token=` trong Alloy (`stage.replace`) + access log
  format gunicorn/uvicorn + nginx `log_format` cho `/ws/`; (3) H1.

H3. Postgres/Redis test đang publish `0.0.0.0` trên host prod, Redis không mật khẩu
- `ss -ltnp`: `0.0.0.0:55432` (desi-test-pg), `0.0.0.0:56379` (desi-test-redis). Chain `DOCKER-USER` rỗng,
  không ufw → mở ra Internet qua IP public (103.190.36.153 có trong `DJANGO_ALLOWED_HOSTS`). Redis không
  `requirepass`; PG mật khẩu không-mặc-định nhưng plaintext trong `scratchpad/run_in_web.sh:5`.
- Nguồn (đã xác minh lại): đây là 2 container test THƯỜNG TRỰC của team, tạo 00:33 +07 23/09 (trước
  phiên audit ~9h) theo quy ước trong `docs/superpowers/plans/*` (`docker start desi-test-pg desi-test-redis`;
  `docs/superpowers/plans/2026-06-02-koc-order-payment-by-type.md:18` hướng dẫn `docker run … -p 5432:5432`
  không bind 127.0.0.1). Không nằm trong `docker-compose*.yml` (mọi port ở đó đều `127.0.0.1`). Vẫn High vì
  đang mở thật ra Internet trên host prod.
- Sửa: tạo lại 2 container với `-p 127.0.0.1:55432:5432` / `-p 127.0.0.1:56379:6379` (+ `--requirepass`);
  sửa hướng dẫn trong docs/plans; thêm rule `DOCKER-USER` chặn inbound tới port docker trừ 80/443
  (Docker bỏ qua ufw). Audit này KHÔNG dừng container vì suite pytest của team/audit đang dùng.

### MEDIUM

M1. Lưới chặn N2 đang ĐỎ trên HEAD — guard không còn chặn gì
- Chạy trực tiếp `test_write_endpoints_do_not_gate_with_require_section` (không cần DB) → FAIL, 3 offender:
  `pancake/transport/views.py:451 retry_order` (POST, thêm ở `1e65138d` 2026-09-17),
  `orders/.../order_address_view.py:129 parse_address_level2` (`03219423` 2026-09-18),
  `:166 lookup_raw_address` (`181199ff` 2026-09-15).
- `retry_order` là ghi thật (queue `retry_lead_order` tạo đơn từ lead) gác bằng `pancake-leads:view`;
  catalog chỉ có `"pancake-leads": ("view",)` (`apps/users/rbac_catalog.py:98`). Hai view địa chỉ là POST-đọc → whitelist.
- `backend:test` (`.gitlab-ci.yml:136-163`, không `allow_failure`) chạy pytest trên MR/master khi `backend/**`
  đổi → hoặc pipeline master đang đỏ, hoặc 3 MR merge không qua CI xanh. Test đỏ mà vẫn merge = guard vô hiệu.
- Sửa: thêm `pancake-leads:edit` (+ grandfather) và gate `retry_order`; whitelist 2 view địa chỉ;
  bật "pipeline must succeed" cho merge.

M2. Điểm mù của lưới N2 (lỗ hổng tái phát, không phải lỗ hổng hiện tại)
- Lưới chỉ bắt `require_section` trần. Ba mẫu lọt lưới, hôm nay đúng nhờ gate trong thân hàm (xác minh bằng
  `scan_isauth_inner.py`): (a) 40 view ghi chỉ `IsAuthenticated`: HR 20 (`hr/transport/views/*` dùng
  `_can(request,"salaries:edit")`…), `channels_core/.../channel_views_impl.py:64-578` (`ChannelService.can_manage`),
  `pancake/transport/views.py:161-329` và `foxia/transport/link_config_views.py:84-130` (`@superuser_only`),
  `exports/transport/views.py:112-126` (lọc `user=request.user`); (b) `require_permission("x:view")` trên
  method ghi: `best_express/transport/api_views.py:185`, `orders/.../lead_assignment_view.py:372`,
  `orders/transport/views/product_profile_views.py:55` — có check `:edit`/`can_assign_sale` bên trong
  (`:190-195`, `:379-383`, `:66-70`); (c) class-based view không được quét.
- Sửa: guard mở rộng — view có method ghi mà `permission_classes` không chứa code non-`:view` → đỏ trừ
  whitelist có lý do; quét cả `permission_classes =` của class.

M3. Che SĐT bị vòng qua bằng in hoá đơn HTML
- `apps/orders/invoice.py:162-165` lấy `customerPhone`/`contactNumber` nguyên vẹn → context `:269` → HTML `:299`.
  Phiếu giao có che (`_mask_phone` `:372-436`) nhưng hoá đơn không.
- Gate in chỉ là record-scope (`orders/.../order_export_view.py:181-207 _scoped_print_queryset`) — sale scope
  `all` KHÔNG có `customer-phone:view` vẫn gọi `GET /api/orders/<id>/invoice` hoặc `bulk_download_invoices`
  (`:309`, cap 200 id) để đọc số thật của đơn người khác. Doc masking không liệt kê luồng in.
- Sửa: áp `phone_visibility_for(request.user)` khi render hoá đơn (cùng luật, miễn sàn TMĐT), hoặc chỉ cho
  in hoá đơn đơn không-của-mình khi có `customer-phone:view`.

M4. Vhost ERP không có security header nào cho SPA
- Đo thật `curl -I https://erp.desiglobal.vn/` → chỉ `server: nginx/1.24.0 (Ubuntu)`; không HSTS, XFO, nosniff,
  CSP; `server_tokens` bật. `customer.delax.vn` có header vì response từ Django `SecurityMiddleware`/
  `XFrameOptionsMiddleware` (`base.py:95,109,1182-1187`) — chỉ phủ `/api/`, không phủ HTML/JS frontend
  (`frontend/nginx.conf` không `add_header` bảo mật). Vhost host `/etc/nginx/sites-enabled/customer.delax.vn` và
  repo `nginx/customer.delax.vn.host.conf.example` không có `add_header` nào; chỉ `tracuu.delax.com.vn:60-63`
  và các `.conf.prod-template` có.
- Hệ quả: ERP nhúng được vào iframe (clickjacking), không HSTS lần tải đầu, không CSP giảm thiểu XSS.
- Sửa: copy 4 `add_header ... always` + `server_tokens off` từ vhost tra-cuu vào block `erp.desiglobal.vn`
  và `customer.delax.vn`; cập nhật file example; CSP `report-only` trước khi enforce.

M5. Chống brute-force chỉ theo IP, không lockout theo tài khoản, mật khẩu mặc định
- `users/.../auth_views.py:37-52` `LoginRateThrottle` key = IP thật (10/min, `base.py:981`); nginx zone
  `desi_api_auth` cũng theo IP. Không throttle theo username, không lockout, không MFA.
  `AUTH_PASSWORD_VALIDATORS` = 4 validator mặc định (`base.py:916-921`, min 8). Đổi mật khẩu có yêu cầu mật
  khẩu cũ + bump `token_version` (`auth_views.py:262-277`) — tốt.
- Sửa: throttle key `username` (5 lần/15 phút) hoặc django-axes; `MinimumLengthValidator` 12; TOTP cho quản trị.

### LOW

L1. `/admin` vẫn lộ Internet — `config/urls.py:7`; live `GET /admin/login/` = 302. Sửa: allow IP nội bộ/VPN ở nginx hoặc bỏ admin trên prod.
L2. Token ingest dùng chung (T2, giữ theo quyết định) — `base.py:641`, `warranty/ingest_views.py:39`.
L3. Dependencies (chạy thật trong container tạm, mount read-only):
- Backend `pip-audit`: 19 phát hiện / 4 gói. `djangorestframework 3.17.0` → 3.17.2: CVE-2026-73228 (request
  parsing), CVE-2026-73229 (AdminRenderer lộ dữ liệu) — DRF hứng Internet, NÊN bump sớm. `django 6.0.7` → 6.0.8:
  CVE-2026-15830 (GeoDjango, không dùng). `cryptography 46.0.7` → 49/50: CVE-2026-69247/69248/69249 + GHSA-537c
  (đã hoãn từ 07/24) — không trên đường request; lên lịch bump major có test Fernet KiotViet. `sqlparse 0.5.5` →
  0.6.0: 5 CVE DoS — chỉ format SQL debug; thấp.
- Frontend `npm audit --omit=dev --package-lock-only`: 2 high, cùng gốc `react-router 7.18.1`
  GHSA-qwww-vcr4-c8h2 (CSRF bypass RSC mode — SPA không dùng RSC) — `fixAvailable=true`, bump.
- EOL: không. Django 6.0, DRF 3.17, celery 5.5.3, redis-py 7.3, React 19.2, Node 22, Python 3.13.
L4. Import Excel không trần size riêng — `inventory/transport/views/import_views.py:58`,
`contact_flow/.../config_views.py:183` (trần duy nhất nginx `client_max_body_size 50m`; `DATA_UPLOAD_MAX_MEMORY_SIZE`
không áp cho file). `openpyxl read_only=True` (`excel_lines.py:72`, `import_service.py:53`) → bom giải nén chỉ tốn
CPU; không `defusedxml` trong requirements (expat stdlib có giới hạn entity). Mẫu đúng có sẵn:
`media/transport/excel_views.py:32,58` (5MB), `stock_document_attachment_service.py:19,60` (10MB + magic bytes).
L5. SSRF gián tiếp qua URL ghi âm — `callcenter/transcribe.py:176 requests.get(url)` với `recording_url` từ payload
Zenify (`callcenter/sync_service.py:100`), không allow-list host/scheme, không chặn IP nội bộ. Sửa: chỉ host Zenify + https.
L6. List endpoint không phân trang (heuristic, 29 view GET `many=True` không limit) — phần lớn bảng nhỏ. Đáng cap:
`channels_core/.../channel_views_impl.py:394 staff_collection`, `users/.../team_views.py:123 department_assignable_users`,
`orders/.../contact_log_view.py:116,168`, `hr/.../leave_attendance_views.py:189 employee_attendance`.
Đơn/khách/contact/audit đều có `parse_required_pagination`.
L7. Prometheus `--web.enable-lifecycle` (`docker-compose.prod.yml:839`) bind 127.0.0.1; cadvisor `privileged` (`:997`). Info.
L8. File chưa commit ở root chứa dữ liệu khách — `age_text_backup.csv` (id khách + tuổi), `product_profiles/` — không gitignore.

## 3. Đã kiểm và ĐẠT (không phải phát hiện)
- AuthN: JWT HS256, `JWT_SIGNING_KEY ≥ 32 byte` ép ở prod (`base.py:1119-1125`); refresh cookie
  `httponly, secure=not DEBUG, samesite=Lax` (`users/.../_view_helpers.py:19-26`); rotation + blacklist
  (`base.py:1132-1133`, app `token_blacklist` :54); `token_version` cho đổi/reset mật khẩu, WS cũng kiểm
  (`channels_core/jwt_middleware.py:28-31`); logout huỷ cả session Django (`auth_views.py:192-201`); refresh từ chối
  token trước đổi mật khẩu (`:157-163`). `RefreshCookieAuthentication` chỉ gắn 1 endpoint video GET
  (`media/transport/stream_auth.py`, `file_views.py:80`) — lý luận SameSite=Lax đúng.
- CSRF: DRF chỉ `VersionedJWTAuthentication` (`base.py:953-955`); session chỉ gate `/api/docs` superuser
  (`config/docs_access.py`), Swagger prod 404 (đo thật).
- AuthZ mặc định: `IsAuthenticated` (`base.py:956`); throttle user 3000/h + anon 600/h (`:964-978`); miễn throttle
  rõ ràng (`common/throttling.py:84-102`).
- Webhook: KiotViet HMAC compare_digest (`kiotviet/webhook_impl.py:97-110`); Best Express `constant_time_compare`
  (`best_express/transport/webhook_views.py:22-32`); CNV token rỗng = từ chối (`cnv_loyalty/transport/webhook_views.py:33-52`);
  CNV OAuth `state`; Hermes/Daily-report/ai_mart `X-Api-Token` compare_digest + throttle riêng; metrics chặn nginx + token
  (`metrics_view.py:31`). Không thấy replay protection (timestamp/nonce) — KiotViet/Best idempotent theo mã đơn, rủi ro thấp.
- Portal public: throttle riêng theo IP thật (`warranty_codes/transport/portal_views.py:55-110`), Zalo token
  compare_digest, `track` cap 10 dòng; form bảo hành 20/h, không trả lại bản ghi cũ (`warranty_views_impl.py:49-60`).
- Input: không `.raw()/.extra()/RawSQL/eval/exec/pickle/yaml.load/subprocess`; 20 `cursor.execute` đều SQL tĩnh hoặc
  tham số hoá (`mart_queries.py:150-158` f-string chỉ chèn hằng; `ai_mart/domain/need_dictionary.py:31-36` 2 hằng UPSERT).
  HTML chỉ ở `orders/invoice.py` (server-side). Excel export chống formula injection (`common/excel_utils.py`).
  FE: 0 `dangerouslySetInnerHTML`; webphone iframe kiểm `event.origin` hai chiều (`public/webphone.html:41,54,199`,
  `WebphoneLeaderProvider.tsx:101,192`).
- Upload/serve: đính kèm phiếu kho sniff magic + 10MB; ảnh chấm công sniff + 800KB
  (`attendance_checkin_serializers.py:8-40`); serve ép `Content-Type` allow-list (`attachment_views.py:96-103`,
  `attendance_event_views.py:160-163`, `portal_views.py:213-221`); MinIO key sinh server; export chỉ tải file của
  mình + UUID (`exports/transport/views.py:139-143`), TTL 24h.
- Secrets: không hardcode; `.env` gitignore (`.gitignore:26-28`); `assert_production_secrets`/`debug_off`
  (`base.py:250-284`); prod `.env`: `DJANGO_DEBUG=0`, `APP_ENV=production`, `DJANGO_ALLOWED_HOSTS` rõ,
  `POSTGRES_BIND_IP` không set (mặc định 127.0.0.1); `scripts/customer_monthly_report.py` không chứa credential.
- Hạ tầng: mọi port compose prod bind `127.0.0.1` (kể cả `compose.prom-port.yml` → `127.0.0.1:9092`); pgbouncer
  không publish; redis `requirepass` (`docker-compose.prod.yml:1085`); backend non-root (`backend/Dockerfile:23-32`);
  TLS 1.2/1.3; rate-limit zone đã cài (`/etc/nginx/conf.d/00-rate-limit-zones.conf`); Grafana tắt sign-up
  (`:907-909`); `ss -ltnp` non-loopback chỉ 22/80/443 (+ 2 port H3).
- Logging/audit: `AuditLogViewSet` read-only (`audit_views_impl.py:119`), user thường chỉ thấy log mình (`:145-158`),
  xoá chỉ qua task retention 90 ngày (`audit/tasks.py:85-113`); body/query redact password/token/phone
  (`middleware_impl.py:25-48,245-248,287`), curl header whitelist 3 header (`:67-71`, không Authorization/X-Api-Token);
  export create/download có audit (`exports/transport/views.py:100,157`); Sentry `send_default_pii=False` BE
  (`observability.py:34`) và FE (`main.tsx:26`).
- Frontend: access token in-memory (`services/http.ts:1-4`); localStorage chỉ user/permissions + UI prefs; route guard
  `RequireAuth` theo `allowed_sections` (`app/routes.tsx:6`) và BE gate độc lập; sourcemap chặn
  (`frontend/nginx.conf:27-29`); `VITE_*` chỉ DSN/WS URL/dev proxy.

## 4. Nghi ngờ / chưa xác minh (không tính vào bảng)
- Nginx host `access.log` có chứa `token=` không: không đọc được (permission denied); format mặc định gần chắc ghi.
- Pipeline GitLab master có đang đỏ vì M1 không — cần xem GitLab.
- Ai đang có tài khoản Grafana (số người đọc được token H2).
- Zip-bomb thực tế trên `parse_import_lines`: chưa thử tải (không chạy exploit).
- Full-suite pytest (`desi-audit-pytest`) mới chạy tới 14% lúc viết; kết quả ở `scratchpad/junit.xml`.

## 5. Bảng tóm tắt
| # | Mức | Phát hiện | Vị trí chính | Trạng thái |
|---|---|---|---|---|
| H1 | High | JWT access 525600' / refresh 365d trên prod | `.env:34-35`, `docker-compose.prod.yml:122-123` | Mới |
| H2 | High | WS token trong URL → uvicorn log → Loki/Grafana | `websocket.ts:74`, `alloy-config.alloy`, `docker logs desi-web` | Mới |
| H3 | High | test-pg/redis (container test thường trực) publish 0.0.0.0, redis không auth, không firewall | host `ss`, docs/superpowers/plans | Mới |
| M1 | Medium | Guard N2 đang đỏ (3 offender), `retry_order` ghi bằng `:view` | `pancake/transport/views.py:451`, `order_address_view.py:129,166` | Mới |
| M2 | Medium | Guard N2 có 3 điểm mù | `test_write_permission_coverage.py` | Mới |
| M3 | Medium | In hoá đơn HTML lộ SĐT đầy đủ | `orders/invoice.py:162-165,269` | Mới |
| M4 | Medium | ERP SPA không HSTS/XFO/nosniff/CSP | `/etc/nginx/sites-enabled/customer.delax.vn`, `nginx/customer.delax.vn.host.conf.example` | Mới |
| M5 | Medium | Không lockout theo tài khoản, mật khẩu min 8 | `auth_views.py:37-52`, `base.py:916-921` | Mới |
| L1 | Low | `/admin` lộ Internet | `config/urls.py:7` | Cũ, chưa đóng |
| L2 | Low | Token ingest dùng chung | `base.py:641` | Cũ, bỏ qua theo quyết định |
| L3 | Low | DRF 3.17.0, Django 6.0.7, cryptography 46.0.7, sqlparse 0.5.5; react-router 7.18.1 | `requirements.txt`, `package.json` | Mới |
| L4 | Low | Import Excel không trần size riêng | `import_views.py:58`, `config_views.py:183` | Mới |
| L5 | Low | Tải ghi âm theo URL Zenify không allow-list | `transcribe.py:176` | Mới |
| L6 | Low | ~29 list endpoint không phân trang (nhỏ) | `scan_pagination.py` | Mới |
| L7 | Low | Prometheus lifecycle, cadvisor privileged | `docker-compose.prod.yml:839,997` | Info |
| L8 | Low | CSV/thư mục dữ liệu khách chưa commit ở root | `age_text_backup.csv`, `product_profiles/` | Mới |
| — | Đã đóng | N2 13 nhóm; warranty claims `:create`; search che SĐT; CORS ingest hẹp | §1 | — |
| — | Chưa đóng | `can_edit_order` fail-open (có lưới bù) | `users/services.py:120-121` | Cũ |

## 6. Top 5 việc nên làm ngay
1. Đặt lại `JWT_ACCESS_MINUTES=60`, `JWT_REFRESH_DAYS=14` trong `.env` prod, restart web (H1) — 5 phút, không ảnh hưởng UX.
2. Bỏ token khỏi URL WebSocket (frame đầu / WS-ticket) và scrub `token=` ở Alloy + log format ngay trong lúc chờ (H2); xoay mật khẩu các admin từng xuất hiện trong log 14 ngày qua.
3. Tạo lại `desi-test-pg`/`desi-test-redis` bind `127.0.0.1` + requirepass; thêm rule `DOCKER-USER` (H3).
4. Làm xanh lại guard N2: `pancake-leads:edit` + gate `retry_order`, whitelist 2 view địa chỉ, mở rộng guard bắt 3 mẫu lọt (M1, M2); kiểm tra vì sao master merge được khi test đỏ.
5. Áp che SĐT vào hoá đơn in (M3) và thêm security header + `server_tokens off` cho vhost ERP (M4) — hai MR nhỏ; kèm bump DRF 3.17.2 + react-router (L3).
