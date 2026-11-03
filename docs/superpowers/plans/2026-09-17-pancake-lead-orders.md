# Lead Pancake → tạo đơn + gán sale theo nhân viên Pancake — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mỗi lead Pancake mới tạo một đơn `order_source=messenger` trong `orders` (đi qua `OrderService.create_order_from_landing` → tự khớp khách, contact-flow, Lark, đẩy Foxia theo đường đơn), gán sale theo bảng ánh xạ "tên nhân viên Pancake → user"; bỏ đẩy lead trực tiếp sang Foxia.

**Architecture:** `apps.pancake` gọi xuống `apps.orders.services.OrderService.create_order_from_landing(payload)` (tầng dưới, hợp lệ với `.importlinter`). Bảng mới `pancake_staff_maps` (tên Pancake → user), poll tự thêm tên mới gặp. `PancakeLead` thêm FK `order` + trạng thái `order_created/order_failed`. Task `retry_lead_order` thay `repush_lead`. FE: bảng "Nhân viên Pancake" trong Cài đặt → Pancake; trang Lead Pancake cột "Đơn hàng".

**Tech Stack:** Django 5 + DRF, Celery, Postgres, React + TS + TanStack Query + vitest.

**Spec:** quyết định chủ dự án 2026-09-17 (trong chat, ghi lại ở §9 của `docs/superpowers/specs/2026-09-16-pancake-phone-leads-design.md` — Task 6 bổ sung): (1) tạo đơn rỗng như landing, `order_source=messenger`; (2) gán sale theo nhân viên Pancake qua bảng map; chưa map → đơn không có sale → chia lead tự động; (3) dedup giữ (kênh, sđt); (4) không đẩy lead trực tiếp nữa; (5) khách không tên → `name` = sđt.

## Global Constraints
- Worktree `/home/desi/desi-order-pancake`, nhánh `feat/pancake-lead-orders` (từ `origin/master` 9b061b24). Không `git add -A`.
- Payload tạo đơn: `{"name": customer_name or phone, "phone": phone, "channel_id": page.channel_id, "order_source": "messenger", "url_page": f"https://pancake.vn/{page.pancake_page_id}?c_id={conversation_id}", "staff_id": <id|None>}`; KHÔNG gửi `product_id`, `raw_address`, tiền. `staff_id` chỉ đưa vào khi map tới user hợp lệ theo `resolve_ingest_staff_id(user.username)` (active, chưa xoá mềm, không role hệ thống); không hợp lệ → bỏ `staff_id` (chia tự động).
- Lead đã có `order` không tạo lại. Lead cũ đã `push_status=ok` (đẩy Foxia trực tiếp trước 17/09) KHÔNG tạo đơn.
- `PancakeLead.push_status` giữ nguyên giá trị cũ để đọc lịch sử; hai trạng thái mới `order_created`, `order_failed`.
- Bình luận tiếng Việt. Test theo khối lệnh dưới.

**Backend test env** (như plan trước; DB `desi_test_pancake` đã có → `--reuse-db`, sau khi thêm migration lần đầu `--create-db`):
```bash
cd /home/desi/desi-order-pancake/backend
docker start desi-test-pg desi-test-redis 2>/dev/null
export POSTGRES_DB=desi_test_pancake POSTGRES_USER=desi POSTGRES_PASSWORD=ci-postgres-pass \
  POSTGRES_HOST=localhost POSTGRES_PORT=5433 REDIS_HOST=localhost REDIS_PORT=6380 REDIS_PASSWORD= \
  APP_ENV=development DJANGO_DEBUG=0 LOG_FORMAT=text ZENIFY_DOMAIN= ZENIFY_API_KEY= ZENIFY_API_SECRET= \
  ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
PYTEST=/home/desi/desi-order/.venv/bin/pytest; PY=/home/desi/desi-order/.venv/bin/python
```
**Frontend:** `docker run --rm -v /home/desi:/home/desi -w /home/desi/desi-order-pancake/frontend node:22-alpine <npx vitest run … | npm run typecheck | npm run lint>` — một lệnh một lúc; 3 lỗi typecheck sẵn ở `src/features/contact-flow/**` là môi trường.

---

### Task 1: Model — `PancakeStaffMap`, `PancakeLead.order`, trạng thái mới

**Files:** Modify `backend/apps/pancake/models.py`, `admin.py`; Create `migrations/0002_…` (makemigrations); Test `backend/apps/pancake/tests/test_pancake_models.py` (thêm).

**Produces:**
```python
class PancakeStaffMap(models.Model):
    pancake_name = CharField(255, unique)          # tên nhân viên Pancake như poll thấy
    user = FK(settings.AUTH_USER_MODEL, SET_NULL, null, blank, related_name="pancake_staff_maps")
    last_seen_at = DateTimeField(null, blank)
    created_at, updated_at
    db_table = "pancake_staff_maps"; ordering = ["pancake_name"]
PancakePushStatus += ORDER_CREATED = "order_created", "Đã tạo đơn"; ORDER_FAILED = "order_failed", "Tạo đơn lỗi"
PancakeLead.order = FK("orders.Order", SET_NULL, null, blank, related_name="pancake_leads")
```
Docstring `PancakeLead`: từ 2026-09-17 lead mới tạo ĐƠN (đơn tự đẩy Foxia); các cột `push_*`/`cohoi_id` chỉ còn cho lead cũ.

- [ ] Test: tạo `PancakeStaffMap(pancake_name="Nhung HN")` unique (IntegrityError khi trùng); `PancakeLead(order=order)` lưu được, `order.pancake_leads.count()==1` (dựng Order như `apps/foxia/tests/test_foxia_order_push.py::_order` — Channel + Customer). Trạng thái mới có trong `PancakePushStatus.values`.
- [ ] `makemigrations pancake --name staff_map_and_lead_order`; admin: đăng ký `PancakeStaffMap` (list_display name/user/last_seen_at, `autocomplete_fields=["user"]` nếu UserAdmin có search_fields, không thì raw_id_fields); `PancakeLeadAdmin.list_display` thêm `order`.
- [ ] `$PYTEST apps/pancake/tests/test_pancake_models.py -q --create-db` → pass; `lint-imports` kept.
- [ ] Commit `feat(pancake): PancakeStaffMap + PancakeLead.order + trạng thái order_created/order_failed`.

### Task 2: `lead_order_service.py` + poll tạo đơn thay vì đẩy lead

**Files:** Create `backend/apps/pancake/application/services/lead_order_service.py`; Modify `poll_service.py`, `tasks.py`; Test `tests/test_pancake_lead_order_service.py` (mới), sửa `tests/test_pancake_poll_service.py`, `tests/test_pancake_tasks.py`.

**Produces:**
```python
# lead_order_service.py
def resolve_staff_id_for(assignee_name: str, now) -> int | None
    # "" → None. get_or_create PancakeStaffMap(pancake_name) ; cập nhật last_seen_at=now.
    # map.user None → None; else resolve_ingest_staff_id(map.user.username) (import từ
    # apps.orders.application.services.order_ingest_service) → id hoặc None.
def build_order_payload(lead: PancakeLead, staff_id: int | None) -> dict   # theo Global Constraints
def create_order_for_lead(lead: PancakeLead) -> PancakeLead
    # Điều kiện: lead.order_id is None và push_status != OK (lead cũ đã đẩy Foxia) — nếu không thoả → trả lead nguyên.
    # try: order = OrderService.create_order_from_landing(payload)   (from apps.orders.services import OrderService)
    #      lead.order=order; push_status=ORDER_CREATED; error=""; pushed_at=now
    # except Exception as exc (noqa BLE001): push_status=ORDER_FAILED; error=f"{type}: {exc}"[:2000]; pushed_at=now
    # save(update_fields=[...]); metrics.pancake_leads_total.labels(push_status=...).inc(); log (không PII)
```
`poll_service.py`: trong `_handle_conversation` thay `push_lead(lead)` bằng `create_order_for_lead(lead)`; `PollResult.pushed` đổi tên `orders_created` (đếm ORDER_CREATED); log "Pancake poll ok: … orders=…". Giữ `build_lead_payload`/`push_lead` (không còn được gọi) → XOÁ cùng test của chúng; `_lookup_source_id` xoá. `apps.pancake` không import `foxia` nữa? (`push_customer` không dùng) — xoá import; `.importlinter` tầng `pancake` vẫn đúng (không cần sửa).
`tasks.py`: `repush_lead` → đổi tên `retry_lead_order(lead_id)` (`name="apps.pancake.tasks.retry_lead_order"`, queue giữ `CELERY_FOXIA_PUSH_QUEUE`, `max_retries=0`): lead không có `order` và `push_status` ∈ {PENDING, FAILED, SKIPPED_NO_MAPPING, ORDER_FAILED} → `create_order_for_lead` → `"created"` / `"failed"` theo kết quả; ngược lại `"not_eligible"`; không có → `"missing"`. Cập nhật `config/settings_parts/celery.py` route (tên mới) và `apps/common/tests/test_celery_routes.py` không cần đổi (route có tên mới).

- [ ] Tests (fixtures: Channel, PancakePage đã gán kênh, lead pending; `settings.FOXIA_PUSH_ENABLED=False` để không dispatch):
  - `create_order_for_lead` tạo Order: `order_source=="messenger"`, `channel==page.channel`, `name`, `phone`, `staff_id is None`, `url_page` đúng; lead `order_id`, `push_status=="order_created"`; Customer được tạo/khớp theo sđt.
  - tên khách rỗng → `order.name == lead.phone`.
  - `PancakeStaffMap(pancake_name="Nhung HN", user=sale_active)` → `order.staff_id == sale.id`; map tới user role hệ thống (`make_admin`) → `staff_id None`; map chưa có → tạo dòng map `user=None`, `last_seen_at` set, `staff_id None`.
  - lead đã `push_status=OK` (lead cũ) → không tạo đơn, không đổi.
  - lead đã có `order` → không tạo lại.
  - `create_order_from_landing` raise (patch `apps.pancake.application.services.lead_order_service.OrderService.create_order_from_landing`, side_effect=ValueError("Kênh bán không tồn tại")) → `order_failed`, `error` chứa "ValueError", không raise.
  - poll: `test_tao_lead_va_day_foxia` → đổi thành `test_tao_lead_va_tao_don` (patch `apps.pancake.application.services.poll_service.create_order_for_lead` hoặc để chạy thật với FOXIA_PUSH_ENABLED=False và assert Order tồn tại); các test cũ patch `PUSH_MODULE` → sửa sang patch `create_order_for_lead` hoặc bỏ nếu chỉ kiểm đẩy Foxia; `result.orders_created`.
  - tasks: `retry_lead_order` với `order_failed` → `"created"`; với `order_created` → `"not_eligible"`; route/beat test cập nhật tên.
- [ ] `$PYTEST apps/pancake apps/common/tests/test_celery_routes.py -q --reuse-db`; `lint-imports`.
- [ ] Commit `feat(pancake): lead mới tạo đơn messenger (qua create_order_from_landing) + gán sale theo map; bỏ đẩy lead trực tiếp`.

### Task 3: API — staff maps + lead list/retry

**Files:** Modify `backend/apps/pancake/transport/views.py`, `urls.py`; Test `tests/test_pancake_api.py`.

**Produces:**
```
GET  /api/pancake/staff-maps        (superuser) → {items:[{id, pancake_name, user_id, user_username, user_display_name, last_seen_at}]}
PUT  /api/pancake/staff-maps        (superuser) body {items:[{id, user_id|null}]} → như GET. user_id phải là user active, chưa xoá mềm, không role hệ thống (dùng resolve_ingest_staff_id(username) để kiểm) → 400 nếu không hợp lệ.
GET  /api/pancake/leads             LeadOut thêm: order_id (int|null). Lọc push_status nhận thêm order_created/order_failed.
POST /api/pancake/leads/<id>/retry-order  (pancake-leads:view) → 202 {"status":"queued"} khi eligible (không có order và status ∈ {pending, failed, skipped_no_mapping, order_failed}); 409 khác; 404. Xoá endpoint repush.
```
`@extend_schema` cho endpoint mới (như các view hiện có) — `grep 'Error \[' | grep apps/pancake` phải rỗng; sinh lại `schema.yaml` + `npm run gen:api`.
- [ ] Tests: GET/PUT staff-maps (superuser ok, viewer 403; PUT user hệ thống → 400; PUT null bỏ gán); leads có `order_id`; retry-order 202/409/404 + patch `views.retry_lead_order.delay`; `repush` không còn (404).
- [ ] Commit `feat(pancake): API ánh xạ nhân viên Pancake → sale; retry-order thay repush; lead có order_id` (+ schema).

### Task 4: FE — bảng Nhân viên Pancake + trang lead cột Đơn hàng

**Files:** Modify `frontend/src/lib/api/pancake.ts` (types `PancakeStaffMap`, `listStaffMaps`, `saveStaffMaps`, `retryLeadOrder`; bỏ `repushLead`; `PancakeLead.order_id`; `PancakePushStatus` thêm 2 giá trị), `features/pancake/queries.ts` (`usePancakeStaffMaps`, key `pancakeKeys.staffMaps`), `i18n/messages/pancake.ts` (keys: `pancake.staff.title` "Nhân viên Pancake", `.description`, `.col.name`, `.col.user` "Sale nội bộ", `.col.lastSeen`, `.noUser` "— Chưa gán —", `.saved`, `.saveFailed`, `.empty`; `pancake.leads.col.order` "Đơn hàng", `.status.order_created` "Đã tạo đơn", `.status.order_failed` "Tạo đơn lỗi", `.retryOrder` "Tạo đơn", `.retryQueued`, `.retryFailed`; vi+en), `pages/PancakeSettingsPage.tsx` (Card thứ 3 "Nhân viên Pancake": bảng tên · `<select aria-label="Sale nội bộ: <tên>">` options từ `listChannelStaff()` (`@/lib/api/channels`, hiển thị `display_name || username`) · lần gặp cuối; đổi select → gọi `saveStaffMaps([{id,user_id}])` ngay, toast, invalidate `pancakeKeys.all`), `pages/PancakeLeadsPage.tsx` (cột cuối "Đơn hàng": có `order_id` → `<Link to={orderDetailPath(order_id)}>#id</Link>` (`orderDetailPath` từ `@/lib/routes`; `Link` từ react-router) + badge trạng thái; không có → badge + nút "Tạo đơn" khi status ∈ {pending, failed, skipped_no_mapping, order_failed}); tests 2 file cập nhật (`PancakeSettingsPage.test.tsx`: mock `@/lib/api/pancake` thêm `listStaffMaps/saveStaffMaps`, mock `listChannelStaff`; test chọn sale gọi `saveStaffMaps([{id:1,user_id:7}])`; `PancakeLeadsPage.test.tsx`: cột cuối "Đơn hàng", lead có `order_id` hiện link `#123`, lead `order_failed` có nút "Tạo đơn" gọi `retryLeadOrder(2)`).
  Lưu ý test Leads page dùng `<Link>` → render trong `MemoryRouter` (xem test khác trong repo dùng `MemoryRouter`, vd `grep -rl MemoryRouter src | head -1`).
- [ ] vitest `src/features/pancake` pass; typecheck; lint.
- [ ] Commit `feat(pancake-ui): bảng ánh xạ nhân viên Pancake → sale; trang lead cột Đơn hàng + Tạo đơn`.

### Task 5: Kiểm chứng + docs
- [ ] Spec §9 thêm (h) 2026-09-17: lead mới tạo đơn messenger, gán sale theo `pancake_staff_maps`, bỏ đẩy lead trực tiếp, lead không tên → name=sđt. Runbook: thêm §2.4 "Cài đặt → Pancake → Nhân viên Pancake: sau lượt poll đầu, gán sale cho từng tên"; §4: nút "Tạo đơn" thay "Đẩy lại"; Foxia của lead xem ở đơn (`foxia_order_pushes`).
- [ ] `$PYTEST apps/pancake apps/foxia apps/orders/tests/test_ingest_api.py apps/common/tests/test_celery_routes.py -q --reuse-db`; `lint-imports`; `makemigrations --check`; spectacular pancake grep rỗng + `diff -q schema.yaml`; FE vitest pancake + modules/messages; typecheck; lint.
- [ ] Commit docs. (Push/MR: controller làm sau khi chủ dự án xác nhận.)
