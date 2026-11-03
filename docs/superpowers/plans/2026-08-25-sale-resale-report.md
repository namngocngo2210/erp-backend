# Tab Resale trên Báo cáo Sale — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm tab "Resale" (vị trí thứ 2, ngay sau "Tổng quan") vào trang Báo cáo Sale, hiển thị doanh thu từ khách mua lại theo từng nhân viên, kèm biểu đồ bar ngang xếp hạng.

**Architecture:** Backend thêm một mixin báo cáo mới (`ResaleReportsMixin`) ghép vào `OrderRepository`, phát hiện "đơn resale" bằng subquery `Exists` tương quan trên chính bảng `orders` (khách đã có đơn giao thành công tạo trước đó). Endpoint mới trả một hàng/nhân viên gồm cả số resale lẫn số tổng để FE tính tỉ trọng. Frontend thêm một tab dùng chung bộ lọc ngày của trang, tách logic số liệu vào selector thuần (test được) và chỉ để phần render chart trong component.

**Tech Stack:** Django 5 + DRF + drf-spectacular · PostgreSQL · React 19 + TypeScript + TanStack Query + recharts v3 + Tailwind/shadcn · pytest (Docker) · vitest

**Spec:** `docs/superpowers/specs/2026-08-25-sale-resale-report-design.md` — đọc spec trước khi làm task đầu tiên.

## Global Constraints

- **Nhánh làm việc:** `feat/sale-resale-report`, worktree `.claude/worktrees/sale-resale-report`. Mọi lệnh chạy trong worktree này, KHÔNG chạy ở repo chính.
- **Tuyệt đối không import `apps.customers` từ module production của `apps.orders`.** `.importlinter` xếp `customers | orders | inventory | hr` **cùng tầng, độc lập**; chỉ `apps.orders.tests.*` được miễn. Dùng `cls._base_orders_queryset()` thay vì import `CANCELLED_INVOICE_STATUS`.
- **Đơn resale = khách đã có ≥1 đơn GTC được tạo TRƯỚC `O.created_at`.** GTC = `status=3` + không `invoice_status=2` + `deleted_at IS NULL` + loại chi nhánh ẩn + loại đơn mirror. Cả 5 mệnh đề đã nằm sẵn trong `_base_orders_queryset()` + `status=3`.
- **Phạm vi đơn:** `_sales_report_orders_queryset(sale_channel_ids=...)`. **Tập trạng thái:** `CLOSED_SALES_STATUS_CODES = ("closed", "closed_express", "closed_scheduled_date", "created_order")`. Không được đổi sang `CLOSED_REVENUE_SALES_STATUS_CODES`.
- **Cột tiền qua JSON luôn là chuỗi decimal** (`str(value or Decimal("0"))`), không phải float.
- **Quyền:** `require_section("dashboard")` — không tạo quyền RBAC mới.
- **i18n:** dùng `t("saleReport.<key>", "<fallback tiếng Việt>")` inline. Namespace `saleReport` KHÔNG có file dịch — đừng tạo.
- **Commit message tiếng Anh, ngắn gọn, KHÔNG kèm dòng `Co-Authored-By`.**
- **Python ≥ 3.12, ruff phải sạch** (ruleset trong `backend/pyproject.toml`; lint là bắt buộc từ MR !1069).
- Không chạy `npm ci` — worktree đã có junction `node_modules` sẵn và đã verify chạy được.

### Lệnh chuẩn (đã verify 2026-08-25 trong chính worktree này)

**Backend test** — `POSTGRES_DB` và `REDIS_CACHE_DB` phải RIÊNG cho mỗi lần chạy đồng thời:

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report" && \
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_resale_rep \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend://app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <PATHS> -q --create-db'
```

**Baseline đã đo:** `apps/orders/tests/test_sale_reports.py` → **45 passed in 152s**. Nếu sau khi sửa mà số passed < 45 + số test mới → có hồi quy.

**Backend lint:**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report" && \
docker run --rm -v "$(pwd -W)/backend://app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

**Frontend** (junction đã tạo, đã verify — `2 passed` file / `28 passed` test):

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report/frontend"
npx vitest run src/features/sale-report
npx tsc --noEmit
npx eslint src/features/sale-report src/pages/DashboardPage.tsx
npx prettier --write <chỉ file mình chạm>
```

---

## File Structure

| File | Trách nhiệm |
|---|---|
| `backend/apps/orders/reporting/resale_reports.py` **(mới)** | Chỉ một việc: gộp doanh thu resale theo nhân viên. Không biết gì về HTTP. |
| `backend/apps/orders/reporting/__init__.py` | Ghép mixin thứ 5 vào `OrderReportsMixin`. |
| `backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py` | View mỏng: đọc query param → gọi repository → `Response`. |
| `backend/apps/orders/transport/views/order_views_impl/__init__.py` | Re-export view. |
| `backend/apps/orders/views.py` | Re-export tầng 2 (facade `apps.orders.views`). |
| `backend/apps/orders/urls.py` | Đăng ký route. |
| `backend/schema.yaml` | Sửa TAY (không regen). |
| `backend/apps/orders/tests/test_sale_reports.py` | Thêm **một** class test. |
| `frontend/src/features/sale-report/queries.ts` | Thêm type payload + hook `useSaleResaleReport`. |
| `frontend/src/features/sale-report/sale-report-selectors.ts` | Nới kiểu tham số `saleKey`/`saleLabel` để dùng lại. |
| `frontend/src/features/sale-report/resale-selectors.ts` **(mới)** | Hàm thuần: parse decimal, tính AOV/tỉ trọng, sort, dòng tổng. |
| `frontend/src/features/sale-report/components/ResaleRevenueChart.tsx` **(mới)** | Chỉ vẽ chart. Không fetch, không tính. |
| `frontend/src/features/sale-report/components/SaleResaleTab.tsx` **(mới)** | Khung tab: loading / lỗi / rỗng + chart + bảng. |
| `frontend/src/pages/DashboardPage.tsx` | Chèn tab vào mảng `tabs`. |
| `frontend/src/features/sale-report/__tests__/resale-selectors.test.ts` **(mới)** | Test selector. |
| `frontend/src/features/sale-report/__tests__/SaleResaleTab.test.tsx` **(mới)** | Test khung tab (mock hook). |

**Thứ tự phụ thuộc:** Task 1 → Task 2. Task 3 → Task 4. Task 1 và Task 3 **độc lập** (Task 3 chỉ cần hợp đồng payload đã ghi ở Task 1's Produces). Task 5 sau tất cả.

---

## Task 1: Backend — `ResaleReportsMixin`

**Files:**
- Create: `backend/apps/orders/reporting/resale_reports.py`
- Modify: `backend/apps/orders/reporting/__init__.py`
- Test: `backend/apps/orders/tests/test_sale_reports.py` (thêm class mới ở CUỐI file)

**Interfaces:**
- Consumes: `_base_orders_queryset()`, `_sales_report_orders_queryset(sale_channel_ids=...)`, `_apply_created_at_date_filters(qs, date_from, date_to)` — cả ba resolve qua MRO của `OrderRepository`, KHÔNG import trực tiếp.
- Produces:
  ```python
  OrderRepository.summary_resale_by_staff(
      date_from: str | None = None,
      date_to: str | None = None,
      *,
      sale_channel_ids: list[int] | None = None,
  ) -> list[dict]
  ```
  Mỗi dict có đúng 8 khoá: `staff_id: int | None`, `staff__username: str | None`, `staff__display_name: str | None`, `resale_orders: int`, `resale_customers: int`, `resale_revenue: str`, `total_orders: int`, `total_revenue: str`. Sắp giảm dần theo `resale_revenue`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào CUỐI `backend/apps/orders/tests/test_sale_reports.py`. Lưu ý `_ReportFixtureMixin` dùng CHUNG một `self.customer` cho mọi đơn, nên class này phải tự tạo khách riêng; và `Order.created_at` là `auto_now_add=True` nên phải backdate bằng `.update()`.

```python
class SaleResaleByStaffTests(_ReportFixtureMixin, TestCase):
    """Doanh thu resale theo nhân viên — đơn của khách ĐÃ từng có đơn giao
    thành công tạo trước đó. Xem docs/superpowers/specs/2026-08-25-sale-resale-report-design.md.
    """

    def _customer(self, suffix):
        phone = f"09000{suffix:05d}"
        return Customer.objects.create(full_name=f"KH{suffix}", phone_raw=phone, phone_normalized=phone)

    def _order_for(self, customer, total, sales_status_code="closed", *, staff=None, status=0, days_ago=0, invoice_status=None, branch_id=""):
        """Đơn của một khách cụ thể, lùi ngày tạo `days_ago` ngày."""
        order = Order.objects.create(
            channel=self.channel,
            customer=customer,
            staff=staff or self.sale,
            name="X",
            phone=customer.phone_normalized,
            has_address=True,
            status=status,
            order_source="landing",
            invoice_status=invoice_status,
            branch_id=branch_id,
            total_after_discount=Decimal(total),
            landing_confirmed_amount=Decimal("0"),
        )
        if sales_status_code is not None:
            OrderAssessment.objects.create(order=order, sales_status_code=sales_status_code)
        if days_ago:
            Order.objects.filter(pk=order.pk).update(
                created_at=timezone.now() - timedelta(days=days_ago)
            )
        return order

    def _rows(self):
        return OrderRepository.summary_resale_by_staff()

    def _row_for(self, staff):
        rows = [r for r in self._rows() if r["staff_id"] == staff.id]
        self.assertEqual(len(rows), 1, f"ky vong dung 1 hang cho staff {staff.id}, co {rows}")
        return rows[0]

    def test_first_ever_order_is_not_resale(self):
        self._order_for(self._customer(1), "100000")

        row = self._row_for(self.sale)
        self.assertEqual(row["resale_orders"], 0)
        self.assertEqual(row["resale_revenue"], "0")
        self.assertEqual(row["total_orders"], 1)
        self.assertEqual(row["total_revenue"], "100000.00")

    def test_order_after_a_delivered_order_is_resale(self):
        customer = self._customer(2)
        self._order_for(customer, "100000", status=3, days_ago=10)  # don GTC truoc do
        self._order_for(customer, "70000")  # don resale

        row = self._row_for(self.sale)
        self.assertEqual(row["resale_orders"], 1)
        self.assertEqual(row["resale_revenue"], "70000.00")
        self.assertEqual(row["resale_customers"], 1)

    def test_prior_cancelled_invoice_does_not_make_resale(self):
        customer = self._customer(3)
        self._order_for(customer, "100000", status=3, days_ago=10, invoice_status=2)
        self._order_for(customer, "70000")

        self.assertEqual(self._row_for(self.sale)["resale_orders"], 0)

    def test_prior_not_delivered_order_does_not_make_resale(self):
        customer = self._customer(4)
        self._order_for(customer, "100000", status=0, days_ago=10)
        self._order_for(customer, "70000")

        self.assertEqual(self._row_for(self.sale)["resale_orders"], 0)

    def test_prior_order_in_hidden_branch_does_not_make_resale(self):
        customer = self._customer(5)
        self._order_for(customer, "100000", status=3, days_ago=10, branch_id=EXCLUDED_BRANCH_IDS[0])
        self._order_for(customer, "70000")

        self.assertEqual(self._row_for(self.sale)["resale_orders"], 0)

    def test_resale_order_outside_closed_statuses_not_counted(self):
        customer = self._customer(6)
        self._order_for(customer, "100000", status=3, days_ago=10)
        self._order_for(customer, "70000", "not_buying")

        row = self._row_for(self.sale)
        self.assertEqual(row["resale_orders"], 0)
        self.assertEqual(row["total_orders"], 0)

    def test_two_resale_orders_same_customer_count_one_customer(self):
        customer = self._customer(7)
        self._order_for(customer, "100000", status=3, days_ago=10)
        self._order_for(customer, "70000", days_ago=2)
        self._order_for(customer, "50000")

        row = self._row_for(self.sale)
        self.assertEqual(row["resale_orders"], 2)
        self.assertEqual(row["resale_customers"], 1)
        self.assertEqual(row["resale_revenue"], "120000.00")

    def test_staff_outside_sales_department_excluded(self):
        customer = self._customer(8)
        self._order_for(customer, "100000", status=3, days_ago=10, staff=self.ads)
        self._order_for(customer, "70000", staff=self.ads)

        self.assertEqual([r for r in self._rows() if r["staff_id"] == self.ads.id], [])

    def test_resale_revenue_never_exceeds_total_revenue(self):
        customer = self._customer(9)
        self._order_for(customer, "100000", status=3, days_ago=10)
        self._order_for(customer, "70000")
        self._order_for(self._customer(10), "40000")

        for row in self._rows():
            self.assertLessEqual(Decimal(row["resale_revenue"]), Decimal(row["total_revenue"]))

    def test_date_filter_narrows_resale_orders(self):
        customer = self._customer(11)
        self._order_for(customer, "100000", status=3, days_ago=30)
        self._order_for(customer, "70000", days_ago=20)  # ngoai khoang loc

        today = timezone.localdate()
        rows = OrderRepository.summary_resale_by_staff(
            date_from=(today - timedelta(days=5)).isoformat(),
            date_to=today.isoformat(),
        )
        self.assertEqual([r for r in rows if r["staff_id"] == self.sale.id], [])
```

Thêm import ở đầu file (`EXCLUDED_BRANCH_IDS` chưa có):

```python
from apps.common.business_constants import EXCLUDED_BRANCH_IDS
```

- [ ] **Step 2: Chạy test để xác nhận nó ĐỎ**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report" && \
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_resale_t1 \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend://app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_sale_reports.py::SaleResaleByStaffTests -q --create-db'
```

Kỳ vọng: **FAIL** — `AttributeError: type object 'OrderRepository' has no attribute 'summary_resale_by_staff'` cho cả 10 test.

- [ ] **Step 3: Viết `resale_reports.py`**

Tạo `backend/apps/orders/reporting/resale_reports.py`:

```python
"""Báo cáo Resale — doanh thu từ khách MUA LẠI, gộp theo nhân viên sale.

Nuôi tab "Resale" của trang Báo cáo Sale. Thiết kế:
docs/superpowers/specs/2026-08-25-sale-resale-report-design.md

Tách module riêng theo đúng khuôn của package (``ads_reports``, ``overview``,
``delivery_quality``): một miền nghiệp vụ một file. Mọi method là classmethod nên
các helper nền (``_base_orders_queryset``, ``_sales_report_orders_queryset``,
``_apply_created_at_date_filters``) resolve qua MRO khi mixin được ghép vào
``OrderRepository`` — KHÔNG import trực tiếp (tránh vòng import).
"""
from decimal import Decimal

from django.db.models import Count, Exists, OuterRef, Q, Sum

from .shared import CLOSED_SALES_STATUS_CODES, DELIVERED_SUCCESS_ORDER_STATUS


class ResaleReportsMixin:
    """summary_resale_* — ghép vào ``OrderRepository`` qua ``OrderReportsMixin``."""

    @classmethod
    def summary_resale_by_staff(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        sale_channel_ids: list[int] | None = None,
    ):
        """Một hàng cho mỗi nhân viên sale: doanh thu resale và doanh thu tổng.

        ĐƠN RESALE = đơn của khách đã có ít nhất một đơn GIAO THÀNH CÔNG được
        TẠO trước nó. Mốc so sánh là ``created_at``, KHÔNG phải ngày giao: hệ
        thống không có cột "ngày giao thành công" đáng tin (trang Theo dõi resale
        đang xài tạm ``updated_at``, mà cột đó trôi mỗi lần bất kỳ ai sửa đơn).
        Hệ quả cố ý: khách đặt đơn 1 hôm nay, đặt đơn 2 ngày mai, đơn 1 giao
        thành công ngày kia -> đơn 2 vẫn tính resale.

        Điều kiện GTC KHÔNG chép tay 5 mệnh đề: ``_base_orders_queryset()`` đã gói
        sẵn ``deleted_at IS NULL`` + loại ``invoice_status=2`` + loại chi nhánh
        ẩn, và nó chạy trên ``Order.objects`` — manager ĐÃ lọc đơn mirror
        (foxia/cnv). Cộng ``status=3`` là ra đúng mệnh đề GTC chuẩn của hệ thống.
        Đi đường này còn tránh phải import ``CANCELLED_INVOICE_STATUS`` từ
        ``apps.customers`` — vi phạm hợp đồng tầng của ``.importlinter``
        (customers | orders cùng tầng, độc lập).

        Phạm vi đơn và tập trạng thái DÙNG CHUNG với tab "Báo cáo theo sale"
        (``summary_sale_daily_by_staff``) để hai tab đặt cạnh nhau không chỏi số:
        ``total_revenue`` của hàm này PHẢI bằng ``total_revenue`` của tab kia trên
        cùng khoảng lọc. Lệch nghĩa là có bug ở filter, không phải "khác định nghĩa".

        Trả cả cặp ``resale_*`` lẫn ``total_*`` trong một lần quét để FE tính
        được "% doanh thu resale trên tổng" mà không phải gọi endpoint thứ hai
        rồi ghép hai bộ số có thể lệch scope.
        """
        queryset = cls._sales_report_orders_queryset(sale_channel_ids=sale_channel_ids)
        queryset = cls._apply_created_at_date_filters(queryset, date_from, date_to)

        # Index ord_gtc_customer_idx — (customer, -created_at) WHERE deleted_at IS
        # NULL AND status = 3 — phủ ĐÚNG subquery này, nên mỗi đơn tốn một index
        # scan có bound thay vì quét bảng.
        prior_gtc = cls._base_orders_queryset().filter(
            customer_id=OuterRef("customer_id"),
            created_at__lt=OuterRef("created_at"),
            status=DELIVERED_SUCCESS_ORDER_STATUS,
        )
        closed_q = Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES)
        resale_q = closed_q & Q(is_resale=True)

        rows = list(
            queryset.annotate(is_resale=Exists(prior_gtc))
            .values("staff_id", "staff__username", "staff__display_name")
            .annotate(
                total_orders=Count("id", filter=closed_q),
                total_revenue=Sum("total_after_discount", filter=closed_q),
                resale_orders=Count("id", filter=resale_q),
                resale_revenue=Sum("total_after_discount", filter=resale_q),
                resale_customers=Count("customer_id", distinct=True, filter=resale_q),
            )
            .order_by("staff__username", "staff_id")
        )
        for row in rows:
            for field in ("total_revenue", "resale_revenue"):
                row[field] = str(row[field] or Decimal("0"))
        # Sắp ở Python chứ không ORM: order_by trên alias của Sum có filter buộc
        # Postgres gói thêm một lớp subquery, mà số hàng ở đây chỉ cỡ số nhân viên.
        rows.sort(key=lambda row: Decimal(row["resale_revenue"]), reverse=True)
        return rows
```

- [ ] **Step 4: Ghép mixin vào `OrderReportsMixin`**

Trong `backend/apps/orders/reporting/__init__.py`:

1. Thêm dòng import (giữ thứ tự alphabet của khối import hiện có):

```python
from .resale_reports import ResaleReportsMixin
```

2. Thêm vào danh sách kế thừa:

```python
class OrderReportsMixin(
    SaleReportsMixin,
    ResaleReportsMixin,
    DeliveryQualityReportsMixin,
    AdsReportsMixin,
    OverviewReportMixin,
):
    """Mixin providing summary_* report methods. Mixed into OrderRepository.

    Facade ghép 5 mixin theo miền — không thêm method nào của riêng nó. Thứ tự
    kế thừa không mang ý nghĩa: 5 mixin không có method trùng tên.
    """
```

3. Trong docstring đầu module, thêm một gạch đầu dòng vào danh sách module:

```
- ``resale_reports``: doanh thu từ khách mua lại, gộp theo nhân viên sale.
```

4. Thêm `"ResaleReportsMixin"` vào `__all__` nếu file có liệt kê tên các mixin (kiểm tra trước khi thêm — chỉ thêm nếu `"SaleReportsMixin"` đã có mặt ở đó).

- [ ] **Step 5: Chạy test để xác nhận XANH**

Dùng đúng lệnh Step 2 nhưng đổi `POSTGRES_DB=desi_resale_t1b`.
Kỳ vọng: **10 passed**.

- [ ] **Step 6: Chạy cả file để xác nhận không hồi quy**

```bash
# ... như lệnh chuẩn, POSTGRES_DB=desi_resale_t1c
sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_sale_reports.py -q --create-db'
```

Kỳ vọng: **55 passed** (45 baseline + 10 mới). Ít hơn = có hồi quy, phải sửa trước khi commit.

- [ ] **Step 7: Lint**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report" && \
docker run --rm -v "$(pwd -W)/backend://app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache apps/orders/reporting/ apps/orders/tests/test_sale_reports.py
```

Kỳ vọng: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report"
git add backend/apps/orders/reporting/resale_reports.py backend/apps/orders/reporting/__init__.py backend/apps/orders/tests/test_sale_reports.py
git commit -m "feat(reports): resale revenue by staff aggregation"
```

---

## Task 2: Backend — endpoint `/api/orders/reports/resale-by-staff`

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py` (thêm view sau `sale_staff_report_summary`)
- Modify: `backend/apps/orders/transport/views/order_views_impl/__init__.py`
- Modify: `backend/apps/orders/views.py`
- Modify: `backend/apps/orders/urls.py`
- Modify: `backend/schema.yaml`
- Test: `backend/apps/orders/tests/test_sale_reports.py` (thêm class thứ hai)

**Interfaces:**
- Consumes: `OrderRepository.summary_resale_by_staff(date_from, date_to, sale_channel_ids=...)` từ Task 1; `OrderRepository.ads_channel_scope() -> tuple[list[int], list[int]]`.
- Produces: `GET /api/orders/reports/resale-by-staff?date_from=&date_to=` → `{"by_staff": [<8 khoá như Task 1>]}`. Tên route Django: `orders-resale-report`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào CUỐI `backend/apps/orders/tests/test_sale_reports.py`:

```python
class SaleResaleReportApiTests(_ReportFixtureMixin, TestCase):
    URL = "/api/orders/reports/resale-by-staff"

    def _payload(self, **params):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.URL, params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_returns_by_staff_rows_with_expected_keys(self):
        customer = Customer.objects.create(
            full_name="KH2", phone_raw="0900011111", phone_normalized="0900011111"
        )
        first = Order.objects.create(
            channel=self.channel, customer=customer, staff=self.sale, name="X",
            phone="0900011111", has_address=True, status=3, order_source="landing",
            total_after_discount=Decimal("100000"), landing_confirmed_amount=Decimal("0"),
        )
        OrderAssessment.objects.create(order=first, sales_status_code="closed")
        Order.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(days=5))
        second = Order.objects.create(
            channel=self.channel, customer=customer, staff=self.sale, name="X",
            phone="0900011111", has_address=True, status=0, order_source="landing",
            total_after_discount=Decimal("70000"), landing_confirmed_amount=Decimal("0"),
        )
        OrderAssessment.objects.create(order=second, sales_status_code="closed")

        rows = self._payload()["by_staff"]
        row = next(r for r in rows if r["staff_id"] == self.sale.id)
        self.assertEqual(
            set(row),
            {
                "staff_id", "staff__username", "staff__display_name",
                "resale_orders", "resale_customers", "resale_revenue",
                "total_orders", "total_revenue",
            },
        )
        self.assertEqual(row["resale_orders"], 1)
        self.assertEqual(row["resale_revenue"], "70000.00")
        self.assertEqual(row["total_revenue"], "170000.00")

    def test_empty_range_returns_empty_list_not_error(self):
        self.assertEqual(self._payload(date_from="2020-01-01", date_to="2020-01-02")["by_staff"], [])

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(self.URL).status_code, (401, 403))

    def test_user_without_dashboard_section_is_denied(self):
        outsider = User.objects.create_user(username=f"outsider-{id(self)}", password="x")
        self.client.force_authenticate(user=outsider)
        self.assertEqual(self.client.get(self.URL).status_code, 403)
```

- [ ] **Step 2: Chạy test để xác nhận nó ĐỎ**

Lệnh chuẩn với `POSTGRES_DB=desi_resale_t2`, paths `apps/orders/tests/test_sale_reports.py::SaleResaleReportApiTests`.
Kỳ vọng: **FAIL** — 404 (route chưa tồn tại) nên `assertEqual(status_code, 200)` đỏ.

- [ ] **Step 3: Thêm view**

Trong `dashboard_report_views.py`, đặt NGAY SAU hàm `sale_staff_report_summary` (trước `def _int_list`):

```python
@extend_schema(
    responses={
        200: inline_serializer(
            "SaleResaleReportResponse",
            fields={
                "by_staff": inline_serializer(
                    "SaleResaleStaffRow",
                    fields={
                        "staff_id": drf_serializers.IntegerField(allow_null=True),
                        "staff__username": drf_serializers.CharField(allow_null=True),
                        "staff__display_name": drf_serializers.CharField(allow_null=True),
                        "resale_orders": drf_serializers.IntegerField(),
                        "resale_customers": drf_serializers.IntegerField(),
                        "resale_revenue": drf_serializers.CharField(),
                        "total_orders": drf_serializers.IntegerField(),
                        "total_revenue": drf_serializers.CharField(),
                    },
                    many=True,
                ),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("dashboard")])
def resale_report_summary(request):
    """Doanh thu resale theo nhân viên — nuôi tab "Resale" của Báo cáo Sale.

    Đơn resale = đơn của khách đã có đơn giao thành công TẠO trước đó. Trả kèm
    ``total_*`` (cùng scope, cùng tập trạng thái với tab "Báo cáo theo sale") để
    FE tính "% doanh thu resale trên tổng" trong một lần gọi.
    """
    date_from = request.query_params.get("date_from", "")
    date_to = request.query_params.get("date_to", "")
    # Cùng scope kênh với dashboard_summary — tính 1 lần cho cả request.
    sale_channel_ids, _excluded_ids = OrderRepository.ads_channel_scope()
    return Response(
        {
            "by_staff": OrderRepository.summary_resale_by_staff(
                date_from=date_from,
                date_to=date_to,
                sale_channel_ids=sale_channel_ids,
            ),
        }
    )
```

- [ ] **Step 4: Nối chuỗi re-export và route**

Chuỗi export gồm 3 chặng — thiếu một chặng là `ImportError` lúc khởi động.

1. `backend/apps/orders/transport/views/order_views_impl/__init__.py`: thêm `resale_report_summary` vào khối `from .dashboard_report_views import (...)` (cạnh `sale_staff_report_summary`, dòng ~49) **và** vào `__all__` (dòng ~105).
2. `backend/apps/orders/views.py`: thêm `resale_report_summary` vào khối `from .transport.views.order_views_impl import (...)` (cạnh `sale_staff_report_summary`, dòng ~19) **và** vào `__all__` (dòng ~93).
3. `backend/apps/orders/urls.py`: thêm `resale_report_summary` vào khối `from .views import (...)` (cạnh `sale_staff_report_summary`, dòng ~33), rồi thêm route NGAY SAU `reports/sale-by-staff`:

```python
    path(
        "reports/resale-by-staff",
        resale_report_summary,
        name="orders-resale-report",
    ),
```

- [ ] **Step 5: Chạy test để xác nhận XANH**

Lệnh chuẩn với `POSTGRES_DB=desi_resale_t2b`, paths `apps/orders/tests/test_sale_reports.py`.
Kỳ vọng: **59 passed** (55 sau Task 1 + 4 mới).

- [ ] **Step 6: Cập nhật `schema.yaml` bằng TAY**

**KHÔNG chạy `spectacular` regen toàn file** — bản trên master đã cũ sẵn ~444 dòng, regen sẽ kéo nhiễu của feature khác vào MR này.

Mở `backend/schema.yaml`, tìm block path `/api/orders/reports/sale-by-staff` để làm mẫu, rồi thêm:

1. **Path** — chèn theo đúng thứ tự alphabet của các key trong `paths:` (`/api/orders/reports/resale-by-staff` đứng TRƯỚC `/api/orders/reports/sale-by-staff`):

```yaml
  /api/orders/reports/resale-by-staff:
    get:
      operationId: api_orders_reports_resale_by_staff_retrieve
      description: |-
        Doanh thu resale theo nhân viên — nuôi tab "Resale" của Báo cáo Sale.

        Đơn resale = đơn của khách đã có đơn giao thành công TẠO trước đó. Trả kèm
        ``total_*`` (cùng scope, cùng tập trạng thái với tab "Báo cáo theo sale") để
        FE tính "% doanh thu resale trên tổng" trong một lần gọi.
      tags:
      - api
      security:
      - jwtAuth: []
      responses:
        '200':
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/SaleResaleReportResponse'
          description: ''
```

Đối chiếu block `sale-by-staff` ngay bên dưới và copy y nguyên các khoá `tags` / `security` nếu chúng khác với mẫu trên.

2. **Components** — thêm 2 schema vào `components: schemas:`, đúng thứ tự alphabet. `required` phải sort theo ALPHABET (không theo thứ tự khai báo):

```yaml
    SaleResaleReportResponse:
      type: object
      properties:
        by_staff:
          type: array
          items:
            $ref: '#/components/schemas/SaleResaleStaffRow'
      required:
      - by_staff
    SaleResaleStaffRow:
      type: object
      properties:
        staff_id:
          type: integer
          nullable: true
        staff__username:
          type: string
          nullable: true
        staff__display_name:
          type: string
          nullable: true
        resale_orders:
          type: integer
        resale_customers:
          type: integer
        resale_revenue:
          type: string
        total_orders:
          type: integer
        total_revenue:
          type: string
      required:
      - resale_customers
      - resale_orders
      - resale_revenue
      - staff__display_name
      - staff__username
      - staff_id
      - total_orders
      - total_revenue
```

- [ ] **Step 7: Lint**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report" && \
docker run --rm -v "$(pwd -W)/backend://app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache apps/orders/
```

Kỳ vọng: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report"
git add backend/apps/orders/transport/views/order_views_impl/ backend/apps/orders/views.py backend/apps/orders/urls.py backend/schema.yaml backend/apps/orders/tests/test_sale_reports.py
git commit -m "feat(reports): resale-by-staff endpoint"
```

---

## Task 3: Frontend — query hook + selector

**Files:**
- Modify: `frontend/src/features/sale-report/queries.ts`
- Modify: `frontend/src/features/sale-report/sale-report-selectors.ts` (chỉ nới KIỂU tham số của `saleKey` / `saleLabel`)
- Create: `frontend/src/features/sale-report/resale-selectors.ts`
- Test: `frontend/src/features/sale-report/__tests__/resale-selectors.test.ts`

**Interfaces:**
- Consumes: hợp đồng payload của Task 2 (`{by_staff: [...]}`) — KHÔNG cần backend chạy được, task này chỉ dựa vào shape.
- Produces:
  ```ts
  // queries.ts
  export type ResaleStaffApiRow = { staff_id: number|null; staff__username: string|null;
    staff__display_name: string|null; resale_orders: number; resale_customers: number;
    resale_revenue: string; total_orders: number; total_revenue: string };
  export type SaleResaleReportPayload = { by_staff: ResaleStaffApiRow[] };
  export function useSaleResaleReport(params: SaleStaffReportParams): UseQueryResult<SaleResaleReportPayload>;

  // resale-selectors.ts
  export type ResaleStaffRow = { key: string; label: string; resaleOrders: number;
    resaleCustomers: number; resaleRevenue: number; totalOrders: number;
    totalRevenue: number; resaleAov: number|null; sharePct: number|null };
  export function buildResaleRows(rows: readonly ResaleStaffApiRow[], unknownLabel: string): ResaleStaffRow[];
  export function sumResaleRows(rows: readonly ResaleStaffRow[], totalLabel: string): ResaleStaffRow;
  ```

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/sale-report/__tests__/resale-selectors.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { ResaleStaffApiRow } from "../queries";
import { buildResaleRows, sumResaleRows } from "../resale-selectors";

const UNKNOWN = "Không xác định";

function apiRow(overrides: Partial<ResaleStaffApiRow> = {}): ResaleStaffApiRow {
  return {
    staff_id: 1,
    staff__username: "sale1",
    staff__display_name: "Sale Một",
    resale_orders: 2,
    resale_customers: 2,
    resale_revenue: "200000.00",
    total_orders: 5,
    total_revenue: "500000.00",
    ...overrides,
  };
}

describe("buildResaleRows", () => {
  it("parses decimal strings into numbers", () => {
    const [row] = buildResaleRows([apiRow()], UNKNOWN);
    expect(row.resaleRevenue).toBe(200000);
    expect(row.totalRevenue).toBe(500000);
  });

  it("prefers display name, falls back to username then unknown label", () => {
    const rows = buildResaleRows(
      [
        apiRow({ staff_id: 1 }),
        apiRow({ staff_id: 2, staff__display_name: null, staff__username: "sale2" }),
        apiRow({ staff_id: null, staff__display_name: null, staff__username: null }),
      ],
      UNKNOWN,
    );
    expect(rows.map((row) => row.label)).toEqual(
      expect.arrayContaining(["Sale Một", "sale2", UNKNOWN]),
    );
  });

  it("gives unassigned staff a stable distinct key", () => {
    const [row] = buildResaleRows(
      [apiRow({ staff_id: null, staff__display_name: null, staff__username: null })],
      UNKNOWN,
    );
    expect(row.key).toBe("__unassigned__");
  });

  it("computes AOV and share percent", () => {
    const [row] = buildResaleRows([apiRow()], UNKNOWN);
    expect(row.resaleAov).toBe(100000);
    expect(row.sharePct).toBe(40);
  });

  it("returns null instead of Infinity or NaN when denominators are zero", () => {
    const [row] = buildResaleRows(
      [apiRow({ resale_orders: 0, resale_revenue: "0", total_orders: 0, total_revenue: "0" })],
      UNKNOWN,
    );
    expect(row.resaleAov).toBeNull();
    expect(row.sharePct).toBeNull();
  });

  it("sorts by resale revenue descending", () => {
    const rows = buildResaleRows(
      [
        apiRow({ staff_id: 1, staff__display_name: "A", resale_revenue: "100000" }),
        apiRow({ staff_id: 2, staff__display_name: "B", resale_revenue: "900000" }),
        apiRow({ staff_id: 3, staff__display_name: "C", resale_revenue: "500000" }),
      ],
      UNKNOWN,
    );
    expect(rows.map((row) => row.label)).toEqual(["B", "C", "A"]);
  });

  it("treats malformed money strings as zero rather than NaN", () => {
    const [row] = buildResaleRows([apiRow({ resale_revenue: "" })], UNKNOWN);
    expect(row.resaleRevenue).toBe(0);
  });

  it("returns an empty array for empty input", () => {
    expect(buildResaleRows([], UNKNOWN)).toEqual([]);
  });
});

describe("sumResaleRows", () => {
  it("sums the columns and recomputes derived values from the totals", () => {
    const rows = buildResaleRows(
      [
        apiRow({ staff_id: 1, resale_orders: 2, resale_customers: 2, resale_revenue: "200000", total_orders: 5, total_revenue: "500000" }),
        apiRow({ staff_id: 2, resale_orders: 3, resale_customers: 1, resale_revenue: "400000", total_orders: 5, total_revenue: "500000" }),
      ],
      UNKNOWN,
    );
    const total = sumResaleRows(rows, "Tổng");

    expect(total.label).toBe("Tổng");
    expect(total.resaleOrders).toBe(5);
    expect(total.resaleCustomers).toBe(3);
    expect(total.resaleRevenue).toBe(600000);
    expect(total.totalRevenue).toBe(1000000);
    expect(total.resaleAov).toBe(120000);
    expect(total.sharePct).toBe(60);
  });

  it("handles an empty row list without dividing by zero", () => {
    const total = sumResaleRows([], "Tổng");
    expect(total.resaleRevenue).toBe(0);
    expect(total.sharePct).toBeNull();
    expect(total.resaleAov).toBeNull();
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận nó ĐỎ**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report/frontend"
npx vitest run src/features/sale-report/__tests__/resale-selectors.test.ts
```

Kỳ vọng: **FAIL** — `Failed to resolve import "../resale-selectors"`.

- [ ] **Step 3: Nới kiểu tham số của `saleKey` / `saleLabel`**

Trong `frontend/src/features/sale-report/sale-report-selectors.ts`, thay hai hàm hiện có bằng bản nhận kiểu cấu trúc hẹp hơn. `SaleStaffDailyRow` thoả kiểu này về mặt cấu trúc nên MỌI call site cũ giữ nguyên, không phải sửa gì:

```ts
/** Phần định danh nhân viên mà cả báo cáo sale lẫn báo cáo resale đều có. */
export type StaffIdentity = {
  staff_id: number | null;
  staff__username: string | null;
  staff__display_name: string | null;
};

/** Khoá gộp của một nhân viên; staff_id null (đơn chưa gán) gom về một khoá riêng. */
export function saleKey(row: StaffIdentity): string {
  return row.staff_id != null ? String(row.staff_id) : "__unassigned__";
}

export function saleLabel(row: StaffIdentity, unknownLabel: string): string {
  return row.staff__display_name || row.staff__username || unknownLabel;
}
```

- [ ] **Step 4: Thêm type + hook vào `queries.ts`**

Nối vào CUỐI `frontend/src/features/sale-report/queries.ts` (giữ nguyên phần đã có):

```ts
/** Một hàng (nhân viên sale) của tab "Resale" — số tiền là chuỗi decimal. */
export type ResaleStaffApiRow = {
  staff_id: number | null;
  staff__username: string | null;
  staff__display_name: string | null;
  /** Số đơn resale đã chốt. */
  resale_orders: number;
  /** Số khách quay lại (distinct) — 1 khách mua 3 lần vẫn đếm 1. */
  resale_customers: number;
  resale_revenue: string;
  /** Tổng đơn chốt (mới + resale) — mẫu số của "% doanh thu resale". */
  total_orders: number;
  total_revenue: string;
};

export type SaleResaleReportPayload = {
  by_staff: ResaleStaffApiRow[];
};

export const saleResaleReportKeys = {
  all: ["sale-resale-report"] as const,
  report: (params: SaleStaffReportParams) => ["sale-resale-report", "report", params] as const,
};

/**
 * GET /api/orders/reports/resale-by-staff — một hàng mỗi nhân viên sale.
 * Cùng chính sách cache/refetch với `useSaleStaffReport` để hai tab của cùng một
 * trang không hành xử khác nhau khi đổi bộ lọc hoặc quay lại tab trình duyệt.
 */
export function useSaleResaleReport(params: SaleStaffReportParams) {
  return useQuery({
    queryKey: saleResaleReportKeys.report(params),
    queryFn: async ({ signal }): Promise<SaleResaleReportPayload> => {
      const query = new URLSearchParams();
      if (params.dateFrom) query.set("date_from", params.dateFrom);
      if (params.dateTo) query.set("date_to", params.dateTo);
      const qs = query.toString();
      const url = qs
        ? `/api/orders/reports/resale-by-staff?${qs}`
        : "/api/orders/reports/resale-by-staff";
      return (await fetchJson(url, { signal })) as SaleResaleReportPayload;
    },
    placeholderData: keepPreviousData,
    refetchInterval: SALE_REPORT_REFRESH_MS,
    refetchOnWindowFocus: "always",
  });
}
```

- [ ] **Step 5: Viết `resale-selectors.ts`**

Tạo `frontend/src/features/sale-report/resale-selectors.ts`:

```ts
/**
 * Selector thuần cho tab "Resale".
 *
 * Backend trả một hàng mỗi nhân viên, tiền dạng chuỗi decimal. Ở đây parse sang
 * số, tính hai chỉ số dẫn xuất (AOV resale, % doanh thu resale trên tổng) và
 * dựng dòng tổng. Tách khỏi component để test được mà không phải render chart.
 */

import { type ResaleStaffApiRow } from "./queries";
import { saleKey, saleLabel } from "./sale-report-selectors";

export type ResaleStaffRow = {
  key: string;
  label: string;
  /** Số đơn resale đã chốt. */
  resaleOrders: number;
  /** Số khách quay lại (distinct) — KHÔNG cộng ngang được giữa các nhân viên, nhưng
   *  dòng tổng vẫn cộng vì backend không trả distinct toàn cục; xem chú thích ở UI. */
  resaleCustomers: number;
  resaleRevenue: number;
  totalOrders: number;
  totalRevenue: number;
  /** Giá trị trung bình một đơn resale; `null` khi chưa có đơn nào. */
  resaleAov: number | null;
  /** % doanh thu resale trên tổng doanh thu; `null` khi tổng bằng 0. */
  sharePct: number | null;
};

/** Chuỗi rỗng / rác -> 0, không để lọt NaN vào phép cộng của dòng tổng. */
function toNumber(value: string): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function divide(numerator: number, denominator: number): number | null {
  return denominator > 0 ? numerator / denominator : null;
}

function derive(
  key: string,
  label: string,
  base: Omit<ResaleStaffRow, "key" | "label" | "resaleAov" | "sharePct">,
): ResaleStaffRow {
  const share = divide(base.resaleRevenue, base.totalRevenue);
  return {
    key,
    label,
    ...base,
    resaleAov: divide(base.resaleRevenue, base.resaleOrders),
    sharePct: share === null ? null : share * 100,
  };
}

/** Một hàng cho mỗi nhân viên, sắp doanh thu resale giảm dần. */
export function buildResaleRows(
  rows: readonly ResaleStaffApiRow[],
  unknownLabel: string,
): ResaleStaffRow[] {
  return rows
    .map((row) =>
      derive(saleKey(row), saleLabel(row, unknownLabel), {
        resaleOrders: row.resale_orders || 0,
        resaleCustomers: row.resale_customers || 0,
        resaleRevenue: toNumber(row.resale_revenue),
        totalOrders: row.total_orders || 0,
        totalRevenue: toNumber(row.total_revenue),
      }),
    )
    .sort((left, right) => right.resaleRevenue - left.resaleRevenue);
}

/**
 * Dòng tổng. Hai chỉ số dẫn xuất được tính LẠI từ tổng chứ không cộng ngang —
 * cộng trung bình của các trung bình là sai khi mỗi hàng có mẫu số khác nhau.
 */
export function sumResaleRows(
  rows: readonly ResaleStaffRow[],
  totalLabel: string,
): ResaleStaffRow {
  const base = rows.reduce(
    (acc, row) => ({
      resaleOrders: acc.resaleOrders + row.resaleOrders,
      resaleCustomers: acc.resaleCustomers + row.resaleCustomers,
      resaleRevenue: acc.resaleRevenue + row.resaleRevenue,
      totalOrders: acc.totalOrders + row.totalOrders,
      totalRevenue: acc.totalRevenue + row.totalRevenue,
    }),
    { resaleOrders: 0, resaleCustomers: 0, resaleRevenue: 0, totalOrders: 0, totalRevenue: 0 },
  );
  return derive("__total__", totalLabel, base);
}
```

- [ ] **Step 6: Chạy test để xác nhận XANH**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report/frontend"
npx vitest run src/features/sale-report
```

Kỳ vọng: **3 file passed**, `28 + 10 = 38 passed` test. File `sale-report-selectors.test.ts` (24 test) phải vẫn xanh — nếu đỏ nghĩa là bước nới kiểu ở Step 3 làm hỏng call site cũ.

- [ ] **Step 7: Type-check**

```bash
npx tsc --noEmit
```

⚠️ Trên master có **một lỗi tsc sẵn có**: `src/features/audit/__tests__/audit-logs-api.test.ts` dùng `.at()` với `lib` < es2022. Lỗi đó KHÔNG phải do nhánh này — bỏ qua nó, nhưng mọi lỗi khác phải sạch.

- [ ] **Step 8: Commit**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report/frontend"
npx prettier --write \
  src/features/sale-report/queries.ts \
  src/features/sale-report/resale-selectors.ts \
  src/features/sale-report/sale-report-selectors.ts \
  src/features/sale-report/__tests__/resale-selectors.test.ts
cd .. && git add frontend/src/features/sale-report/ && git commit -m "feat(sale-report): resale query hook and selectors"
```

---

## Task 4: Frontend — chart, tab, và chèn vào trang

**Files:**
- Create: `frontend/src/features/sale-report/components/ResaleRevenueChart.tsx`
- Create: `frontend/src/features/sale-report/components/SaleResaleTab.tsx`
- Modify: `frontend/src/pages/DashboardPage.tsx`
- Test: `frontend/src/features/sale-report/__tests__/SaleResaleTab.test.tsx`

**Interfaces:**
- Consumes: `useSaleResaleReport`, `ResaleStaffApiRow`, `buildResaleRows`, `sumResaleRows`, `ResaleStaffRow` (Task 3); `ChartTooltip` từ `@/features/dashboard/components/DashboardPrimitives`; `CHART_COLORS` từ `@/features/dashboard/dashboard-constants`; `formatRevenueShort` từ `@/lib/metrics`; `formatAdsCurrency` / `formatRatioPct` từ `@/features/ads/report-grouping`.
- Produces: `<SaleResaleTab dateFrom?: string dateTo?: string />` — tab key `"resale"` trong `DashboardPage`.

- [ ] **Step 1: Viết test thất bại**

Tạo `frontend/src/features/sale-report/__tests__/SaleResaleTab.test.tsx`. Chart KHÔNG được test ở đây — `ResponsiveContainer` của recharts đo ra 0×0 trong jsdom nên không render con; mọi khẳng định phải nhắm vào bảng.

⚠️ **Bọc `I18nProvider` và gọi `afterEach(cleanup)`** — mọi component test trong repo
này đều làm vậy (`SaleMetricTable.test.tsx`, `DashboardPage.tabs.test.tsx`). Render trần
không có provider thì `useI18n()` không có context và các khẳng định theo nhãn tiếng Việt
sẽ trượt.

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/i18n";
import { SaleResaleTab } from "../components/SaleResaleTab";
import type { SaleResaleReportPayload } from "../queries";

afterEach(cleanup);

const useSaleResaleReport = vi.hoisted(() => vi.fn());
vi.mock("../queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../queries")>()),
  useSaleResaleReport: (...args: unknown[]) => useSaleResaleReport(...args),
}));

function mockQuery(overrides: Record<string, unknown>) {
  useSaleResaleReport.mockClear();
  useSaleResaleReport.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: undefined,
    ...overrides,
  });
}

function renderTab(props: { dateFrom?: string; dateTo?: string } = {}) {
  return render(
    <I18nProvider>
      <SaleResaleTab {...props} />
    </I18nProvider>,
  );
}

const payload: SaleResaleReportPayload = {
  by_staff: [
    {
      staff_id: 1,
      staff__username: "sale1",
      staff__display_name: "Sale Một",
      resale_orders: 2,
      resale_customers: 2,
      resale_revenue: "200000",
      total_orders: 5,
      total_revenue: "500000",
    },
  ],
};

describe("SaleResaleTab", () => {
  it("renders a staff row with its resale revenue", () => {
    mockQuery({ data: payload });
    renderTab({ dateFrom: "2026-08-01", dateTo: "2026-08-25" });

    expect(screen.getByText("Sale Một")).toBeInTheDocument();
    expect(screen.getByText("40.0%")).toBeInTheDocument();
  });

  it("shows an empty state instead of a table when there is no data", () => {
    mockQuery({ data: { by_staff: [] } });
    renderTab();

    expect(screen.getByText("Chưa có dữ liệu.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("surfaces the error message", () => {
    mockQuery({ isError: true, error: new Error("Sập rồi") });
    renderTab();

    expect(screen.getByText("Sập rồi")).toBeInTheDocument();
  });

  it("passes the page date filter through to the query", () => {
    mockQuery({ data: payload });
    renderTab({ dateFrom: "2026-08-01", dateTo: "2026-08-25" });

    expect(useSaleResaleReport).toHaveBeenCalledWith({
      dateFrom: "2026-08-01",
      dateTo: "2026-08-25",
    });
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận nó ĐỎ**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report/frontend"
npx vitest run src/features/sale-report/__tests__/SaleResaleTab.test.tsx
```

Kỳ vọng: **FAIL** — `Failed to resolve import "../components/SaleResaleTab"`.

- [ ] **Step 3: Viết `ResaleRevenueChart.tsx`**

```tsx
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ChartTooltip } from "@/features/dashboard/components/DashboardPrimitives";
import { CHART_COLORS } from "@/features/dashboard/dashboard-constants";
import { formatRevenueShort } from "@/lib/metrics";

import { type ResaleStaffRow } from "../resale-selectors";

type ResaleRevenueChartProps = {
  rows: ResaleStaffRow[];
  /** Nhãn series, hiện trong tooltip. */
  seriesName: string;
};

/**
 * Bar NGANG xếp hạng doanh thu resale theo nhân viên. Chỉ vẽ — dữ liệu đã được
 * `buildResaleRows` sắp giảm dần từ trước.
 *
 * Nhân viên có doanh thu resale 0 bị lọc khỏi CHART (thanh dài 0 chỉ tốn chiều
 * cao) nhưng VẪN nằm trong bảng bên dưới, nơi số 0 là thông tin có ích.
 */
export function ResaleRevenueChart({ rows, seriesName }: ResaleRevenueChartProps) {
  const data = rows
    .filter((row) => row.resaleRevenue > 0)
    .map((row) => ({ name: row.label, value: row.resaleRevenue }));

  if (data.length === 0) return null;

  return (
    <ResponsiveContainer width="100%" height={Math.max(260, data.length * 40 + 72)}>
      <BarChart data={data} layout="vertical" margin={{ top: 8, right: 56, left: 8, bottom: 8 }}>
        <CartesianGrid
          strokeDasharray="2 4"
          stroke="var(--border)"
          strokeOpacity={0.55}
          horizontal={false}
        />
        <XAxis
          type="number"
          tickFormatter={formatRevenueShort}
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          stroke="var(--border)"
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          type="category"
          dataKey="name"
          width={150}
          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
          stroke="var(--border)"
          tickLine={false}
          axisLine={false}
        />
        <Tooltip
          cursor={{ fill: "var(--muted)", fillOpacity: 0.4 }}
          content={
            <ChartTooltip formatter={(entry) => formatRevenueShort(Number(entry.value ?? 0))} />
          }
        />
        <Bar dataKey="value" name={seriesName} fill={CHART_COLORS.blue} radius={[0, 4, 4, 0]}>
          <LabelList
            dataKey="value"
            position="right"
            formatter={(value: number) => formatRevenueShort(Number(value ?? 0))}
            className="fill-muted-foreground text-[11px]"
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
```

- [ ] **Step 4: Viết `SaleResaleTab.tsx`**

```tsx
import { useMemo } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { LogoSpinner } from "@/components/ui/logo-spinner";
import { formatAdsCurrency, formatRatioPct } from "@/features/ads/report-grouping";
import { useI18n } from "@/i18n";

import { useSaleResaleReport } from "../queries";
import { type ResaleStaffRow, buildResaleRows, sumResaleRows } from "../resale-selectors";
import { ResaleRevenueChart } from "./ResaleRevenueChart";

type SaleResaleTabProps = {
  dateFrom?: string;
  dateTo?: string;
};

function formatInt(value: number): string {
  return value.toLocaleString("vi-VN");
}

function formatDerivedCurrency(value: number | null): string {
  return value === null ? "—" : formatAdsCurrency(value);
}

/**
 * Tab "Resale" — doanh thu từ khách MUA LẠI, theo nhân viên sale. Dùng bộ lọc
 * ngày của trang. `Tabs` chỉ mount panel đang mở nên query chỉ chạy khi vào tab.
 */
export function SaleResaleTab({ dateFrom, dateTo }: SaleResaleTabProps) {
  const { t } = useI18n();
  const query = useSaleResaleReport({ dateFrom, dateTo });
  const unknownLabel = t("dashboard.unknownStaff", "Không xác định");
  const totalLabel = t("saleReport.totalRow", "Tổng");

  const rows = useMemo(
    () => buildResaleRows(query.isError ? [] : (query.data?.by_staff ?? []), unknownLabel),
    [query.isError, query.data, unknownLabel],
  );
  const totals = useMemo(() => sumResaleRows(rows, totalLabel), [rows, totalLabel]);

  const error = query.error
    ? query.error instanceof Error
      ? query.error.message
      : String(query.error)
    : null;

  const cells = (row: ResaleStaffRow) => (
    <>
      <td className="px-3 py-2 text-right tabular-nums">{formatInt(row.resaleOrders)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{formatInt(row.resaleCustomers)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{formatAdsCurrency(row.resaleRevenue)}</td>
      <td className="px-3 py-2 text-right tabular-nums">
        {formatDerivedCurrency(row.resaleAov)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{formatRatioPct(row.sharePct)}</td>
    </>
  );

  return (
    <div className="space-y-4">
      {error && (
        <Card className="rounded-xl border-destructive/50 bg-destructive/10">
          <CardContent className="py-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}
      <Card className="rounded-xl border-border bg-card">
        <CardContent className="pt-6">
          {query.isLoading ? (
            <div className="flex min-h-[160px] items-center justify-center">
              <LogoSpinner size={36} />
            </div>
          ) : rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("saleReport.empty", "Chưa có dữ liệu.")}
            </p>
          ) : (
            <div className="space-y-6">
              <ResaleRevenueChart
                rows={rows}
                seriesName={t("saleReport.resaleRevenueHeader", "Doanh thu resale")}
              />
              <div className="overflow-x-auto">
                <table data-slot="table" className="w-full min-w-[760px] text-sm">
                  <thead>
                    <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
                      <th className="px-3 py-2 text-left font-medium">
                        {t("saleReport.staffHeader", "Sale")}
                      </th>
                      <th className="px-3 py-2 text-right font-medium">
                        {t("saleReport.resaleOrdersHeader", "Đơn resale")}
                      </th>
                      <th className="px-3 py-2 text-right font-medium">
                        {t("saleReport.resaleCustomersHeader", "Khách quay lại")}
                      </th>
                      <th className="px-3 py-2 text-right font-medium">
                        {t("saleReport.resaleRevenueHeader", "Doanh thu resale")}
                      </th>
                      <th className="px-3 py-2 text-right font-medium">
                        {t("saleReport.resaleAovHeader", "AOV resale")}
                      </th>
                      <th className="px-3 py-2 text-right font-medium">
                        {t("saleReport.resaleShareHeader", "% trên tổng doanh thu")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr
                        key={row.key}
                        className="border-b border-border/60 last:border-b-0 hover:bg-muted/40"
                      >
                        <td className="px-3 py-2 font-medium text-foreground">{row.label}</td>
                        {cells(row)}
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="border-t border-border font-semibold">
                      <td className="px-3 py-2">{totalLabel}</td>
                      {cells(totals)}
                    </tr>
                  </tfoot>
                </table>
                <p className="mt-2 text-xs text-muted-foreground">
                  {t(
                    "saleReport.resaleNote",
                    "Đơn resale = đơn của khách đã có ít nhất một đơn giao thành công trước đó. Doanh thu chỉ tính các đơn đã chốt / đã lên đơn, cùng phạm vi với tab \"Báo cáo theo sale\". Cột \"Khách quay lại\" đếm riêng theo từng nhân viên, nên dòng Tổng có thể đếm trùng một khách được nhiều sale phục vụ.",
                  )}
                </p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 5: Chèn tab vào `DashboardPage.tsx`**

1. Thêm import cạnh hai import tab hiện có (giữ thứ tự alphabet):

```tsx
import { SaleResaleTab } from "@/features/sale-report/components/SaleResaleTab";
```

2. Trong mảng `tabs`, chèn phần tử NGAY SAU `key: "overview"` và TRƯỚC `key: "by-sale"`:

```tsx
    {
      key: "resale",
      label: t("saleReport.tabResale", "Resale"),
      content: (
        <div className="pt-4">
          <SaleResaleTab dateFrom={dateFilter.from} dateTo={dateFilter.to} />
        </div>
      ),
    },
```

Không đụng tới `showSharedFilters` — tab này DÙNG bộ lọc ngày chung, chỉ tab `sale-detail` mới ẩn nó.

- [ ] **Step 6: Sửa `DashboardPage.tabs.test.tsx` — BẮT BUỘC, không phải "nếu đỏ"**

File `frontend/src/pages/__tests__/DashboardPage.tabs.test.tsx` mock TOÀN BỘ module
`@/features/sale-report/queries` bằng factory chỉ trả **một** export:

```tsx
vi.mock("@/features/sale-report/queries", () => ({
  useSaleStaffReport: (...args: unknown[]) => mockUseSaleStaffReport(...args),
}));
```

`SaleResaleTab` import `useSaleResaleReport` từ đúng module đó, nên sau Task 4 factory
này thiếu export → test **chắc chắn vỡ**. Đây là hệ quả biết trước, phải sửa ngay trong
task này chứ không chờ nó đỏ.

Ba sửa đổi trong file test đó:

1. Thêm mock thứ hai cạnh mock hiện có (khai `const` TRƯỚC `vi.mock`, và dùng
   `vi.hoisted` nếu file đang khai bằng `const mockUseSaleStaffReport = vi.fn()` ở
   scope module — kiểm cách file đang làm rồi làm y hệt):

```tsx
const mockUseSaleResaleReport = vi.fn();
vi.mock("@/features/sale-report/queries", () => ({
  useSaleStaffReport: (...args: unknown[]) => mockUseSaleStaffReport(...args),
  useSaleResaleReport: (...args: unknown[]) => mockUseSaleResaleReport(...args),
}));
```

2. Trong `renderPage()`, thêm giá trị trả về mặc định cho hook mới (ngay dưới
   `mockUseSaleStaffReport.mockReturnValue(...)`):

```tsx
  mockUseSaleResaleReport.mockClear();
  mockUseSaleResaleReport.mockReturnValue({
    isLoading: false,
    isError: false,
    data: { by_staff: [] },
    error: null,
  });
```

3. Cập nhật test đầu tiên cho đúng 4 tab (đổi cả tên test — tiêu đề "3 tab" thành sai):

```tsx
  it("hiện đủ 4 tab, mặc định mở Tổng quan", () => {
    renderPage();
    for (const label of [
      "Tổng quan",
      "Resale",
      "Báo cáo theo sale",
      "Báo cáo chi tiết theo sale",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });
```

Thêm một test khẳng định tab mới lười gọi API đúng như các tab khác:

```tsx
  it("chỉ gọi API resale khi mở tab Resale", () => {
    renderPage();
    expect(mockUseSaleResaleReport).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Resale" }));
    expect(mockUseSaleResaleReport).toHaveBeenCalledWith({
      dateFrom: "2026-03-01",
      dateTo: "2026-03-31",
    });
  });
```

- [ ] **Step 7: Chạy test để xác nhận XANH**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report/frontend"
npx vitest run src/features/sale-report src/pages/__tests__/DashboardPage.tabs.test.tsx
```

Kỳ vọng: tất cả xanh — 4 file, gồm 4 test mới của `SaleResaleTab` và 5 test của
`DashboardPage.tabs` (4 cũ + 1 mới).

- [ ] **Step 8: Type-check + lint**

```bash
npx tsc --noEmit
npx eslint src/features/sale-report src/pages/DashboardPage.tsx src/pages/__tests__/DashboardPage.tabs.test.tsx
```

Bỏ qua lỗi tsc sẵn có của `src/features/audit/__tests__/audit-logs-api.test.ts` (`.at()` vs lib es2022). Mọi lỗi khác phải sạch.

- [ ] **Step 9: Commit**

Chỉ chạy prettier trên ĐÍCH DANH file mình chạm — `--write` cả thư mục sẽ sinh nhiễu format của file khác:

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report/frontend"
npx prettier --write \
  src/features/sale-report/components/ResaleRevenueChart.tsx \
  src/features/sale-report/components/SaleResaleTab.tsx \
  src/features/sale-report/__tests__/SaleResaleTab.test.tsx \
  src/pages/DashboardPage.tsx \
  src/pages/__tests__/DashboardPage.tabs.test.tsx
cd .. && git add frontend/src/ && git commit -m "feat(sale-report): resale tab with staff ranking chart"
```

---

## Task 5: Chốt — chạy đủ suite, cập nhật spec, push, MR

**Files:**
- Modify: `docs/superpowers/specs/2026-08-25-sale-resale-report-design.md` (bảng Sprint)

**Interfaces:**
- Consumes: mọi thứ của Task 1–4.
- Produces: nhánh đã push + link MR.

- [ ] **Step 1: Chạy TRỌN suite frontend**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report/frontend"
npx vitest run 2>&1 | tail -20
```

**Bắt buộc:** trước khi kết luận có hồi quy, đo lại baseline trên master. Ghi chú cũ (164 file / 1249 test XANH, 2026-08-18) có thể đã lỗi thời — baseline repo này từng đổi trong vòng 1 ngày. Nếu có file đỏ, `git stash` rồi chạy lại trên master để phân biệt hồi quy thật với nợ đỏ có sẵn.

- [ ] **Step 2: Chạy suite backend của app orders**

Chạy nền vì lâu (>10 phút thì shell của harness bị kill nhưng container vẫn chạy — dùng `docker run -d` + vòng chờ, hoặc redirect ra file log):

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report" && \
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_resale_final \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend://app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders -q --create-db' \
  > /tmp/resale-backend.log 2>&1; tail -20 /tmp/resale-backend.log
```

⚠️ Nợ đỏ ĐÃ BIẾT trên master, KHÔNG phải do nhánh này: test N+1 export đơn hàng assert cứng 9 query trong khi master đang 10. Nếu gặp, xác nhận bằng cách chạy trên worktree sạch rồi bỏ qua.

- [ ] **Step 3: Chạy import-linter**

Hợp đồng tầng là ràng buộc quan trọng nhất của task này (xem Global Constraints). Phải đặt `HOME=/tmp`, nếu không `pip install --user` sẽ ghi `.local/` vào chính worktree đang mount rồi lọt vào `git status`:

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report" && \
MSYS_NO_PATHCONV=1 docker run --rm -e HOME=/tmp \
  -v "$(pwd -W)/backend://app" -w //app desi-backend-test:latest \
  sh -c 'pip install -q --user import-linter && /tmp/.local/bin/lint-imports --config .importlinter' 2>&1 | tail -20
```

Kỳ vọng: contract về `customers | orders` KHÔNG có vi phạm mới. Nếu `apps.orders.reporting.resale_reports -> apps.customers.*` xuất hiện → đã import sai, quay lại Task 1 dùng `_base_orders_queryset()`.

- [ ] **Step 4: Lint toàn bộ phần đã chạm**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report"
docker run --rm -v "$(pwd -W)/backend://app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
cd frontend && npx eslint src/features/sale-report src/pages/DashboardPage.tsx && npx tsc --noEmit
```

- [ ] **Step 5: Kiểm `git status` sạch**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report"
git status --porcelain
```

Kỳ vọng: **rỗng**. Nếu thấy `backend/.local/` → import-linter đã ghi bậy vì thiếu `HOME=/tmp`; xoá đi. Nếu thấy `frontend/node_modules` → junction lọt vào git; kiểm `.gitignore`.

- [ ] **Step 6: Cập nhật bảng Sprint trong spec**

Đổi cả 5 dòng của bảng Sprint từ `⬜ chưa làm` sang `✅ xong`, và thêm ngay dưới bảng một mục ghi lại **đã làm gì / còn nợ gì**:

```markdown
### Ghi nhận sau khi thực hiện (2026-08-25)

**Đã làm:** toàn bộ S1–S5. Backend `summary_resale_by_staff` + endpoint
`/api/orders/reports/resale-by-staff`; frontend tab "Resale" (vị trí 2) với chart bar
ngang + bảng 6 cột. Test: <N> test backend mới, <M> test frontend mới.

**Chưa làm (cố ý, xem "Ngoài phạm vi"):** xuất Excel, biểu đồ diễn biến theo ngày,
lọc theo `era` ngành hàng.

**Còn nợ sau deploy:** 3 mục ở phần "Rủi ro & việc phải làm sau deploy" — EXPLAIN trên
prod, đối chiếu tay một kỳ với tab "Báo cáo theo sale", và báo trước cho phòng Sale về
chênh lệch với số họ tự đếm trên trang Theo dõi resale.
```

Thay `<N>` / `<M>` bằng số thật đã đếm được.

- [ ] **Step 7: Commit và push kèm tạo MR**

Không có `glab` / token trên máy này — tạo MR bằng push options:

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/sale-resale-report"
git add docs/superpowers/specs/2026-08-25-sale-resale-report-design.md
git commit -m "docs: mark resale report sprints done"
git push -u origin feat/sale-resale-report \
  -o merge_request.create \
  -o merge_request.title="feat(sale-report): tab Resale - doanh thu khach mua lai theo nhan vien" \
  -o merge_request.remove_source_branch
```

Nếu push báo "Everything up-to-date" mà MR chưa được tạo: xoá remote branch (`git push origin --delete feat/sale-resale-report`) rồi push lại với push options.

- [ ] **Step 8: Gửi link MR cho user**

Lấy URL MR từ output của `git push` và gửi kèm tóm tắt ngắn: đã làm gì, số test, và **cảnh báo trước khi deploy** rằng đây là tab MỚI nên không có số cũ nào bị đổi — nhưng phòng Sale sẽ thấy % resale khác với số họ tự đếm trên trang Theo dõi resale, vì hai nơi đo hai thứ khác nhau.

⚠️ **Sau khi gửi link MR, ĐÓNG BĂNG nhánh này.** User merge MR rất nhanh; mọi thay đổi phát sinh sau đó phải đi bằng MR mới trên nhánh mới.

---

## Self-Review

**Spec coverage:**

| Mục spec | Task phủ |
|---|---|
| Định nghĩa đơn resale + mệnh đề GTC | Task 1 Step 3 |
| Mốc `created_at` không phải ngày giao | Task 1 Step 3 (docstring) + test `test_two_resale_orders_same_customer_count_one_customer` |
| Gán theo `Order.staff` của đơn resale | Task 1 Step 3 (`values("staff_id", ...)`) |
| Phạm vi `_sales_report_orders_queryset` + 4 mã chốt | Task 1 Step 3 + test `test_staff_outside_sales_department_excluded`, `test_resale_order_outside_closed_statuses_not_counted` |
| Module riêng `resale_reports.py` | Task 1 Step 3 |
| Ghép mixin | Task 1 Step 4 |
| Trả cả `total_*` lẫn `resale_*` | Task 1 Step 3 + Task 2 Step 1 |
| Payload gộp theo nhân viên, không có chiều ngày | Task 1 Interfaces |
| Endpoint + quyền `dashboard` | Task 2 Step 3 + test `test_user_without_dashboard_section_is_denied` |
| `schema.yaml` sửa tay | Task 2 Step 6 |
| Hook + selector FE | Task 3 |
| Chart bar ngang + bảng + chèn tab vị trí 2 | Task 4 |
| Test backend 10 ca của bảng trong spec | Task 1 Step 1 (9 ca) + Task 2 Step 1 (ca quyền) |
| Test FE selector + component | Task 3 Step 1, Task 4 Step 1 |
| Không test chart bằng jsdom | Task 4 Step 1 (ghi rõ) |
| Cập nhật Sprint + ghi chú đã/chưa làm | Task 5 Step 6 |
| 3 việc sau deploy | Task 5 Step 6 + Step 8 |

Không có mục nào trong spec thiếu task.

**Placeholder scan:** không có "TBD" / "TODO" / "tương tự Task N" / "xử lý lỗi phù hợp". `<N>` / `<M>` ở Task 5 Step 6 là chỗ điền số đếm được lúc chạy, đã nói rõ cách lấy. `<PATHS>` trong lệnh mẫu ở Global Constraints là tham số của template, mọi Step dùng nó đều ghi path cụ thể.

**Type consistency:**
- `summary_resale_by_staff` — 8 khoá khai ở Task 1 Interfaces, dùng nguyên vẹn ở Task 2 Step 1 (`set(row)` assert đúng 8 tên), Task 2 Step 3 (`extend_schema`), Task 2 Step 6 (`schema.yaml`), Task 3 (`ResaleStaffApiRow`). Khớp.
- `ResaleStaffRow` (9 trường) — khai ở Task 3, dùng ở Task 4 (`cells`, `ResaleRevenueChart`). Khớp.
- `buildResaleRows` / `sumResaleRows` — cùng chữ ký ở Task 3 Interfaces, Task 3 Step 5, Task 4 Step 4. Khớp.
- `saleKey` / `saleLabel` nhận `StaffIdentity` (Task 3 Step 3) — `ResaleStaffApiRow` và `SaleStaffDailyRow` đều thoả về cấu trúc. Khớp.
- `useSaleResaleReport` — tên dùng nhất quán ở Task 3 Step 4, Task 4 Step 4, và trong `vi.mock` của Task 4 Step 1. Khớp.
