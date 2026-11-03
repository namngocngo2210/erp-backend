# Audit 2026-07-26 Follow-ups — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đóng toàn bộ 10 mục của `docs/audit-2026-07-26-full-stack.md` — từ lỗ hổng phân quyền hóa đơn tới nợ kiến trúc cũ — không để lại mục nào.

**Architecture:** Mọi mục đều là "áp một cách làm đã có trong repo sang code path chưa áp". Không thêm dependency backend, không đổi hợp đồng API. Refactor cấu trúc giữ **facade re-export** đúng khuôn `apps/orders/views.py` để import cũ không vỡ.

**Tech Stack:** Django 6 + DRF + Celery + Postgres 16 · React 19 + Vite 7 + TanStack Query · pytest · vitest · ruff · import-linter

## Global Constraints

- Python ≥ 3.12, Go không dùng. Django 6.0.7, DRF 3.17.
- `ruff check .` phải pass theo `backend/pyproject.toml` (ruleset `E4,E7,E9,F,B,C4,DTZ,LOG,PIE,RET,SIM,T20,UP`). Vi phạm mới = CI đỏ.
- `lint-imports` phải pass. **Không thêm entry mới vào `ignore_imports`** — chỉ được xoá.
- Frontend: `npm run typecheck` + `npm run lint:strict` (0 warning) + `npm run format:check` phải pass. Node 22 (`engines: >=22 <24`).
- Nếu sửa serializer/view có schema: chạy `python manage.py spectacular --file schema.yaml` rồi `npm run gen:api`, commit cả hai (CI có drift-check 2 chiều).
- Commit message tiếng Anh, ngắn, **không** kèm `Co-Authored-By`.
- Test backend chạy bằng Docker theo `LỆNH TEST` dưới đây. **Không bao giờ `cache.clear()`** trong test (FLUSHDB đè Redis thật).

### LỆNH TEST (backend)

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_audit0726 \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <PATHS> -q'
```

Lần đầu trên DB mới thêm `--create-db`. Ruff chạy riêng (image không có ruff):

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

---

## File Structure

| File | Trách nhiệm | Task |
|---|---|---|
| `backend/apps/orders/transport/views/order_views_impl/order_export_view.py` | 4 endpoint in ấn — thêm scope + cap | 1 |
| `backend/apps/orders/tests/test_order_print_permissions.py` | **Mới** — test quyền 4 endpoint in ấn | 1 |
| `nginx/conf.d/default.conf` | Chặn `/api/orders/metrics` | 2 |
| `backend/apps/orders/transport/views/order_views_impl/metrics_view.py` | Phòng thủ tầng app cho metrics | 2 |
| `backend/apps/orders/application/services/order_service_helpers.py` | Xoá `verify_channel_token` | 3 |
| `backend/apps/orders/repositories.py` | `icontains`→`contains`; `list_kiotviet_jobs` RBAC scope | 4 |
| `backend/apps/customers/customer_resale_service.py` | `icontains`→`contains` | 4 |
| `backend/apps/orders/transport/views/order_views_impl/order_crud_view.py` | `list_kiotviet_jobs` dùng scope thật | 4 |
| `backend/apps/users/services.py` + `serializers.py` | Sửa N+1 `DepartmentSerializer` | 5 |
| `frontend/vite.config.ts`, `frontend/src/main.tsx`, `frontend/package.json` | Sourcemap + Sentry release | 6 |
| `backend/apps/common/management/commands/report_index_usage.py` | **Mới** — đo index usage | 7 |
| `backend/apps/orders/migrations/0109_drop_dead_indexes.py` | **Mới** — drop index chết | 7 |
| `backend/config/settings_parts/celery.py`, `docker-compose*.yml` | Queue `slow` | 8 |
| `backend/apps/orders/migrations/0001_squashed_0108_*.py` | **Mới** — squash | 9 |
| `backend/apps/{audit,callcenter,customers,google_ads,users,warranty,kiotviet}/transport/` | Chuyển views sang transport/ | 10–12 |
| `frontend/src/features/orders/*`, `frontend/src/pages/OrdersPage.tsx` | Tách file lớn | 13–14 |

---

## Task 1: N1 — scope + cap cho 4 endpoint in ấn 🔴

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_export_view.py:29-142`
- Test: `backend/apps/orders/tests/test_order_print_permissions.py` (mới)

**Interfaces:**
- Consumes: `_apply_user_channel_scope(filters: dict, user, section: str) -> tuple[dict, bool]` từ `._view_helpers`; `OrderService.can_edit_channel(user, channel_code: str) -> bool`.
- Produces: hằng `PRINT_MAX_IDS = 200` export từ `order_export_view` (Task nào khác dùng thì import từ đây).

**Bối cảnh:** 4 endpoint hiện chỉ có `require_section(*ORDER_SECTIONS)` = kiểm tra quyền `orders:view`, **không** kiểm tra bản ghi. Khuôn đúng đã có ngay trong file này ở `bulk_export_excel` (dòng 178-197).

- [ ] **Step 1: Viết test đỏ**

Tạo `backend/apps/orders/tests/test_order_print_permissions.py`. Dựng 2 user: `owner` (scope `own`, được gán channel A) và một đơn của user khác cùng channel A, cộng một đơn ở channel B. Bám theo cách dựng fixture RBAC của `apps/orders/tests/test_orders_status_counts.py` và `apps/users/tests/` (đọc trước để lấy đúng helper tạo Role/Permission/UserChannel — **không tự phát minh**, và nhớ `Role.objects.create(name="Administrator")` sẽ trùng `rbac_roles_name_key` vì migration 0015 đã seed, nên đặt tên role khác).

Bốn test bắt buộc:

```python
def test_bulk_invoices_excludes_orders_outside_own_scope(...):
    # owner scope=own, đơn other_order.staff = user khác
    resp = api_client.post("/api/orders/bulk-invoices", {"ids": [own_order.id, other_order.id]}, format="json")
    # KHÔNG được xuất hiện dữ liệu của other_order
    assert resp.status_code in (200, 400)
    assert str(other_order.invoice_code) not in resp.content.decode()

def test_single_invoice_denies_order_of_unassigned_channel(...):
    resp = api_client.get(f"/api/orders/{order_channel_b.id}/invoice")
    assert resp.status_code == 403

def test_bulk_invoices_rejects_too_many_ids(...):
    resp = api_client.post("/api/orders/bulk-invoices", {"ids": list(range(1, 202))}, format="json")
    assert resp.status_code == 400

def test_bulk_delivery_slips_excludes_orders_outside_own_scope(...):
    ...  # tương tự test 1 cho /api/orders/bulk-delivery-slips
```

Vì `invoice_response`/`bulk_invoice_response` gọi HTTP ra KiotViet, **phải mock**: patch `apps.orders.invoice.get_invoice_by_code` (và `get_invoice_detail_by_id` nếu delivery slip dùng — đọc `apps/orders/invoice.py` để lấy tên đúng) trả về payload tối thiểu. Test phải xác minh *đơn nào được truyền vào* — cách chắc nhất là assert trên tập id mà mock được gọi với.

- [ ] **Step 2: Chạy test, xác nhận ĐỎ**

Run: `<LỆNH TEST với PATHS=apps/orders/tests/test_order_print_permissions.py -v>`
Expected: FAIL — bulk trả cả đơn ngoài scope, cap 200 chưa có nên trả 200 thay vì 400.

- [ ] **Step 3: Thêm helper scope dùng chung trong `order_export_view.py`**

Đặt ngay sau `logger = logging.getLogger(__name__)`:

```python
# Trần số đơn cho một lần in gộp. Mỗi đơn là MỘT lượt gọi HTTP sang KiotViet
# (invoice.py: bulk_invoice_response lặp get_invoice_by_code), nên `ids` không
# chặn trần đồng nghĩa với việc client tự tạo được vài nghìn round-trip trong
# một request — vừa treo worker vừa đốt rate-limit của KiotViet.
PRINT_MAX_IDS = 200


def _scoped_print_queryset(request, ids=None):
    """Queryset đơn mà user THẬT SỰ được xem, dùng chung cho 4 endpoint in ấn.

    Trả về (queryset, error_response). error_response khác None thì trả luôn.
    Cùng khuôn scope với bulk_export_excel: `own` → lọc theo staff_id, còn
    channel/department → lọc theo channel code được cấp (audit 2026-07-26 N1).
    """
    scope_filters, scope_empty = _apply_user_channel_scope({}, request.user, "orders")
    queryset = Order.objects.select_related("kiotviet_channel", "channel").filter(
        deleted_at__isnull=True
    )
    if scope_empty:
        return queryset.none(), None
    if scope_filters.get("owner_user_id"):
        queryset = queryset.filter(staff_id=scope_filters["owner_user_id"])
    elif scope_filters.get("channel"):
        queryset = queryset.filter(channel__code__in=scope_filters["channel"])
    if ids is not None:
        if len(ids) > PRINT_MAX_IDS:
            return queryset.none(), Response(
                {"detail": f"Chỉ in được tối đa {PRINT_MAX_IDS} đơn mỗi lần."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        queryset = queryset.filter(id__in=ids)
    return queryset, None
```

- [ ] **Step 4: Áp vào 2 endpoint single**

Trong `download_order_invoice` và `download_order_delivery_slip`, thay
`Order.objects...get(id=order_id, deleted_at__isnull=True)` bằng lấy từ
`_scoped_print_queryset(request)` rồi `.filter(pk=order_id).first()`. Khi không tìm thấy:
giữ 404 nếu đơn không tồn tại, trả **403** nếu đơn tồn tại nhưng ngoài scope — phân biệt
bằng một lần `Order.objects.filter(pk=order_id, deleted_at__isnull=True).exists()`. Thêm
tiếp kiểm tra `OrderService.can_edit_channel(request.user, order.channel.code)` → 403, đúng
pattern `update_order_inline`/`close_case`. `download_order_invoice` hiện chỉ
`select_related("kiotviet_channel")` — phải thêm `"channel"` (helper đã có) nếu không sẽ
sinh query phụ.

- [ ] **Step 5: Áp vào 2 endpoint bulk**

```python
def bulk_download_invoices(request):
    ids = request.data.get("ids", [])
    if not ids:
        return Response({"detail": "Chưa chọn đơn hàng."}, status=status.HTTP_400_BAD_REQUEST)
    orders, error = _scoped_print_queryset(request, ids)
    if error is not None:
        return error
    try:
        return bulk_invoice_response(orders)
    ...
```

Y hệt cho `bulk_download_delivery_slips`.

- [ ] **Step 6: Chạy test, xác nhận XANH**

Run: `<LỆNH TEST với PATHS=apps/orders/tests/test_order_print_permissions.py -v>`
Expected: 4 passed.

- [ ] **Step 7: Regression + lint**

Run: `<LỆNH TEST với PATHS=apps/orders>` → không được có fail mới so với baseline.
Run: `ruff check --no-cache .` → All checks passed.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/orders/transport/views/order_views_impl/order_export_view.py backend/apps/orders/tests/test_order_print_permissions.py
git commit -m "fix(orders): enforce record scope and id cap on invoice/delivery-slip printing"
```

---

## Task phát sinh từ review Task 1

Review Task 1 phơi ra một họ lỗi **cùng loại N1 nhưng ở phía GHI**, nặng hơn. Ba task dưới
được thêm vào plan sau khi Task 1 xong, và Task 1B phải làm ngay sau Task 1.

### Task 1B 🔴 — bulk write action không có record-level authz

`backend/apps/orders/transport/views/order_views_impl/order_action_view.py`

| Endpoint | Dòng | Vấn đề |
|---|---|---|
| `bulk_delete_orders` | 145-165 | `filter(id__in=ids)` từ client, chỉ gate `can_edit_channel` |
| `transfer_landing_orders` | 189-223 | **Không có gate bản ghi nào**; còn trả `name`+`phone` khách trong `invalid_orders` → PII leak |
| `restore_cancelled_orders` | 263-291 | như trên |
| `cancel_kiotviet_orders` | 389-432 | như trên |
| `run_kiotviet_action` (2 nhánh) | 519-584 | như trên |
| `order_crud_view.py` | 151, 270 | cùng loại |

**Gốc rễ:** `UserService.can_edit_order` (`apps/users/services.py:102-103`) trả `True` **vô điều
kiện** cho scope `OWN` và `ALL`. Với `OWN` nó đúng theo docstring ("chặn theo owner ở tầng
view") — nhưng **tầng view không chặn**. Hệ quả: user scope `OWN` xoá / huỷ / chuyển / push
KiotViet được đơn của bất kỳ ai. Destructive, nên nặng hơn N1 (chỉ đọc).

**Sửa:** áp `_apply_user_channel_scope` vào queryset của cả 6 endpoint (khuôn Task 1), tức
scope `own` → `staff_id=user.id`. Bỏ PII (`name`, `phone`) khỏi `invalid_orders`, chỉ trả `id`.
Test: user scope OWN gọi bulk delete với id đơn người khác → đơn đó **không** bị xoá.

**✅ ĐÃ LÀM** (xem `.superpowers/sdd/task-1b-report.md`): helper dùng chung
`scoped_orders_queryset` trong `_view_helpers.py`, áp cho cả 8 truy vấn (6 ở
`order_action_view.py` + `get_kship_options` / `create_kship_delivery` ở `order_crud_view.py`);
`invalid_orders` chỉ còn `id` (FE `OrdersTable` + type ở `use-orders-data.ts` sửa theo).
30 test mới ở `apps/orders/tests/test_order_bulk_action_permissions.py`; regression
`apps/orders` 618 passed. **Chưa làm (cùng file, cần quyết định thiết kế):**
`update_order_inline` (GET/PATCH/DELETE 1 đơn theo id), `duplicate_order`,
`list_order_history` vẫn không có record-scope — xem mục "Còn lại" của report.

### Task 1C — `can_edit_order` không mirror read-scope cho DEPARTMENT

`apps/users/services.py:87-106` docstring ghi *"mirror đúng read-scope"*, nhưng nhánh
DEPARTMENT so vào `assigned_channel_codes` = chỉ `UserChannel`
(`order_service_helpers.py:193-205`), còn read-scope DEPARTMENT mở kênh qua `UserDepartment`
(`apps/users/scoping.py:42-56`). → user department-scope **không sửa/đóng case/xoá được** đơn
của phòng ban mình trừ khi được gán `UserChannel` riêng. Sửa: truyền thêm
`department_channel_codes(user)` vào tập cho phép khi scope là DEPARTMENT.

### Task 1D — ba việc nhỏ còn lại quanh in ấn

1. In phiếu giao **mutate** `is_delivery_invoice_printed=True` (`apps/orders/invoice.py:509,564`)
   nhưng chỉ cần quyền `:view` → đổi sang yêu cầu quyền ghi, hoặc bỏ mutate khỏi đường in.
2. Không có throttle trên 4 endpoint in (`DEFAULT_THROTTLE_RATES` chỉ có `login`,
   `warranty_register`); mỗi request tới 200 round-trip HTTP tuần tự sang KiotViet.
3. Cho FE gửi `section` (validate theo `ORDER_SECTIONS`) như `bulk_export_excel`, để user chỉ
   có `koc-orders`/`landing-orders`/`outside-orders` không rơi về scope `OWN` khi in.

---

## Task 2: C3 — chặn `/metrics` ở nginx container + phòng thủ tầng app

**Files:**
- Modify: `nginx/conf.d/default.conf` (thêm location trước `location /api/` ở dòng 32)
- Modify: `backend/apps/orders/transport/views/order_views_impl/metrics_view.py`
- Modify: `backend/config/settings_parts/observability.py` (thêm setting)
- Modify: `.env.example`
- Test: `backend/apps/orders/tests/test_metrics_endpoint_guard.py` (mới)

**Interfaces:**
- Produces: setting `METRICS_ALLOW_TOKEN: str` (rỗng = tắt kiểm tra token).

- [ ] **Step 1: nginx**

Thêm vào `nginx/conf.d/default.conf` **trước** `location /api/` (nginx ưu tiên
`location =` nên vị trí không tối quan trọng, nhưng đặt cạnh nhau cho dễ đọc). Copy đúng
khuôn đã có ở `nginx/customer.delax.vn.conf.prod-template:40-46`:

```nginx
  # Prometheus metrics: chỉ mạng nội bộ. Endpoint là AllowAny theo quy ước
  # Prometheus (metrics_view.py) nên việc chặn nằm ở đây — file này trước đây
  # thiếu block này trong khi conf của host đã có (audit 2026-07-26 C3).
  location = /api/orders/metrics {
    set $backend_upstream http://web:8000;
    proxy_pass $backend_upstream;
    proxy_set_header Host $host;
    allow 127.0.0.1;
    allow 172.16.0.0/12;
    allow 10.0.0.0/8;
    allow 192.168.0.0/16;
    deny all;
  }
```

⚠️ Đọc `location /api/` hiện tại trước khi viết: phải sao lại **đúng** các `proxy_set_header`
/ tên biến upstream nó đang dùng, nếu không metrics sẽ 502. Sau khi sửa, verify cú pháp:
`docker run --rm -v "$(pwd -W)/nginx/conf.d:/etc/nginx/conf.d:ro" nginx:alpine nginx -t`
(nếu lỗi vì thiếu `web` upstream lúc test tĩnh thì chỉ cần cú pháp hợp lệ là đủ).

- [ ] **Step 2: Viết test đỏ cho tầng app**

`backend/apps/orders/tests/test_metrics_endpoint_guard.py`:

```python
from django.test import override_settings
# GET /api/orders/metrics
# - METRICS_ALLOW_TOKEN rỗng  → 200 (giữ hành vi cũ, Prometheus scrape nội bộ)
# - có token, request không gửi → 403
# - có token, gửi đúng header X-Metrics-Token → 200
```

- [ ] **Step 3: Chạy, xác nhận ĐỎ.** Expected: FAIL (chưa có setting, luôn 200).

- [ ] **Step 4: Thêm setting**

`backend/config/settings_parts/observability.py`:

```python
# Phòng thủ lớp hai cho /metrics: nginx đã chặn theo IP, nhưng nếu môi trường
# nào dùng sai file nginx thì endpoint hở (audit 2026-07-26 C3). Rỗng = tắt.
METRICS_ALLOW_TOKEN = os.getenv("METRICS_ALLOW_TOKEN", "").strip()
```

Kiểm tra đầu file đã `import os` chưa; nếu chưa thì thêm. Ghi biến vào `.env.example` kèm
một dòng giải thích.

- [ ] **Step 5: Áp vào view**

Trong `prometheus_metrics`, ngay đầu hàm:

```python
    expected = getattr(settings, "METRICS_ALLOW_TOKEN", "")
    if expected:
        provided = (_request.headers.get("X-Metrics-Token") or "").strip()
        if not provided or not secrets.compare_digest(provided, expected):
            return HttpResponse("forbidden", status=403, content_type="text/plain")
```

Thêm `import secrets` và `from django.conf import settings`. Tham số hiện tên `_request` —
đổi thành `request` và cập nhật chỗ dùng, hoặc giữ `_request` cho nhất quán; ruff `ARG` không
nằm trong ruleset nên cả hai đều pass, chọn `request` cho dễ đọc.

- [ ] **Step 6: Chạy test, xác nhận XANH.** Run `<LỆNH TEST PATHS=apps/orders/tests/test_metrics_endpoint_guard.py -v>`

- [ ] **Step 7: Commit**

```bash
git add nginx/conf.d/default.conf backend/config/settings_parts/observability.py backend/apps/orders/transport/views/order_views_impl/metrics_view.py backend/apps/orders/tests/test_metrics_endpoint_guard.py .env.example
git commit -m "fix(ops): restrict prometheus metrics endpoint at nginx and app layer"
```

---

## Task 3: T4 — xoá `verify_channel_token` (dead code fail-open)

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_helpers.py:227-233`

- [ ] **Step 1: Xác nhận thật sự không có call site**

```bash
cd backend && grep -rn "verify_channel_token" apps config
```
Expected: chỉ 1 dòng — chính định nghĩa. Nếu ra nhiều hơn, **DỪNG** và báo lại: giả định của
plan sai, không được xoá.

- [ ] **Step 2: Xoá hàm** `verify_channel_token` (cả decorator `@staticmethod`). Kiểm tra
`ChannelApiToken` / `hashlib` có còn được dùng trong file không; nếu thành import thừa thì
xoá luôn (ruff F401 sẽ bắt).

- [ ] **Step 3: Test + lint**

Run: `<LỆNH TEST PATHS=apps/orders>` và `ruff check --no-cache .`
Expected: không fail mới, ruff pass.

- [ ] **Step 4: Commit**

```bash
git add backend/apps/orders/application/services/order_service_helpers.py
git commit -m "refactor(orders): drop unused fail-open verify_channel_token"
```

---

## Task 4: C2 + T3 — sửa tìm kiếm seq-scan và RBAC scope cho job KiotViet

> Gộp C2 và T3 vì **cả hai đụng `list_kiotviet_jobs`** — tách ra sẽ conflict.

**Files:**
- Modify: `backend/apps/orders/repositories.py:895-968`
- Modify: `backend/apps/customers/customer_resale_service.py:122-134`
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_crud_view.py:198-220`
- Test: `backend/apps/orders/tests/test_order_search_index_usage.py` (mới)

**Interfaces:**
- Consumes: `resolved_channel_codes(user, section) -> None | set[str] | OWN_SCOPE_SENTINEL`, `OWN_SCOPE_SENTINEL` từ `apps.users.scoping`.
- Produces: `OrderRepository.list_kiotviet_jobs(filters: dict, *, channel_codes: set[str] | None = None, requested_by_only: int | None = None)` — chữ ký mở rộng, `requested_by_only` là id user khi scope là `own`.

**Nguyên tắc (đã ghi ở `repositories.py:339-343`):** `__icontains` → `UPPER(col) LIKE` → không
dùng được trigram GIN bare-column → seq-scan. `__contains` → dùng được. Tên khách là ngoại lệ
vì có functional index `order_name_upper_trgm_idx` nên **giữ** `name__icontains`.

- [ ] **Step 1: Viết test đỏ — assert vào SQL sinh ra**

`backend/apps/orders/tests/test_order_search_index_usage.py`. Không đo thời gian (không ổn
định trong CI), mà assert vào SQL: `str(queryset.query)` **không được** chứa `UPPER(` trên các
cột code/phone.

```python
def test_kiotviet_jobs_search_avoids_upper_on_code_columns():
    qs = OrderRepository.list_kiotviet_jobs({"q": "HD12345"})
    sql = str(qs.query).upper()
    # tên khách vẫn được phép UPPER (có functional index); code/phone thì không
    assert sql.count("UPPER(") <= 1

def test_history_by_phone_uses_normalized_column():
    qs = OrderRepository.list_history_by_phone("0901234567")
    sql = str(qs.query)
    assert "phone_normalized" in sql
    assert "UPPER" not in sql.upper()
```

Thêm test hành vi để chứng minh không làm hỏng tìm kiếm: tạo 2 đơn, tìm bằng một đoạn giữa
của `invoice_code` → vẫn ra đúng đơn; tìm bằng SĐT dạng `+84…` → vẫn khớp đơn lưu `09…`.

Và test cho T3: user non-superuser có scope `department` phải thấy job của người khác trong
phạm vi, user scope `own` chỉ thấy job của mình.

- [ ] **Step 2: Chạy, xác nhận ĐỎ.**

- [ ] **Step 3: Sửa `list_history_by_phone` (`repositories.py:910`)**

```python
        ).filter(
            Q(customer__phone_normalized=phone_normalized)
            | Q(phone_normalized=phone_normalized)
        ).order_by("-created_at")
```

Lý do: tham số vào đã là số chuẩn hoá; so vào cột `phone` thô bằng `icontains` vừa seq-scan
vừa **sai** (đơn lưu `+84…` không khớp). `phone_normalized` được `Order.save()` derive nên
luôn có (`models.py:449-454`), và có B-tree `order_phone_norm_idx`.

- [ ] **Step 4: Sửa ô tìm kiếm `list_kiotviet_jobs` (`repositories.py:957-968`)**

OR xuyên join không có index tốt. Áp đúng kỹ thuật materialize id mà `list_orders` dùng
(và đã ghi lý do ở dòng 363-369): lọc trên `orders` trước bằng `__contains`, lấy id, rồi
`order_id__in`:

```python
        q = str(filters.get("q") or "").strip()
        if q:
            # OR xuyên join order__* bằng icontains làm seq-scan cả orders lẫn bảng job
            # (audit 2026-07-26 C2). Lọc orders trước bằng __contains để dùng trigram GIN
            # bare-column, rồi thu về id — cùng kỹ thuật với list_orders.
            order_q = Q()
            if len(q) >= 3:
                order_q |= Q(order_code__contains=q)
                order_q |= Q(invoice_code__contains=q)
                order_q |= Q(old_invoice_code__contains=q)
                order_q |= Q(shipping_code__contains=q)
                order_q |= Q(external_order_id__contains=q)
                order_q |= Q(name__icontains=q)  # functional index order_name_upper_trgm_idx
            digits = normalize_phone(q)
            if digits and len(digits) >= 3:
                order_q |= Q(phone_normalized__contains=digits)
            condition = Q(error_message__contains=q) if len(q) >= 3 else Q()
            if order_q:
                matched_ids = list(
                    Order.objects.filter(order_q)
                    .order_by("-created_at")
                    .values_list("id", flat=True)[:SEARCH_MATCH_ID_CAP]
                )
                condition |= Q(order_id__in=matched_ids)
            queryset = queryset.filter(condition) if condition else queryset.none()
```

Dùng lại hằng cap đã có trong file cho `list_orders` (đọc quanh dòng 370-380 để lấy đúng tên;
nếu nó là local thì nâng thành module-level `SEARCH_MATCH_ID_CAP` và dùng ở cả hai chỗ — DRY).
`normalize_phone` import từ `apps.customers.phone_utils` (file đã import ở chỗ khác, kiểm tra
trước khi thêm).

- [ ] **Step 5: T3 — cho `list_kiotviet_jobs` nhận scope thật**

Mở rộng chữ ký: `def list_kiotviet_jobs(filters, *, channel_codes=None, requested_by_only=None)`.
Trong thân: nếu `requested_by_only` khác None thì `queryset.filter(requested_by_id=requested_by_only)`.

Rồi ở `order_crud_view.py:214-217` thay khối hardcode bằng:

```python
    from apps.users.scoping import OWN_SCOPE_SENTINEL, resolved_channel_codes

    scope = resolved_channel_codes(request.user, "orders")
    channel_codes = None
    requested_by_only = None
    if scope is OWN_SCOPE_SENTINEL:
        requested_by_only = request.user.id
    elif scope is not None:
        channel_codes = scope
    queryset = OrderRepository.list_kiotviet_jobs(
        filters, channel_codes=channel_codes, requested_by_only=requested_by_only
    )
```

Xoá `filters["requested_by"] = request.user.id` (nó ghi đè cả filter `requested_by` mà người
dùng chọn). Giữ nguyên việc đọc `requested_by` từ query param.

- [ ] **Step 6: Sửa `customer_resale_service.py:124-133`**

```python
        if query_text:
            digits = normalize_phone(query_text)
            search = Q(name__icontains=query_text) | Q(customer__full_name__icontains=query_text)
            if len(query_text) >= 3:
                search |= Q(shipping_code__contains=query_text)
                search |= Q(order_code__contains=query_text)
                search |= Q(invoice_code__contains=query_text)
            if digits and len(digits) >= 3:
                search |= Q(phone_normalized__contains=digits)
                search |= Q(customer__phone_normalized__contains=digits)
            scoped = scoped.filter(search)
```

Bỏ `Q(phone__icontains=...)` và `Q(customer__phone_raw__icontains=...)` — cả hai đã được
`phone_normalized` phủ và không có index. ⚠️ Đọc kỹ khối cache facet ngay dưới
(`_resale_cache_suffix`): key cache theo `query_text` nên không cần đổi, nhưng **phải xác nhận**
không có chỗ nào giả định danh sách field cũ.

- [ ] **Step 7: Chạy test, xác nhận XANH**

Run: `<LỆNH TEST PATHS="apps/orders apps/customers" -q>`
Expected: test mới pass, không fail mới ở `apps/customers` (trang resale có test riêng — đọc
và chạy chúng).

- [ ] **Step 8: Cập nhật schema nếu chữ ký endpoint đổi**

Chữ ký repository là nội bộ nên schema **không** đổi. Xác minh: `python manage.py spectacular
--file schema.yaml` rồi `git diff --stat backend/schema.yaml` → phải rỗng. Nếu có diff thì
commit kèm `npm run gen:api`.

- [ ] **Step 9: Commit**

```bash
git add backend/apps/orders/repositories.py backend/apps/customers/customer_resale_service.py backend/apps/orders/transport/views/order_views_impl/order_crud_view.py backend/apps/orders/tests/test_order_search_index_usage.py
git commit -m "perf(search): use trigram-friendly contains and apply rbac scope to kiotviet jobs"
```

---

## Task 5: T1 — sửa N+1 ở danh sách User

**Files:**
- Modify: `backend/apps/users/services.py:28-46` (`admin_list_users`)
- Modify: `backend/apps/users/serializers.py:30-31` (`DepartmentSerializer.get_channel_ids`)
- Test: `backend/apps/users/tests/test_users_list_queries.py` (**đã tồn tại** — mở rộng)

**Interfaces:** không đổi API. `get_channel_ids` vẫn trả `list[int]`.

- [ ] **Step 1: Đọc test hiện có**

`apps/users/tests/test_users_list_queries.py` đã có (nó nằm trong `.importlinter`
`ignore_imports`). Đọc để dùng lại fixture, và thêm test mới theo **kiểu so sánh hai cỡ**
(khuôn đợt 07-24 dùng cho `/api/orders`, bền hơn chốt trần cứng):

```python
def test_users_list_query_count_does_not_grow_with_user_count(...):
    # 3 user, mỗi user có department gắn 2 channel
    with CaptureQueriesContext(connection) as small:
        client.get("/api/users/admin/users?limit=100&offset=0")   # đường dẫn thật: đọc urls.py
    # thêm 12 user nữa (tổng 15), cùng department
    with CaptureQueriesContext(connection) as large:
        client.get("/api/users/admin/users?limit=100&offset=0")
    assert len(large) == len(small), (
        f"N+1: 3 user={len(small)} query, 15 user={len(large)} query"
    )
```

⚠️ `resolve_effective_permissions` có cache Redis theo user → số query phụ thuộc cache nóng
hay lạnh. Để test tiền định: **không** dùng `cache.clear()` (cấm — FLUSHDB đè Redis thật).
Thay vào đó gọi endpoint một lần trước khi đo (warm-up) ở cả hai lần đo, hoặc patch
`apps.users.rbac.resolve_effective_permissions` bằng giá trị dựng sẵn. Chọn cách patch cho
ổn định và ghi rõ lý do bằng comment.

- [ ] **Step 2: Chạy, xác nhận ĐỎ** — 15 user phải ra nhiều query hơn 3 user (mỗi user +1 vì
`department.channels.filter()`).

- [ ] **Step 3: Prefetch trong `admin_list_users`**

```python
        from apps.channels_core.models import Channel

        queryset = (
            User.objects.filter(deleted_at__isnull=True)
            .select_related("department")
            .prefetch_related(
                "role_assignments",
                "department_links",
                # DepartmentSerializer.get_channel_ids đọc qua .all() để ăn cache này;
                # trước đây nó .filter() nên sinh 1 query MỖI user (audit 2026-07-26 T1).
                Prefetch(
                    "department__channels",
                    queryset=Channel.objects.filter(deleted_at__isnull=True),
                ),
                Prefetch(
                    "kiotviet_mappings",
                    queryset=UserKiotvietMapping.objects.select_related("kiotviet_channel"),
                ),
            )
            .order_by("id")
        )
```

⚠️ `apps.users` nằm **dưới** `apps.channels_core` trong contract phân tầng? Kiểm tra
`.importlinter`: thứ tự là `apps.users : apps.channels_core` **cùng một tầng** (ngăn bằng `:`)
nên import ngang hàng là hợp lệ. Chạy `lint-imports` để chắc; nếu đỏ thì **không** thêm vào
`ignore_imports` (Global Constraints) — chuyển sang cách khác: prefetch bằng chuỗi
`"department__channels"` không cần import Channel, và lọc `deleted_at` trong Python ở serializer.

- [ ] **Step 4: Sửa serializer để ăn cache**

```python
    def get_channel_ids(self, obj: Department) -> list[int]:
        # .all() để dùng prefetch cache của admin_list_users; .filter() sẽ query lại
        # DB cho từng user (cùng nguyên tắc đã ghi ở UserSerializer bên dưới).
        return [channel.id for channel in obj.channels.all() if channel.deleted_at is None]
```

Lọc `deleted_at` bằng Python để hàm vẫn đúng khi **không** có prefetch (các endpoint khác
dùng `DepartmentSerializer` một mình) — fallback an toàn, chỉ chậm hơn.

- [ ] **Step 5: Chạy test, xác nhận XANH**

Run: `<LỆNH TEST PATHS=apps/users -q>`

- [ ] **Step 6: Ưu tiên thấp cùng nhóm** — sửa luôn `serializers.py:257,264,281` (Roles page:
`RolePermission/RoleSectionScope/RoleFieldRule.objects.filter(role=obj)` → N+1 theo số role)
bằng prefetch ở queryset của endpoint Roles + đọc qua `.all()`. Nếu queryset đó không nằm
trong `UserService`, tìm bằng `grep -rn "RoleSerializer" apps/users`. Thêm một
`assertNumQueries`-kiểu-so-sánh cho trang Roles.

- [ ] **Step 7: Lint + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
cd backend && python -m piptools 2>/dev/null; lint-imports   # hoặc: docker run ... lint-imports
git add backend/apps/users/
git commit -m "perf(users): prefetch department channels and role rules to remove n+1"
```

---

## Task 6: T2 — sourcemap cho Sentry

**Files:**
- Modify: `frontend/vite.config.ts:69-86`
- Modify: `frontend/src/main.tsx:17-26`
- Modify: `frontend/package.json` (devDep `@sentry/vite-plugin`)
- Modify: `frontend/Dockerfile` (loại `*.map` khỏi image)
- Modify: `.gitlab-ci.yml` (truyền biến build)
- Modify: `.env.example`

**Interfaces:** build nhận env `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT`, `VITE_SENTRY_RELEASE`.

- [ ] **Step 1: Cài plugin**

```bash
cd frontend && npm install --save-dev @sentry/vite-plugin
```

- [ ] **Step 2: `vite.config.ts`**

Đổi `sourcemap: false` → `sourcemap: "hidden"` (sinh `.map` nhưng **không** gắn
`//# sourceMappingURL` vào bundle → không lộ ra client). Thêm plugin vào mảng `plugins`, chỉ
bật khi có token để build local/CI không có token vẫn chạy:

```ts
      env.SENTRY_AUTH_TOKEN &&
        sentryVitePlugin({
          org: env.SENTRY_ORG,
          project: env.SENTRY_PROJECT,
          authToken: env.SENTRY_AUTH_TOKEN,
          release: { name: env.VITE_SENTRY_RELEASE || undefined },
          sourcemaps: { filesToDeleteAfterUpload: ["dist/**/*.map"] },
        }),
```

`filesToDeleteAfterUpload` xoá map sau khi upload → không cần sửa Dockerfile nữa; **nếu**
plugin không bật (không có token) thì map vẫn nằm trong `dist` nên Step 4 vẫn cần.

- [ ] **Step 3: `main.tsx` — thêm `release` để map khớp**

```ts
    Sentry.init({
      dsn: sentryDsn,
      environment: import.meta.env.MODE,
      release: import.meta.env.VITE_SENTRY_RELEASE as string | undefined,
      sendDefaultPii: false,
    });
```

- [ ] **Step 4: `frontend/Dockerfile`** — sau bước build, thêm `RUN find /app/dist -name "*.map" -delete`
(hoặc tương đương ở stage copy) để image nginx không bao giờ serve sourcemap. Đọc Dockerfile
hiện tại trước để chèn đúng stage.

- [ ] **Step 5: CI** — ở job `frontend:build` trong `.gitlab-ci.yml`, đặt
`VITE_SENTRY_RELEASE: $CI_COMMIT_SHA`. `SENTRY_AUTH_TOKEN`/`SENTRY_ORG`/`SENTRY_PROJECT` để
người vận hành khai ở Settings → CI/CD → Variables (ghi vào phần "Cần người vận hành" của
báo cáo audit). Không hardcode token.

- [ ] **Step 6: Verify build thật**

```bash
cd frontend && npm run build
ls dist/assets/*.map | head          # phải CÓ map khi build local (chưa upload)
grep -rl "sourceMappingURL" dist/assets/*.js | head   # phải RỖNG (hidden)
npm run typecheck && npm run lint:strict && npm run format:check
```

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/src/main.tsx frontend/Dockerfile .gitlab-ci.yml .env.example
git commit -m "chore(frontend): emit hidden sourcemaps and upload them to sentry"
```

---

## Task 7: C1 — đo index usage và drop index chết

**Files:**
- Create: `backend/apps/common/management/commands/report_index_usage.py`
- Create: `backend/apps/orders/migrations/0109_drop_dead_indexes.py`
- Modify: `backend/apps/orders/models.py:334-435` (bỏ khai báo tương ứng)
- Create: `docs/runbooks/2026-07-26-drop-dead-indexes.md`
- Test: `backend/apps/common/tests/test_report_index_usage.py` (mới)

**Interfaces:** command `python manage.py report_index_usage [--table orders] [--min-scans 0]`.

**Phụ thuộc:** phải làm **sau Task 4**. Task 4 loại bỏ mọi `phone__icontains`, nhờ đó
`order_phone_idx` và `order_phone_trgm_idx` mới thật sự hết người dùng.

- [ ] **Step 1: Viết command đo (cho người vận hành)**

Truy vấn `pg_stat_user_indexes` + `pg_postmaster_start_time()` để biết số liệu tích luỹ bao
lâu (số `idx_scan=0` vô nghĩa nếu Postgres vừa restart). In bảng: index, `idx_scan`, size,
uptime. Test bằng `CaptureQueriesContext`/`call_command` — chỉ cần chạy được và ra output có
header, không assert số liệu (phụ thuộc môi trường).

- [ ] **Step 2: Chốt danh sách drop — CHỈ 4 index đã chứng minh static**

| Index | Bằng chứng |
|---|---|
| `order_product_trgm_idx` | GIN bare-column trên `product`; query duy nhất là `product__icontains` → dùng `order_product_upper_trgm_idx`. Không có `product__contains` nào. |
| `order_phone_trgm_idx` | GIN bare-column trên `phone`; sau Task 4 không còn query nào trên cột `phone`. |
| `order_phone_idx` | B-tree trên `phone`; mọi tra cứu qua `phone_normalized`. |
| `order_channel_owner_idx` | `channel_owner` chỉ xuất hiện ở phía ghi. |

Trước khi viết migration, **verify lại từng cái**:

```bash
cd backend && grep -rn "product__contains\|phone__contains\|phone__icontains\|phone__exact\|filter(phone=\|channel_owner__\|filter(channel_owner=" apps --include="*.py" | grep -v migrations
```
Kết quả phải không còn chỗ nào dùng 4 cột đó theo kiểu cần index. Có chỗ nào → **loại index
đó khỏi danh sách drop** và ghi lý do vào runbook.

**KHÔNG drop** `status_idx` và `order_created_at_idx` trong đợt này: chúng chỉ *nghi*, cần số
liệu `idx_scan` thật. Ghi vào runbook để đợt sau quyết theo số.

- [ ] **Step 3: Xoá 4 khai báo khỏi `models.py`** (dòng 339, 341, 388, 389 — kiểm lại số dòng
sau các task trước).

- [ ] **Step 4: Sinh migration rồi sửa thành CONCURRENTLY**

```bash
python manage.py makemigrations orders --name drop_dead_indexes
```

Sửa file sinh ra: thêm `atomic = False` vào class `Migration`, và bọc mỗi `RemoveIndex` bằng
`SeparateDatabaseAndState` với `RunSQL("DROP INDEX CONCURRENTLY IF EXISTS <name>")` +
`state_operations=[migrations.RemoveIndex(...)]`. `CONCURRENTLY` không chạy trong transaction
— thiếu `atomic = False` sẽ lỗi lúc migrate. Đặt `reverse_sql` tạo lại index để rollback
được (copy đúng định nghĩa từ `models.py` trước khi xoá, gồm `gin_trgm_ops`).

- [ ] **Step 5: Test migration chạy được**

Run: `<LỆNH TEST PATHS=apps/orders -q --create-db>` (`--create-db` để replay toàn bộ
migration từ đầu, đúng thứ cần verify).

- [ ] **Step 6: Runbook**

`docs/runbooks/2026-07-26-drop-dead-indexes.md`: câu SQL đo, cách đọc `idx_scan` cùng
`pg_postmaster_start_time()`, lệnh migrate, cách rollback (`migrate orders 0108`), và danh
sách "nghi nhưng chưa drop" (`status_idx`, `order_created_at_idx`) kèm ngưỡng quyết định.

- [ ] **Step 7: Commit**

```bash
git add backend/apps/common/management/commands/report_index_usage.py backend/apps/common/tests/test_report_index_usage.py backend/apps/orders/models.py backend/apps/orders/migrations/0109_drop_dead_indexes.py docs/runbooks/2026-07-26-drop-dead-indexes.md
git commit -m "perf(db): drop four unused orders indexes and add index usage command"
```

---

## Task 8: T5 — tách queue `slow`

**Files:**
- Modify: `backend/config/settings_parts/celery.py:20-50`
- Modify: `docker-compose.prod.yml` (thêm service `worker-slow`)
- Modify: `docker-compose.local.yml`, `docker-compose.staging.yml` (giữ đồng bộ)
- Modify: `.env.example`
- Test: `backend/apps/common/tests/test_celery_routes.py` (mới)

**Interfaces:** `CELERY_SLOW_QUEUE = os.getenv("CELERY_SLOW_QUEUE", "slow")`.

- [ ] **Step 1: Viết test đỏ** — với mỗi task trong danh sách dưới, `CELERY_TASK_ROUTES`
phải trả queue `slow`:

```python
SLOW_TASKS = [
    "apps.callcenter.tasks.transcribe_pending_call_records",
    "apps.callcenter.tasks.extract_pending_call_insights",
    "apps.callcenter.tasks.sync_zenify_call_histories",
    "apps.customers.tasks.warm_report_cache",
    "apps.customers.tasks.rebuild_segment_snapshot",
]
def test_slow_tasks_routed_off_default_queue():
    from django.conf import settings
    for name in SLOW_TASKS:
        assert settings.CELERY_TASK_ROUTES[name]["queue"] == settings.CELERY_SLOW_QUEUE
```

Thêm một test bảo vệ: **mọi** entry trong `CELERY_BEAT_SCHEDULE` phải có route (hoặc cố ý
thuộc `default`) — chống việc thêm task nặng mới mà quên route:

```python
def test_every_beat_task_has_explicit_route_or_is_known_default():
    routed = set(settings.CELERY_TASK_ROUTES)
    beat_tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    unrouted = beat_tasks - routed
    assert unrouted <= KNOWN_DEFAULT_QUEUE_TASKS, f"task mới chưa route: {unrouted - KNOWN_DEFAULT_QUEUE_TASKS}"
```

`KNOWN_DEFAULT_QUEUE_TASKS` là frozenset liệt kê các task ngắn cố ý để ở `default` (các
`purge_*`, `sweep_*`, `ops_heartbeat`, ads sync…). Danh sách này là **tài liệu sống**: ai thêm
task beat mới sẽ bị test bắt và phải quyết định queue.

- [ ] **Step 2: Chạy, xác nhận ĐỎ.**

- [ ] **Step 3: Thêm queue + routes** vào `celery.py`, kèm comment nêu lý do (LLM/transcription
+ precompute báo cáo chiếm hết 4 slot của worker `default`, làm sweep điều phối lead bị chờ —
audit 2026-07-26 T5).

- [ ] **Step 4: Thêm worker vào compose**

Copy khuôn `worker-kiotviet` trong `docker-compose.prod.yml` (dòng ~372-385) thành
`worker-slow`: `-Q slow`, `--concurrency=${SLOW_WORKER_CONCURRENCY:-2}`,
`DB_APPLICATION_NAME: desi-worker-slow`, `container_name: desi-worker-slow`, giữ healthcheck
`celery inspect ping` và `mem_limit` tương ứng. Ghi biến vào `.env.example`.

⚠️ Đọc memory/runbook về prod: `docker compose pull` kéo cả image core và recreate nhiều
service cùng lúc gây trùng tên container. Runbook deploy phải nêu: chỉ
`docker compose up -d worker-slow`.

- [ ] **Step 5: Verify compose hợp lệ**

```bash
docker compose -f docker-compose.prod.yml config >/dev/null && echo OK
docker compose -f docker-compose.local.yml config >/dev/null && echo OK
```

- [ ] **Step 6: Chạy test + commit**

```bash
git add backend/config/settings_parts/celery.py backend/apps/common/tests/test_celery_routes.py docker-compose.prod.yml docker-compose.local.yml docker-compose.staging.yml .env.example
git commit -m "perf(celery): route long-running tasks to dedicated slow queue"
```

---

## Task 9: T6a — squash 110 migration của `orders`

**Files:**
- Create: `backend/apps/orders/migrations/0001_squashed_0108_*.py` (tên do Django sinh)
- Keep: toàn bộ file cũ (Django gắn `replaces`, **không xoá**)
- Create: `docs/runbooks/2026-07-26-squash-orders-migrations.md`

**Vì sao an toàn:** `squashmigrations` sinh file có `replaces = [...]`; môi trường **đã** qua
các migration cũ vẫn hợp lệ, môi trường mới chạy file squash. Giữ file cũ nên không cần biết
trạng thái migration prod — đây chính là điểm mà audit 07-23 còn e ngại.

- [ ] **Step 1: Chạy squash trong container**

```bash
# trong container test (có Django + DB)
python manage.py squashmigrations orders 0108 --no-input
```

Nếu Django báo không squash được vì `RunPython` không `elidable`: **không** đi ép elidable
(sửa hàm dữ liệu là rủi ro thật). Thay vào đó squash tới mốc trước migration data đầu tiên
gây chặn, ví dụ `squashmigrations orders 0050`. Ghi rõ mốc đã chọn và lý do vào runbook.

- [ ] **Step 2: Verify replay từ số 0**

```bash
<LỆNH TEST PATHS=apps/orders -q --create-db>
```
`--create-db` dựng lại DB test từ migration → đây là phép thử thật cho file squash.

- [ ] **Step 3: Verify không có state drift**

```bash
python manage.py makemigrations --check --dry-run
```
Expected: "No changes detected". Có changes = file squash mô tả thiếu state → **DỪNG**, không
commit, báo lại.

- [ ] **Step 4: Đo lợi ích** — ghi vào runbook thời gian `--create-db` trước/sau (chạy 2 lần
lấy số thật, không ước lượng).

- [ ] **Step 5: Runbook + commit**

Runbook nêu: mốc squash, khi nào được xoá file cũ (sau khi **mọi** môi trường có
`showmigrations orders` cho thấy đã qua mốc), và lệnh kiểm tra đó.

```bash
git add backend/apps/orders/migrations/ docs/runbooks/2026-07-26-squash-orders-migrations.md
git commit -m "chore(orders): squash migrations up to 0108"
```

---

## Task 10–12: T6c — chuyển 7 app sang `transport/`

Khuôn mẫu **đã có**: `apps/orders/views.py` là facade 143 dòng re-export từ
`apps/orders/transport/views/order_views_impl`. Làm đúng như vậy cho từng app.

> **Lưu ý sửa lại nhận định cũ:** audit 07-23 ghi "`orders` có cả `views.py` phẳng lẫn
> `transport/`". Kiểm tra lại: `orders/views.py` là facade thuần (0 hàm), nên `orders` **đã
> xong**. Việc còn lại chỉ là 7 app dưới.

**Chia 3 task để tránh conflict và giữ MR review được:**

| Task | App | Kích thước |
|---|---|---|
| 10 | `warranty` (74 LOC/2 def), `kiotviet` (30/1), `audit` (130/4), `callcenter` (137/3) | nhỏ |
| 11 | `google_ads` (331/8) | vừa |
| 12 | `customers` (683/21), `users` (667/20) | lớn — làm cuối |

**Quy trình cho MỖI app (lặp lại, không tắt bước):**

- [ ] **Step 1: Chạy test của app, ghi số baseline.** `<LỆNH TEST PATHS=apps/<app> -q>`
- [ ] **Step 2:** Tạo `apps/<app>/transport/__init__.py`, `apps/<app>/transport/views/__init__.py`,
  và `apps/<app>/transport/views/<app>_views_impl.py` (hoặc package `<app>_views_impl/` chia
  theo nhóm nghiệp vụ nếu > 300 LOC — xem `orders/transport/views/order_views_impl/` làm mẫu).
- [ ] **Step 3:** Di chuyển **nguyên văn** thân các view sang file mới. Không sửa logic, không
  đổi tên hàm, không "tranh thủ" refactor — đây là commit thuần cấu trúc để diff đọc được.
- [ ] **Step 4:** Biến `apps/<app>/views.py` thành facade: docstring
  `"""Facade module to preserve public imports while delegating to layered implementation."""`
  + `from .transport.views.<app>_views_impl import (...)` liệt kê đúng các tên cũ + `__all__`.
- [ ] **Step 5:** `grep -rn "apps.<app>.views\|from .views import\|from apps.<app> import views" apps config`
  → mọi import cũ phải vẫn hoạt động nhờ facade. `urls.py` không cần đổi.
- [ ] **Step 6:** Chạy `<LỆNH TEST PATHS=apps/<app> -q>` → **cùng số test, cùng kết quả** với Step 1.
- [ ] **Step 7:** `lint-imports` + `ruff check --no-cache .` → pass. Nếu `lint-imports` đỏ vì
  đường dẫn module đổi, cập nhật entry **đã có** trong `ignore_imports` cho khớp tên mới
  (đổi tên entry cũ là hợp lệ; **thêm** entry mới thì không).
- [ ] **Step 8:** `python manage.py spectacular --file schema.yaml` → `git diff --stat` phải
  rỗng (chỉ đổi vị trí code, không đổi hợp đồng). Có diff = đã vô tình đổi behavior → điều tra.
- [ ] **Step 9: Commit** `refactor(<app>): move views to transport layer behind facade`

Sau Task 12, xoá các entry `ignore_imports` nào đã hết vi phạm và chạy lại `lint-imports`.

---

## Task 13–14: T6b — tách 4 file frontend quá lớn

Tách theo **trục nghiệp vụ**, không theo số dòng. Mỗi file mới phải có một trách nhiệm gọi
tên được.

| Task | File | LOC | Hướng tách |
|---|---|---:|---|
| 13 | `frontend/src/features/orders/use-orders-data.ts` | 2786 | `use-orders-filters.ts` (filter/scope/localStorage) · `use-orders-table.ts` (list + phân trang + status counts) · `use-orders-bulk-actions.ts` (bulk delete/export/kiotviet) · `use-orders-catalogs.ts` (product/address/kship catalog + cache TTL) · `use-orders-data.ts` giữ lại làm **facade** compose 4 hook trên và re-export type |
| 14 | `frontend/src/pages/OrdersPage.tsx` (2087) · `frontend/src/features/orders/OrderFormDialog.tsx` (2073) · `frontend/src/features/dashboard/components/DashboardSections.tsx` (1443) | | `OrdersPage` → tách toolbar/filter-bar, bảng, dialog-host. `OrderFormDialog` → tách theo block form (thông tin KH / sản phẩm / vận chuyển / CRM). `DashboardSections` → 1 file/section |

**Quy trình cho MỖI task:**

- [ ] **Step 1:** `cd frontend && npm test -- --run` → ghi số baseline (đợt 07-24 đo được
  704/704 pass trên Node 22).
- [ ] **Step 2:** Tách file, **giữ nguyên hành vi**. Public API của `use-orders-data.ts`
  không đổi — `OrdersPage` và các consumer khác không được phải sửa import.
- [ ] **Step 3:** `npm run typecheck` → 0 lỗi.
- [ ] **Step 4:** `npm run lint:strict` → **0 warning** (ngưỡng CI). Chú ý react-compiler báo
  lỗi tại **dòng chứa comment `eslint-disable`**, không phải dòng `useEffect` — và 3
  `useEffect` có deps hẹp **có chủ ý** (bảo vệ bản nháp chưa lưu của người dùng khỏi bị wipe
  khi refetch realtime): **không** viết lại để chiều lint, giữ disable kèm giải thích.
- [ ] **Step 5:** `npm test -- --run` → **cùng số test pass** như Step 1.
- [ ] **Step 6:** `npm run format:check` → pass (hoặc `npm run format` rồi commit).
- [ ] **Step 7:** `npm run build` → thành công, không vượt `chunkSizeWarningLimit` mới nào.
- [ ] **Step 8: Commit** `refactor(orders): split use-orders-data into focused hooks`

---

## Kiểm tra cuối (sau tất cả task)

- [ ] Full backend suite: `<LỆNH TEST PATHS=apps -q>` → so với baseline đầu phiên, **không có
  fail mới**. Báo cáo con số thật (đã chạy: xem `docs/audit-2026-07-26-full-stack.md` nhật ký).
- [ ] `ruff check --no-cache .` → All checks passed.
- [ ] `lint-imports` → pass, và số entry `ignore_imports` **giảm** so với 25.
- [ ] `python manage.py makemigrations --check --dry-run` → No changes detected.
- [ ] `python manage.py spectacular --file schema.yaml` + `npm run gen:api` → `git status` sạch.
- [ ] Frontend: `npm run typecheck && npm run lint:strict && npm run format:check && npm test -- --run && npm run build`.
- [ ] `docker compose -f docker-compose.prod.yml config >/dev/null`.
- [ ] Cập nhật `docs/audit-2026-07-26-full-stack.md`: thêm mục "Nhật ký thực hiện" ghi từng
  mục đã đóng + bằng chứng đo được, đúng khuôn nhật ký của `audit-2026-07-23-full-stack.md`.
- [ ] Cập nhật phần "Cần người vận hành": biến CI Sentry, migrate index drop, deploy
  `worker-slow`, `METRICS_ALLOW_TOKEN`.

---

## Ghi chú rủi ro

| Task | Rủi ro | Cách chặn |
|---|---|---|
| 1 | Siết quá tay → user hợp lệ mất quyền in | Test có ca **được phép** (đơn trong scope vẫn in được), không chỉ ca bị chặn |
| 4 | `__contains` case-sensitive → tìm kiếm mã chữ thường không ra | Mã đơn/hóa đơn trong hệ này là chữ IN + số; test có ca thực tế. Tên khách giữ `icontains`. |
| 7 | Drop oan một index đang được dùng | Chỉ drop 4 cái đã chứng minh static + verify lại bằng grep ở Step 2; migration có `reverse_sql` tạo lại |
| 9 | File squash mô tả thiếu state | `makemigrations --check` + replay `--create-db` bắt buộc pass |
| 10–12 | Đổi vị trí code làm vỡ import ngoài | Facade giữ nguyên mọi tên; `spectacular` diff rỗng là bằng chứng hợp đồng không đổi |
| 13–14 | Refactor FE làm hồi quy im lặng | Số test pass phải khớp baseline; public API hook không đổi |
