# Audit chi tiết — Security (2026-08-19)

> Báo cáo gốc của luồng audit bảo mật. Bản tổng hợp: `../audit-2026-08-19-full-stack.md`.
> Phạm vi: backend (Django 6 + DRF/ninja), frontend (React SPA), nginx, docker-compose,
> scripts. Chỉ đọc code, không chạy exploit.
> ⚠️ Mục 2 (token ingest / CORS) đã được quyết định **BỎ QUA** — giữ lại trong tài liệu
> để tham chiếu, không nằm trong kế hoạch thực hiện.

## Tổng quan cơ chế auth

- **AuthN**: JWT HS256 (simplejwt) qua `VersionedJWTAuthentication` — có `token_version`
  thu hồi khi đổi mật khẩu. Access token **in-memory ở FE** (`frontend/src/services/http.ts:1-4`),
  refresh token **cookie httpOnly, Secure, SameSite=Lax**
  (`apps/users/transport/views/users_views_impl/_view_helpers.py:20-25`). django-ninja
  dùng chung lõi qua `apps/users/auth_core.py`. Thiết kế tốt.
- **AuthZ**: RBAC catalog tĩnh (`apps/users/rbac_catalog.py`) đủ hạt
  view/create/edit/delete/approve/export; DRF mặc định IsAuthenticated + throttle toàn cục
  (`config/settings_parts/base.py:819-834`).

## 1. [HIGH — hệ thống] Endpoint GHI chỉ gác bằng `:view` (`require_section`)

`require_section(x)` = `x:view` (`apps/users/permissions.py:26-33`). Catalog **có**
`:edit`/`:delete` nhưng endpoint ghi không dùng. Đã xác minh **không có check bổ sung
bên trong**:

| File:line | Endpoint | Hệ quả |
|---|---|---|
| `warehouses/transport/views/warehouse_views.py:113-125` | PATCH/DELETE kho | `warehouses:view` xóa mềm được kho |
| `orders/.../product_catalog_views.py:298,378,441` | PATCH/DELETE sản phẩm, danh mục | `products:view` xóa được sản phẩm |
| `orders/.../price_book_views.py:91` | PATCH/DELETE bảng giá | sửa giá bằng quyền xem |
| `orders/.../phone_blocklist_view.py:143` | PATCH/DELETE blocklist SĐT | gỡ chặn bằng quyền xem |
| `orders/.../order_action_view.py:86-124` | DELETE đơn hàng | xóa mềm đơn chỉ cần `:view` (PATCH đã có field-policy !1247, DELETE chưa) |
| `inventory/.../flow_history_views.py:464,910,929` | sửa note phiếu hoàn, sửa/xóa mã chờ khớp | `stock:view` ghi được sổ kho |
| `inventory attachment_views.py:48,118` + `document_views.py:209` + `return_views.py:46` + `stock_views.py:178` | upload/xóa file phiếu kho, thao tác phiếu | `stock-documents:view` là đủ |
| `warranty/journey_views.py:23,43` + `claim_views.py:114` | thêm/sửa/xóa journey, hoàn tất claim | `warranty:view` — trong khi `bulk_delete` cùng file (:139-140) gác đúng `:delete` |
| `customers/.../customer_crud_view.py:67` + `resale_tracking_view.py:134,186,232,268` | PATCH/DELETE khách, resale | `customers:view` xóa được khách |
| `contact_flow/.../config_views.py:80-157` | PATCH/DELETE cấu hình tác nghiệp | `contact_flow:view` |
| `facebook_ads/.../app_config_views.py:60`, `google_ads/.../config_views.py:61` | PATCH/DELETE config app ads | sửa token config bằng `:view` |
| `orders/.../kship_note_preset_view.py:104`, `kship_sender_address_view.py:127` | PATCH/DELETE preset | tương tự |

Codebase **đã tự nhận diện đây là bug** và fix cục bộ ≥2 lần: `loyalty_view.py:96-97`
và `foxia/transport/api_views.py:117`. Mẫu ĐÚNG: `best_express/transport/api_views.py:249-312`.

**Khuyến nghị**: sweep một lần theo quy ước GET=`:view` / POST=`:create` /
PATCH,PUT=`:edit` / DELETE=`:delete` + **test kiến trúc** quét mọi view method ghi mà
permission chỉ chứa require_section → fail. *(→ ĐANG LÀM: nhánh
fix/write-endpoints-permissions.)*

## 2. [MEDIUM — ĐÃ QUYẾT ĐỊNH BỎ QUA] Token ingest dùng chung, phơi qua CORS `*`

`ORDERS_INGEST_API_TOKEN` dùng chung 3 cửa ghi (landing ingest, Lark ingest, warranty
claim ingest); nginx mở CORS `*` + header `X-Api-Token` trên `/api/orders/ingest` →
token nằm trong JS landing công khai. compare_digest + fail-closed prod: tốt.
*Không thực hiện theo quyết định 2026-08-19.*

## 3. [LOW→INFO] Webhook tích hợp ngoài — verify tốt

- KiotViet: HMAC-SHA256 `X-Hub-Signature`, `hmac.compare_digest`, thiếu secret → 500
  (`kiotviet/webhook_impl.py:93-522`). Tốt.
- Best Express: Bearer token `constant_time_compare` (`webhook_views.py:22-50`). Tốt.
- CNV: token tĩnh, `constant_time_compare`, token rỗng ⇒ từ chối tất cả
  (`cnv_loyalty/transport/webhook_views.py:48-72`). Tốt.
- CNV OAuth callback AllowAny — có `state` chống CSRF dùng một lần. Hợp lý.

Không có injection: không `.raw()`/`.extra()`/f-string SQL trong code nghiệp vụ;
`cursor.execute` chỉ SQL tĩnh/migration/ANALYZE; ILIKE tham số hóa
(`common/db_lookups.py:28`).

## 4. [LOW] Portal tra cứu bảo hành công khai

Tất cả AllowAny nhưng đều có throttle riêng (PortalActivate/Read/MediaThrottle), key
theo IP thật CF→XFF→REMOTE (ClientIpMixin). `track` không oracle. Kênh Zalo có token
riêng compare_digest. Lưu ý: mã kích hoạt 10 ký tự entropy thấp → throttle activate là
lớp phòng thủ duy nhất chống dò hàng loạt. Xác nhận rate `warranty_portal_*` đủ chặt.

## 5. [LOW] `product_image` portal stream không giới hạn dung lượng

`portal_views.py:175-201` — stream từ MinIO không cap (comment tự thừa nhận chờ Task 12).
Upload đã cap 5MB/8 ảnh nên rủi ro thấp; có PortalMediaThrottle.

## 6. [INFO] Secrets — quản lý tốt

Không credential thật hardcode; `.env.example` toàn change-me; compose dùng
`${VAR:?required}`. Guard fail-fast: `assert_production_secrets()` (base.py:240-277),
`assert_production_debug_off()`, JWT key < 32 byte raise ở prod (base.py:976-980),
staging guard secret outbound (base.py:284-327). `CORS_ALLOW_CREDENTIALS = False`.

## 7. [INFO] Nginx / infra — chắc

TLS 1.2/1.3 + HSTS 1 năm + security headers trên 3 vhost prod; `/api/orders/metrics`
chặn IP nội bộ + lớp 2 METRICS_ALLOW_TOKEN; mọi port compose bind 127.0.0.1; backend
container non-root (Dockerfile:23,32); login brute-force 2 lớp (nginx zone
`desi_api_auth` burst=20 + LoginRateThrottle theo IP thật); WS bắt buộc JWT + kiểm
token_version, đóng 4401 nếu anonymous; không dangerouslySetInnerHTML; access token
không vào localStorage; sourcemap chặn ở nginx (frontend/nginx.conf:27-29); Excel export
chống formula-injection tập trung (`common/excel_utils.py`); Swagger prod chỉ superuser
+ 404 cho người khác.

## 8. [LOW] Điểm nhỏ

- `/admin` đăng ký trần (`config/urls.py:7`), không IP-restrict ở nginx → chặn theo IP
  nội bộ hoặc đổi path.

## Ưu tiên xử lý

1. **HIGH #1** — sweep quyền `:view` trên endpoint ghi + test kiến trúc (đang làm).
2. ~~MEDIUM #2~~ — bỏ qua theo quyết định.
3. **LOW #8** — thu hẹp bề mặt `/admin`.
4. #4/#5 gia cố; #3/#6/#7 đang tốt, giữ nguyên.

Điểm sáng: guard fail-fast secret/DEBUG, throttle mặc định toàn cục, non-root container,
cookie httpOnly + token in-memory. Rủi ro tồn đọng tập trung ở **tầng phân quyền hành
động (action-level authZ)**, không phải AuthN hay hạ tầng.
