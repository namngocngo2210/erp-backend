# Landing ingest `staff_username` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho `POST /api/orders/ingest` nhận `staff_username` để gán sẵn sale phụ trách và bỏ qua bộ chia tự động, chỉ với đơn có `order_source` khác `landing`.

**Architecture:** Ba tầng, mỗi tầng một việc. Serializer (transport) chuẩn hoá + quyết định field có hiệu lực hay không; `OrderIngestService` (application) tra username → `staff_id` rồi đẩy int qua queue; `create_order_from_landing` (domain) gán `staff_id` ngay lúc `Order.objects.create`. Không cần cờ DB "đừng chia": cả `assign_if_unassigned` lẫn cron `sweep_unassigned` đều tự tránh đơn đã có `staff_id`.

**Tech Stack:** Django 5 + DRF, pytest (`pytest-django`), prometheus_client, ruff.

**Spec:** `docs/superpowers/specs/2026-08-12-landing-ingest-staff-username-design.md`

## Global Constraints

- Không migration, không đổi model — chỉ thêm field vào serializer + logic.
- `order_source` luôn xét **sau** `OrderSource.normalize(..., default=OrderSource.LANDING)`, không đọc giá trị thô.
- Khoá tra user: `username__iexact` + `is_active=True` + `deleted_at IS NULL`.
- Không tra ra user hợp lệ → **lenient**: vẫn tạo đơn, vẫn chia tự động, chỉ log warning + tăng metric. Không trả 4xx.
- Không ghi journey event `staff_assigned` cho đường này.
- Comment code viết tiếng Việt, giải thích **vì sao**, khớp giọng file đang sửa.
- `ruff check .` phải sạch (ruleset trong `backend/pyproject.toml`, lint là bắt buộc từ MR !1069).
- Mọi lệnh chạy trong worktree `desi-order-ingest-staff-username`, nhánh `feat/ingest-staff-username`.

---

## Chuẩn bị môi trường test (làm một lần, trước Task 1)

- [ ] **Bước 1: Kiểm tra container Postgres/Redis test còn sống**

```bash
docker ps --filter name=desi-test --format '{{.Names}}'
```

Kỳ vọng: in ra `desi-test-pg` và `desi-test-redis`. Nếu thiếu (Docker Desktop restart là mất, container không có restart policy) thì dựng lại:

```bash
docker network create desi-test-net 2>/dev/null
docker run -d --name desi-test-pg --network desi-test-net \
  -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -e POSTGRES_DB=desi_order \
  public.ecr.aws/docker/library/postgres:16-alpine
docker run -d --name desi-test-redis --network desi-test-net \
  public.ecr.aws/docker/library/redis:7-alpine
```

- [ ] **Bước 2: Lưu lệnh chạy test dùng suốt plan**

Dùng nguyên khối này cho mọi bước "Run"; `<paths>` thay theo từng task. `POSTGRES_DB` và `REDIS_CACHE_DB` phải **riêng cho plan này** để không giẫm worktree khác đang chạy song song:

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-ingest-staff-username && \
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_ingest_staff \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Bẫy đã biết: thiếu `ENCRYPTION_KEY` → mọi test ERROR ở migration; image tên `desi-backend-test:latest` (không phải `desi-order-web-test`); Git Bash phải dùng `$(pwd -W)` và `-w //app`.

---

## Cấu trúc file

| File | Trách nhiệm | Thay đổi |
|---|---|---|
| `backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py` | Hợp đồng payload landing ingest | Thêm field `staff_username` + luật gate theo `order_source` trong `validate()` |
| `backend/apps/orders/metrics.py` | Prometheus counters của orders | Thêm `landing_ingest_staff_hint_total` |
| `backend/apps/orders/application/services/order_ingest_service.py` | Điều phối ingest (trace → validate → kênh → lọc lead → enqueue) | Thêm `resolve_ingest_staff_id()` + resolve `staff_username` → `payload["staff_id"]` |
| `backend/apps/orders/application/services/order_service_commands.py` | Tạo đơn từ payload landing | `Order.objects.create(..., staff_id=payload.get("staff_id"))` |
| `backend/apps/orders/tests/test_ingest_api.py` | Test suite của landing ingest | Thêm 3 class test |

Không tạo file mới: cả 3 điểm sửa đều nằm đúng module đã có trách nhiệm tương ứng, và test suite của endpoint này đã tập trung ở một file (theo tái cấu trúc 2026-08-05).

---

### Task 1: Hợp đồng payload — field `staff_username` + luật gate

**Files:**
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py:189-243`
- Test: `backend/apps/orders/tests/test_ingest_api.py`

**Interfaces:**
- Consumes: `OrderSource.normalize` (đã có), `LandingOrderSerializer` (đã có).
- Produces: `LandingOrderSerializer.validated_data["staff_username"]` — `str` đã trim, **chỉ tồn tại** khi giá trị không rỗng **và** `validated_data["order_source"] != OrderSource.LANDING`. Mọi ca khác: key không có mặt. Task 2 đọc key này.

- [ ] **Step 1: Write the failing test**

Thêm vào cuối `backend/apps/orders/tests/test_ingest_api.py`:

```python
class LandingOrderSerializerStaffUsernameTests(TestCase):
    """Luật gate của `staff_username` — thuần serializer, không cần DB kênh/user."""

    def _validated(self, **extra):
        serializer = LandingOrderSerializer(
            data={"channel_id": "Facebook Ads Team", "phone": "0909123456", **extra}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.validated_data

    def test_keeps_trimmed_username_for_non_landing_source(self):
        data = self._validated(order_source="zalo", staff_username="  sale.a  ")
        self.assertEqual(data["staff_username"], "sale.a")

    def test_drops_username_for_landing_source(self):
        data = self._validated(order_source="landing", staff_username="sale.a")
        self.assertNotIn("staff_username", data)

    def test_drops_username_when_order_source_absent(self):
        # Thiếu order_source → normalize về "landing" → bỏ qua chỉ định sale.
        data = self._validated(staff_username="sale.a")
        self.assertNotIn("staff_username", data)

    def test_drops_username_when_order_source_invalid(self):
        # Giá trị lạ cũng rơi về "landing" theo normalize → bỏ qua.
        data = self._validated(order_source="not-a-source", staff_username="sale.a")
        self.assertNotIn("staff_username", data)

    def test_drops_blank_username(self):
        for blank in ("", "   "):
            with self.subTest(value=blank):
                data = self._validated(order_source="zalo", staff_username=blank)
                self.assertNotIn("staff_username", data)

    def test_drops_null_username(self):
        data = self._validated(order_source="zalo", staff_username=None)
        self.assertNotIn("staff_username", data)
```

- [ ] **Step 2: Run test to verify it fails**

Run: khối lệnh ở Chuẩn bị với `<paths>` = `apps/orders/tests/test_ingest_api.py::LandingOrderSerializerStaffUsernameTests`

Expected: FAIL — `staff_username` chưa là field khai báo nên bị DRF bỏ khỏi `validated_data`, `test_keeps_trimmed_username_for_non_landing_source` báo `KeyError: 'staff_username'`.

- [ ] **Step 3: Write minimal implementation**

Trong `kiotviet_landing_serializers.py`, thêm field ngay dưới `order_source` (dòng 202):

```python
    order_source = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    # Sale phụ trách do bên gửi chỉ định. CHỈ có hiệu lực với đơn nguồn khác
    # "landing" — xem `validate()`. max_length 150 khớp `AbstractUser.username`.
    staff_username = serializers.CharField(
        max_length=150, required=False, allow_blank=True, allow_null=True
    )
```

Trong `validate()`, thay khối cuối (dòng 239-243) bằng:

```python
        # Luồng landing: thiếu/không hợp lệ → default "landing" (đồng bộ Lark/Excel).
        attrs["order_source"] = OrderSource.normalize(
            attrs.get("order_source"), default=OrderSource.LANDING
        )

        # Chỉ định sale chỉ dành cho đơn nguồn KHÁC landing: đơn từ landing page
        # luôn phải đi qua bộ chia tự động. `normalize` phía trên đã gộp cả ba ca
        # "thiếu field" / "rỗng" / "giá trị lạ" về "landing", nên một phép so sánh
        # ở đây là đủ — không cần đọc lại giá trị thô. Key bị bỏ hẳn (không phải
        # để rỗng) để tầng dưới chỉ cần kiểm tra sự tồn tại.
        staff_username = str(attrs.pop("staff_username", "") or "").strip()
        if staff_username and attrs["order_source"] != OrderSource.LANDING:
            attrs["staff_username"] = staff_username
        return attrs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `<paths>` = `apps/orders/tests/test_ingest_api.py::LandingOrderSerializerStaffUsernameTests`
Expected: PASS (6 test).

- [ ] **Step 5: Chạy lại toàn bộ test của file để chắc không phá hợp đồng cũ**

Run: `<paths>` = `apps/orders/tests/test_ingest_api.py`
Expected: PASS toàn bộ.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-ingest-staff-username
git add backend/apps/orders/transport/serializers/order_serializers_impl/kiotviet_landing_serializers.py backend/apps/orders/tests/test_ingest_api.py
git commit -m "feat(orders): accept staff_username in landing ingest payload"
```

---

### Task 2: Resolve username → `staff_id` trong `OrderIngestService`

**Files:**
- Modify: `backend/apps/orders/application/services/order_ingest_service.py` (imports dòng 7-29; thêm hàm sau `resolve_channel_ref` kết ở dòng 88; chèn vào `ingest_landing_order` sau khối resolve kênh dòng 170-171)
- Modify: `backend/apps/orders/metrics.py:58-77`
- Test: `backend/apps/orders/tests/test_ingest_api.py`

**Interfaces:**
- Consumes: `LandingOrderSerializer.validated_data["staff_username"]` từ Task 1.
- Produces:
  - `resolve_ingest_staff_id(username) -> int | None` trong `apps.orders.application.services.order_ingest_service`.
  - `payload["staff_id"]: int` trong payload đẩy qua `dispatch("orders.landing_ingest")`; key `staff_username` bị pop khỏi payload. Task 3 đọc `payload.get("staff_id")`.
  - `landing_ingest_staff_hint_total` trong `apps.orders.metrics`, label `result` ∈ {`assigned`, `unknown_username`}.

- [ ] **Step 1: Write the failing test**

Thêm vào `backend/apps/orders/tests/test_ingest_api.py`. Bổ sung import ở đầu file:

```python
from apps.orders.application.services.order_ingest_service import resolve_ingest_staff_id
from apps.users.models import User
```

Rồi thêm 2 class:

```python
class ResolveIngestStaffIdTests(TestCase):
    def test_matches_username_case_insensitively(self):
        user = User.objects.create_user(username="Sale.A", password="x")
        self.assertEqual(resolve_ingest_staff_id("sale.a"), user.id)

    def test_returns_none_for_unknown_username(self):
        self.assertIsNone(resolve_ingest_staff_id("khong-ton-tai"))

    def test_ignores_inactive_user(self):
        User.objects.create_user(username="sale.off", password="x", is_active=False)
        self.assertIsNone(resolve_ingest_staff_id("sale.off"))

    def test_ignores_soft_deleted_user(self):
        User.objects.create_user(
            username="sale.deleted", password="x", deleted_at=timezone.now()
        )
        self.assertIsNone(resolve_ingest_staff_id("sale.deleted"))


class LandingIngestPayloadStaffTests(TestCase):
    """Payload đẩy sang queue phải mang `staff_id` (int) và KHÔNG mang username."""

    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(
            code="facebook-ads-team", name="Facebook Ads Team", owner="Facebook Ads Team"
        )
        self.sale = User.objects.create_user(username="sale.designated", password="x")

    def _dispatch_kwargs(self, **extra):
        """POST ingest với dispatch bị chặn — chỉ soi payload, không tạo đơn."""
        captured = {}

        def _capture(_task_name, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="test-job")

        with patch(
            "apps.orders.application.services.order_ingest_service.dispatch",
            side_effect=_capture,
        ):
            response = self.client.post(
                "/api/orders/ingest",
                data={"channel_id": "Facebook Ads Team", "phone": "0909123456", **extra},
                format="json",
                **ingest_headers(),
            )
        self.assertEqual(response.status_code, 200, response.data)
        return captured["payload"]

    def test_resolved_username_becomes_staff_id(self):
        payload = self._dispatch_kwargs(order_source="zalo", staff_username="sale.designated")
        self.assertEqual(payload["staff_id"], self.sale.id)
        self.assertNotIn("staff_username", payload)

    def test_unknown_username_leaves_no_staff_id(self):
        payload = self._dispatch_kwargs(order_source="zalo", staff_username="khong-ton-tai")
        self.assertNotIn("staff_id", payload)
        self.assertNotIn("staff_username", payload)

    def test_landing_source_leaves_no_staff_id(self):
        payload = self._dispatch_kwargs(order_source="landing", staff_username="sale.designated")
        self.assertNotIn("staff_id", payload)
```

Thêm import `from django.utils import timezone` ở đầu file nếu chưa có.

- [ ] **Step 2: Run test to verify it fails**

Run: `<paths>` = `apps/orders/tests/test_ingest_api.py::ResolveIngestStaffIdTests apps/orders/tests/test_ingest_api.py::LandingIngestPayloadStaffTests`
Expected: FAIL ngay lúc collect — `ImportError: cannot import name 'resolve_ingest_staff_id'`.

- [ ] **Step 3: Write minimal implementation**

3a. `backend/apps/orders/metrics.py` — thêm counter sau `landing_ingest_processed_total` (dòng 62) và khai vào `__all__`:

```python
landing_ingest_staff_hint_total = Counter(
    "desi_landing_ingest_staff_hint_total",
    "Landing ingest payloads designating a sale via staff_username.",
    ["result"],  # assigned | unknown_username
)
```

```python
__all__ = [
    "_ENABLED",
    "landing_ingest_received_total",
    "landing_ingest_validated_total",
    "landing_ingest_enqueued_total",
    "landing_ingest_processed_total",
    "landing_ingest_staff_hint_total",
    "landing_ingest_process_latency_seconds",
]
```

3b. `order_ingest_service.py` — thêm vào khối import metrics (dòng 20-23) và import `User`:

```python
from apps.orders.metrics import (
    landing_ingest_enqueued_total,
    landing_ingest_staff_hint_total,
    landing_ingest_validated_total,
)
from apps.orders.models import (
    IngestSource,
    LandingIngestTrace,
    LandingIngestTraceStatus,
    Product,
)
from apps.users.models import User
```

3c. Thêm hàm ngay sau `resolve_channel_ref` (kết ở dòng 88):

```python
def resolve_ingest_staff_id(username) -> int | None:
    """Resolve `staff_username` trong payload ingest → id user, hoặc None.

    Chỉ nhận tài khoản CÒN dùng được: `is_active` và chưa xoá mềm. Tài khoản sale
    đã nghỉ coi như không khớp để đơn rơi về bộ chia tự động, thay vì nằm chết ở
    một tài khoản không ai đăng nhập.
    """
    text = str(username or "").strip()
    if not text:
        return None
    user = (
        User.objects.filter(username__iexact=text, is_active=True, deleted_at__isnull=True)
        .only("id")
        .first()
    )
    return user.id if user else None
```

3d. Trong `ingest_landing_order`, chèn ngay sau dòng `payload["channel_id"] = channel.id`:

```python
        # Chỉ định sale (chỉ có mặt với đơn nguồn khác landing — serializer đã gate).
        # Resolve NGAY ở tầng request thay vì trong task: payload qua queue phải là
        # JSON thuần, và typo tên sale cần hiện trong log request kèm trace_id.
        staff_username = payload.pop("staff_username", "")
        if staff_username:
            staff_id = resolve_ingest_staff_id(staff_username)
            if staff_id is None:
                # CỐ Ý lenient (đồng bộ lark-ingest): sai một cái tên không đáng
                # để mất lead, đơn rơi về bộ chia tự động. Metric là đường duy
                # nhất để ops thấy bên gửi đang gõ sai tên hàng loạt.
                landing_ingest_staff_hint_total.labels(result="unknown_username").inc()
                logger.warning(
                    "Landing ingest staff_username không khớp tài khoản nào: trace_id=%s username=%s",
                    trace.id,
                    staff_username,
                )
            else:
                landing_ingest_staff_hint_total.labels(result="assigned").inc()
                payload["staff_id"] = staff_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `<paths>` = `apps/orders/tests/test_ingest_api.py::ResolveIngestStaffIdTests apps/orders/tests/test_ingest_api.py::LandingIngestPayloadStaffTests`
Expected: PASS (7 test).

- [ ] **Step 5: Commit**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-ingest-staff-username
git add backend/apps/orders/application/services/order_ingest_service.py backend/apps/orders/metrics.py backend/apps/orders/tests/test_ingest_api.py
git commit -m "feat(orders): resolve ingest staff_username to staff_id"
```

---

### Task 3: Gán sale lúc tạo đơn + test end-to-end ma trận quyết định

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_commands.py:212-249`
- Test: `backend/apps/orders/tests/test_ingest_api.py`

**Interfaces:**
- Consumes: `payload["staff_id"]` từ Task 2.
- Produces: `Order.staff_id` được set ngay lúc `create` → `assign_if_unassigned` no-op và cron `sweep_unassigned` bỏ qua đơn này.

- [ ] **Step 1: Write the failing test**

Thêm import ở đầu `backend/apps/orders/tests/test_ingest_api.py`:

```python
from apps.orders.application.services.lead_assignment_service import LeadAssignmentService
from apps.orders.application.services.shift_service import ShiftService
```

Thêm class test. Mỗi ca dùng **số điện thoại riêng** — bộ lọc lead chặn trùng phone+kênh trong ngày, dùng lại số sẽ nhận 409 thay vì 200:

```python
class LandingIngestStaffUsernameTests(TestCase):
    """Ma trận quyết định: khi nào đơn gán theo payload, khi nào rơi về bộ chia.

    `pick_staff_for` bị patch trả `auto_picked` nên "đi vào bộ chia" quan sát được
    bằng chính danh tính sale trên đơn, không phải bằng việc mock có được gọi.
    `current_shift_and_date` phải patch vì test DB không có cấu hình ca — thiếu nó
    bộ chia skip im lặng và hai nhánh trở nên không phân biệt được.
    """

    def setUp(self):
        self.client = APIClient()
        self.channel = Channel.objects.create(
            code="facebook-ads-team", name="Facebook Ads Team", owner="Facebook Ads Team"
        )
        self.designated = User.objects.create_user(username="sale.designated", password="x")
        self.auto_picked = User.objects.create_user(username="sale.auto", password="x")

    def _ingest(self, phone, **extra):
        """POST ingest chạy task đồng bộ. Trả (order, mock pick_staff_for)."""
        payload = {
            "channel_id": "Facebook Ads Team",
            "name": "Nguyen Van A",
            "phone": phone,
            "address": "1 Nguyen Trai, Q1",
            **extra,
        }

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with (
            patch(
                "apps.orders.application.services.order_ingest_service.dispatch",
                side_effect=_run_task,
            ),
            patch.object(
                ShiftService,
                "current_shift_and_date",
                return_value=("morning", timezone.localdate()),
            ),
            patch.object(
                LeadAssignmentService, "pick_staff_for", return_value=self.auto_picked
            ) as pick_mock,
        ):
            response = self.client.post(
                "/api/orders/ingest", data=payload, format="json", **ingest_headers()
            )
        self.assertEqual(response.status_code, 200, response.data)
        return Order.objects.latest("id"), pick_mock

    def test_non_landing_source_assigns_designated_sale(self):
        order, pick_mock = self._ingest(
            "0909000001", order_source="zalo", staff_username="sale.designated"
        )
        self.assertEqual(order.staff_id, self.designated.id)
        pick_mock.assert_not_called()

    def test_unknown_username_falls_back_to_auto_assign(self):
        order, pick_mock = self._ingest(
            "0909000002", order_source="zalo", staff_username="khong-ton-tai"
        )
        self.assertEqual(order.staff_id, self.auto_picked.id)
        pick_mock.assert_called_once()

    def test_inactive_user_falls_back_to_auto_assign(self):
        User.objects.create_user(username="sale.off", password="x", is_active=False)
        order, _ = self._ingest(
            "0909000003", order_source="zalo", staff_username="sale.off"
        )
        self.assertEqual(order.staff_id, self.auto_picked.id)

    def test_soft_deleted_user_falls_back_to_auto_assign(self):
        User.objects.create_user(
            username="sale.deleted", password="x", deleted_at=timezone.now()
        )
        order, _ = self._ingest(
            "0909000004", order_source="zalo", staff_username="sale.deleted"
        )
        self.assertEqual(order.staff_id, self.auto_picked.id)

    def test_landing_source_ignores_designation(self):
        order, _ = self._ingest(
            "0909000005", order_source="landing", staff_username="sale.designated"
        )
        self.assertEqual(order.staff_id, self.auto_picked.id)

    def test_absent_order_source_ignores_designation(self):
        order, _ = self._ingest("0909000006", staff_username="sale.designated")
        self.assertEqual(order.staff_id, self.auto_picked.id)

    def test_blank_username_falls_back_to_auto_assign(self):
        order, _ = self._ingest("0909000007", order_source="zalo", staff_username="   ")
        self.assertEqual(order.staff_id, self.auto_picked.id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `<paths>` = `apps/orders/tests/test_ingest_api.py::LandingIngestStaffUsernameTests`
Expected: FAIL — `test_non_landing_source_assigns_designated_sale` báo `order.staff_id` là `sale.auto` (hoặc None) chứ không phải `sale.designated`, vì `create_order_from_landing` còn bỏ qua `payload["staff_id"]`. 6 test còn lại PASS sẵn (hành vi chia tự động không đổi) — đó là baseline chứng minh mình không phá gì.

- [ ] **Step 3: Write minimal implementation**

Trong `order_service_commands.py`, thêm kwarg vào `Order.objects.create(...)` ở `create_order_from_landing` — đặt cạnh `order_source` (dòng 243-248) cho dễ đọc cùng luật:

```python
            # Tôn trọng order_source từ payload (đã được serializer normalize);
            # chỉ fallback "landing" khi thiếu/không hợp lệ để giữ đúng luồng landing.
            order_source=OrderSource.normalize(
                payload.get("order_source"), default=OrderSource.LANDING
            ),
            # Sale do bên gửi chỉ định (đã resolve ở OrderIngestService). Gán NGAY
            # lúc create, không gán sau: `assign_if_unassigned` bên dưới return sớm
            # khi đã có staff nên đơn không bị chia tự động, và
            # `build_order_created_row` ở cuối hàm mang luôn staff_id/tên sale nên
            # FE nhận realtime thấy đúng người phụ trách ở dòng vừa hiện.
            staff_id=payload.get("staff_id"),
            show_orders=False,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `<paths>` = `apps/orders/tests/test_ingest_api.py::LandingIngestStaffUsernameTests`
Expected: PASS (7 test).

- [ ] **Step 5: Chạy full test file + các suite liên quan tới bộ chia và tạo đơn**

Run: `<paths>` = `apps/orders/tests/test_ingest_api.py apps/orders/tests/test_lead_assignment.py apps/orders/tests/test_order_create_api.py`
Expected: PASS toàn bộ. Nếu `test_lead_assignment.py` đỏ, đọc kỹ: đây là suite chứng minh bộ chia tự động chưa bị đổi hành vi.

- [ ] **Step 6: Lint**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-ingest-staff-username
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Expected: `All checks passed!`. Không tự ý xoá import mà ruff báo F401 trước khi grep `import *` — file serializer nằm trong chuỗi star-import, đã có tiền lệ xoá nhầm làm 8 test ERROR lúc collect.

- [ ] **Step 7: Commit**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-ingest-staff-username
git add backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/tests/test_ingest_api.py
git commit -m "feat(orders): assign designated sale on landing ingest"
```

---

### Task 4: Chạy suite orders đầy đủ + đẩy MR

**Files:** không sửa code (trừ khi suite lộ hồi quy).

- [ ] **Step 1: Chạy toàn bộ app orders**

Run: `<paths>` = `apps/orders`

Expected: chỉ còn các test đã đỏ sẵn trên master (nếu có). So sánh với baseline bằng cách chạy cùng lệnh trên `origin/master` trước khi kết luận là do mình — đừng sửa code theo một test đỏ chưa xác định nguồn.

- [ ] **Step 2: Đẩy nhánh và tạo MR**

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-ingest-staff-username
git push -o merge_request.create -o merge_request.title="feat(orders): designate sale via staff_username on landing ingest" \
  -u origin feat/ingest-staff-username
```

Không có `glab`/token trên máy này nên MR tạo bằng push option; lấy link MR từ output của `git push`.

- [ ] **Step 3: Gửi link MR cho user**

Kèm một dòng tóm tắt: field mới, luật gate, ca lenient, và "không migration".

---

## Self-review

**Spec coverage:**

| Yêu cầu trong spec | Task |
|---|---|
| Field `staff_username` ≤150, optional | Task 1 |
| Gate theo `order_source` sau normalize (rỗng/lạ/`landing` → bỏ qua) | Task 1 |
| Trim, rỗng/trắng = không gửi | Task 1 |
| Khoá tra `username__iexact` + `is_active` + chưa xoá | Task 2 |
| Resolve ở tầng request, đẩy `staff_id` int qua queue | Task 2 |
| Lenient khi không tra ra + log warning | Task 2 |
| Counter `desi_landing_ingest_staff_hint_total` | Task 2 |
| Gán `staff_id` lúc `Order.objects.create` | Task 3 |
| Không cờ DB, không migration | Toàn plan — không có bước migration nào |
| Không ghi journey event | Task 3 (không thêm gọi `log_staff_change`) |
| Ma trận test 7 ca + test serializer | Task 1 + Task 3 |

**Đã cố ý không test:** giá trị counter Prometheus (assert trên registry đa tiến trình vừa giòn vừa không nói lên hành vi nghiệp vụ). Counter chỉ cần được gọi đúng nhánh — điều này đã do test hành vi bao phủ.
