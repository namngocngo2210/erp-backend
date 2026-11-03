# Sale Contacts Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trang "Danh sách contact" mục Sale — 1 dòng = 1 khách hàng gộp từ đơn luồng sale landing, với chips tác nghiệp, Kết quả inline, lịch sử liên hệ và ghi chú/log cuộc gọi.

**Architecture:** List = aggregate query (DISTINCT ON customer) trên `orders` + derive "tác nghiệp cần" bằng rule thuần; KHÔNG có bảng state materialized. "Kết quả" ghi qua PATCH order (crm) hiện có vào đơn mới nhất. Lịch sử = UNION của `OrderJourneyEvent` (đã log sales_status_changed sẵn) + bảng mới `ContactActivity` append-only (chỉ kind `call`/`note`).

**Tech Stack:** Django + DRF + drf-spectacular (backend, apps/orders), PostgreSQL (DISTINCT ON), React + TS + shadcn/ui + React Query, openapi-typescript.

## Global Constraints

- Worktree: `C:/Users/MYPC/Desktop/workspace/desi-order-wt-sale-contacts`, branch `feat/sale-contacts-page`. MỌI thay đổi và commit thực hiện trong worktree này. Commit message tiếng Anh, ngắn, KHÔNG có dòng Co-Authored-By.
- Spec gốc: `docs/sale-contacts-page-design.md` (cùng repo).
- Backend test (Windows): chạy trong docker. Tìm compose file local (`docker-compose*.yml` ở repo root) và service test (`web-test` / container `desi-order-web-test`). Khi chạy full-suite hoặc nhiều file test, LUÔN thêm `-e POSTGRES_DB=desi_contacts_<số-ngẫu-nhiên>` (DB test dùng chung đã hỏng). Test đơn lẻ: `docker compose -f <compose-file> run --rm -e POSTGRES_DB=desi_ct1 web-test pytest apps/orders/tests/<file> -v` (điều chỉnh theo tên service thật). Cần `REDIS_PASSWORD=testpassword` nếu test chạm Redis.
- Frontend test: `cd frontend && npm ci` (worktree chưa có node_modules — KHÔNG junction/symlink từ repo chính, vitest sẽ hỏng) rồi `npm test -- --run <pattern>`. Baseline fail sẵn trên master: return-rate BE, OrderFormDialog + 2 mobile-viewport FE — không phải lỗi của bạn, đừng sửa.
- FE code mới: CẤM `react-select` (dùng `components/ui` select/searchable-select), GET bằng `useQuery` + `queries.ts` per-feature, API module typed từ `src/api/schema.d.ts`. Light theme, lucide-react.
- Sau khi regen `frontend/src/api/schema.d.ts` LUÔN chạy `npx prettier --write src/api/schema.d.ts` (nếu không sẽ ra diff giả ~28k dòng).
- Python: tuân theo layering `transport/views` → `application/services` → models. View mỏng, logic ở service, rule thuần ở module riêng.
- Mỗi task: tự verify tên symbol/file thật trước khi sửa (codebase lớn — nếu tên trong plan lệch với thực tế, theo THỰC TẾ và ghi chú lại trong báo cáo).

---

### Task 1: RBAC section `contacts`

**Files:**
- Modify: `backend/apps/users/rbac_catalog.py` (thêm section `contacts` với actions `view`, `edit` — đọc file trước để theo đúng cấu trúc catalog hiện có)
- Modify: `backend/apps/users/models.py` (tìm `VALID_SECTIONS` — thêm `"contacts"`)
- Test: `backend/apps/users/tests/test_contacts_section.py`

**Interfaces:**
- Produces: permission codes `contacts:view`, `contacts:edit` seed được qua cơ chế seed hiện có; `"contacts"` hợp lệ trong `VALID_SECTIONS` và `allowed_sections` của `/me`.
- Tham khảo: `backend/apps/users/tests/test_outside_orders_section.py` là test mẫu cho việc thêm section (đọc nó trước, mirror cấu trúc).

- [ ] **Step 1: Đọc pattern** — Đọc `backend/apps/users/rbac_catalog.py`, `backend/apps/users/tests/test_outside_orders_section.py`, và chỗ khai báo `VALID_SECTIONS` trong `backend/apps/users/models.py`.
- [ ] **Step 2: Viết failing test** — mirror test_outside_orders_section.py: assert `"contacts"` nằm trong VALID_SECTIONS; assert seed tạo permission `contacts:view` và `contacts:edit` (dùng đúng helper seed mà test mẫu dùng).
- [ ] **Step 3: Chạy test → FAIL** (section chưa tồn tại).
- [ ] **Step 4: Thêm `contacts` vào rbac_catalog + VALID_SECTIONS** theo đúng format các section hiện có (label tiếng Việt: "Danh sách contact").
- [ ] **Step 5: Chạy test → PASS.**
- [ ] **Step 6: Commit** — `feat: add contacts RBAC section`

---

### Task 2: Rule "tác nghiệp cần" (`contact_rules.py`)

**Files:**
- Create: `backend/apps/orders/domain/contact_rules.py`
- Test: `backend/apps/orders/tests/test_contact_rules.py`

**Interfaces:**
- Produces:
  - `ACTION_KEYS: tuple[str, ...]` = `("new_customer", "new_lead", "call_2", "call_3", "recheck_phone", "nurture", "follow_up", "awaiting_delivery", "post_sale", "none")`
  - `derive_action_needed(sales_status_code: str | None, order_status: int, is_new_customer: bool) -> str`
- Consumes: `OrderStatus` từ `apps.orders.models` (DELIVERED_SUCCESS=3, CANCELLED=6).

- [ ] **Step 1: Viết failing tests** (mỗi nhánh 1 test):

```python
import pytest

from apps.orders.domain.contact_rules import ACTION_KEYS, derive_action_needed


@pytest.mark.parametrize(
    "code,status,is_new,expected",
    [
        # Đơn hủy → không tác nghiệp, bất kể trạng thái sale
        ("no_answer_1", 6, True, "none"),
        # Đơn giao thành công → chăm sóc sau bán, bất kể trạng thái sale
        (None, 3, True, "post_sale"),
        ("closed", 3, False, "post_sale"),
        # Chưa có trạng thái sale
        (None, 0, True, "new_customer"),
        ("", 0, True, "new_customer"),
        (None, 0, False, "new_lead"),
        # Chuỗi gọi
        ("no_answer_1", 0, True, "call_2"),
        ("no_answer_2", 1, False, "call_3"),
        ("no_answer_3", 0, True, "recheck_phone"),
        ("unreachable", 0, True, "recheck_phone"),
        ("wrong_number", 0, False, "recheck_phone"),
        # Chăm sóc
        ("need_more_consulting", 0, False, "nurture"),
        ("scheduled", 0, True, "follow_up"),
        ("call_back_later", 1, False, "follow_up"),
        ("closed_scheduled_date", 0, False, "follow_up"),
        # Đã chốt, chờ giao
        ("closed", 2, False, "awaiting_delivery"),
        ("closed_express", 0, True, "awaiting_delivery"),
        ("created_order", 1, False, "awaiting_delivery"),
        # Trạng thái kết thúc / rác
        ("not_buying", 0, False, "none"),
        ("spam", 0, True, "none"),
        ("duplicate", 0, False, "none"),
        ("customer_bombed", 0, False, "none"),
        ("cancelled", 0, False, "none"),
        ("warranty", 0, False, "none"),
        # Code lạ (catalog động có thể thêm) → none, không crash
        ("some_future_code", 0, True, "none"),
    ],
)
def test_derive_action_needed(code, status, is_new, expected):
    assert derive_action_needed(code, status, is_new) == expected


def test_action_keys_stable():
    assert "new_customer" in ACTION_KEYS and "none" in ACTION_KEYS
```

- [ ] **Step 2: Chạy test → FAIL** (module chưa tồn tại).
- [ ] **Step 3: Implement:**

```python
"""Rule suy diễn "tác nghiệp cần" cho trang Danh sách contact.

Điểm chân lý duy nhất — đổi mapping chỉ sửa file này. Input là trạng thái của
ĐƠN MỚI NHẤT của khách (sales_status_code + Order.status) và loại khách.
Xem docs/sale-contacts-page-design.md §5.
"""

from apps.orders.models import OrderStatus

ACTION_KEYS: tuple[str, ...] = (
    "new_customer",
    "new_lead",
    "call_2",
    "call_3",
    "recheck_phone",
    "nurture",
    "follow_up",
    "awaiting_delivery",
    "post_sale",
    "none",
)

# sales_status_code → action key (các code không cần diễn giải theo Order.status)
_CODE_TO_ACTION = {
    "no_answer_1": "call_2",
    "no_answer_2": "call_3",
    "no_answer_3": "recheck_phone",
    "unreachable": "recheck_phone",
    "wrong_number": "recheck_phone",
    "need_more_consulting": "nurture",
    "scheduled": "follow_up",
    "call_back_later": "follow_up",
    "closed_scheduled_date": "follow_up",
    "closed": "awaiting_delivery",
    "closed_express": "awaiting_delivery",
    "created_order": "awaiting_delivery",
}


def derive_action_needed(
    sales_status_code: str | None, order_status: int, is_new_customer: bool
) -> str:
    """Suy ra tác nghiệp cần từ trạng thái đơn mới nhất. Không raise với code lạ."""
    if order_status == OrderStatus.CANCELLED:
        return "none"
    if order_status == OrderStatus.DELIVERED_SUCCESS:
        return "post_sale"
    code = (sales_status_code or "").strip()
    if not code:
        return "new_customer" if is_new_customer else "new_lead"
    return _CODE_TO_ACTION.get(code, "none")
```

- [ ] **Step 4: Chạy test → PASS.**
- [ ] **Step 5: Commit** — `feat: contact action-needed rules`

---

### Task 3: Model `ContactActivity` + migration

**Files:**
- Modify: `backend/apps/customers/models.py` (thêm model cuối file — đặt ở app customers vì trục là Customer; import Order bằng string ref `"orders.Order"` để tránh vòng import)
- Create: migration qua `makemigrations customers`
- Test: `backend/apps/customers/tests/test_contact_activity_model.py`

**Interfaces:**
- Produces model:

```python
class ContactActivityKind(models.TextChoices):
    CALL = "call", "Cuộc gọi"
    NOTE = "note", "Ghi chú"


class ContactActivity(models.Model):
    """Log liên hệ append-only cấp khách hàng (trang Danh sách contact).

    CHỈ lưu call/note. Lịch sử đổi trạng thái sale KHÔNG lưu ở đây —
    đọc từ OrderJourneyEvent (event_type=sales_status_changed) đã có sẵn.
    """

    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.CASCADE, related_name="contact_activities"
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="contact_activities",
    )
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="contact_activities",
    )
    kind = models.CharField(max_length=16, choices=ContactActivityKind.choices)
    note = models.TextField(blank=True, default="")
    call_meta = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "contact_activities"
        indexes = [
            models.Index(fields=["customer", "-created_at"], name="contact_act_cust_created_idx"),
            models.Index(fields=["staff", "created_at"], name="contact_act_staff_idx"),
        ]
```

(`settings` import đã có trong file? — kiểm tra, nếu chưa thì `from django.conf import settings`.)

- [ ] **Step 1: Viết failing test** — tạo Customer + ContactActivity kind=note, assert lưu/đọc được, ordering theo -created_at qua index queryset; kind=call với call_meta dict round-trip.
- [ ] **Step 2: Chạy test → FAIL** (model chưa tồn tại).
- [ ] **Step 3: Thêm model + chạy `makemigrations customers`** (trong docker test service với `python manage.py makemigrations customers`). Đọc migration sinh ra, kiểm tra tên index ≤ 30 ký tự.
- [ ] **Step 4: Chạy test → PASS.**
- [ ] **Step 5: Commit** — `feat: ContactActivity append-only log model`

---

### Task 4: Service danh sách contact (`ContactListService`)

**Files:**
- Create: `backend/apps/orders/application/services/contact_list_service.py`
- Test: `backend/apps/orders/tests/test_contact_list_service.py`

**Interfaces:**
- Consumes: `derive_action_needed` (Task 2), `ContactActivity` (Task 3), `OrderJourneyEvent` (model sẵn có trong apps/orders — VERIFY tên/field: order, event_type, created_at), `Order`, `Customer`.
- Consumes: hằng loại nguồn sàn trong `apps/orders/models.py` (comment ~dòng 97: "Nguồn đơn sàn TMĐT — không thuộc luồng sale Landing"; VERIFY tên constant thật, ví dụ `MARKETPLACE_ORDER_SOURCES`) + loại `koc`.
- Produces:

```python
class ContactListService:
    @staticmethod
    def list_contacts(
        *,
        user,                    # request.user — áp scope OWN/ALL qua scope_for("contacts")
        date_from=None,          # datetime.date | None — lọc created_at đơn MỚI NHẤT
        date_to=None,
        q: str = "",
        channel_ids: list[int] | None = None,
        order_sources: list[str] | None = None,
        staff_ids: list[int] | None = None,
        action_needed: list[str] | None = None,
        interaction: str = "",   # "" | "none" | "recent"
        customer_type: str = "", # "" | "new" | "old"
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """→ {"items": [dict], "total": int, "action_counts": {key: {"pending": n, "total": n}}}"""
```

Item dict shape (hợp đồng với Task 5 serializer và FE):

```python
{
    "customer_id": int, "customer_code": str, "full_name": str,
    "phone_raw": str, "is_new_customer": bool,
    "latest_order": {
        "id": int, "created_at": iso_str, "order_source": str,
        "channel_id": int | None, "channel_name": str,
        "status": int, "sales_status_code": str | None,
        "next_followup_at": iso_str | None,
        "staff_id": int | None, "staff_name": str,
    },
    "order_count": int,
    "action_needed": str,       # ACTION_KEYS
    "waiting_since": iso_str | None,   # max(latest_order.created_at, last_activity_at) nếu action != none
    "last_activity_at": iso_str | None,
}
```

**Thuật toán (viết đúng như sau):**

1. Base queryset: `Order.objects.filter(deleted_at__isnull=True, customer_id__isnull=False).exclude(order_source__in=<marketplace+koc>)`. Scope: nếu `scope_for("contacts") == "own"` (dùng `resolve_effective_permissions(user).scope_for("contacts")`, so sánh với `DataScope.OWN` — import từ apps.users.models) → thêm `.filter(staff_id=user.id)`.
2. Nếu có date range → `.filter(created_at__date__gte=date_from)` / `__lte=date_to`.
3. Nếu `q` → filter theo customer: `.filter(Q(customer__full_name__icontains=q) | Q(customer__phone_normalized__icontains=q) | Q(customer__phone_raw__icontains=q))`.
4. channel/source/staff filter trực tiếp trên Order.
5. Đơn mới nhất mỗi khách: `base.order_by("customer_id", "-created_at", "-id").distinct("customer_id")` → `.values("id", "customer_id", "created_at", "status", "assessment__sales_status_code", "customer__is_new_customer")` → **materialize thành list Python** (bài học seq-scan: không dùng lazy subquery trong bước sau).
6. Last-activity per customer (1 query): `ContactActivity.objects.filter(customer_id__in=[...]).values("customer_id").annotate(last=Max("created_at"))` → dict. Last journey event per latest-order (1 query): `OrderJourneyEvent.objects.filter(order_id__in=[...], event_type__in=["sales_status_changed", "assessment_created"]).values("order_id").annotate(last=Max("created_at"))` → dict. `last_activity_at = max` của 2 nguồn.
7. Với mỗi row: `action = derive_action_needed(...)`; `pending = last_activity_at is None or last_activity_at < row.created_at` (chưa tương tác từ khi data mới về).
8. `action_counts`: đếm total + pending theo action key trên TOÀN tập (trước khi lọc theo `action_needed`), thêm key `"all"` (tổng/pending toàn bộ, loại `none` khỏi pending). Sau đó áp filter `action_needed`, `interaction` ("none" = pending, "recent" = not pending), `customer_type`.
9. Sort: `created_at` đơn mới nhất DESC. `total` = len sau filter. Slice `[offset:offset+limit]` → fetch full rows: `Order.objects.filter(id__in=page_ids).select_related("customer", "assessment", "channel", "staff")` → build item dicts (giữ thứ tự theo page_ids).
10. `order_count` per customer trên trang (1 query aggregate với customer_id__in của TRANG, không toàn tập).

- [ ] **Step 1: Đọc VERIFY** — mở `backend/apps/orders/models.py` tìm: tên constant nguồn sàn (~dòng 97), model `OrderJourneyEvent` (tên field created_at/event_type), field `Order.staff`, `Order.channel` (tên quan hệ + field name hiển thị của Channel — VERIFY `channel.name`). Mở `apps/users/rbac.py` + `apps/users/models.py` xác nhận import `DataScope`, `resolve_effective_permissions`.
- [ ] **Step 2: Viết failing tests** (pytest + django_db). Dựng fixture: 2 customer (1 new, 1 old), mỗi customer 2 đơn landing (đơn sau mới hơn), 1 đơn shopee (phải bị loại), 1 đơn của staff khác (test scope OWN). Case:
  - `test_one_row_per_customer_latest_order` — trả đúng 1 row/customer, latest_order.id = đơn mới nhất.
  - `test_marketplace_and_koc_excluded`.
  - `test_action_counts_pending_vs_total` — customer chưa có activity nào → pending; thêm ContactActivity note sau created_at đơn mới nhất → pending giảm.
  - `test_scope_own_filters_by_staff` — user OWN chỉ thấy contact có đơn mình phụ trách (dùng helper tạo role/permission như test RBAC mẫu `backend/apps/users/test_helpers.py` — đọc trước).
  - `test_filter_by_action_needed_and_interaction`.
  - `test_pagination_total`.
- [ ] **Step 3: Chạy test → FAIL.**
- [ ] **Step 4: Implement service** theo thuật toán trên (giữ hàm nhỏ: `_latest_order_rows`, `_last_activity_map`, `_build_items`).
- [ ] **Step 5: Chạy test → PASS.**
- [ ] **Step 6: Commit** — `feat: contact list service (latest-order-per-customer + action counts)`

---

### Task 5: API `GET /api/sale/contacts`

**Files:**
- Create: `backend/apps/orders/transport/views/order_views_impl/contact_list_view.py`
- Modify: file urls của orders (tìm nơi khai báo route `lead-assignment` trong `backend/apps/orders/` — thêm route cạnh đó) 
- Create: `backend/apps/orders/transport/serializers/contact_serializers.py` (serializer response cho extend_schema)
- Test: `backend/apps/orders/tests/test_contact_list_api.py`

**Interfaces:**
- Consumes: `ContactListService.list_contacts` (Task 4).
- Produces: endpoint `GET /api/sale/contacts` (DRF `@api_view(["GET"])` + `@permission_classes([IsAuthenticated, require_section("contacts")])` + `@extend_schema(operation_id="sale_contacts_list", parameters=[...], responses=...)`). Query params: `date_from`, `date_to` (YYYY-MM-DD), `q`, `channel` (multi, int), `order_source` (multi), `staff` (multi, int), `action_needed` (multi), `interaction`, `customer_type`, `limit` (default 20, max 100), `offset`.
- Response serializers đặt tên: `ContactListResponseSerializer` { items: ContactItemSerializer(many), total: int, action_counts: DictField } — theo pattern serializer response của `AdminUserListResponseSerializer` (đọc `backend/apps/users/serializers.py` mục đó) và một view mẫu trong `order_views_impl/` (đọc `lead_assignment_view.py` để mirror cách khai extend_schema + parse query params).

- [ ] **Step 1: Đọc pattern** — `backend/apps/orders/transport/views/order_views_impl/lead_assignment_view.py` (hoặc file view impl bất kỳ) + urls registration của nó.
- [ ] **Step 2: Viết failing API tests** — dùng APIClient pattern của `backend/apps/orders/tests/test_order_list_filters_api.py` (đọc trước): login user có quyền `contacts:view` scope ALL → GET trả 200 với items/total/action_counts; user không quyền → 403; limit clamp về 100; date filter hoạt động.
- [ ] **Step 3: Chạy test → FAIL.**
- [ ] **Step 4: Implement view** (mỏng: parse params → gọi service → Response). Parse multi param bằng `request.query_params.getlist("channel")`.
- [ ] **Step 5: Chạy test → PASS.**
- [ ] **Step 6: Regen schema.yaml** — tìm cách project regen `backend/schema.yaml` (grep "schema.yaml" trong scripts/Makefile/README; thường là `python manage.py spectacular --file schema.yaml`). Chạy và commit cùng code.
- [ ] **Step 7: Commit** — `feat: sale contacts list API`

---

### Task 6: API lịch sử liên hệ + ghi chú/log gọi

**Files:**
- Create: `backend/apps/orders/application/services/contact_activity_service.py`
- Create: `backend/apps/orders/transport/views/order_views_impl/contact_activity_view.py`
- Modify: urls (cạnh route Task 5)
- Modify: `backend/apps/orders/transport/serializers/contact_serializers.py`
- Modify: `docs/sale-contacts-page-design.md` (§3.1–3.2: ContactActivity chỉ còn call/note; lịch sử status đọc từ OrderJourneyEvent; bỏ backfill; cập nhật bảng tiến độ S1–S2)
- Test: `backend/apps/orders/tests/test_contact_activity_api.py`

**Interfaces:**
- Produces:
  - `GET /api/sale/contacts/<customer_id>/activities?limit&offset` → `{"items": [...], "total": int}`. Item: `{"type": "status_change"|"call"|"note", "at": iso, "actor_name": str, "note": str, "from_code": str|None, "to_code": str|None, "from_label": str|None, "to_label": str|None, "order_id": int|None, "call_meta": dict|None}`. Nguồn: UNION OrderJourneyEvent (event_type in `["assessment_created","sales_status_changed"]`, join orders theo customer_id, loại đơn marketplace/koc) + ContactActivity (kind call/note). Sort `at` DESC, merge trong Python sau khi materialize 2 list (mỗi list đã limit thô `offset+limit` phần tử), total = count 2 nguồn cộng lại.
  - `POST /api/sale/contacts/<customer_id>/activities` body `{"kind": "note"|"call", "note": str?, "order_id": int?, "call_meta": {...}?}` → 201 item vừa tạo (shape như trên). Validate: kind bắt buộc thuộc {note, call}; note ≤ 4000 ký tự; kind=note thì note bắt buộc không rỗng; order_id (nếu có) phải là đơn của đúng customer đó; staff = request.user. Quyền: GET = `contacts:view`, POST = `require_permission("contacts:view")` là đủ (sale nào thấy contact đều ghi chú được) — dùng `require_section("contacts")`.
  - Label cho from/to: dùng `SALES_STATUS_LABELS` từ `apps/orders/crm_catalog.py` (VERIFY tên), fallback code thô.
  - Scope OWN: user OWN chỉ xem/ghi activity của customer mà đơn mới nhất thuộc mình — reuse check qua ContactListService hoặc query đơn giản: tồn tại Order của customer với staff=user (nếu scope own). Trả 404 nếu ngoài scope (không lộ tồn tại).

- [ ] **Step 1: Viết failing tests** — tạo customer + đơn + đổi sales_status qua `OrderServiceHelpers._upsert_order_assessment` (hoặc PATCH order API nếu tiện hơn — mirror test hiện có về assessment, tìm bằng grep `sales_status_code` trong backend tests) để sinh journey event thật; assert GET trả item type=status_change với from/to label; POST note → 201, xuất hiện trong GET; POST kind lạ → 400; kind=note thiếu note → 400; order_id của customer khác → 400; user OWN không phụ trách → 404.
- [ ] **Step 2: Chạy test → FAIL.**
- [ ] **Step 3: Implement service + view.** Service tách 2 hàm: `list_activities(customer, limit, offset) -> dict`, `create_activity(customer, actor, payload) -> dict`.
- [ ] **Step 4: Chạy test → PASS.**
- [ ] **Step 5: Cập nhật design doc** (§3.1, §3.2, §7 tiến độ S1 ✅ S2 ✅ + ghi chú §8: "phát hiện OrderJourneyEvent đã log status change → bỏ kind status_change + backfill").
- [ ] **Step 6: Regen schema.yaml, commit** — `feat: contact activities API (timeline + note/call log)`

---

### Task 7: FE typed API + queries + section routing

**Files:**
- Modify: `frontend/src/api/schema.d.ts` (regen `npm run gen:api` — VERIFY tên script trong frontend/package.json; sau đó `npx prettier --write src/api/schema.d.ts`)
- Create: `frontend/src/lib/api/contacts.ts`
- Create: `frontend/src/features/contacts/queries.ts`
- Modify: `frontend/src/lib/routes.ts` (AppSection + SECTION_TO_PATH `contacts: "/sales/contacts"`)
- Modify: `frontend/src/app/modules.ts` (module `sales`: thêm `{ key: "contacts", labelKey: "topnav.tabs.contacts", icon: Contact }` sau `landing-orders`; import `Contact` đã có sẵn trong file)
- Modify: file i18n chứa `topnav.tabs.*` (grep `topnav.tabs.landingOrders` trong `frontend/src/i18n/` — thêm `contacts: "Danh sách contact"` cả vi lẫn en nếu có 2 file)
- Modify: `frontend/src/App.tsx` (render branch cho section `"contacts"` — tìm nơi các section khác render page component, ví dụ nơi render `ResaleTrackingPage`, thêm case tương tự; trang tự quản data bằng React Query nên KHÔNG cần thêm gì vào `loadSectionData` ngoài nhánh no-op nếu pattern yêu cầu)
- Test: `frontend/src/lib/routes.test.ts` (thêm case) — VERIFY file test routes hiện có

**Interfaces:**
- Produces `lib/api/contacts.ts` (types derive từ `components["schemas"]` sau regen — nếu spectacular đặt tên khác, map theo `paths`):

```typescript
import { fetchJson } from "@/services/http";
import type { paths } from "@/api/schema";  // VERIFY: file orders.ts hiện có import kiểu gì — mirror y hệt

export type ContactListParams = {
  date_from?: string; date_to?: string; q?: string;
  channel?: number[]; order_source?: string[]; staff?: number[];
  action_needed?: string[]; interaction?: string; customer_type?: string;
  limit: number; offset: number;
};
export type ContactListResponse =
  paths["/api/sale/contacts"]["get"]["responses"]["200"]["content"]["application/json"];
export type ContactItem = ContactListResponse["items"][number];
export type ContactActivityItem = /* derive tương tự từ paths activities GET */;

export async function getContacts(params: ContactListParams, init?: { signal?: AbortSignal }): Promise<ContactListResponse> { /* build URLSearchParams (mảng → append lặp), fetchJson */ }
export async function getContactActivities(customerId: number, params: { limit: number; offset: number }): Promise<{ items: ContactActivityItem[]; total: number }> { ... }
export async function createContactActivity(customerId: number, payload: { kind: "note" | "call"; note?: string; order_id?: number; call_meta?: Record<string, unknown> }): Promise<ContactActivityItem> { /* POST */ }
```

- Produces `features/contacts/queries.ts` (mirror `features/customers/queries.ts`):

```typescript
export const contactKeys = {
  all: ["contacts"] as const,
  list: (params: ContactListParams) => ["contacts", "list", params] as const,
  activities: (customerId: number) => ["contacts", "activities", customerId] as const,
};
export function useContacts(params: ContactListParams) { /* useQuery + keepPreviousData + queryFn ({signal}) */ }
export function useContactActivities(customerId: number | null) { /* enabled: customerId !== null */ }
export function useCreateContactActivity() { /* useMutation; onSuccess: invalidate activities(customerId) + contacts list */ }
```

- [ ] **Step 1: Regen schema** — trong worktree: backend schema.yaml đã regen ở Task 5/6; `cd frontend && npm ci && npm run gen:api && npx prettier --write src/api/schema.d.ts`. VERIFY diff schema.d.ts chỉ chứa phần contacts mới.
- [ ] **Step 2: Failing test routes** — thêm case vào `frontend/src/lib/routes.test.ts`: `sectionFromPath("/sales/contacts") === "contacts"`, `pathFromSection("contacts") === "/sales/contacts"`. Chạy → FAIL.
- [ ] **Step 3: Implement routes + modules + i18n + App.tsx branch + lib/api + queries.** Chú ý `parseOrdersView` regex có nhóm `(?:orders|landing-orders|...)` — `/sales/contacts` KHÔNG được match nhầm (kiểm tra: regex yêu cầu `\d+` cuối nên an toàn; thêm test nếu rẻ).
- [ ] **Step 4: Chạy test routes + `npm run lint` scoped file mới → PASS.** Kiểm tra nav test hiện có (`src/app/__tests__/nav-layout.test.ts`, `modules.test.ts`) không gãy.
- [ ] **Step 5: Commit** — `feat: contacts section routing + typed API + queries`

---

### Task 8: Trang ContactsPage — bảng + chips + bộ lọc + phân trang

**Files:**
- Create: `frontend/src/features/contacts/pages/ContactsPage.tsx` (component chính, giữ < ~300 dòng bằng cách tách con)
- Create: `frontend/src/features/contacts/contact-actions.ts`
- Create: `frontend/src/features/contacts/components/ContactActionChips.tsx`
- Create: `frontend/src/features/contacts/components/ContactsTable.tsx`
- Test: `frontend/src/features/contacts/__tests__/contact-actions.test.ts`

**Interfaces:**
- Consumes: `useContacts` (Task 7), `PaginationPages` (grep component này trong `frontend/src/components/` — dùng đúng props như trang khác đang dùng), carrier label helper (grep "carrier" trong frontend/src — reuse helper nhãn nhà mạng dưới SĐT như cột phone OrdersTable), badge Khách mới/cũ (grep `is_new_customer` trong frontend/src — reuse cách OrdersTable render).
- Produces `contact-actions.ts`:

```typescript
export type ContactActionKey =
  | "new_customer" | "new_lead" | "call_2" | "call_3" | "recheck_phone"
  | "nurture" | "follow_up" | "awaiting_delivery" | "post_sale" | "none";

export const CONTACT_ACTIONS: Record<ContactActionKey, { label: string; badgeClass: string }> = {
  new_customer: { label: "KH mới", badgeClass: "bg-red-500 text-white border-transparent" },
  new_lead: { label: "Lead mới", badgeClass: "bg-rose-500 text-white border-transparent" },
  call_2: { label: "Gọi lần 2", badgeClass: "bg-emerald-600 text-white border-transparent" },
  call_3: { label: "Gọi lần 3", badgeClass: "bg-emerald-700 text-white border-transparent" },
  recheck_phone: { label: "Check lại số", badgeClass: "bg-amber-500 text-white border-transparent" },
  nurture: { label: "Chăm sóc thêm", badgeClass: "bg-teal-600 text-white border-transparent" },
  follow_up: { label: "Gọi theo hẹn", badgeClass: "bg-blue-600 text-white border-transparent" },
  awaiting_delivery: { label: "Chờ giao", badgeClass: "bg-sky-100 text-sky-700 border-transparent" },
  post_sale: { label: "Chăm sóc sau bán", badgeClass: "bg-violet-600 text-white border-transparent" },
  none: { label: "—", badgeClass: "bg-muted text-muted-foreground border-transparent" },
};
/** Thứ tự chip trên thanh lọc (không gồm none). */
export const CHIP_ORDER: ContactActionKey[] = [
  "new_customer", "new_lead", "call_2", "call_3", "recheck_phone",
  "nurture", "follow_up", "awaiting_delivery", "post_sale",
];
```

**UI spec:**
- Toolbar: date range (mirror DateFilter/date-range component trang orders — grep `DateFilter` để reuse), ô search (tên/SĐT, debounce 300ms), select nguồn khách (order_source + channel), select "Tương tác" (Tất cả / Chưa tương tác / Mới tương tác), select loại khách (Tất cả / Khách mới / Khách cũ).
- Chips hàng dưới toolbar: chip "Tất cả" + CHIP_ORDER; mỗi chip hiển thị `pending/total` từ `action_counts`; click toggle filter `action_needed` (single-select là đủ); chip active đổi màu đậm.
- Bảng cột: `#` (STT theo trang) · Khách hàng (full_name + code) · SĐT (số + nhãn nhà mạng, nút copy) · Thao tác nhanh (nút gọi 📞 — Task 10, nút ➕ ghi chú — Task 9) · Nguồn khách (channel_name, fallback order_source label) · Ngày data về (created_at latest order, format `HH:mm dd/MM/yyyy`) · Loại khách (badge) · Tác nghiệp cần (badge theo CONTACT_ACTIONS + nút icon lịch sử — Task 9) · Kết quả (select inline — Task 10) · Thời gian chờ (humanize từ waiting_since: `<1h`, `Xh`, `Xd`; đỏ dần khi >24h) · SL đơn (order_count, click mở trang chi tiết đơn mới nhất `orderDetailPath(latest_order.id)`).
- Loading dùng `LogoSpinner` (grep để lấy import path); phân trang `PaginationPages` + page size select (10/20/50/100).
- State filter đưa hết vào 1 object `params` truyền `useContacts(params)` — đổi filter reset offset về 0.

- [ ] **Step 1: Failing test** cho `contact-actions.ts` (mọi ACTION key có label; CHIP_ORDER không chứa none) + helper `humanizeWaiting(iso: string | null): string` (viết trong contact-actions.ts, test các mốc <1h/2h/3d).
- [ ] **Step 2: Chạy test → FAIL.** Implement contact-actions.ts → PASS.
- [ ] **Step 3: Implement ContactActionChips, ContactsTable, ContactsPage** (chips/table nhận props thuần — không tự fetch; page giữ state + query). Nút gọi/ghi chú/lịch sử để placeholder `onClick` prop (Task 9-10 nối).
- [ ] **Step 4: `npm run lint` (file mới) + `npm test -- --run contacts` → PASS; `npm run build` (hoặc `tsc --noEmit` theo script sẵn có) không lỗi type ở file mới.**
- [ ] **Step 5: Commit** — `feat: contacts page (table, chips, filters, pagination)`

---

### Task 9: Popover lịch sử liên hệ + dialog ghi chú

**Files:**
- Create: `frontend/src/features/contacts/components/ContactHistoryPopover.tsx`
- Create: `frontend/src/features/contacts/components/ContactNoteDialog.tsx`
- Modify: `frontend/src/features/contacts/pages/ContactsPage.tsx` + `ContactsTable.tsx` (nối 2 nút)
- Test: `frontend/src/features/contacts/__tests__/ContactHistoryPopover.test.tsx`

**Interfaces:**
- Consumes: `useContactActivities`, `useCreateContactActivity` (Task 7); `Popover`/`Dialog` từ `components/ui`; `FormDialogShell` (grep import path — chuẩn dialog form của repo).
- `ContactHistoryPopover({ customerId, customerName, children })` — children = trigger (nút icon History nhỏ cạnh badge tác nghiệp, `lucide-react` History size 14). Nội dung: list activity: dòng `HH:mm dd/MM · actor_name` + mô tả: status_change → `"{from_label || "—"} → {to_label}"`; note → note text; call → `"Cuộc gọi {duration}s"` + note nếu có. Empty state "Chưa có lịch sử liên hệ". Max height + scroll. Nút "+ Ghi chú" ở footer mở ContactNoteDialog.
- `ContactNoteDialog({ customerId, open, onOpenChange })` — textarea bắt buộc, submit → `useCreateContactActivity` kind=note, toast thành công (sonner), đóng dialog.

- [ ] **Step 1: Failing test** — render ContactHistoryPopover với query mock (dùng pattern test FE hiện có: tìm test component mẫu có QueryClientProvider trong `frontend/src/**/__tests__/`), mock `getContactActivities` trả 1 status_change + 1 note → mở trigger → thấy label from→to và note text.
- [ ] **Step 2: Chạy → FAIL. Implement 2 component + nối vào bảng.**
- [ ] **Step 3: Chạy test + lint → PASS.**
- [ ] **Step 4: Commit** — `feat: contact history popover + note dialog`

---

### Task 10: Kết quả inline + nút gọi Zenify + log cuộc gọi

**Files:**
- Create: `frontend/src/features/contacts/components/ContactResultSelect.tsx`
- Modify: `frontend/src/features/contacts/pages/ContactsPage.tsx`, `ContactsTable.tsx`
- Test: `frontend/src/features/contacts/__tests__/ContactResultSelect.test.tsx`

**Interfaces:**
- Kết quả select: options từ CRM catalog — reuse `useCrmCatalogs()` (`features/customers/queries.ts`) → `sales_status_options` (fallback hằng `CRM_SALES_STATUS_OPTIONS` — xem `features/orders/BlockCrmRating.tsx`, KHÔNG import từ đó nếu gây kéo react-select vào bundle: copy mảng options sang `contact-actions.ts` nếu cần). Dùng `searchable-select` từ `components/ui`.
- Ghi kết quả: PATCH order hiện có — grep FE nơi PATCH `/api/orders/${id}` với body `{crm: {...}}` (OrdersPage/OrderDetailPage `buildPatchPayload`) và mirror: `fetchJson(\`/api/orders/${latestOrderId}\`, { method: "PATCH", body: JSON.stringify({ crm: { sales_status_code: code } }) })`. Bọc trong `useMutation` đặt tại `features/contacts/queries.ts` (`useUpdateContactResult`), onSuccess → invalidate `contactKeys.all` (list nhỏ, refetch chấp nhận được).
- Nút gọi: tích hợp webphone — đọc `frontend/src/features/callcenter/WebphoneProvider.tsx` + `webphone-call-state.ts` để lấy hook/context public (VERIFY tên, ví dụ `useWebphone()`; đã merge branch zenify v2). Hành vi: click gọi → `dial(phone_raw)` (đúng API thật của provider); đồng thời nhớ `{customerId, startedAt}` vào ref. Đăng ký effect theo call state: khi cuộc gọi (do trang này khởi tạo) chuyển sang trạng thái kết thúc → `createContactActivity(customerId, { kind: "call", call_meta: { direction: "outbound", duration_s, answered } })` với duration từ state provider (nếu provider không expose duration → tính từ startedAt, ghi chú lại trong báo cáo). Nếu webphone chưa kết nối/không bật → nút gọi disabled + tooltip "Webphone chưa sẵn sàng".
- [ ] **Step 1: Failing test ContactResultSelect** — render với options mock, chọn option → gọi onChange với code; hiển thị label hiện tại từ `sales_status_code`.
- [ ] **Step 2: Chạy → FAIL. Implement select + mutation + nối cột Kết quả.**
- [ ] **Step 3: Implement nút gọi + log call** (đọc kỹ WebphoneProvider trước; nếu provider không expose đủ event để biết cuộc gọi kết thúc, fallback: log activity kind=call NGAY khi bấm gọi với call_meta `{direction:"outbound"}` không duration — ghi rõ quyết định vào báo cáo + design doc §8).
- [ ] **Step 4: Test + lint → PASS.**
- [ ] **Step 5: Commit** — `feat: inline result select + zenify call button with call logging`

---

### Task 11: Tổng verify + cập nhật tiến độ + MR

**Files:**
- Modify: `docs/sale-contacts-page-design.md` (bảng tiến độ §7: đánh dấu sprint ✅; §8 ghi chú những gì đã làm/chưa làm, các quyết định lệch spec)

- [ ] **Step 1: Chạy toàn bộ test backend liên quan** — `pytest apps/orders/tests/test_contact_rules.py apps/orders/tests/test_contact_list_service.py apps/orders/tests/test_contact_list_api.py apps/orders/tests/test_contact_activity_api.py apps/customers/tests/test_contact_activity_model.py apps/users/tests/test_contacts_section.py -v` trong docker với `-e POSTGRES_DB=desi_ct_final` → tất cả PASS.
- [ ] **Step 2: FE:** `npm test -- --run` (chấp nhận baseline fail đã biết: OrderFormDialog + 2 mobile-viewport), `npm run lint`, `npm run build` → PASS/không lỗi mới.
- [ ] **Step 3: Cập nhật design doc tiến độ + ghi chú.**
- [ ] **Step 4: Pull master mới nhất, kiểm tra conflict** — `git fetch origin master && git merge origin/master` (resolve nếu có, chạy lại test file đụng chạm).
- [ ] **Step 5: Push + tạo MR** — push branch, lấy link tạo MR từ output, báo lại link.
- [ ] **Step 6: Commit cuối** — `docs: update sale contacts progress`
