# Spec 1 — Backend Perf & Safety Quick Wins

**Ngày:** 2026-05-28
**Branch:** `backend-perf-safety-improvements-20260528`
**Nguồn:** `docs/backend_review_report.md` (P0 + phần P1 an toàn)

## Mục tiêu

Các sửa đổi cơ học, độc lập, rủi ro thấp — **không** refactor cấu trúc, **không** đổi schema DB. Mỗi thay đổi nhỏ và verify được riêng. Đây là đợt đầu trong chuỗi 3 spec.

## Phạm vi (in-scope)

### 1. Connection pooling DB
- **File:** `backend/config/settings_parts/base.py` (block `DATABASES`, ~dòng 91).
- **Thay đổi:** thêm vào `default`:
  - `CONN_MAX_AGE = int(os.getenv("DJANGO_CONN_MAX_AGE", "600"))`
  - `CONN_HEALTH_CHECKS = True`
  - `OPTIONS = {"connect_timeout": 10}`
- **Lý do:** hiện `CONN_MAX_AGE=0` (mặc định) ⇒ mở/đóng kết nối Postgres mỗi request. Không có PgBouncer (đã kiểm tra docker-compose) nên persistent connection an toàn.
- **Rủi ro:** thấp. Nếu deploy worker dài hạn, connection được tái dùng — đúng mong muốn.

### 2. Vá N+1 query (2 điểm isolated)
**2a. Export Excel — thiếu prefetch `need_type`**
- **File:** `backend/apps/orders/transport/views/order_views_impl/order_export_view.py` (~dòng 142).
- **Hiện tại:** `.prefetch_related("assessment", "assessment__needs")`.
- **Sửa:** dùng `Prefetch("assessment__needs", queryset=OrderNeed.objects.select_related("need_type"))`. Import `Prefetch` và `OrderNeed` đúng module.
- **Tác động:** `export_excel.py:103` truy cập `n.need_type.name_vi` mỗi need ⇒ hiện N+1; sửa xong còn 1 query cho toàn bộ need_type.

**2b. `get_product_count()` chạy `.count()` mỗi object**
- **File serializer:** `backend/apps/orders/transport/serializers/order_serializers_impl/product_item_crm_serializers.py` (~dòng 180).
- **File view (queryset):** `backend/apps/orders/transport/views/order_views_impl/order_ingest_product_dashboard_view.py` (~dòng 417, nơi build `ProductCategory` queryset).
- **Sửa:** view thêm `.annotate(product_count=Count("products"))`; serializer đổi `get_product_count` SerializerMethodField → `product_count = serializers.IntegerField(read_only=True)`.
- **Lưu ý:** nếu `products` có quan hệ lọc (vd soft-delete), dùng `Count("products", filter=Q(products__deleted_at__isnull=True))` để khớp ngữ nghĩa cũ. Phải kiểm tra logic `obj.products` hiện tại trước khi đổi để giữ đúng con số.

### 3. Thu hẹp except quá rộng (chỉ 2 nhóm rõ ràng an toàn)
**3a.** `backend/apps/audit/middleware_impl.py` (~dòng 177): bare `except:` khi parse body để build curl
- Đổi → `except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):` + `logger.debug(...)`.

**3b.** `backend/apps/kiotviet/models.py` (~dòng 23/37/57): `except Exception: return value` khi decrypt
- Đổi → bắt cụ thể (`ValueError`, `TypeError`, và `InvalidToken` của Fernet nếu thư viện expose) + `logger.warning(...)` trước khi trả nguyên giá trị.
- Phải kiểm tra exception thật mà lib mã hóa ném ra trước khi chốt danh sách.

**Ngoài scope:** không sweep mọi `except Exception` trong codebase (tránh scope creep + rủi ro đổi hành vi nuốt lỗi có chủ đích như audit/webhook).

### 4. Fail-fast secret/password ở production
- **File:** `backend/config/settings_parts/base.py`.
- **Thay đổi:** khi `IS_PRODUCTION`:
  - raise `RuntimeError` nếu `SECRET_KEY` rỗng hoặc == `"dev-secret-key-change-me"`.
  - raise `RuntimeError` nếu `POSTGRES_PASSWORD` == default `"desi_order"`.
- **Lưu ý:** đặt check SAU khi đã đọc env; dev (`IS_PRODUCTION=False`) không bị ảnh hưởng. Phải đảm bảo không phá `manage.py`/migrate ở môi trường CI nếu CI set `APP_ENV=production` — kiểm tra CI env trước (mặc định CI không phải production).

### 5. Security headers (gate sau IS_PRODUCTION + toggle env)
- **File:** `backend/config/settings_parts/base.py`.
- **Thay đổi (chỉ khi `IS_PRODUCTION`):**
  - `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` (nginx terminate TLS, set `X-Forwarded-Proto`).
  - `SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "31536000"))`
  - `SECURE_HSTS_INCLUDE_SUBDOMAINS = True`
  - `SESSION_COOKIE_SECURE = True`, `CSRF_COOKIE_SECURE = True`
  - `SECURE_SSL_REDIRECT = os.getenv("DJANGO_SSL_REDIRECT", "0") == "1"` (mặc định **off** để tránh redirect loop sau nginx; bật khi đã xác nhận).
- **Rủi ro:** nếu bật cookie secure mà có client gọi qua HTTP nội bộ sẽ mất cookie — nhưng production đi qua nginx HTTPS nên ổn. SSL_REDIRECT để off mặc định là biện pháp an toàn.

### 6. Sửa BOM pytest.ini
- **File:** `backend/pytest.ini` (hiện UTF-8 **with BOM** — xác nhận bằng `file`).
- **Sửa:** ghi lại không BOM; giữ nội dung cũ + thêm `testpaths = apps`.
- **Tác động:** mở lại khả năng chạy `pytest` (đang hỏng do BOM ở dòng `[pytest]`).

## Out-of-scope (để Spec 2 / Spec 3)
- N+1 resolve customer / map sale channel trong sync KiotViet (nằm sâu trong file lớn → Spec 2).
- Mọi thay đổi schema/index/on_delete/JSONField/unique (Spec 3).
- Refactor file lớn (Spec 2).

## Chiến lược test & verify
- Backend test chạy qua `python manage.py test` với Postgres/Redis tạm (creds theo memory dự án). pytest sẽ được verify collect lại sau khi fix BOM.
- Test trọng tâm theo từng thay đổi:
  - (2a) test export orders có cột `needs` → assert số query không tăng theo số need (dùng `assertNumQueries`).
  - (2b) test list product categories → assert `product_count` đúng + `assertNumQueries` ổn định.
  - (3) test decrypt với token hỏng → trả plaintext + có log.
  - (4) test settings: với `APP_ENV=production` + secret default → raise.
- **Baseline đã biết fail trên master sạch** (không tính vào kết quả): customers aggregate test, FE OrderFormDialog, mobile-card, websocket.

## Tiêu chí hoàn thành
- Tất cả thay đổi áp dụng, test liên quan xanh (trừ baseline đã biết).
- `pytest --collect-only` chạy được (không lỗi BOM).
- Không thay đổi hành vi API ngoài việc giảm số query.
