# Tab Tư vấn giữ đơn ở lại + Nhóm 10 chia theo sheet — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đơn đổ về không có địa chỉ ở lại tab Tư vấn vĩnh viễn (không nhảy sang Landing/Messenger xác nhận đơn khi sale điền địa chỉ), và báo cáo sale Nhóm 10 chia cột theo 3 tab đó thay vì theo nguồn đơn.

**Architecture:** Luật phân Sheet 1/2/3 hiện chép tay ở **bốn** chỗ (`sheet_rules.sheet_q`, `order_list_view`, `repositories._section_filter_overrides`, và bản sao Q "thiếu thông tin" ngay trong `repositories._apply_list_filters`). Gom về `sheet_rules.py` làm nguồn sự thật duy nhất qua hai helper mới `info_incomplete_q()` và `consulting_q()`, rồi thêm một khoá filter `consulting` (`"only"` / `"exclude"`) để ba call site dùng chung. Nhóm 10 annotate `sheet` bằng chính `sheet_q()` đó nên báo cáo và màn hình không thể lệch nhau.

**Tech Stack:** Django 5 + DRF, PostgreSQL, pytest (chạy trong Docker); React 19 + TypeScript + Vitest.

## Global Constraints

- Worktree riêng cho plan này (tạo ở Task 0), branch `feat/consulting-tab-sticky`, cắt từ **master mới nhất**.
- `Order.has_address` là cờ **bất biến** ghi lúc ingest (migration `0094`). Không được ghi đè nó ở bất kỳ luồng sửa đơn nào.
- **Không thêm migration** trong plan này — không có cột mới, không có index mới.
- Quy tắc nghiệp vụ, dùng nguyên văn ở mọi nơi:
  ```
  Tư vấn (Sheet 3) = thiếu Tên/SĐT/Địa chỉ  HOẶC  has_address = False
  Sheet 1          = nguồn landing/hotline/zalo  AND NOT(Tư vấn)
  Sheet 2          = nguồn messenger            AND NOT(Tư vấn)
  ```
- Giữ nguyên tập nguồn của từng endpoint. `sheet3_view` hiện lọc bằng `order_source_exclude=["koc", *MARKETPLACE_ORDER_SOURCES]` (nên nguồn legacy `kol` vẫn lọt vào board Tư vấn), còn `sheet_q()` dùng `SHEET3_SOURCES` (4 nguồn). **Không hợp nhất hai tập nguồn này** — đổi sẽ làm đơn `kol` biến mất khỏi board Tư vấn, ngoài phạm vi yêu cầu. Chỉ hợp nhất phần "thiếu thông tin / has_address".
- Repo không có CI — chạy test/lint tay.
- Lệnh test backend (đổi `POSTGRES_DB` và `REDIS_CACHE_DB` cho MỖI lần chạy đồng thời, nếu không sẽ lỗi giả `database already exists`):
  ```bash
  cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
    -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_consulting -e REDIS_HOST=desi-test-redis \
    -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
    -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
    -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
    -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
    sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
  ```
  Container hạ tầng mất sau khi Docker Desktop restart — dựng lại nếu gặp `failed to resolve host 'desi-test-pg'`:
  ```bash
  docker network create desi-test-net
  docker run -d --name desi-test-pg --network desi-test-net -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -e POSTGRES_DB=desi_order public.ecr.aws/docker/library/postgres:16-alpine
  docker run -d --name desi-test-redis --network desi-test-net public.ecr.aws/docker/library/redis:7-alpine
  ```
- **KHÔNG BAO GIỜ** gọi `cache.clear()` trong test mới (FLUSHDB đè Redis thật).
- Lint backend (image test không có ruff):
  ```bash
  cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting && docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
  ```
- Baseline test đỏ SẴN trên master (không phải hồi quy): frontend `RequireAuth.test.tsx` (2 test) và `use-orders-data.realtime-upsert-filters.test.tsx` (5 test); backend `test_report_trends` (6 test) và `realtime-upsert-filters` phía FE. Kiểm chứng trên repo chính trước khi nghi thay đổi của mình.

---

## File Structure

| File | Trách nhiệm | Thay đổi |
| --- | --- | --- |
| `backend/apps/orders/sheet_rules.py` | Luật phân sheet — nguồn sự thật duy nhất | Thêm `info_incomplete_q()`, `consulting_q()`; `classify_sheet` nhận `has_address`; `sheet_q` theo luật mới |
| `backend/apps/orders/repositories.py` | Bộ lọc list dùng chung | `_apply_list_filters` dùng `info_incomplete_q()` + thêm khoá `consulting`; `_section_filter_overrides` dùng khoá đó |
| `backend/apps/orders/transport/views/order_views_impl/order_list_view.py` | List Sheet 1/2 | Đổi `info_complete="true"` thành `consulting="exclude"` |
| `backend/apps/orders/transport/views/order_views_impl/sheet3_view.py` | List Sheet 3 | Đổi `info_complete="false"` thành `consulting="only"` |
| `backend/apps/orders/application/services/lead_assignment_service.py` | Bộ chia đơn tự động | Truyền `has_address` vào `classify_sheet` |
| `backend/apps/orders/_repositories_reports.py` | Aggregate báo cáo | `summary_closed_revenue_by_staff_source` → `..._sheet`, group theo `sheet` |
| `backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py` | Endpoint summary | Đổi tên key payload |
| `frontend/src/features/orders/landing-tabs.ts` | Nhãn tab Landing | Tách `SHEET_LABELS` để dùng chung |
| `frontend/src/features/dashboard/sale-sheet-revenue.ts` | Dựng ma trận Nhóm 10 (đổi tên từ `sale-source-revenue.ts`) | Trục cột theo sheet |
| `frontend/src/features/dashboard/components/SaleSheetRevenueSection.tsx` | Render Nhóm 10 (đổi tên) | Nhãn cột theo sheet |
| `frontend/src/features/dashboard/types.ts` | Kiểu payload summary | `order_source` → `sheet`, đổi tên key |
| `frontend/src/pages/DashboardPage.tsx` | Lắp Nhóm 10 | Đổi tên component + prop |

---

### Task 0: Dựng worktree cho plan này

**Files:**
- Không sửa file nào. Chỉ dựng môi trường.

**Interfaces:**
- Consumes: không có.
- Produces: worktree `c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting` trên branch `feat/consulting-tab-sticky`.

- [ ] **Step 1: Pull master mới nhất**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order && git pull --ff-only origin master
```

- [ ] **Step 2: Tạo worktree anh em**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order && git worktree add -b feat/consulting-tab-sticky ../desi-order-wt-consulting master
```

Phải là worktree **anh em** (`desi-order-wt-*` nằm cạnh repo chính), không phải worktree lồng trong repo — worktree lồng làm vitest 3 vỡ kiểu dual-instance.

- [ ] **Step 3: Tạo junction node_modules cho frontend**

```powershell
New-Item -ItemType Junction -Path "c:\Users\MYPC\Desktop\workspace\desi-order-wt-consulting\frontend\node_modules" -Target "c:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```

- [ ] **Step 4: Xác nhận hạ tầng test backend còn sống**

```bash
docker ps --filter name=desi-test --format "{{.Names}}"
```

Kỳ vọng: in ra `desi-test-pg` và `desi-test-redis`. Nếu thiếu, dựng lại bằng lệnh ở mục Global Constraints, rồi nối redis vào network nếu cần: `docker network connect desi-test-net desi-test-redis`.

---

### Task 1: Đưa luật "Tư vấn" về một chỗ trong `sheet_rules.py`

**Files:**
- Modify: `backend/apps/orders/sheet_rules.py:85-123`
- Modify: `backend/apps/orders/application/services/lead_assignment_service.py:77-83`
- Test: `backend/apps/orders/tests/test_lead_assignment.py`

**Interfaces:**
- Consumes: không có.
- Produces: ba hàm public trong `apps.orders.sheet_rules`:
  - `info_incomplete_q() -> Q` — đơn thiếu Tên **hoặc** SĐT **hoặc** Địa chỉ.
  - `consulting_q() -> Q` — `info_incomplete_q() | Q(has_address=False)`.
  - `classify_sheet(*, order_source: str | None, name: str, phone: str, raw_address: str = "", address: str = "", has_address: bool = True) -> str | None`.
  - `sheet_q(sheet: str) -> Q` giữ nguyên chữ ký, đổi hành vi.
  Task 2 và Task 3 dùng `consulting_q()`; Task 4 dùng `sheet_q()`.

- [ ] **Step 1: Viết test đỏ cho `classify_sheet` và `sheet_q`**

Thêm class sau vào cuối `backend/apps/orders/tests/test_lead_assignment.py`:

```python
class ConsultingStickyRuleTests(TestCase):
    """Luật Tư vấn dính (2026-08-11): đơn đổ về KHÔNG có địa chỉ thuộc Sheet 3
    vĩnh viễn, kể cả sau khi sale điền đủ Tên/SĐT/Địa chỉ — và KHÔNG lọt sang
    Sheet 1/2. Đây là luật dùng chung cho bộ chia đơn, list view và báo cáo."""

    def setUp(self):
        self.channel = Channel.objects.create(code="ch-sticky", name="Sticky", owner="Sticky")
        self.customer = Customer.objects.create(
            full_name="KH Sticky", phone_raw="0909777001", phone_normalized="0909777001"
        )

    def _order(self, **kwargs):
        defaults = {
            "channel": self.channel,
            "customer": self.customer,
            "order_source": "landing",
            "name": "Nguyen A",
            "phone": "0909777001",
            "raw_address": "1 Nguyen Trai, Q1",
        }
        return Order.objects.create(**{**defaults, **kwargs})

    def test_classify_no_ingest_address_stays_sheet3_even_when_complete(self):
        sheet = classify_sheet(
            order_source="landing",
            name="Nguyen A",
            phone="0909777001",
            raw_address="1 Nguyen Trai, Q1",
            has_address=False,
        )
        self.assertEqual(sheet, "sheet3")

    def test_classify_messenger_no_ingest_address_is_sheet3_not_sheet2(self):
        sheet = classify_sheet(
            order_source="messenger",
            name="Nguyen A",
            phone="0909777001",
            raw_address="1 Nguyen Trai, Q1",
            has_address=False,
        )
        self.assertEqual(sheet, "sheet3")

    def test_classify_defaults_has_address_true_keeps_old_behaviour(self):
        # Không truyền has_address → đơn cũ (trước migration 0094) giữ hành vi cũ.
        self.assertEqual(
            classify_sheet(
                order_source="landing",
                name="Nguyen A",
                phone="0909777001",
                raw_address="1 Nguyen Trai, Q1",
            ),
            "sheet1",
        )

    def test_sheet_q_sticky_order_only_matches_sheet3(self):
        sticky = self._order(has_address=False)
        self.assertIn(sticky, Order.objects.filter(sheet_q("sheet3")))
        self.assertNotIn(sticky, Order.objects.filter(sheet_q("sheet1")))
        self.assertNotIn(sticky, Order.objects.filter(sheet_q("sheet2")))

    def test_sheet_q_complete_order_with_ingest_address_is_sheet1(self):
        normal = self._order()
        self.assertIn(normal, Order.objects.filter(sheet_q("sheet1")))
        self.assertNotIn(normal, Order.objects.filter(sheet_q("sheet3")))

    def test_sheet_q_missing_phone_still_sheet3_when_ingest_had_address(self):
        no_phone = self._order(phone="")
        self.assertIn(no_phone, Order.objects.filter(sheet_q("sheet3")))
        self.assertNotIn(no_phone, Order.objects.filter(sheet_q("sheet1")))

    def test_three_sheets_are_disjoint(self):
        self._order(has_address=False)
        self._order()
        self._order(order_source="messenger")
        self._order(phone="")
        ids1 = set(Order.objects.filter(sheet_q("sheet1")).values_list("id", flat=True))
        ids2 = set(Order.objects.filter(sheet_q("sheet2")).values_list("id", flat=True))
        ids3 = set(Order.objects.filter(sheet_q("sheet3")).values_list("id", flat=True))
        self.assertEqual(ids1 & ids2, set())
        self.assertEqual(ids1 & ids3, set())
        self.assertEqual(ids2 & ids3, set())
        self.assertEqual(len(ids1 | ids2 | ids3), 4)
```

Thêm import cần thiết vào đầu file nếu chưa có (kiểm tra khối import hiện tại trước khi thêm, đừng thêm trùng):

```python
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order
from apps.orders.sheet_rules import classify_sheet, sheet_q
```

- [ ] **Step 2: Chạy test để xác nhận nó đỏ**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_consulting_t1 -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_lead_assignment.py::ConsultingStickyRuleTests -q --create-db'
```

Kỳ vọng: FAIL. `classify_sheet() got an unexpected keyword argument 'has_address'` ở 3 test đầu; `test_sheet_q_sticky_order_only_matches_sheet3` fail vì đơn sticky vẫn khớp `sheet_q("sheet1")`.

- [ ] **Step 3: Viết luật mới trong `sheet_rules.py`**

Trong `backend/apps/orders/sheet_rules.py`, thay toàn bộ đoạn từ `def classify_sheet(` tới hết file bằng:

```python
def info_incomplete_q() -> "Q":
    """``Q`` lọc đơn THIẾU thông tin — mirror phủ định của ``is_info_complete``.

    NULL và chuỗi rỗng đều tính là trống, khớp cách ingest ghi dữ liệu.
    """
    from django.db.models import Q

    address_blank = (Q(raw_address__isnull=True) | Q(raw_address="")) & (
        Q(address__isnull=True) | Q(address="")
    )
    name_blank = Q(name__isnull=True) | Q(name="")
    phone_blank = Q(phone__isnull=True) | Q(phone="")
    return address_blank | name_blank | phone_blank


def consulting_q() -> "Q":
    """``Q`` lọc đơn thuộc luồng Tư vấn (Sheet 3).

    Hai vế:
    - thiếu Tên/SĐT/Địa chỉ — thuộc tính ĐỘNG, hết thiếu là ra khỏi vế này;
    - ``has_address=False`` — cờ BẤT BIẾN ghi lúc ingest, sale điền địa chỉ
      không xoá được. Đây là vế làm tab Tư vấn "dính": đơn đổ về không có địa
      chỉ ở lại Tư vấn kể cả khi đã đủ thông tin, thay vì nhảy sang Sheet 1/2
      và mất dấu với sale đang theo (yêu cầu 2026-08-11).

    Đơn tạo trước migration 0094 có ``has_address`` default True nên chỉ dính
    theo vế thứ nhất — không backfill được, không còn dấu vết địa chỉ lúc đổ đơn.
    """
    from django.db.models import Q

    return info_incomplete_q() | Q(has_address=False)


def classify_sheet(
    *,
    order_source: str | None,
    name: str,
    phone: str,
    raw_address: str = "",
    address: str = "",
    has_address: bool = True,
) -> str | None:
    """Phân loại 1 đơn vào sheet1/sheet2/sheet3, hoặc None nếu không thuộc 3 sheet.

    Dùng chung cho auto-assign (lúc ingest) và workload/transfer. Phải khớp
    ``sheet_q`` — hai hàm là hai cách diễn đạt (Python / SQL) của CÙNG một luật.

    ``has_address`` mặc định True để caller cũ (đơn không qua ingest) giữ nguyên
    hành vi; luồng ingest phải truyền cờ thật của đơn.
    """
    consulting = (not is_info_complete(
        name=name, phone=phone, raw_address=raw_address, address=address
    )) or (not has_address)
    src = (order_source or "").strip().lower()
    # Tư vấn xét TRƯỚC: đơn dính vế has_address vẫn "đủ thông tin" nên nếu để
    # sau sẽ bị sheet1/sheet2 vợt mất.
    if consulting and src in SHEET3_SOURCES:
        return "sheet3"
    if (not consulting) and src in SHEET2_SOURCES:
        return "sheet2"
    if (not consulting) and src in SHEET1_SOURCES:
        return "sheet1"
    return None


def sheet_q(sheet: str) -> "Q":
    """Trả về ``django.db.models.Q`` lọc Order thuộc ``sheet`` (mirror ``classify_sheet``).

    Ba tập RỜI NHAU: sheet1/sheet2 loại hẳn đơn thuộc luồng Tư vấn, nên một đơn
    không bao giờ hiện ở hai tab cùng lúc.

    Note: đơn có order_source NULL bị SQL IN loại, khớp ``classify_sheet`` (map
    None → "" nên không match tuple nguồn nào).
    """
    from django.db.models import Q

    consulting = consulting_q()
    if sheet == "sheet1":
        return Q(order_source__in=SHEET1_SOURCES) & ~consulting
    if sheet == "sheet2":
        return Q(order_source__in=SHEET2_SOURCES) & ~consulting
    if sheet == "sheet3":
        return Q(order_source__in=SHEET3_SOURCES) & consulting
    raise ValueError(f"Sheet không hợp lệ: {sheet}")
```

- [ ] **Step 4: Truyền cờ thật từ bộ chia đơn**

Trong `backend/apps/orders/application/services/lead_assignment_service.py`, thay lời gọi `classify_sheet` (hiện ở dòng 77-83):

```python
        sheet = classify_sheet(
            order_source=order.order_source,
            name=order.name,
            phone=order.phone,
            raw_address=order.raw_address,
            address=order.address,
            has_address=order.has_address,
        )
```

- [ ] **Step 5: Chạy test để xác nhận nó xanh**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_consulting_t1 -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_lead_assignment.py -q --create-db'
```

Kỳ vọng: PASS, cả file (gồm các test auto-assign cũ — chúng dùng đơn `has_address` default True nên không đổi kết quả).

- [ ] **Step 6: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting
git add backend/apps/orders/sheet_rules.py backend/apps/orders/application/services/lead_assignment_service.py backend/apps/orders/tests/test_lead_assignment.py
git commit -m "feat(orders): make consulting sheet sticky via ingest has_address flag"
```

---

### Task 2: Cho ba list endpoint dùng chung luật qua khoá filter `consulting`

**Files:**
- Modify: `backend/apps/orders/repositories.py:138-158` (`_section_filter_overrides`) và `:682-695` (`_apply_list_filters`)
- Modify: `backend/apps/orders/transport/views/order_views_impl/order_list_view.py:412-418`
- Modify: `backend/apps/orders/transport/views/order_views_impl/sheet3_view.py:85-93`
- Test: `backend/apps/orders/tests/test_order_list_contract.py`

**Interfaces:**
- Consumes: `consulting_q()` và `info_incomplete_q()` từ Task 1.
- Produces: khoá filter `"consulting"` được `_apply_list_filters` hiểu, nhận `"only"` (giữ lại đơn Tư vấn) hoặc `"exclude"` (loại đơn Tư vấn); giá trị khác/rỗng là no-op. Task 4 không dùng khoá này (báo cáo annotate trực tiếp bằng `sheet_q`).

- [ ] **Step 1: Viết test đỏ cho 3 endpoint**

Thêm class sau vào cuối `backend/apps/orders/tests/test_order_list_contract.py`:

```python
class LandingSheetRoutingTests(TestCase):
    """Đơn đi đúng MỘT tab Landing và không nhảy tab khi sale bổ sung thông tin.

    Khoá hành vi của yêu cầu 2026-08-11: đơn ingest về không có địa chỉ
    (has_address=False) ở lại tab Tư vấn kể cả sau khi điền đủ Tên/SĐT/Địa chỉ,
    và KHÔNG xuất hiện ở Landing/Messenger xác nhận đơn.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-sheet-routing")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(code="fb-route", name="FB Route", owner="FB Route")
        self.customer = Customer.objects.create(
            full_name="KH Route", phone_raw="0909555001", phone_normalized="0909555001"
        )

    def _order(self, **kwargs):
        defaults = {
            "channel": self.channel,
            "customer": self.customer,
            "order_source": "landing",
            "name": "Nguyen A",
            "phone": "0909555001",
            "raw_address": "1 Nguyen Trai, Q1",
        }
        return Order.objects.create(**{**defaults, **kwargs})

    def _ids(self, endpoint, body=None):
        response = self.client.post(
            endpoint, {"limit": "100", "offset": "0", **(body or {})}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {item["id"] for item in response.json()["results"]}

    def test_sticky_order_stays_in_sheet3_after_address_filled(self):
        # Đơn đổ về không địa chỉ, sale đã điền đủ thông tin sau đó.
        sticky = self._order(has_address=False)

        self.assertIn(sticky.id, self._ids("/api/orders/sheet3"))
        self.assertNotIn(sticky.id, self._ids("/api/orders/landing", {"sheet": "1"}))
        self.assertNotIn(sticky.id, self._ids("/api/orders/landing", {"sheet": "2"}))

    def test_sticky_messenger_order_not_in_sheet2(self):
        sticky = self._order(order_source="messenger", has_address=False)

        self.assertIn(sticky.id, self._ids("/api/orders/sheet3"))
        self.assertNotIn(sticky.id, self._ids("/api/orders/landing", {"sheet": "2"}))

    def test_complete_order_with_ingest_address_goes_to_sheet1_only(self):
        normal = self._order()

        self.assertIn(normal.id, self._ids("/api/orders/landing", {"sheet": "1"}))
        self.assertNotIn(normal.id, self._ids("/api/orders/sheet3"))

    def test_order_missing_phone_still_in_sheet3(self):
        no_phone = self._order(phone="")

        self.assertIn(no_phone.id, self._ids("/api/orders/sheet3"))
        self.assertNotIn(no_phone.id, self._ids("/api/orders/landing", {"sheet": "1"}))

    def test_summary_tab_shows_every_landing_order(self):
        # Tab "Tổng hợp" không gửi `sheet` → thấy cả đơn Tư vấn lẫn đơn xác nhận,
        # nên không đơn nào biến mất khỏi giao diện.
        sticky = self._order(has_address=False)
        normal = self._order()

        summary_ids = self._ids("/api/orders/landing")
        self.assertIn(sticky.id, summary_ids)
        self.assertIn(normal.id, summary_ids)

    def test_sheet1_and_sheet3_are_disjoint(self):
        self._order(has_address=False)
        self._order()
        self._order(phone="")

        sheet1 = self._ids("/api/orders/landing", {"sheet": "1"})
        sheet3 = self._ids("/api/orders/sheet3")
        self.assertEqual(sheet1 & sheet3, set())
```

Kiểm tra khối import ở đầu file trước khi thêm; cần có `Channel`, `Customer`, `Order`, `APIClient`, `make_admin`, `TestCase`.

- [ ] **Step 2: Chạy test để xác nhận nó đỏ**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_consulting_t2 -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=10 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_order_list_contract.py::LandingSheetRoutingTests -q --create-db'
```

Kỳ vọng: FAIL. `test_sticky_order_stays_in_sheet3_after_address_filled` báo đơn không có trong `/api/orders/sheet3` (vì hiện tại `info_complete="false"` loại nó) và có mặt trong `sheet=1` (vì hiện tại nó đủ thông tin).

- [ ] **Step 3: Thêm khoá filter `consulting` và dùng lại `info_incomplete_q()`**

Trong `backend/apps/orders/repositories.py`, thay khối xử lý `info_complete` trong `_apply_list_filters` (hiện dòng 682-695) bằng:

```python
        # info_complete: "true" = có Tên+SĐT+Địa chỉ; "false" = thiếu.
        # Q dùng chung với sheet_rules để list view / export / bộ chia đơn không lệch.
        info_complete = str(filters.get("info_complete") or "").strip().lower()
        if info_complete in ("true", "false"):
            incomplete = info_incomplete_q()
            if info_complete == "true":
                queryset = queryset.exclude(incomplete)
            else:
                queryset = queryset.filter(incomplete)
        # consulting: trục phân tab Landing. "only" = board Tư vấn (Sheet 3),
        # "exclude" = Sheet 1/2. Khác info_complete ở chỗ có tính cờ bất biến
        # has_address, nên đơn đổ về không địa chỉ KHÔNG rời Tư vấn khi được
        # điền đủ thông tin (yêu cầu 2026-08-11).
        consulting = str(filters.get("consulting") or "").strip().lower()
        if consulting == "only":
            queryset = queryset.filter(consulting_q())
        elif consulting == "exclude":
            queryset = queryset.exclude(consulting_q())
```

Thêm import ở đầu `repositories.py` (đặt cùng cụm import `apps.orders`):

```python
from apps.orders.sheet_rules import consulting_q, info_incomplete_q
```

Nếu import này gây vòng lặp import (`sheet_rules` không import `repositories` nên không được phép xảy ra — nhưng nếu ruff/Django báo lỗi), chuyển sang import cục bộ trong thân `_apply_list_filters` giống cách file này đã làm với các import Django khác.

- [ ] **Step 4: Đổi 3 call site sang khoá mới**

Trong `backend/apps/orders/transport/views/order_views_impl/order_list_view.py`, thay khối phân Sheet 1/2 (hiện dòng 412-418):

```python
    # Phân Sheet 1/2 (trang Landing): sheet=1 = Landing+Hotline+Zalo; sheet=2 =
    # Messenger. Cả hai LOẠI đơn thuộc board Tư vấn (thiếu thông tin hoặc đổ về
    # không có địa chỉ) — xem sheet_rules.consulting_q. Không truyền sheet =
    # hành vi cũ (tab Tổng hợp: mọi nguồn trừ koc).
    sheet = str(source.get("sheet") or "").strip()
    if sheet == "1":
        filters["consulting"] = "exclude"
        filters["order_source_exclude"] = ["koc", "kol", "messenger", *MARKETPLACE_ORDER_SOURCES]
    elif sheet == "2":
        filters["consulting"] = "exclude"
        filters["order_source"] = "messenger"
```

Trong `backend/apps/orders/transport/views/order_views_impl/sheet3_view.py`, trong dict `filters`, thay dòng `"info_complete": "false",` bằng:

```python
        # Board Tư vấn: thiếu thông tin HOẶC đổ về không có địa chỉ (cờ bất biến).
        "consulting": "only",
```

Giữ nguyên dòng `"has_address": source.get("has_address", ""),` ngay dưới — đó là chip lọc của người dùng, áp AND trong phạm vi board và vẫn có nghĩa vì board chứa cả đơn thiếu SĐT mà có địa chỉ.

Trong `backend/apps/orders/repositories.py`, trong `_section_filter_overrides` (hiện dòng 149-157), thay hai dòng `overrides["info_complete"] = "true"` bằng `overrides["consulting"] = "exclude"`, giữ nguyên phần còn lại:

```python
        sheet = str(source.get("sheet") or "").strip()
        if sheet == "1":
            overrides["consulting"] = "exclude"
            overrides["order_source_exclude"] = [
                "koc", "kol", "messenger", *MARKETPLACE_ORDER_SOURCES,
            ]
        elif sheet == "2":
            overrides["consulting"] = "exclude"
            overrides["order_source"] = "messenger"
```

- [ ] **Step 5: Chạy test để xác nhận nó xanh**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_consulting_t2 -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=10 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_order_list_contract.py apps/orders/tests/test_ingest_api.py apps/orders/tests/test_marketplace_order_source.py apps/orders/tests/test_orders_export_definition.py -q --create-db'
```

Kỳ vọng: PASS toàn bộ. Bốn file này là vùng va chạm: `test_ingest_api.py` khoá bộ lọc `has_address`, `test_marketplace_order_source.py` khoá phạm vi Sheet 3, `test_orders_export_definition.py` khoá đường xuất Excel theo sheet.

Nếu `test_marketplace_order_source.py::LandingListExcludesMarketplaceTests` đỏ: fixture ở đó tạo đơn thiếu thông tin **không** đặt `has_address=False`, và điều đó vẫn đúng theo luật mới (thiếu thông tin là đủ để vào Sheet 3). Đọc kỹ thông báo lỗi trước khi sửa fixture — nếu nó đỏ thì nhiều khả năng là lỗi thật ở Step 3/4.

- [ ] **Step 6: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting
git add backend/apps/orders/repositories.py backend/apps/orders/transport/views/order_views_impl/order_list_view.py backend/apps/orders/transport/views/order_views_impl/sheet3_view.py backend/apps/orders/tests/test_order_list_contract.py
git commit -m "feat(orders): route landing sheets through shared consulting filter"
```

---

### Task 3: Nhóm 10 group theo sheet ở backend

**Files:**
- Modify: `backend/apps/orders/_repositories_reports.py:509-567`
- Modify: `backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py:30,64,92`
- Test: `backend/apps/orders/tests/test_sale_reports.py:226-342`

**Interfaces:**
- Consumes: `sheet_q()` từ Task 1.
- Produces: `OrderRepository.summary_closed_revenue_by_staff_sheet(date_from=None, date_to=None, *, sale_channel_ids=None) -> list[dict]`. Mỗi dict có khoá: `staff_id`, `staff__username`, `staff__display_name`, `sheet` (một trong `"sheet1"` / `"sheet2"` / `"sheet3"` / `""`), `closed_orders` (int), `closed_revenue` (str), `returned_orders` (int), `net_revenue` (str). Payload `/api/orders/dashboard/summary` đổi khoá `closed_revenue_by_staff_source` → `closed_revenue_by_staff_sheet`. Task 4 tiêu thụ đúng shape này.

- [ ] **Step 1: Viết test đỏ**

Trong `backend/apps/orders/tests/test_sale_reports.py`, sửa class `SaleSourceClosedRevenueTests`.

Đổi docstring + hai helper ở đầu class:

```python
class SaleSheetClosedRevenueTests(_ReportFixtureMixin, TestCase):
    """Nhóm 10 Báo cáo Sale — đơn chốt, đơn hoàn & doanh thu theo (sale, sheet).

    Từ 2026-08-11 trục cột là 3 tab của trang Landing (Landing xác nhận đơn /
    Messenger xác nhận đơn / Tư vấn) thay cho nguồn đơn, dùng đúng
    ``sheet_rules.sheet_q`` mà màn hình dùng nên báo cáo không lệch tab.

    Vẫn khác Nhóm 9 ở hai điểm: chỉ nhìn 4 nguồn lead nội bộ (bỏ sàn / KOC /
    KOL / đơn chưa gắn nguồn) và tách thêm số đơn hoàn + doanh thu giao thành
    công (tổng trừ tiền đơn hoàn) trên rổ hoàn 4/5/12/13.
    """

    URL = "/api/orders/dashboard/summary"

    def _rows(self):
        return OrderRepository.summary_closed_revenue_by_staff_sheet()

    def _row_for(self, rows, sheet):
        matches = [row for row in rows if row["sheet"] == sheet]
        self.assertEqual(len(matches), 1, f"kỳ vọng đúng 1 hàng cho sheet {sheet}")
        return matches[0]
```

`_ReportFixtureMixin._order` tạo đơn có `name="X"` và `phone` nhưng **không** có địa chỉ — theo luật mới các đơn đó rơi vào `sheet3`. Vì vậy mọi test cũ trong class phải đổi `"landing"` thành `"sheet3"` ở lời gọi `_row_for`. Cụ thể, trong các test `test_returned_orders_counted_and_deducted_from_net_revenue`, `test_whole_return_bucket_counted_as_returned`, `test_scheduled_and_unclosed_statuses_not_counted`, `test_cancelled_orders_excluded`, đổi:

```python
        row = self._row_for(self._rows(), "sheet3")
```

Thay `test_rows_split_per_source` bằng:

```python
    def test_rows_split_per_sheet(self):
        # Đủ Tên+SĐT+Địa chỉ và ingest có địa chỉ → Sheet 1 / Sheet 2 theo nguồn.
        self._order("100000", "closed", raw_address="1 Nguyen Trai")
        self._order("200000", "closed", order_source="messenger", raw_address="2 Le Loi")
        # Thiếu địa chỉ → Tư vấn.
        self._order("400000", "closed")

        rows = self._rows()
        self.assertEqual({row["sheet"] for row in rows}, {"sheet1", "sheet2", "sheet3"})
        self.assertEqual(
            Decimal(self._row_for(rows, "sheet2")["closed_revenue"]), Decimal("200000")
        )
        self.assertEqual(
            sum(Decimal(row["closed_revenue"]) for row in rows), Decimal("700000")
        )

    def test_sticky_consulting_order_counted_in_sheet3_not_sheet1(self):
        # Đơn đổ về không địa chỉ, sale điền đủ rồi chốt → vẫn là doanh thu Tư vấn.
        self._order("500000", "closed", raw_address="3 Tran Hung Dao", has_address=False)

        rows = self._rows()
        self.assertEqual([row["sheet"] for row in rows], ["sheet3"])
        self.assertEqual(Decimal(rows[0]["closed_revenue"]), Decimal("500000"))
```

Trong `test_marketplace_koc_and_sourceless_orders_excluded`, đổi dòng assert cuối cùng về sheet:

```python
        rows = self._rows()
        self.assertEqual([row["sheet"] for row in rows], ["sheet3"])
        self.assertEqual(Decimal(rows[0]["closed_revenue"]), Decimal("100000"))
```

Trong `test_exposed_on_dashboard_summary`, đổi khoá payload và giá trị kỳ vọng:

```python
        payload = self.client.get(self.URL).json()
        rows = payload["closed_revenue_by_staff_sheet"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sheet"], "sheet3")
        self.assertEqual(rows[0]["closed_orders"], 1)
        self.assertEqual(rows[0]["returned_orders"], 1)
```

Cuối cùng, `_ReportFixtureMixin._order` cần nhận thêm hai tham số. Trong `_ReportFixtureMixin`, sửa chữ ký và thân hàm `_order`:

```python
    def _order(
        self,
        total,
        sales_status_code=None,
        *,
        staff=None,
        status=0,
        order_source="landing",
        landing_confirmed="0",
        raw_address="",
        has_address=True,
    ):
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            staff=staff or self.sale,
            name="X",
            phone="0900000009",
            raw_address=raw_address,
            has_address=has_address,
            status=status,
            order_source=order_source,
            total_after_discount=Decimal(total),
            landing_confirmed_amount=Decimal(landing_confirmed),
        )
        if sales_status_code is not None:
            OrderAssessment.objects.create(order=order, sales_status_code=sales_status_code)
        return order
```

- [ ] **Step 2: Chạy test để xác nhận nó đỏ**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_consulting_t3 -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=11 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_sale_reports.py::SaleSheetClosedRevenueTests -q --create-db'
```

Kỳ vọng: FAIL với `AttributeError: type object 'OrderRepository' has no attribute 'summary_closed_revenue_by_staff_sheet'`.

- [ ] **Step 3: Đổi aggregate sang trục sheet**

Trong `backend/apps/orders/_repositories_reports.py`, thay toàn bộ method `summary_closed_revenue_by_staff_source` (hiện dòng 509-567) bằng:

```python
    @classmethod
    def summary_closed_revenue_by_staff_sheet(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        """Nhóm 10 — một hàng cho mỗi cặp (nhân viên sale, sheet Landing).

        Trục cột là 3 tab của trang Landing (``sheet1`` Landing xác nhận đơn /
        ``sheet2`` Messenger xác nhận đơn / ``sheet3`` Tư vấn), phân bằng ĐÚNG
        ``sheet_rules.sheet_q`` mà list view dùng — báo cáo và màn hình không
        thể lệch nhau. Vì Sheet 3 dính theo cờ bất biến ``has_address``, cột Tư
        vấn chính là doanh thu chốt được từ data đổ về không có địa chỉ.

        Trả hàng PHẲNG; FE tự dựng ma trận sale × sheet. Tối đa ~25 sale × 3
        sheet = 75 hàng, một query group-by.

        Chỉ nhìn ``SALE_SOURCE_REPORT_ORDER_SOURCES`` (đúng 4 nguồn của
        ``SHEET3_SOURCES``) — điều kiện này tự loại luôn đơn có ``order_source``
        NULL/rỗng, nên tổng của Nhóm 10 CỐ Ý nhỏ hơn Nhóm 9 đúng bằng phần đơn
        sàn / KOC / chưa gắn nguồn.

        Bốn số, tất cả tính trên cùng tập "đơn đã chốt"
        (``CLOSED_REVENUE_SALES_STATUS_CODES`` — đã chốt / hoả tốc / đã lên đơn,
        KHÔNG gồm "đã chốt, hẹn ngày", cùng tiêu chí Nhóm 9):

        - ``closed_orders`` / ``closed_revenue``: số đơn chốt và tổng tiền của
          chính tập đơn đó, kể cả đơn sau này bị hoàn.
        - ``returned_orders``: trong tập trên, bao nhiêu đơn rơi vào rổ hoàn
          ``RETURN_ORDER_STATUSES`` (4/5/12/13) — cả đơn đang trên đường hoàn,
          không chỉ đơn đã hoàn xong.
        - ``net_revenue``: ``closed_revenue`` trừ tiền của các đơn hoàn đó.

        Đơn đã huỷ đã bị ``_base_orders_queryset`` loại, không cần điều kiện thêm.
        """
        from apps.orders.sheet_rules import sheet_q

        queryset = cls._sales_report_orders_queryset(sale_channel_ids=sale_channel_ids)
        queryset = cls._apply_created_at_date_filters(queryset, date_from, date_to)
        queryset = queryset.filter(order_source__in=SALE_SOURCE_REPORT_ORDER_SOURCES)
        # sheet3 xét TRƯỚC: ba Q rời nhau nên thứ tự không đổi kết quả, nhưng
        # đặt vậy để đọc khớp thứ tự ưu tiên nghiệp vụ (mirror classify_sheet).
        sheet_case = Case(
            When(sheet_q("sheet3"), then=Value("sheet3")),
            When(sheet_q("sheet2"), then=Value("sheet2")),
            When(sheet_q("sheet1"), then=Value("sheet1")),
            default=Value(""),
            output_field=CharField(),
        )
        closed_q = Q(assessment__sales_status_code__in=CLOSED_REVENUE_SALES_STATUS_CODES)
        returned_q = closed_q & Q(status__in=RETURN_ORDER_STATUSES)
        net_q = closed_q & ~Q(status__in=RETURN_ORDER_STATUSES)
        rows = list(
            queryset.annotate(sheet=sheet_case)
            .values(
                "staff_id",
                "staff__username",
                "staff__display_name",
                "sheet",
            )
            .annotate(
                closed_orders=Count("id", filter=closed_q),
                closed_revenue=Sum("total_after_discount", filter=closed_q),
                returned_orders=Count("id", filter=returned_q),
                net_revenue=Sum("total_after_discount", filter=net_q),
            )
            .order_by("staff__username", "staff_id", "sheet")
        )
        for row in rows:
            for field in ("closed_revenue", "net_revenue"):
                row[field] = str(row[field] or Decimal("0"))
            for field in ("closed_orders", "returned_orders"):
                row[field] = row[field] or 0
        return rows
```

Bổ sung `Case`, `When`, `Value`, `CharField` vào khối import `django.db.models` ở đầu `_repositories_reports.py` nếu chưa có. Kiểm tra trước bằng:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting && grep -n "from django.db.models import" -A 5 backend/apps/orders/_repositories_reports.py | head -12
```

- [ ] **Step 4: Đổi tên key ở endpoint summary**

Trong `backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py`, đổi ba chỗ.

Dòng 30, trong `inline_serializer("DashboardSummaryResponse", fields={...})`:

```python
                "closed_revenue_by_staff_sheet": drf_serializers.JSONField(),
```

Dòng 64-66:

```python
    closed_revenue_by_staff_sheet = OrderRepository.summary_closed_revenue_by_staff_sheet(
        date_from=date_from, date_to=date_to, sale_channel_ids=sale_channel_ids
    )
```

Dòng 92, trong dict `data`:

```python
        "closed_revenue_by_staff_sheet": closed_revenue_by_staff_sheet,
```

- [ ] **Step 5: Chạy test để xác nhận nó xanh**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_consulting_t3 -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=11 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_sale_reports.py apps/orders/tests/test_dashboard_summary_api.py -q --create-db'
```

Kỳ vọng: PASS cả hai file. `test_dashboard_summary_api.py` có thể tham chiếu khoá cũ — nếu đỏ, đổi `closed_revenue_by_staff_source` thành `closed_revenue_by_staff_sheet` ở đó.

- [ ] **Step 6: Cập nhật `schema.yaml` đúng một khoá**

Chỉ sửa tên property, KHÔNG regen toàn file (`schema.yaml` đang lệch sẵn ~444 dòng so với code; regen sẽ kéo nhiễu của feature khác vào MR này):

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting && grep -n "closed_revenue_by_staff_source" backend/schema.yaml
```

Đổi từng dòng tìm được thành `closed_revenue_by_staff_sheet`, giữ nguyên thụt lề. Sau đó sinh lại type FE:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting/frontend && npm run gen:api
```

- [ ] **Step 7: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting
git add backend/apps/orders/_repositories_reports.py backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py backend/apps/orders/tests/test_sale_reports.py backend/apps/orders/tests/test_dashboard_summary_api.py backend/schema.yaml frontend/src/api/schema.d.ts
git commit -m "feat(reports): group sale report block 10 by landing sheet"
```

---

### Task 4: Nhóm 10 hiển thị 3 cột sheet ở frontend

**Files:**
- Modify: `frontend/src/features/orders/landing-tabs.ts`
- Rename + modify: `frontend/src/features/dashboard/sale-source-revenue.ts` → `frontend/src/features/dashboard/sale-sheet-revenue.ts`
- Rename + modify: `frontend/src/features/dashboard/components/SaleSourceRevenueSection.tsx` → `frontend/src/features/dashboard/components/SaleSheetRevenueSection.tsx`
- Modify: `frontend/src/features/dashboard/types.ts:98-105,134`
- Modify: `frontend/src/pages/DashboardPage.tsx:146-151`
- Rename + modify: `frontend/src/features/dashboard/__tests__/sale-source-revenue.test.tsx` → `frontend/src/features/dashboard/__tests__/sale-sheet-revenue.test.tsx`

**Interfaces:**
- Consumes: shape hàng từ Task 3 (`sheet` thay `order_source`, key payload `closed_revenue_by_staff_sheet`).
- Produces: `SHEET_LABELS` export từ `features/orders/landing-tabs.ts`; `SALE_SHEET_REPORT_KEYS`, `buildSaleSheetMatrix`, `SaleSheetCell`, `SaleSheetMatrixRow`, `SaleSheetMatrix` export từ `features/dashboard/sale-sheet-revenue.ts`; component `SaleSheetRevenueSection`.

- [ ] **Step 1: Tách `SHEET_LABELS` dùng chung**

Trong `frontend/src/features/orders/landing-tabs.ts`, thay khối `LANDING_SHEET_TABS` bằng:

```ts
/** Nhãn 3 sheet của trang Landing — nguồn duy nhất, dùng chung với báo cáo sale
 * Nhóm 10 để hai chỗ không lệch chữ. */
export const SHEET_LABELS = {
  sheet1: "Landing xác nhận đơn",
  sheet2: "Messenger xác nhận đơn",
  sheet3: "Tư vấn",
} as const;

export type SheetKey = keyof typeof SHEET_LABELS;

// Tab riêng cho trang Landing: 3 sheet thay tab "Đơn hàng sale".
export const LANDING_SHEET_TABS: Array<{ key: LandingTabKey; label: string }> = [
  { key: "summary", label: "Tổng hợp" },
  { key: "sheet1", label: SHEET_LABELS.sheet1 },
  { key: "sheet2", label: SHEET_LABELS.sheet2 },
  { key: "sheet3", label: SHEET_LABELS.sheet3 },
  { key: "delivered", label: "Giao thành công" },
  { key: "cancelled", label: "Đã hủy" },
  { key: "blocklist", label: "Danh sách blocklist" },
];
```

- [ ] **Step 2: Viết test đỏ cho ma trận theo sheet**

Đổi tên file test rồi sửa nội dung:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting
git mv frontend/src/features/dashboard/__tests__/sale-source-revenue.test.tsx frontend/src/features/dashboard/__tests__/sale-sheet-revenue.test.tsx
```

Trong file vừa đổi tên, thay toàn bộ nội dung bằng:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { I18nProvider, useI18n } from "@/i18n";
import { SaleSheetRevenueSection } from "../components/SaleSheetRevenueSection";
import { buildSaleSheetMatrix, SALE_SHEET_REPORT_KEYS } from "../sale-sheet-revenue";
import type { SaleSheetRevenueRow } from "../types";

function makeRow(overrides: Partial<SaleSheetRevenueRow>): SaleSheetRevenueRow {
  return {
    staff_id: 1,
    staff__username: "sale",
    staff__display_name: null,
    sheet: "sheet1",
    closed_orders: 0,
    closed_revenue: "0",
    returned_orders: 0,
    net_revenue: "0",
    ...overrides,
  };
}

function Harness({ rows }: { rows: SaleSheetRevenueRow[] }) {
  const { t } = useI18n();
  return <SaleSheetRevenueSection t={t} rows={rows} unknownStaffLabel="Không xác định" />;
}

function renderSection(rows: SaleSheetRevenueRow[]) {
  return render(
    <I18nProvider>
      <Harness rows={rows} />
    </I18nProvider>,
  );
}

const ROWS = [
  makeRow({
    staff_id: 1,
    staff__display_name: "Linh",
    sheet: "sheet1",
    closed_orders: 10,
    closed_revenue: "1000000",
    returned_orders: 2,
    net_revenue: "800000",
  }),
  makeRow({
    staff_id: 2,
    staff__display_name: "Hạnh",
    sheet: "sheet3",
    closed_orders: 30,
    closed_revenue: "3000000",
    returned_orders: 5,
    net_revenue: "2500000",
  }),
];

describe("SaleSheetRevenueSection", () => {
  it("mỗi sheet 4 cột + nhóm cột Tổng, cột đầu là tên nhân viên", () => {
    renderSection(ROWS);

    const headerCells = screen.getAllByRole("columnheader");
    const groupCount = SALE_SHEET_REPORT_KEYS.length + 1; // + nhóm "Tổng"
    expect(groupCount).toBe(4);
    // tầng 1: 1 (Nhân viên, rowSpan) + mỗi nhóm 1 ô; tầng 2: mỗi nhóm 4 chỉ số
    expect(headerCells).toHaveLength(1 + groupCount + groupCount * 4);
    expect(headerCells[0]).toHaveTextContent("Nhân viên");
    expect(screen.getAllByText("Landing xác nhận đơn").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Messenger xác nhận đơn").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Tư vấn").length).toBeGreaterThan(0);
    // Trục nguồn đơn đã bị thay hẳn.
    expect(screen.queryByText("Hotline")).toBeNull();
    expect(screen.queryByText("Zalo")).toBeNull();
  });

  it("sắp xếp theo tổng doanh thu giảm dần", () => {
    renderSection(ROWS);

    const names = screen.getAllByTestId("sale-sheet-staff").map((el) => el.textContent);
    expect(names).toEqual(["Hạnh", "Linh"]);
  });

  it("hàng Tổng cộng đúng doanh thu, đơn chốt và đơn hoàn", () => {
    renderSection(ROWS);

    const totalRow = screen.getByTestId("sale-sheet-total-row");
    expect(within(totalRow).getByText("4.000.000")).toBeInTheDocument();
    expect(within(totalRow).getByText("3.300.000")).toBeInTheDocument();
    expect(within(totalRow).getAllByText("40").length).toBeGreaterThan(0);
    expect(within(totalRow).getAllByText("7").length).toBeGreaterThan(0);
  });

  it("không có dòng nào -> hiện empty state", () => {
    renderSection([]);

    expect(screen.getByText("Không có dữ liệu để hiển thị")).toBeInTheDocument();
    expect(screen.queryByTestId("sale-sheet-total-row")).toBeNull();
  });

  it("thiếu tên hiển thị -> dùng nhãn Không xác định", () => {
    renderSection([makeRow({ staff_id: null, staff__username: null, closed_orders: 1 })]);

    expect(screen.getByTestId("sale-sheet-staff")).toHaveTextContent("Không xác định");
  });
});

describe("buildSaleSheetMatrix", () => {
  it("trải đủ 3 sheet kể cả sheet không phát sinh trong kỳ", () => {
    const { rows } = buildSaleSheetMatrix([
      makeRow({ sheet: "sheet3", closed_orders: 3, closed_revenue: "300" }),
    ]);

    expect(rows).toHaveLength(1);
    expect(rows[0].cells).toHaveLength(3);
    const sheet3Index = SALE_SHEET_REPORT_KEYS.indexOf("sheet3");
    expect(rows[0].cells[sheet3Index]).toEqual({
      closedOrders: 3,
      closedRevenue: 300,
      returnedOrders: 0,
      netRevenue: 0,
    });
    const sheet1Index = SALE_SHEET_REPORT_KEYS.indexOf("sheet1");
    expect(rows[0].cells[sheet1Index]).toEqual({
      closedOrders: 0,
      closedRevenue: 0,
      returnedOrders: 0,
      netRevenue: 0,
    });
  });

  it("ô Tổng của mỗi sale là cộng ngang các sheet", () => {
    const { rows } = buildSaleSheetMatrix([
      makeRow({
        sheet: "sheet1",
        closed_orders: 2,
        closed_revenue: "200",
        returned_orders: 1,
        net_revenue: "100",
      }),
      makeRow({
        sheet: "sheet3",
        closed_orders: 5,
        closed_revenue: "500",
        returned_orders: 4,
        net_revenue: "400",
      }),
    ]);

    expect(rows[0].total).toEqual({
      closedOrders: 7,
      closedRevenue: 700,
      returnedOrders: 5,
      netRevenue: 500,
    });
  });

  it("hàng Tổng là cộng dọc toàn bộ sale", () => {
    const { totals } = buildSaleSheetMatrix([
      makeRow({ staff_id: 1, closed_orders: 2, closed_revenue: "200", net_revenue: "150" }),
      makeRow({ staff_id: 2, closed_orders: 3, closed_revenue: "300", net_revenue: "250" }),
    ]);

    const sheet1Index = SALE_SHEET_REPORT_KEYS.indexOf("sheet1");
    expect(totals.cells[sheet1Index].closedOrders).toBe(5);
    expect(totals.total).toEqual({
      closedOrders: 5,
      closedRevenue: 500,
      returnedOrders: 0,
      netRevenue: 400,
    });
  });

  it("sắp xếp sale theo tổng doanh thu giảm dần", () => {
    const { rows } = buildSaleSheetMatrix([
      makeRow({ staff_id: 1, staff__display_name: "Linh", closed_revenue: "1000" }),
      makeRow({ staff_id: 2, staff__display_name: "Hạnh", closed_revenue: "3000" }),
    ]);

    expect(rows.map((row) => row.staff__display_name)).toEqual(["Hạnh", "Linh"]);
  });

  it("đơn không rơi vào sheet nào vẫn được cộng vào ô Tổng", () => {
    // Backend trả sheet="" cho đơn ngoài 3 tab -> không được âm thầm hụt tổng.
    const { rows, totals } = buildSaleSheetMatrix([
      makeRow({ sheet: "", closed_orders: 4, closed_revenue: "400" }),
    ]);

    expect(rows[0].total.closedRevenue).toBe(400);
    expect(rows[0].cells.every((cell) => cell.closedRevenue === 0)).toBe(true);
    expect(totals.total.closedRevenue).toBe(400);
  });

  it("payload rỗng hoặc undefined -> không có hàng nào", () => {
    expect(buildSaleSheetMatrix([]).rows).toEqual([]);
    expect(buildSaleSheetMatrix(undefined).rows).toEqual([]);
  });
});
```

- [ ] **Step 3: Chạy test để xác nhận nó đỏ**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting/frontend && npx vitest run src/features/dashboard/__tests__/sale-sheet-revenue.test.tsx
```

Kỳ vọng: FAIL ngay ở bước resolve import — `Failed to resolve import "../sale-sheet-revenue"`.

- [ ] **Step 4: Đổi kiểu payload**

Trong `frontend/src/features/dashboard/types.ts`, thay `SaleSourceRevenueRow` (dòng 98-105) bằng:

```ts
export type SaleSheetRevenueRow = {
  staff_id: number | null;
  staff__username: string | null;
  staff__display_name: string | null;
  /** "sheet1" | "sheet2" | "sheet3", hoặc "" khi đơn không rơi vào tab nào. */
  sheet: string;
  closed_orders: number;
  closed_revenue: string;
  returned_orders: number;
  net_revenue: string;
};
```

và dòng 134:

```ts
  closed_revenue_by_staff_sheet?: SaleSheetRevenueRow[];
```

- [ ] **Step 5: Viết module ma trận theo sheet**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting
git mv frontend/src/features/dashboard/sale-source-revenue.ts frontend/src/features/dashboard/sale-sheet-revenue.ts
```

Thay toàn bộ nội dung `frontend/src/features/dashboard/sale-sheet-revenue.ts` bằng:

```ts
import { SHEET_LABELS, type SheetKey } from "@/features/orders/landing-tabs";

import type { SaleSheetRevenueRow } from "./types";

/**
 * Ba sheet hiển thị ở Nhóm 10, thứ tự CỐ ĐỊNH và luôn đủ cột kể cả kỳ không
 * phát sinh — bảng không nhảy cột khi đổi kỳ. Phải khớp giá trị `sheet` mà
 * backend annotate từ `sheet_rules.sheet_q`; sheet nào lệch thì hàng của
 * backend rơi vào ô không tồn tại và biến mất khỏi cột nhưng vẫn vào ô Tổng.
 */
export const SALE_SHEET_REPORT_KEYS: readonly SheetKey[] = ["sheet1", "sheet2", "sheet3"];

export const SALE_SHEET_REPORT_LABELS: Record<SheetKey, string> = SHEET_LABELS;

export type SaleSheetCell = {
  closedOrders: number;
  closedRevenue: number;
  returnedOrders: number;
  /** Doanh thu giao thành công = `closedRevenue` trừ tiền đơn hoàn. */
  netRevenue: number;
};

export type SaleSheetMatrixRow = {
  staffId: number | null;
  staff__display_name: string | null;
  staff__username: string | null;
  /** Ô theo từng sheet, luôn đủ `SALE_SHEET_REPORT_KEYS` phần tử. */
  cells: SaleSheetCell[];
  /** Cộng ngang 3 sheet — nhóm cột "Tổng" ở cuối bảng. */
  total: SaleSheetCell;
};

export type SaleSheetMatrix = {
  rows: SaleSheetMatrixRow[];
  /** Cộng dọc toàn bộ sale; `totals.total` là tổng toàn bảng. */
  totals: Pick<SaleSheetMatrixRow, "cells" | "total">;
};

function emptyCell(): SaleSheetCell {
  return { closedOrders: 0, closedRevenue: 0, returnedOrders: 0, netRevenue: 0 };
}

function addInto(target: SaleSheetCell, source: SaleSheetCell): void {
  target.closedOrders += source.closedOrders;
  target.closedRevenue += source.closedRevenue;
  target.returnedOrders += source.returnedOrders;
  target.netRevenue += source.netRevenue;
}

function toCell(row: SaleSheetRevenueRow): SaleSheetCell {
  return {
    closedOrders: row.closed_orders || 0,
    closedRevenue: Number(row.closed_revenue || 0),
    returnedOrders: row.returned_orders || 0,
    netRevenue: Number(row.net_revenue || 0),
  };
}

/**
 * Dựng ma trận sale × sheet từ các hàng phẳng `(staff, sheet)` của API.
 *
 * Sắp xếp theo tổng doanh thu chốt toàn sheet giảm dần; bằng nhau thì theo tên
 * hiển thị để thứ tự ổn định giữa các lần render.
 */
export function buildSaleSheetMatrix(
  rows: readonly SaleSheetRevenueRow[] | undefined,
): SaleSheetMatrix {
  const sheetIndex = new Map<string, number>(
    SALE_SHEET_REPORT_KEYS.map((sheet, index) => [sheet, index]),
  );
  const byStaff = new Map<string, SaleSheetMatrixRow>();

  for (const row of rows ?? []) {
    const key = String(row.staff_id ?? `unknown:${row.staff__username ?? ""}`);
    let entry = byStaff.get(key);
    if (!entry) {
      entry = {
        staffId: row.staff_id,
        staff__display_name: row.staff__display_name,
        staff__username: row.staff__username,
        cells: SALE_SHEET_REPORT_KEYS.map(() => emptyCell()),
        total: emptyCell(),
      };
      byStaff.set(key, entry);
    }
    const cell = toCell(row);
    // Đơn không rơi vào sheet nào (backend trả "") vẫn được cộng vào ô Tổng để
    // tổng bảng không âm thầm hụt so với API.
    const index = sheetIndex.get(row.sheet ?? "");
    if (index !== undefined) addInto(entry.cells[index], cell);
    addInto(entry.total, cell);
  }

  const result = [...byStaff.values()].sort(
    (a, b) =>
      b.total.closedRevenue - a.total.closedRevenue ||
      (a.staff__display_name || a.staff__username || "").localeCompare(
        b.staff__display_name || b.staff__username || "",
      ),
  );

  const totals: SaleSheetMatrix["totals"] = {
    cells: SALE_SHEET_REPORT_KEYS.map(() => emptyCell()),
    total: emptyCell(),
  };
  for (const row of result) {
    row.cells.forEach((cell, index) => addInto(totals.cells[index], cell));
    addInto(totals.total, row.total);
  }

  return { rows: result, totals };
}
```

- [ ] **Step 6: Đổi component sang trục sheet**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting
git mv frontend/src/features/dashboard/components/SaleSourceRevenueSection.tsx frontend/src/features/dashboard/components/SaleSheetRevenueSection.tsx
```

Trong file vừa đổi tên, sửa 6 chỗ.

Chỗ 1 — khối import (dòng 6 và 10-15), thay bằng:

```tsx
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import type { useI18n } from "@/i18n";

import { formatCurrency, resolveDisplayName } from "../dashboard-utils";
import {
  buildSaleSheetMatrix,
  SALE_SHEET_REPORT_KEYS,
  SALE_SHEET_REPORT_LABELS,
  type SaleSheetCell,
} from "../sale-sheet-revenue";
import type { SaleSheetRevenueRow } from "../types";
```

(Bỏ hẳn import `ORDER_SOURCE_LABELS` — trục nguồn không còn dùng.)

Chỗ 2 — docstring + chữ ký hàm (dòng 19-36):

```tsx
/**
 * Nhóm 10 — đơn chốt, đơn hoàn và doanh thu của từng sale, tách theo 3 tab của
 * trang Landing. Hàng = nhân viên, mỗi sheet chiếm 4 cột, nhóm cột "Tổng" ở
 * cuối là cộng ngang (không phải chỉ số mới).
 *
 * Chỉ nhìn 4 nguồn lead nội bộ — đơn sàn / KOC / KOL (tên cũ của KOC) / chưa
 * gắn nguồn đã bị backend loại, nên tổng ở đây CỐ Ý nhỏ hơn Nhóm 9.
 */
export function SaleSheetRevenueSection({
  t,
  rows,
  unknownStaffLabel,
}: {
  t: Translate;
  rows: readonly SaleSheetRevenueRow[] | undefined;
  unknownStaffLabel: string;
}) {
  const matrix = useMemo(() => buildSaleSheetMatrix(rows), [rows]);

  const groups = [
    ...SALE_SHEET_REPORT_KEYS.map((sheet) => ({
      key: sheet as string,
      label: SALE_SHEET_REPORT_LABELS[sheet],
    })),
    { key: "total", label: t("dashboard.totalRow", "Tổng") },
  ];
```

Chỗ 3 — `renderCells` giữ nguyên, nhưng đổi kiểu tham số:

```tsx
  const renderCells = (cell: SaleSheetCell, key: string, bold: boolean) => {
```

Chỗ 4 — tiêu đề và ghi chú card (dòng 71-81):

```tsx
          {t(
            "dashboard.saleSheetRevenueTitle",
            "Nhóm 10 — Đơn chốt, đơn hoàn & doanh thu theo Sale và tab Landing",
          )}
```

```tsx
          {t(
            "dashboard.saleSheetRevenueHint",
            'Chia theo 3 tab của trang Đơn hàng Landing. Tab "Tư vấn" gồm đơn đổ về không có địa chỉ hoặc thiếu Tên/SĐT — đơn đã vào Tư vấn thì ở lại đó kể cả sau khi sale điền đủ thông tin, nên cột này là doanh thu chốt được từ data không địa chỉ. Chỉ tính đơn có nguồn Landing / Zalo / Messenger / Hotline — không gồm đơn sàn (TikTok, Shopee), đơn KOC/KOL và đơn chưa gắn nguồn, nên tổng ở đây nhỏ hơn Nhóm 9. Số đơn chốt = đơn ở trạng thái Đã chốt / Hoả tốc / Đã lên đơn (KHÔNG gồm "Đã chốt, hẹn ngày"); tổng doanh thu là tiền của chính các đơn đó. Số đơn hoàn = trong số đó, đơn đang hoàn hoặc đã hoàn. DThu giao thành công = tổng doanh thu trừ tiền các đơn hoàn — vẫn gồm đơn đang trên đường giao, nên rộng hơn Nhóm 4. Đơn đã huỷ không tính ở cột nào.',
          )}
```

Chỗ 5 — `data-testid` của ô tên nhân viên (dòng 134):

```tsx
                      data-testid="sale-sheet-staff"
```

Chỗ 6 — hai lời gọi `renderCells` còn tham chiếu hằng cũ (dòng 141, 148, 153):

```tsx
                    {row.cells.map((cell, index) =>
                      renderCells(cell, `${row.staffId}-${SALE_SHEET_REPORT_KEYS[index]}`, false),
                    )}
```

```tsx
                <tr data-testid="sale-sheet-total-row" className="font-semibold text-foreground">
```

```tsx
                  {matrix.totals.cells.map((cell, index) =>
                    renderCells(cell, `totals-${SALE_SHEET_REPORT_KEYS[index]}`, true),
                  )}
```

- [ ] **Step 7: Lắp vào DashboardPage**

Trong `frontend/src/pages/DashboardPage.tsx`, thay khối Nhóm 10 (dòng 146-151):

```tsx
          {/* Nhóm 10 — Đơn chốt & doanh thu trước/sau hoàn theo Sale và tab Landing */}
          <SaleSheetRevenueSection
            t={t}
            rows={summary?.closed_revenue_by_staff_sheet}
            unknownStaffLabel={unknownStaffLabel}
          />
```

và đổi dòng import tương ứng ở đầu file (tìm bằng `grep -n "SaleSourceRevenueSection" frontend/src/pages/DashboardPage.tsx`):

```tsx
import { SaleSheetRevenueSection } from "@/features/dashboard/components/SaleSheetRevenueSection";
```

Đường dẫn import có thể là tương đối trong file gốc — giữ đúng dạng đang có, chỉ đổi tên file và tên component.

- [ ] **Step 8: Chạy test để xác nhận nó xanh**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting/frontend && npx vitest run src/features/dashboard/__tests__/sale-sheet-revenue.test.tsx
```

Kỳ vọng: PASS toàn bộ file.

- [ ] **Step 9: Commit**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting
git add -A frontend/src
git commit -m "feat(reports): show sale report block 10 by landing sheet columns"
```

---

### Task 5: Kiểm chứng toàn cục và đẩy MR

**Files:**
- Không sửa file nguồn trừ khi phát hiện hồi quy.

**Interfaces:**
- Consumes: kết quả Task 1-4.
- Produces: nhánh đã push, link MR.

- [ ] **Step 1: Tìm chỗ còn sót tên cũ**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting && grep -rn "closed_revenue_by_staff_source\|summary_closed_revenue_by_staff_source\|SaleSourceRevenue\|sale-source-revenue\|SALE_SOURCE_REPORT_SOURCES\|buildSaleSourceMatrix" backend frontend/src --include=*.py --include=*.ts --include=*.tsx
```

Kỳ vọng: không có kết quả nào (trừ `frontend/src/api/schema.d.ts` nếu chưa regen — nếu vậy chạy lại `npm run gen:api`).

- [ ] **Step 2: Lint backend**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting && docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Kỳ vọng: `All checks passed!`. Lỗi hay gặp: import `Case/When/Value/CharField` thêm ở Task 3 không dùng hết, hoặc import `consulting_q` thừa.

**Đừng tin ruff khi nó bảo xoá một import trông như "unused"** — kiểm tra `grep -n "import \*" <file>` trước; repo này từng có 8 test ERROR lúc collect vì xoá nhầm import được star re-export.

- [ ] **Step 3: Typecheck + lint frontend**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting/frontend && npx tsc --noEmit && npm run lint:strict
```

Kỳ vọng: cả hai sạch.

- [ ] **Step 4: Chạy toàn bộ suite backend orders**

Suite dài — chạy nền, redirect ra file, KHÔNG để shell nền của harness giết nửa chừng:

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting && MSYS_NO_PATHCONV=1 docker run -d --name desi-consulting-suite --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_consulting_full -e REDIS_HOST=desi-test-redis \
  -e REDIS_PASSWORD= -e REDIS_CACHE_DB=12 -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders apps/users -q --create-db'
```

Chờ xong rồi lấy log:

```bash
docker wait desi-consulting-suite && docker logs desi-consulting-suite | tail -40 && docker rm desi-consulting-suite
```

Kỳ vọng: chỉ còn các test đỏ baseline (`test_report_trends` 6 test). Bất kỳ đỏ nào khác là hồi quy.

- [ ] **Step 5: Chạy toàn bộ suite frontend**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting/frontend && npm run test:ci
```

Kỳ vọng: đúng 2 file đỏ / 7 test đỏ baseline (`RequireAuth.test.tsx`, `use-orders-data.realtime-upsert-filters.test.tsx`).

- [ ] **Step 6: Kiểm tra bằng mắt trên app đang chạy**

Trên stack dev local (port 8080), đăng nhập `admin` / `admin123`:

1. Vào **Đơn hàng → Đơn hàng landing**, tab **Tư vấn**. Chọn một đơn chưa có địa chỉ, ghi lại mã đơn.
2. Điền đầy đủ Tên + SĐT + Địa chỉ cho đơn đó, lưu.
3. Quay lại tab **Tư vấn** → đơn **vẫn còn**. Sang tab **Landing xác nhận đơn** và **Messenger xác nhận đơn** → đơn **không xuất hiện**. Sang tab **Tổng hợp** → đơn có mặt.
4. Vào **Báo cáo → Báo cáo sale**, tab Tổng quan, kéo xuống Nhóm 10: bảng có đúng 4 nhóm cột — Landing xác nhận đơn, Messenger xác nhận đơn, Tư vấn, Tổng. Không còn cột Zalo / Hotline.
5. Kiểm tra cộng ngang: với một sale bất kỳ, `Tổng` = `sheet1 + sheet2 + sheet3` ở cả 4 chỉ số.

- [ ] **Step 7: Push và tạo MR**

```bash
cd c:/Users/MYPC/Desktop/workspace/desi-order-wt-consulting
git push -u origin feat/consulting-tab-sticky \
  -o merge_request.create \
  -o merge_request.title="feat(orders): sticky consulting tab and sheet-based sale report block 10" \
  -o merge_request.remove_source_branch
```

Trong mô tả MR, ghi rõ hai điểm để người vận hành không báo lỗi nhầm:

- Đơn tạo **trước migration 0094** có `has_address` mặc định True nên không dính theo cờ; chúng vẫn rời tab Tư vấn khi được điền đủ thông tin. Không backfill được.
- Nguồn legacy `kol` vẫn lọt vào board Tư vấn như trước (tập nguồn của `sheet3_view` không đổi), nhưng **không** được tính vào Nhóm 10 (báo cáo chỉ nhìn 4 nguồn nội bộ) — hành vi này giống hệt trước thay đổi.

Lấy link MR từ output lệnh push và gửi cho người yêu cầu. **Sau khi gửi link MR, đóng băng nhánh này** — thay đổi mới đi MR mới.
