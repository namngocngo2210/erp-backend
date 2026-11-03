# Báo cáo rà soát Backend (Django)

**Phạm vi:** `backend/` — tập trung `apps/orders`, `apps/kiotviet`, models/DB, config/kiến trúc
**Ngày:** 2026-05-27
**Người rà soát:** Claude (skill backend-review) + xác minh thủ công

> Lưu ý: báo cáo đã **xác minh lại** các phát hiện tự động và loại bỏ/đính chính những điểm sai (xem mục "Đính chính" ở cuối). Mức độ được gán theo góc nhìn vận hành thực tế, không chỉ theo lý thuyết.

---

## Tổng quan mức độ

| Mức | Số lượng | Trọng tâm |
|-----|----------|-----------|
| 🔴 Critical | 3 | File khổng lồ + trùng lặp logic sync; N+1 API call tuần tự khi sync KiotViet; thiếu connection pooling DB |
| 🟠 High | 6 | N+1 query (export, serializer); thiếu composite index; default secret/password yếu; except quá rộng |
| 🟡 Medium | 7 | Cấu trúc app chưa nhất quán; on_delete CASCADE chuỗi; lạm dụng JSONField; pytest config; security headers |
| 🟢 Low | 4 | đặt tên index, max_length phone, prefetch_related(None) |

**Đánh giá chung:** Codebase có nền tảng tốt (orders đã theo clean architecture, soft-delete + unique constraint có điều kiện, dùng Decimal cho tiền, log theo app, audit writer bất đồng bộ). **Hai vấn đề lớn nhất** là: (1) toàn bộ logic đồng bộ KiotViet nằm trong vài file 1.500–3.000 dòng và **gọi HTTP API tuần tự trong vòng lặp** — đây là nút thắt hiệu năng thật sự; (2) thiếu connection pooling DB ở mọi request. Vấn đề N+1 ở orders nhỏ và cục bộ hơn (đa số đã được xử lý tốt).

---

## ⚡ Hiệu năng & Database

### 1. Sync KiotViet gọi HTTP API tuần tự trong vòng lặp (N+1 API) — `apps/kiotviet/management/commands/sync_invoices_between_channels.py`
**Mức:** 🔴 Critical

**Vấn đề:** Vòng lặp xử lý từng invoice (≈ dòng 859–941) gọi 3–5 request KiotViet cho mỗi đơn (`get_invoice_by_code`, `find_customer_by_phone`, `create_customer`, `create_invoice`, `patch_invoice_status_after_create`), **tuần tự**, có thêm `time.sleep()` giữa các call. Pre-scan kênh B (≈ 496–561) còn fetch toàn bộ invoice phía B trước khi xử lý.

**Tác động:** Sync 1.000 đơn ⇒ 3.000–5.000 request HTTP tuần tự, mỗi cái ~0.3s + sleep ⇒ **15–25 phút** cho một lần sync. Đây là nút thắt vận hành lớn nhất của backend hiện tại.

**Cách sửa:**
- Dùng `concurrent.futures.ThreadPoolExecutor(max_workers=5..10)` cho phần fetch/độc lập (KiotViet không có ràng buộc tuần tự giữa các đơn khác nhau).
- Pre-load `phone → customer` và `ChannelKiotvietMapping` một lần vào dict thay vì query/gọi API theo từng đơn (xem mục 4 và 5).
- Backoff theo cấp số nhân + xử lý HTTP 429 (hiện chỉ có linear backoff 2s/4s/6s, không bắt rate-limit).

---

### 2. Thiếu connection pooling DB — `backend/config/settings_parts/base.py:91`
**Mức:** 🔴 Critical (theo tải) → thực tế High

**Vấn đề (đã xác minh):** Block `DATABASES` không có `CONN_MAX_AGE` lẫn `CONN_HEALTH_CHECKS`. Django mặc định `CONN_MAX_AGE=0` ⇒ **mở + đóng một kết nối Postgres mới cho mỗi request**.

**Tác động:** Tăng latency mỗi request (TCP + auth handshake), dễ cạn connection của Postgres khi nhiều worker/tải cao. Với quy mô đang tăng lên 100–150 người dùng, đây là điểm cần xử lý sớm.

**Cách sửa:**
```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "CONN_MAX_AGE": 600,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"connect_timeout": 10},
        # ...
    }
}
```
Nếu chạy nhiều worker, cân nhắc thêm PgBouncer (transaction pooling) ở tầng hạ tầng.

---

### 3. N+1 khi export Excel — `apps/orders/export_excel.py:103`
**Mức:** 🟠 High (đã xác minh)

**Vấn đề:** View export prefetch `assessment__needs` nhưng **không** prefetch `need_type` ([order_export_view.py:142](backend/apps/orders/transport/views/order_views_impl/order_export_view.py#L142)). Dòng 103 truy cập `n.need_type.name_vi` cho mỗi need ⇒ mỗi need là 1 query.

**Tác động:** Chỉ kích hoạt khi cột `needs` được chọn, nhưng khi đó số query = tổng số need của tất cả đơn được export. Với vài trăm đơn → vài trăm query thừa.

**Cách sửa:** Trong queryset export, đổi prefetch thành:
```python
from django.db.models import Prefetch
.prefetch_related(
    "assessment",
    Prefetch("assessment__needs",
             queryset=OrderNeed.objects.select_related("need_type")),
)
```

---

### 4. N+1 DB: resolve customer theo từng đơn — `apps/kiotviet/infrastructure/services/invoice_payload_builder.py` (`resolve_target_customer`, ≈ 220)
**Mức:** 🟠 High

**Vấn đề:** Mỗi đơn gọi `find_customer_by_phone(phone)` ⇒ 1 query/đơn (hoặc 1 API call/đơn).

**Cách sửa:** Trước vòng lặp, gom toàn bộ phone rồi `Customer.objects.filter(phone__in=phones)` một lần, dựng dict `phone → customer`, tra cứu trong bộ nhớ.

---

### 5. N+1 DB: map sale channel theo từng đơn — `apps/kiotviet/infrastructure/services/id_mappers.py` (`map_sale_channel_id`, ≈ 56–70)
**Mức:** 🟠 High

**Vấn đề:** Mỗi đơn thực hiện 2 query `ChannelKiotvietMapping` ⇒ 2.000 query cho 1.000 đơn. Mapping là dữ liệu cấu hình ít thay đổi.

**Cách sửa:** Pre-load toàn bộ `ChannelKiotvietMapping` (cho cặp kênh nguồn/đích) một lần vào dict `(kiotviet_channel_id, external_id) → target`, dùng lại trong suốt phiên sync.

---

### 6. SerializerMethodField `get_product_count()` gọi `.count()` mỗi object — `apps/orders/transport/serializers/order_serializers_impl/product_item_crm_serializers.py:180`
**Mức:** 🟠 High

**Vấn đề:** Serialize danh sách category ⇒ mỗi category 1 query `COUNT(*)`.

**Cách sửa:** `annotate(product_count=Count("products"))` ở queryset và đổi field thành `IntegerField(read_only=True)`.

---

### 7. Thiếu composite index cho các tổ hợp filter hay dùng — `apps/orders/models.py`
**Mức:** 🟠 High

**Vấn đề (đã đính chính):** *Không phải* "FK thiếu db_index" — Django tự tạo index cho mọi FK. Vấn đề thật là các **truy vấn nhiều điều kiện** (vd `OrderAssessment.filter(sales_status_code, customer_classification_code)`, hoặc `SaleReminder.filter(status='pending', due_at__lte=now)`) chưa có composite/partial index phù hợp; hiện chỉ có các single-column index rời rạc.

**Cách sửa:** Bổ sung `Meta.indexes` composite cho các tổ hợp filter thực tế (đo bằng `django-debug-toolbar`/`pg_stat_statements` để chọn đúng tổ hợp, tránh đánh index thừa). Ví dụ partial index cho reminder đang chờ:
```python
models.Index(fields=["due_at"], condition=Q(status="pending"),
             name="sale_reminder_pending_due_idx")
```

---

### 8. `list_orders` chạy `COUNT` + `SELECT` riêng — `apps/orders/repositories.py:392`
**Mức:** 🟡 Medium

**Vấn đề:** 2 query mỗi lần phân trang. Chấp nhận được nếu cần tổng chính xác; nếu UI chỉ cần "có trang sau" thì fetch `limit+1` row sẽ rẻ hơn.

---

## 🧹 Cấu trúc code

### 9. File khổng lồ + trùng lặp logic sync — `apps/kiotviet`
**Mức:** 🔴 Critical

**Vấn đề:**
- `management/commands/sync_invoices_between_channels.py` — **3.029 dòng**, ~31 method, riêng `_sync_invoice()` ≈ 446 dòng (lồng 5–6 tầng if/try).
- `infrastructure/services/sync_invoices_service.py` — **2.462 dòng**, là bản **port gần như y hệt** logic từ command ⇒ mọi sửa đổi/bug phải đồng bộ ở 2 nơi.
- `import_kiotviet_invoices.py` — **1.538 dòng**, một command gánh 8 chế độ (`--diff`, `--update-status`, `--update-phone`, ...) dùng chung biến `summary`.

**Tác động:** Rất khó test, debug, mở rộng; rủi ro lệch logic giữa command và service.

**Cách sửa:** Command chỉ parse args và gọi service; **xóa bản trùng** — command dùng lại service thay vì giữ 2 bản. Tách `_sync_invoice` thành các bước nhỏ (resolve customer / build payload / push / patch status). Tách 8 chế độ của `import_*` thành nhiều command hoặc nhiều use-case riêng.

---

### 10. Helper trùng định nghĩa ở nhiều file
**Mức:** 🟡 Medium

`_first_non_empty()`, `_to_decimal()` được định nghĩa lại trong `sync_invoices_between_channels.py`, `import_kiotviet_invoices.py`, `invoice_payload_builder.py`. Gom về một module `utils` dùng chung.

---

### 11. Cấu trúc app chưa nhất quán
**Mức:** 🟡 Medium

`orders` theo clean architecture rất tốt (domain/application/infrastructure/transport), nhưng `kiotviet`, `customers`, `users` còn trộn business logic vào `models.py`/`views.py`. Nên thống nhất chuẩn để dễ onboard nhân sự mới (đang scale lên 100–150 người).

---

## 🗄️ Model / Database

### 12. on_delete CASCADE tạo chuỗi xóa dữ liệu audit — `apps/orders/models.py`
**Mức:** 🟡 Medium

`OrderAssessment.order` (CASCADE) + `OrderNeed.order_assessment` (CASCADE): hard-delete một Order sẽ xóa luôn assessment + needs (dữ liệu nghiệp vụ/đánh giá). Soft-delete hiện che được, nhưng nếu có hard-delete thì mất audit trail. Cân nhắc `SET_NULL` hoặc đảm bảo quy trình chỉ soft-delete.

### 13. Lạm dụng JSONField cho dữ liệu cần truy vấn
**Mức:** 🟡 Medium

`CustomerResaleFollowUp.contact_logs/resale_orders`, một số field log/metadata lưu JSON nhưng lại cần lọc/aggregate ⇒ khó index, report chậm. Nên tách các trường hay query ra cột riêng (vd `last_contact_at`, `contact_count`) và giữ JSON cho phần raw.

### 14. Một số constraint định danh ngoài cần rà lại
**Mức:** 🟡 Medium

`Order.external_order_id` / `order_code` chưa có unique constraint (theo channel). Cần xác nhận business rule rồi cân nhắc `UniqueConstraint` có điều kiện (`deleted_at__isnull=True`) để chống nhập trùng từ ingest. *(Không chắc chắn 100% về rule — cần xác nhận trước khi thêm.)*

### 15. `Job` (dispatch) dùng `TextField` cho cột hay filter
**Mức:** 🟢 Low

`queue`, `task_type` nên là `CharField(max_length=...)` để index hiệu quả hơn.

---

## 🔒 Bảo mật & Config (thứ yếu, vẫn nên xử lý)

### 16. Default secret/password yếu trong settings — `base.py`
**Mức:** 🟠 High

**Đính chính quan trọng:** Secrets **KHÔNG** bị lộ trên git — `.env` đã được gitignore (`.env`, `.env.*`, `!.env.example`); `git ls-files` chỉ thấy `.env.example`. File `.env` trên máy là local hợp lệ.

**Vấn đề thật:** `SECRET_KEY` fallback `"dev-secret-key-change-me"`, `POSTGRES_PASSWORD` fallback `"desi_order"`. Nếu deploy mà quên set env, hệ thống chạy với key/password đoán được mà không báo lỗi.

**Cách sửa:** Khi `IS_PRODUCTION`, raise lỗi nếu các biến nhạy cảm chưa được set hoặc còn là giá trị default.

### 17. `except` quá rộng / bare except
**Mức:** 🟠 High

- `apps/audit/middleware_impl.py:177` — `except:` trần (bắt cả `KeyboardInterrupt`/`SystemExit`).
- `apps/kiotviet/models.py` (≈ 23/37/57) — `except Exception: return value` khi decrypt thất bại ⇒ trả về ciphertext mà không log.
- Nhiều chỗ `except Exception: pass` ở webhook/payload builder.

**Cách sửa:** Bắt exception cụ thể (`json.JSONDecodeError`, `InvalidToken`, ...) và log ít nhất ở mức warning.

### 18. Thiếu security headers production rõ ràng
**Mức:** 🟡 Medium

Chưa set `SECURE_HSTS_SECONDS`, `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` cho production.

### 19. pytest config tối thiểu
**Mức:** 🟡 Medium

Không có coverage/`testpaths`/markers. (Lưu ý: theo memory dự án, pytest.ini hiện có lỗi BOM khiến pytest hỏng — chạy test qua `manage.py test`.) Nên sửa pytest.ini và thêm đo coverage.

---

## ✅ Điểm làm tốt

- `orders` tách lớp domain/application/infrastructure/transport rất rõ ràng.
- Tránh N+1 cơ bản tốt: `repositories.py` dùng `Prefetch` + `select_related` cho assessment/needs/journey; list view batch sẵn `zalo_map`, `next_reminder_map`; dùng `Exists()/Subquery()` thay vì query trong loop.
- Soft-delete nhất quán + `UniqueConstraint` có điều kiện `deleted_at__isnull=True`.
- Dùng `DecimalField` cho tiền tệ (không dùng float).
- **Tất cả** HTTP call ngoài đều có `timeout` (đã kiểm tra facebook_ads, zalo_bridge, lark, kiotviet).
- `EncryptedCharField` cho credentials KiotViet; kiểm tra độ dài JWT key, fail-fast ở production.
- Audit writer bất đồng bộ (queue) không chặn request.
- Tách task nặng sang Go services thay vì Celery.

---

## 🗺️ Kế hoạch ưu tiên đề xuất

**P0 (ngay):**
1. Thêm `CONN_MAX_AGE` + `CONN_HEALTH_CHECKS` cho DB (sửa 5 phút, lợi ích lớn).
2. Song song hóa + pre-load mapping/customer cho sync KiotViet (mục 1, 4, 5) — giảm thời gian sync hàng chục lần.

**P1 (sprint này):**
3. Gỡ trùng lặp command/service KiotViet, tách `_sync_invoice` (mục 9).
4. Vá N+1 export (mục 3) + `get_product_count` (mục 6).
5. Fail-fast khi thiếu secret/password ở production (mục 16); thay `except` rộng bằng cụ thể (mục 17).

**P2 (sprint sau):**
6. Đo query thực tế rồi thêm composite/partial index (mục 7).
7. Rà on_delete CASCADE (mục 12), tách JSONField hay query (mục 13), xác nhận unique cho order code (mục 14).
8. Security headers production + sửa pytest config/coverage (mục 18, 19).

---

## Đính chính so với rà soát tự động ban đầu

- ❌ "Secrets bị lộ" → **Sai.** `.env` đã gitignore, không nằm trong git.
- ❌ "Customer/staff FK thiếu db_index" → **Sai.** Django tự tạo index cho FK; vấn đề thật là composite index (mục 7).
- ❌ "HTTP request thiếu timeout" → **Sai.** Đã kiểm tra, tất cả đều có timeout.
- ⚠️ "Export N+1 = 400 query" → đúng về bản chất nhưng phụ thuộc cột `needs` được chọn và số đơn export.
