# Audit toàn hệ thống — 2026-07-26

> Phạm vi: backend, frontend, database, security, code structure, tech stack.
> Baseline: master `ef1ffc86`. Phương pháp: đọc code trực tiếp, đối chiếu index với query
> thật, đếm bằng script — không suy đoán.
> Quan hệ với đợt trước: `audit-2026-07-23-full-stack.md` đã đóng N0/N1/N2/C1/C2/T1/T2/T7.
> Tài liệu này **không lặp lại** các mục đó; đã verify chúng nằm trong code (xem phụ lục A).

## Đánh giá tổng quan

Chất lượng kỹ thuật cao hơn hẳn mặt bằng công ty 50 người: CI thật đã chạy trên GitLab,
contract phân tầng cưỡng chế, OpenAPI drift-check 2 chiều, RBAC/ABAC có resolver + cache,
webhook ký HMAC `compare_digest`, raw SQL dùng named param (không có chỗ nào nối chuỗi),
settings có validation secret lúc khởi động, index có đo đạc kèm giải thích tại chỗ.

Nhưng đợt này lộ ra **một lỗ hổng phân quyền cho phép mọi nhân viên tải toàn bộ hóa đơn
của công ty**, và một nhóm vấn đề chung mang cùng dấu hiệu: **kiến thức tối ưu/bảo mật đã
có ở một code path nhưng chưa được áp ở các path song song**.

| Mảng | Trạng thái |
|---|---|
| Security (cấu hình, secrets, crypto) | Tốt |
| Security (phân quyền record-level) | **Hở nghiêm trọng ở 4 endpoint in ấn** |
| Database (schema/index) | Khá — nhưng bảng `orders` đang gánh 41 index, chưa ai kiểm chứng cái nào còn sống |
| Database (query pattern) | Khá — 3 code path còn seq-scan vì `icontains` |
| Backend (kiến trúc) | Khá — nợ phân tầng cũ còn tồn |
| Frontend | Tốt về hạ tầng — Sentry đang mù vì thiếu sourcemap |
| CI/CD | Tốt (sau đợt 07-24) — cần xác nhận đã gắn Runner |

---

## 🔴 NGHIÊM TRỌNG

### N1. Bốn endpoint in hóa đơn / phiếu giao không kiểm tra quyền trên bản ghi — mọi nhân viên tải được hóa đơn toàn công ty

`apps/orders/transport/views/order_views_impl/order_export_view.py`

| Endpoint | Dòng | Kiểm tra duy nhất |
|---|---|---|
| `GET  /api/orders/<id>/invoice` | 34–61 | `require_section(*ORDER_SECTIONS)` |
| `GET  /api/orders/<id>/delivery-slip` | 69–96 | `require_section(*ORDER_SECTIONS)` |
| `POST /api/orders/bulk-invoices` | 104–119 | `require_section(*ORDER_SECTIONS)` |
| `POST /api/orders/bulk-delivery-slips` | 127–142 | `require_section(*ORDER_SECTIONS)` |

`require_section` chỉ là shim của `require_permission(f"{section}:view")`
(`apps/users/permissions.py:26-33`) — nó kiểm tra **user có quyền xem mục Đơn hàng hay
không**, hoàn toàn không biết tới bản ghi nào. Sau đó code truy vấn thẳng theo id do client
gửi, không giao với scope:

```python
orders = Order.objects.select_related("kiotviet_channel").filter(
    id__in=ids, deleted_at__isnull=True
)          # order_export_view.py:109 — `ids` lấy nguyên từ request.data
return bulk_invoice_response(orders)
```

Đã loại trừ mọi lớp bù: `MIDDLEWARE` không có middleware scope nào
(`config/settings_parts/base.py:67-83`); không có `get_queryset` chung vì đây là
function-based view.

**Bằng chứng đây là oversight chứ không phải chủ ý — cùng file, cách 40 dòng:**
`bulk_export_excel` (dòng 150–197) áp scope rất kỹ, docstring ghi rõ *"cả nhánh `ids` cũng
áp scope để không xuất được đơn ngoài phạm vi được cấp"*, và code làm đúng thế:

```python
if scope_filters.get("owner_user_id"):
    orders = orders.filter(staff_id=scope_filters["owner_user_id"])
elif scope_filters.get("channel"):
    orders = orders.filter(channel__code__in=scope_filters["channel"])
```

**Khai thác:** một sale chỉ được gán 1 kênh, scope `own`, gửi
`POST /api/orders/bulk-invoices` với `{"ids": [1, 2, ..., 500000]}` → nhận về một tài liệu
HTML chứa hóa đơn của **toàn bộ công ty**: tên, số điện thoại, địa chỉ khách, sản phẩm, số
tiền. Không cần leo quyền, không cần lỗi nào khác. Id là số nguyên tuần tự nên không có rào
cản đoán id.

**Kèm theo — không có cap trên `ids`.** `bulk_invoice_response`
(`apps/orders/invoice.py:523-541`) gọi `get_invoice_by_code` **một lần cho mỗi đơn**, đồng
bộ, trong lòng request. 5.000 id = 5.000 round-trip HTTP tới KiotViet trong một request:
tự DoS worker và đốt rate-limit của KiotViet. So sánh: nhánh export Excel có
`EXPORT_MAX_ROWS`, 4 endpoint này không có gì.

**Không có test nào phủ quyền của 4 endpoint này** (grep toàn bộ `apps/orders/tests`).

**Sửa** — thêm đúng thứ `bulk_export_excel` đang làm:

1. Cả 4 endpoint: `_apply_user_channel_scope({}, request.user, section)` rồi giao vào
   queryset (`staff_id` cho `own`, `channel__code__in` cho channel/department).
2. Hai endpoint single: thêm `if not OrderService.can_edit_channel(...)` → 403, đúng
   pattern mà `update_order_inline`/`close_case`/`create_contact_log` đang dùng.
3. Cap `len(ids)` (đề xuất 200 — bằng giới hạn in thực tế) và trả 400 khi vượt.
4. Test: user scope `own` gọi bulk với id của đơn người khác → phải không nhận được đơn đó.

Rủi ro sửa: thấp. Chỉ siết lại, không đổi hợp đồng API.
Ưu tiên: **làm trước mọi việc khác trong tài liệu này.**

---

## 🟠 CAO

### C1. Bảng `orders` đang gánh 41 index — trong đó 10 GIN trigram, và chưa ai kiểm chứng cái nào còn được dùng

Đếm bằng script trên `apps/orders/models.py`:

| Loại | Số |
|---|---:|
| `Meta.indexes` | 31 (10 GIN trigram, 12 partial) |
| `UniqueConstraint` (partial unique index) | 2 |
| `db_index=True` trên field | 2 |
| Index tự sinh của ForeignKey | 5 |
| Primary key | 1 |
| **Tổng** | **41** |

`orders` đồng thời là bảng **ghi nóng nhất** hệ thống: webhook KiotViet cập nhật từng đơn,
cron `kv-import-create-only` / `kv-update-invoice-status` / `kv-sync-returns` ghi liên tục.
Mỗi UPDATE phải maintain 41 index, và GIN là loại đắt nhất khi ghi.

Đối chiếu index với query thật cho thấy **ít nhất 4 index gần như chắc chắn không bao giờ
được dùng** — riêng chúng đã là 2 GIN:

| Index | Vì sao nghi đã chết |
|---|---|
| `order_product_trgm_idx` (GIN, `product`) | Query duy nhất trên cột này là `product__icontains` (`repositories.py:456`) → Postgres sinh `UPPER(product) LIKE` → dùng `order_product_upper_trgm_idx`. Index bare-column chỉ phục vụ `__contains` (case-sensitive) mà **không có call site nào**. Đây là GIN trên `TextField` — đắt nhất trong 10 cái. |
| `order_phone_trgm_idx` (GIN, `phone`) | Không có query nào chạy `phone__contains`. Chỉ có `phone__icontains` (2 chỗ, xem C2) — mà `icontains` không dùng được index bare-column này. |
| `order_phone_idx` (B-tree, `phone`) | Cùng lý do; mọi tra cứu chính xác đều qua `phone_normalized` (`save()` tự derive, `models.py:449-454`). |
| `order_channel_owner_idx` | `channel_owner` chỉ xuất hiện ở phía **ghi** (4 call site `channel_owner=`), không có `filter(channel_owner…)` nào. |

Thêm 2 cái đáng nghi (cần số liệu mới kết luận): `status_idx` — chính comment ở
`models.py:357` viết *"90% of orders are status=3/active, so status_idx is useless here"*;
và `order_created_at_idx`, gần như bị `ord_act_cr_desc_idx` (partial `deleted_at IS NULL`)
phủ hết vì mọi query list đều lọc `deleted_at__isnull=True`.

**Điểm mấu chốt: `pg_stat_statements` đã bật ở đợt 07-24, nên giờ trả lời được câu này bằng
số thay vì suy luận.** Chạy trên prod:

```sql
SELECT relname, indexrelname, idx_scan, pg_size_pretty(pg_relation_size(indexrelid))
FROM pg_stat_user_indexes
WHERE relname = 'orders'
ORDER BY idx_scan ASC, pg_relation_size(indexrelid) DESC;
```

`idx_scan = 0` sau ≥1 tuần uptime (kiểm `pg_postmaster_start_time()` để chắc số liệu không
vừa bị reset) là bằng chứng đủ để drop. Drop bằng `DROP INDEX CONCURRENTLY` trong migration
`atomic = False`, mỗi đợt vài cái, đo lại tốc độ ghi giữa các đợt.

Lợi ích: giảm write amplification trên đúng bảng nóng nhất, và giảm thời gian
`kv-reconcile` — job dài nhất hệ thống.

### C2. Ba code path còn tìm kiếm bằng `icontains` → seq-scan toàn bảng, dù cách làm đúng đã có sẵn trong repo

`OrderRepository.list_orders` đã xử lý đúng và ghi lại lý do rất rõ
(`apps/orders/repositories.py:339-343`):

> *Code search via `__contains` (case-sensitive LIKE → uses the bare-column trigram GIN
> indexes; `__icontains` wraps in UPPER() and seq-scans 500k rows).*

Nhưng ba chỗ khác vẫn làm đúng điều cảnh báo đó:

| Vị trí | Vấn đề |
|---|---|
| `apps/orders/repositories.py:959-967` — `list_kiotviet_jobs` | 8 điều kiện `icontains` nối OR, 7 trong số đó xuyên qua join `order__…`. Không index nào phục vụ được → seq-scan `orders` + `order_kiotviet_jobs` cho mỗi lần gõ ô tìm kiếm trang Trạng thái đồng bộ. |
| `apps/orders/repositories.py:910` — `list_history_by_phone` | `Q(phone__icontains=phone_normalized)`. Nghịch lý: tham số đã là số đã chuẩn hoá nhưng lại đem so vào cột `phone` **thô** bằng `icontains`. Vừa seq-scan, vừa dễ sai kết quả (số lưu dạng `+84…` sẽ không khớp). |
| `apps/customers/customer_resale_service.py:125-133` | 8 điều kiện `icontains` OR trên `name`/`phone`/`shipping_code`/`order_code`/`invoice_code` + 3 field customer. Đúng trang đã có tài liệu sự cố hiệu năng (`docs/resale-tracking-performance.md`). |

**Sửa** — áp đúng khuôn của `list_orders`:
- Code (`order_code`, `invoice_code`, `shipping_code`, `old_invoice_code`) → `__contains`,
  yêu cầu ≥ 3 ký tự.
- Tên → giữ `__icontains` (đã có functional index `order_name_upper_trgm_idx`).
- Điện thoại → chuyển sang `phone_normalized__contains` (có `order_phone_norm_trgm_idx`);
  ở `list_history_by_phone` thì dùng thẳng `phone_normalized=` (exact, B-tree).
- Với `list_kiotviet_jobs`: OR xuyên join không có index tốt — nên tách thành `id__in` từ
  một subquery đã lọc trên `orders` trước, đúng kỹ thuật materialize id mà `list_orders` đã
  dùng và ghi chú ở dòng 363-369.

Đo trước/sau bằng `EXPLAIN (ANALYZE, BUFFERS)`; đây là các query người dùng gõ liên tục nên
cải thiện cảm nhận được ngay.

### C3. `/api/orders/metrics` chỉ được chặn ở nginx của host, không chặn ở nginx trong container

Endpoint là `AllowAny` + `authentication_classes([])` (`metrics_view.py:17-19`) — đúng quy
ước Prometheus, nhưng nghĩa là **toàn bộ việc chặn nằm ở tầng nginx**.

- `nginx/customer.delax.vn.conf.prod-template:40-46` — có `location = /api/orders/metrics`
  với allow/deny nội bộ. ✅
- `nginx/conf.d/default.conf` — chỉ chặn `/nginx_status` (dòng 66-74). `/api/orders/metrics`
  rơi vào `location /api/` (dòng 32) và được proxy thẳng ra backend. ❌

Nếu bất kỳ môi trường nào (staging, một lần dựng lại, hay ai đó publish port container) dùng
file thứ hai, metrics lộ ra internet: số đơn theo kênh, tỉ lệ lỗi, thông tin nội bộ về
lưu lượng và cấu trúc kinh doanh.

**Sửa** — hai lớp:
1. Thêm block allow/deny giống template vào `nginx/conf.d/default.conf` (5 dòng).
2. Bổ sung phòng thủ ở tầng app: cho phép `METRICS_ALLOW_TOKEN` (header) hoặc chốt theo IP
   nội bộ, để bảo mật không phụ thuộc hoàn toàn vào việc chọn đúng file nginx.

---

## 🟡 TRUNG BÌNH

### T1. N+1 ở danh sách User — `DepartmentSerializer` dùng `.filter()` nên bỏ qua prefetch cache

`apps/users/serializers.py:30-31`:

```python
def get_channel_ids(self, obj: Department) -> list[int]:
    return list(obj.channels.filter(deleted_at__isnull=True).values_list("id", flat=True))
```

`UserSerializer` nhúng `department = DepartmentSerializer(read_only=True)`, và
`admin_list_users` chỉ `select_related("department")` — **không** prefetch
`department__channels` (`apps/users/services.py:28-41`). `.filter()` trên related manager
luôn tạo query mới, kể cả khi đã prefetch. → **1 query mỗi user** trên trang Users.

Đáng chú ý: chính file này, dòng 118-121, ghi đúng nguyên tắc *"sort/lọc bằng Python thay vì
`.filter()`/`.order_by()` mới (sẽ query lại DB từng user)"* — `UserSerializer` tuân thủ, còn
`DepartmentSerializer` thì không.

**Sửa:** thêm `Prefetch("department__channels", queryset=Channel.objects.filter(deleted_at__isnull=True))`
vào `admin_list_users`, đổi serializer sang `[c.id for c in obj.channels.all()]`. Chốt bằng
`assertNumQueries` theo đúng kiểu so-sánh-hai-cỡ-trang mà đợt 07-24 đã dùng cho `/api/orders`
(3 user vs 15 user phải ra cùng số query).

Ba chỗ tương tự, tải nhẹ hơn nên ưu tiên thấp: `apps/users/serializers.py:257,264,281`
(`RolePermission/RoleSectionScope/RoleFieldRule.objects.filter(role=obj)` — N+1 theo số
role trên trang Roles), `apps/facebook_ads/serializers.py:28`, `apps/google_ads/serializers.py:31`.

### T2. Sentry đang nhận stack trace đã minify — báo lỗi frontend không truy được về dòng code

`frontend/vite.config.ts:70` đặt `sourcemap: false`, và `package.json` không có
`@sentry/vite-plugin`. `Sentry.init` (`frontend/src/main.tsx:20-24`) cũng không truyền
`release`. Hệ quả: mọi lỗi production về Sentry chỉ có frame kiểu
`vendor-react-a1b2c3.js:1:48219` — biết là có lỗi, không biết ở đâu. Công cụ đã trả tiền
nhưng chỉ dùng được một nửa.

**Sửa:** `sourcemap: "hidden"` (sinh map nhưng **không** gắn comment vào bundle, nên không
serve công khai), thêm `@sentry/vite-plugin` upload map lúc build kèm `release` =
commit SHA, và loại `*.map` khỏi image nginx frontend. Chi phí ~1h, đổi lấy toàn bộ giá trị
của lớp error tracking.

### T3. `list_kiotviet_jobs` hardcode scope "của tôi", bỏ qua RBAC — kèm tham số chết

`order_crud_view.py:214-217`:

```python
channel_codes = None
if not resolve_effective_permissions(request.user).is_superuser:
    filters["requested_by"] = request.user.id
queryset = OrderRepository.list_kiotviet_jobs(filters, channel_codes=channel_codes)
```

`channel_codes` được khai rồi truyền vào nhưng **luôn là `None`** — tham số scope của
repository không bao giờ được dùng. Thay vào đó view ép `requested_by = user.id`: mọi
non-superuser chỉ thấy job **do chính mình** yêu cầu, bất kể RBAC cấp scope
`department` hay `all`. Không phải lỗ hổng (siết chặt hơn mức cần), nhưng là **nghiệp vụ
sai**: trưởng nhóm không xem được job đồng bộ của nhân viên mình để xử lý sự cố.

**Sửa:** dùng `resolved_channel_codes(user, "orders")` như các endpoint khác — `own` → giữ
`requested_by`; `channel`/`department` → truyền `channel_codes`; `all`/superuser → `None`.

### T4. `verify_channel_token` là dead code có logic fail-open

`apps/orders/application/services/order_service_helpers.py:228-233`:

```python
active_tokens = ChannelApiToken.objects.filter(channel=channel, is_active=True)...
if not active_tokens:
    return True          # ← kênh chưa cấu hình token ⇒ chấp nhận MỌI token
```

Grep toàn repo: **không có call site nào**. Hiện tại vô hại, nhưng đây đúng là loại hàm mà
người sau sẽ tìm thấy và gọi lại — tên hàm nghe như đang xác thực, còn hành vi thật là mở
cửa. Đối chiếu: endpoint ingest thật (`order_ingest_views.py:62-70`) fail-**closed** đúng
bài và ghi rõ lý do.

**Sửa:** xoá hàm. Nếu còn cần về sau thì viết lại fail-closed (không token active → từ chối).

### T5. Queue `default` đang gánh nhóm task lệch nhau về thời lượng

`CELERY_TASK_ROUTES` (`config/settings_parts/celery.py:27-50`) route đúng 20 task nặng sang
`kiotviet` / `orders` / `lark_noti` / `audit`, và routes áp cho cả beat nên 39 entry beat
không cần khai queue — phần này **đúng**, worker cũng đã tách 2 container theo queue.

Vấn đề còn lại là **19 task không nằm trong routes** nên rơi vào `default`, và chúng lệch
nhau rất xa về thời lượng:

- Dài, I/O-bound: `callcenter.transcribe_pending_call_records`,
  `callcenter.extract_pending_call_insights` (gọi Gemini),
  `callcenter.sync_zenify_call_histories`.
- Nặng CPU/DB: `customers.warm_report_cache`, `customers.rebuild_segment_snapshot`
  (đúng nhóm đã gây `report-precompute-timeout-2026-07-22`).
- Ngắn nhưng cần đúng giờ: `orders.sweep_floating_pool`,
  `orders.sweep_lead_assignment`, `contact_flow.sweep_operation_entry`, các job purge.

Worker `default` chạy `--concurrency=4`. Ba task transcribe/LLM cùng lúc + một
`warm_report_cache` là đủ chiếm hết 4 slot; các sweep điều phối lead — thứ người dùng cảm
nhận được ngay — phải chờ.

**Sửa:** thêm queue `slow` cho nhóm callcenter + report precompute, route trong
`CELERY_TASK_ROUTES`, chạy bằng worker thứ ba `--concurrency=2`. Đúng khuôn đã làm cho
`worker-kiotviet` nên rủi ro thấp; cần thêm queue vào `-Q` của compose khi deploy.

### T6. Nợ cũ chưa xử lý — trạng thái không đổi so với 07-23

Đã verify vẫn còn nguyên, giữ lại để không rơi khỏi tầm ngắm:

| Mục | Trạng thái hôm nay |
|---|---|
| T3 cũ — `orders` có cả `views.py` phẳng lẫn `transport/` | Còn; `.importlinter` treo 25 entry nợ, nặng nhất `orders → kiotviet` |
| T4 cũ — 108 migration của `orders` | Còn; chờ người vận hành xác nhận `showmigrations` prod trước khi squash |
| T5 cũ — file FE quá lớn | Còn, **không phình thêm**: `use-orders-data.ts` 2786, `OrdersPage.tsx` 2087, `OrderFormDialog.tsx` 2073, `DashboardSections.tsx` 1443 |
| T6 cũ — `dev` / `master` phân kỳ | Còn; vẫn là quyết định của con người, không phải của code |

---

## Đề xuất tech stack

**Không đổi gì.** Django 6 + DRF + Celery + Postgres + React 19/Vite 7 vẫn là lựa chọn đúng,
và không finding nào ở trên phát sinh từ giới hạn của stack — cả 9 mục đều là chỗ chưa áp
nhất quán một cách làm đã có trong repo. Đổi stack sẽ không sửa được cái nào trong số đó.

Kết luận từ `tech-stack-evolution-2026.md` và audit 07-23 (không tách microservices, không
rewrite DRF→ninja, không Next.js, không Kafka, chưa cần read replica) giữ nguyên hiệu lực.

Hai thành phần đã thêm ở 07-24 cần **hoàn tất vận hành** thay vì thêm cái mới:

| Thành phần | Trạng thái | Việc còn lại |
|---|---|---|
| `pg_stat_statements` | Đã cấu hình trong compose | `CREATE EXTENSION` + restart theo runbook — **C1 phụ thuộc trực tiếp vào việc này** |
| PgBouncer | Đã có, opt-in (`DB_PGBOUNCER=0`) | Validate staging rồi cutover; sau đó bật lại `CONN_MAX_AGE` |

Điều duy nhất nên **thêm** cho giai đoạn 100–150 nhân sự, và chỉ sau khi C1 có số liệu:
một **test hiệu năng hồi quy** cho 5–6 endpoint nóng nhất (orders list, resale tracking,
kiotviet jobs, customer report). Đợt này lộ ra rằng tối ưu bị mất dần theo từng code path
mới; `assertNumQueries` chặn được N+1 nhưng không chặn được seq-scan. Rẻ hơn nhiều so với
bất kỳ hạ tầng mới nào.

---

## Thứ tự thực hiện đề xuất

| # | Việc | Rủi ro | Ước lượng |
|---|---|---|---|
| 1 | **N1** — scope + cap `ids` cho 4 endpoint in ấn, kèm test quyền | Thấp | 3–4h |
| 2 | **C3** — chặn `/metrics` ở `conf.d/default.conf` + phòng thủ tầng app | Thấp | 1h |
| 3 | **T4** — xoá `verify_channel_token` | Thấp | 15' |
| 4 | **C2** — `icontains` → `__contains`/`phone_normalized` ở 3 code path | Thấp–TB | 1 ngày + đo EXPLAIN |
| 5 | **T1** — sửa N+1 `DepartmentSerializer` + `assertNumQueries` | Thấp | 2h |
| 6 | **T2** — sourcemap cho Sentry | Thấp | 1h |
| 7 | **C1** — đo `pg_stat_user_indexes`, drop index chết theo đợt | TB (cần số liệu prod trước) | 0,5 ngày + theo dõi |
| 8 | **T3** — `list_kiotviet_jobs` dùng RBAC scope | Thấp | 2h |
| 9 | **T5** — tách queue `slow` | TB (đụng compose khi deploy) | 0,5 ngày |
| 10 | **T6** — nợ cũ (squash migration, tách file FE, dứt điểm `transport/`) | TB | theo sprint |

> N1 nên vào nhánh riêng, merge trước mọi thứ khác: nó là lỗ hổng dữ liệu khách hàng đang mở
> trên prod, và bản sửa không đụng gì tới các mục còn lại.

---

## Phụ lục A — verify các mục đợt 07-23 (đã đóng trong code)

| Mục | Kiểm chứng hôm nay |
|---|---|
| N0/N1 — CI thật | `.gitlab-ci.yml` có 10 job (lint · lint-imports · test · openapi-contract · pip-audit · frontend lint/test/openapi-types/build · docker manual); `pytest apps -q --junitxml` ở stage test |
| N2 — vòng đời JWT | `docker-compose.prod.yml:66-67` → `JWT_ACCESS_MINUTES:-60`, `JWT_REFRESH_DAYS:-14`; `.env.example` khớp |
| C1 — pg_stat_statements | `docker-compose.prod.yml:194-197` có `shared_preload_libraries`, `max=5000`, `track=top`, `track_utility=off` |
| C2 — PgBouncer | `docker-compose.prod.yml:238-241` service opt-in qua `profiles: ["pgbouncer"]` |
| T2 — ruff | `backend/pyproject.toml` có ruleset `E4,E7,E9,F,B,C4,DTZ,LOG,PIE,RET,SIM,T20,UP` |
| T7 — CI frontend | Comment sai đã xoá; `engines.node` ghim `>=22 <24` |

**Vẫn cần người vận hành** (không làm được từ code, giữ nguyên từ 07-24): gắn GitLab Runner ·
`CREATE EXTENSION pg_stat_statements` + restart Postgres · validate rồi cutover PgBouncer.
(Mục "xác nhận `showmigrations` prod trước khi squash" đã **hết hiệu lực** — xem T6a ở nhật ký.)

---

# Nhật ký thực hiện — 2026-07-26 → 27

Nhánh `fix/audit-2026-07-26-followups`. Plan: `docs/superpowers/plans/2026-07-26-audit-followups.md`.
Baseline đầu phiên trên `master`: **2213 passed, 0 failed** (886 s) — nên mọi test đỏ xuất hiện
sau đó đều thuộc về đợt này, không có vùng xám.

## Đã đóng

| Mục | Thay đổi | Bằng chứng đo được |
|---|---|---|
| **N1** | 4 endpoint in ấn: scope bản ghi + cap 200 id qua `ListField(max_length=…)` | Bỏ nhánh `scope_empty` → user CHANNEL không kênh in được **cả 3 đơn** (rò rỉ hiện rõ), phục hồi → xanh |
| **C3** | `nginx/conf.d/default.conf` chặn `/api/orders/metrics`; thêm `METRICS_ALLOW_TOKEN` làm lớp hai ở app | `nginx -t` OK; 4 test guard |
| **T4** | Xoá `verify_channel_token` (dead code fail-open) | grep xác nhận 0 call site trước khi xoá |
| **C2** | `icontains` → `__contains`/`phone_normalized` ở 3 code path | `EXPLAIN (ANALYZE,BUFFERS)` trên 80k đơn/20k job: query job **84 ms → 4,2 ms**. Với `enable_seqscan=off`, dạng `contains` chuyển sang BitmapOr trên 5 trigram index (31 ms) còn `icontains` **vẫn Seq Scan dù bị phạt cost 1e10** (348 ms) |
| **T1** | Prefetch `department__channels` + 3 quan hệ của Role; serializer đọc qua `.all()` | Trước fix đo được **3 user = 7 query, 15 user = 19 query** (đúng +1/user) |
| **T2** | `sourcemap: "hidden"` + `@sentry/vite-plugin` + `release = $CI_COMMIT_SHA` | 97 `.map` sinh ra; `grep sourceMappingURL dist/assets/*.js` **rỗng**; `.js` phục vụ người dùng **+0 byte** |
| **C1** | Drop 2 index chết, **thêm** 1 index thiếu, thêm command `report_index_usage` | Rollback chứng minh trên DB sạch `0109→0108→0109`; `pg_index WHERE NOT indisvalid` rỗng ở cả 3 mốc |
| **T3** | `list_kiotviet_jobs` dùng `order_record_scope_q` thay vì hardcode `requested_by` | Test scope `department` thấy job của nhân viên; `own` chỉ thấy của mình |
| **T5** | Queue `slow` + `worker-slow` (prod concurrency 2, staging 1) | 3 `compose config` OK; test guard bắt mọi beat task mới chưa quyết định queue |
| **T6c** | **10/10 app** có `views.py` là facade — nợ phân tầng trả hết | Mỗi app test trước = sau; `diff schema` **trống tuyệt đối** ở cả 3 đợt |
| **T6b** | `use-orders-data.ts` **2786 → 742** (−73 %); `OrdersPage` 2087 → 1055; `OrderFormDialog` 2073 → 1199; `DashboardSections` 1443 → 12 (barrel + 11 file) | Public API chứng minh không đổi bằng **TypeScript Compiler API** (liệt kê mọi export + mọi prop kèm type rồi diff): 147/147, 219/219, 21/21, 48/48 — **0 khác biệt** |

## Bốn lỗ hổng PHÁT SINH trong lúc sửa — nặng hơn cái ban đầu

Review N1 lộ ra cả một họ lỗi cùng loại ở **phía ghi**:

| Mục | Nội dung |
|---|---|
| **1B** 🔴 | 6 bulk write action (`bulk_delete_orders`, `transfer_landing_orders`, `restore_cancelled_orders`, `cancel_kiotviet_orders`, `run_kiotviet_action`×2) query thẳng theo `ids` client gửi. Vì `can_edit_order` trả `True` **vô điều kiện** cho scope `OWN`, user scope OWN **xoá/huỷ/chuyển/push được đơn của bất kỳ ai**. `transfer_landing_orders` còn trả `name`+`phone` khách trong `invalid_orders` → **PII leak** chỉ với quyền `landing-orders:view` |
| **1E** 🔴 | `update_order_inline` (GET/PATCH/**DELETE**), `duplicate_order`, `list_order_history(?phone=)` cùng lỗ. Giải bằng `order_record_scope_q(user, sections)` — Q-union theo section: mỗi section = (điều kiện nhận dạng đơn) AND (scope của user ở section đó) |
| **1C** | `can_edit_order` docstring ghi *"mirror đúng read-scope"* nhưng nhánh DEPARTMENT chỉ đọc `UserChannel`, còn read-scope DEPARTMENT mở kênh qua `UserDepartment` → **user department-scope không sửa được đơn của phòng ban mình** |
| **1F** | `close_case` cùng lỗ; `customer_orders` (trả PII) đã siết; in phiếu giao **ghi** `is_delivery_invoice_printed` nhưng chỉ cần `:view` → đổi sang yêu cầu `{section}:edit`; thêm throttle `order_print` 120/min **theo user** (không theo IP: kho ngồi chung NAT) |

Một quyết định **cố ý không siết**: `customer_order_aggregate` chỉ trả số đếm và là tín hiệu
cảnh báo liên kênh ("khách này từng bom hàng của người khác") — siết theo OWN sẽ luôn trả 0
đúng lúc cần cảnh báo nhất. Có test khoá quyết định này.

## Ba tiền đề của audit hoá ra SAI — sửa lại tại đây

1. **T6a (squash migration) — không có việc để làm.** `orders` **đã được squash từ 2026-07-03**
   (`0001_squashed_0089`, commit `7361843d`). Con số "108 migration replay" đếm *số file*, không
   phải số node: đo thật chỉ **20 node**. Dư địa còn lại đo A/B 4 lượt cho **47,0 s → 48,0 s** —
   trong sai số, dấu không nhất quán; `squashmigrations` báo *"No optimizations possible"*. Thêm
   hai rào chắn thật: `0090→0108` gặp `CircularDependencyError` qua `customers.0028`, và
   `0099→0108` phá `test_return_slip_migration.py` (test đáng giữ). **Đã revert bản squash thử
   nghiệm** và ghi lại lý do ở `docs/runbooks/2026-07-26-squash-orders-migrations.md` để đợt audit
   sau không đề xuất lần thứ ba.
2. **C1 — chỉ 2/4 index thực sự chết, không phải 4.** `order_phone_trgm_idx` và `order_phone_idx`
   **vẫn có người dùng**: C2 cố ý giữ cột `phone` thô làm nhánh dự phòng (đơn có SĐT không chuẩn
   hoá được sẽ có `phone_normalized` rỗng và biến mất khỏi tìm kiếm), và `lead_filter_service`
   chạy `filter(phone__in=…)` ở **mỗi lần ingest lead**. Bù lại phát hiện chiều ngược: `external_order_id`
   **không có index nào** trong khi được tìm kiếm → đã thêm trigram GIN.
3. **T6c — `orders` không hề "nửa vời".** Audit 07-23 ghi *"`orders` có cả `views.py` phẳng lẫn
   `transport/`"*; thực tế `views.py` của nó là facade thuần 143 dòng, đã xong từ trước. Việc thật
   nằm ở 7 app khác (nay đã xong cả, cộng `facebook_ads` phát hiện thêm).

## Hai bug test đỏ theo GIỜ CHẠY — đã sửa

Cả hai đều xanh ở baseline 16:00 và đỏ khi chạy lại sau nửa đêm; nếu không bắt sẽ thành CI đỏ
ngẫu nhiên mỗi đêm:

- `test_contact_list_operation::test_bucket_today` — fixture dùng cứng `now + 30 phút`; chạy sau
  23h30 thì `due_at` rơi sang hôm sau → bucket `upcoming`.
- `test_dashboard_summary_api::…filter_by_date_range` — `timezone.now().date()` lấy ngày theo
  **UTC** còn endpoint diễn giải `date_from`/`date_to` theo giờ **local (+07)**; trong khung
  00:00–07:00 giờ VN hai cách lệch nhau một ngày.

## Vòng review cuối — 3 lỗ hổng CÙNG LỚP còn sót, và 1 hồi quy do chính đợt này gây ra

Một review độc lập cuối nhánh (soi riêng phần phân quyền, có PoC gọi request thật) bác bỏ kết
luận "N1 đã đóng". Ba endpoint cùng lớp vẫn mở, đều đã được vá sau đó:

| | Endpoint | Khai thác đã chứng minh |
|---|---|---|
| **C1** | `POST /api/orders/<id>/contact-log` | Sale scope `own` **archive được đơn bất kỳ** + chèn log liên hệ giả. Ghi đúng cột mà `close_case` ghi — `close_case` đã vá, cái này thì không |
| **C2** | `GET /api/orders/<id>/journey-events` | Đọc `note` + `payload_json` (chứa **transcript cuộc gọi**) của đơn bất kỳ. Gate dùng `order_channel_codes_scope`, mà hàm này **trả `None` khi scope = OWN** → điều kiện luôn False. Helper này đã bị **xoá** vì hết call site và vì tên của nó mời gọi dùng sai |
| **C3** | `GET /api/orders/search?q=` | Không scope gì; trả `name`/`phone` không mask/`address`/số tiền → oracle liệt kê toàn bộ CSDL khách |

**Hồi quy do chính đợt này tạo ra (I1):** `list_kiotviet_jobs` trước đây chặn cứng
`requested_by = user.id`. Mục T3 thay bằng scope RBAC (ý định đúng: trưởng nhóm cần xem job của
nhân viên), nhưng với scope `ALL` thì `order_record_scope_q` trả `Q()` rỗng → không lọc gì, trong
khi serializer trả `order_phone` **thô** và không áp `field_policy`. Đã vá bằng mask số điện thoại
— không dùng `field_policy` vì nó khớp theo **tên khoá output**, mà serializer này phát `order_phone`
chứ không phải `phone`, nên một rule `{"orders": {"phone": "hidden"}}` sẽ **im lặng không có tác dụng**.

**Fail-open ở chính hạ tầng vừa dựng (I4):** `_SECTION_ORDER_IDENTITY_Q.get(section, Q())` —
section chưa khai báo → `Q()` → với scope ALL thì mở toàn bộ đơn công ty. Đã đổi thành lookup
**fail-closed** (`ImproperlyConfigured`), và validate **trước** vòng lặp vì bên trong nó bị ba
short-circuit khác nuốt mất.

**Tám test là tautology (I8):** các ca "user CHANNEL scope + 0 kênh" xanh **kể cả khi xoá sạch
logic scope**, vì `can_edit_order` chặn trước. Đã thay bằng **một** unit test gọi thẳng
`order_record_scope_q`, phủ cả 8 ca và miễn nhiễm với gate backstop.

## Lưới chặn cấu trúc — để lỗ hổng không tái sinh

Gốc rễ vẫn còn: `can_edit_order` fail-open cho `OWN`/`ALL`, nên mọi bản vá đều nằm ở **từng view**
— một view mới quên gọi `scoped_order_or_error` là mở lại lỗ hổng, không có gì báo động.

`backend/apps/orders/tests/test_record_scope_coverage.py` duyệt URLconf, lấy source view bằng AST,
đi theo lời gọi helper cùng module, và bắt mọi view nhận `order_id`/`ids` không đi qua record scope.
Danh sách trắng là **tài liệu sống**: view mới không nằm trong đó và không gọi helper → test đỏ.

Nó **tự bắt được 4 endpoint chưa ai vá** ngay khi vừa dựng: `dispatch_batch` và 3 endpoint
resale-tracking (`test_resale_channel_scope_api.py` đã ghi nhận vấn đề này từ trước mà chưa ai xử
lý). Cả 4 đã vá xong, `_KNOWN_GAPS` **rỗng**.

Một quyết định **cố ý không siết**, có 6 test khoá kèm lập luận: phiếu bảo hành. Lỗ hổng có thật và
khai thác được, nhưng `Role.scope_default` mặc định là `OWN` và **nhân viên bảo hành không bao giờ
là `Order.staff`** → siết sẽ khiến họ không tạo được phiếu nào. Có test chứng minh bằng code rằng
siết sẽ khớp 0 đơn.

## ⚠️ Điều quan trọng nhất về giá trị thực tế của cả đợt này

`apps/users/migrations/0015_backfill_roles_from_legacy.py` seed **mọi role phòng ban với
`scope_default = ALL`** (chỉ phòng ADS có override `orders → channel`), và `0022` set
`landing-orders = ALL` cho mọi role. **Với scope ALL, `order_record_scope_q` trả `Q()` → không siết
gì cả.**

Nghĩa là: toàn bộ giá trị bảo mật của đợt này chỉ hiện thực **sau khi cấu hình lại scope cho từng
role**. Deploy rồi tuyên bố "N1 đã đóng" là kết luận sai. Việc cần làm là một buổi rà soát RBAC:
quyết định từng role thực sự cần scope nào, rồi hạ `scope_default` xuống mức tối thiểu đủ dùng.

## Cần người vận hành trước/khi deploy

1. **Rà role prod chỉ có `{section}:view` mà thực tế đang in phiếu giao** — sẽ bị 403 sau đợt này
   (hệ quả có chủ ý của việc chặn ghi-sau-quyền-đọc). Cấp `{section}:edit` cho họ.
2. **Đổi hành vi tìm kiếm cần thông báo:** từ khoá 1–2 ký tự nay trả rỗng; `external_order_id`
   phân biệt hoa/thường; từ khoá có chữ cái không còn khớp theo số điện thoại.
3. **Deploy `worker-slow`:** chỉ `docker compose -f docker-compose.prod.yml up -d worker-slow`
   (không recreate cả stack), và **phải restart `beat`** — `CELERY_TASK_ROUTES` áp ở phía producer.
   `scripts/deploy-prod.sh` + `staging-refresh.sh` đã được cập nhật danh sách service.
4. **Sentry:** khai `SENTRY_AUTH_TOKEN` (masked+protected, scope `project:releases`), `SENTRY_ORG`,
   `SENTRY_PROJECT` ở Settings → CI/CD → Variables. Nhớ set cả `VITE_SENTRY_DSN` — thiếu nó thì
   `Sentry.init` không chạy và cả chuỗi sourcemap vô nghĩa, mà build vẫn xanh nên rất dễ quên.
5. **Migration `0109`** dùng `DROP INDEX CONCURRENTLY`: không khoá bảng nhưng chạy lâu trên bảng
   lớn, và fail thì để lại index `INVALID` — cách phát hiện/dọn ở
   `docs/runbooks/2026-07-26-drop-dead-indexes.md`.
6. **Smoke test form tạo/sửa đơn** trước prod: React Compiler nay **thực sự compile**
   `OrderFormDialog` (trước đó bail out hoàn toàn), số `react.memo_cache_sentinel` đi từ 7 → 59.

7. **Rà `Role.scope_default` của các role kho** — tài khoản kho để `shipments` scope OWN sẽ không
   xuất được đơn của sale khác sau khi `dispatch_batch` được siết.
8. **Buổi rà soát RBAC** (xem cảnh báo ở mục trên) — đây là việc quyết định giá trị thật của cả đợt.

## Còn nợ có chủ ý (nêu rõ thay vì im lặng)

- `OrderFormDialog` dừng ở 1199 dòng: ba block lớn nhất đều render `react-select`, mà
  `eslint.config.js` allowlist import đó **theo từng đường dẫn file**. Tách tiếp buộc phải sửa
  allowlist — không làm vòng bằng cách re-export react-select từ file được phép.
- `status_idx` và `order_created_at_idx` *nghi* đã chết nhưng **chưa drop**: cần số `idx_scan`
  thật từ prod sau ≥1 tuần uptime. Ngưỡng quyết định đã ghi trong runbook.
- `CELERY_TASK_TIME_LIMIT` toàn cục là 30 s trong khi 5 task nhóm `slow` override 360–900 s —
  task nặng mới nào quên override sẽ bị cắt ở giây 30.
- `can_edit_order` **vẫn fail-open** cho `OWN`/`ALL`. Không sửa trong đợt này vì chữ ký hàm không
  nhận `order` nên không thể biết owner; sửa đúng phải đổi chữ ký và chạm cả
  `_build_order_list_context` (gọi mỗi dòng danh sách để sinh cờ `can_edit` cho FE) — rủi ro siết
  oan cao. Lưới chặn cấu trúc đang gánh vai trò đó. Nên xử lý dứt điểm ở sprint sau.
- `GET /api/orders/search` vẫn trả `phone`/`address`/số tiền **thô** cho đơn *trong* scope. Có nên
  mask không là quyết định nghiệp vụ (CSKH bảo hành có cần số đầy đủ?), chưa đổi.
- `POST /api/warranty/claims` gate bằng `warranty:view` (quyền ĐỌC) dù nó GHI — nên là
  `warranty:create`. Chưa đổi vì role prod nằm trong DB, không có seed để đối chiếu; đổi mù có thể
  khoá tổ bảo hành.
- `update_customer_resale_note` khoá theo `customer_id` chứ không phải `order_id` nên lưới cấu trúc
  không bắt — residual gap đã ghi nhận trong code.

## Bẫy môi trường đáng ghi cho lần sau

- Chạy nhiều test run song song trên **cùng `REDIS_CACHE_DB`** làm cache RBAC bị bump giữa hai
  lần đo → test đếm query đỏ ngẫu nhiên. Cấp `REDIS_CACHE_DB` riêng, không chỉ `POSTGRES_DB`.
- Quá nhiều run đồng thời gây `OperationalError: canceling statement due to statement timeout`
  ngay ở bước flush DB — nhiễu hạ tầng, không phải regression.
- `npm run test` qua Git Bash trên máy dev **nuốt output vitest và trả exit code 1 giả**; phải
  gọi thẳng binary qua PowerShell.
- `squashmigrations` **không** mang `atomic` từ migration gốc sang file squash — app nào dùng
  `CREATE INDEX CONCURRENTLY` sẽ fail khi migrate từ số 0 nếu quên thêm `atomic = False`.
