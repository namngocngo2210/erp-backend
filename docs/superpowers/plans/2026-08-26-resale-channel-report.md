# Tab Resale theo kênh — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đổi tab "Resale" từ suy luận theo lịch sử mua sang lấy đúng đơn của **kênh Resale**, và trình bày theo 4 rổ của Báo cáo tổng (tổng · giao thành công · chờ giao · hoàn · rủi ro hoàn) với KPI tiles + chart + bảng theo nhân viên.

**Architecture:** Viết lại `ResaleReportsMixin.summary_resale_by_staff` — bỏ hẳn correlated subquery `Exists(prior_gtc)` và scope kênh Ads, thay bằng lọc `channel_id IN resale_channel_ids()`. Các mệnh đề rổ dùng lại đúng hằng số của `summary_overview` nên hai báo cáo không thể lệch định nghĩa. Payload đổi từ list phẳng sang `{kpis, by_staff}` để FE dựng hàng KPI mà không cần cộng lại ở client.

**Tech Stack:** Django 5 + DRF + drf-spectacular · PostgreSQL · React 19 + TypeScript + TanStack Query + recharts v3 + Tailwind/shadcn · pytest (Docker) · vitest

**Spec:** `docs/superpowers/specs/2026-08-26-resale-channel-report-design.md` — đọc trước khi làm task đầu tiên. Spec này THAY THẾ `2026-08-25-sale-resale-report-design.md` (bản đã deploy qua MR !1366); đừng đọc bản cũ, nó mô tả hành vi ta đang gỡ bỏ.

## Global Constraints

- **Nhánh:** `feat/resale-channel-report`, worktree `.claude/worktrees/resale-channel-report`. Mọi lệnh chạy trong worktree này, KHÔNG ở repo chính.
- **BẪY LỚN NHẤT — `conftest.py` có fixture `autouse` ép `apps.orders.reporting.shared.SALE_FB_CHANNEL_IDS = (9_999_000_000,)` cho TOÀN BỘ suite.** Vì thế:
  - Module production phải đọc qua helper `resale_channel_ids()` định nghĩa TRONG `shared.py`, **không** `from .shared import SALE_FB_CHANNEL_IDS` (import trực tiếp bind giá trị lúc import → monkeypatch không ăn → test XANH GIẢ).
  - Mọi test của tab này phải tự patch lại hằng số về id kênh test, bằng `unittest.mock.patch` trong `setUp` (các class ở đây là `django.test.TestCase`, không dùng được fixture `monkeypatch` của pytest).
- **Không sao chép hằng số rổ.** Import `LEAD_ORDER_Q`, `CANCELLED_ORDER_Q`, `DELIVERED_SUCCESS_ORDER_STATUS`, `RETURN_ORDER_STATUSES`, `RETURN_RISK_ORDER_STATUSES`, `OVERVIEW_EXCLUDED_BRANCH_IDS` từ `.shared`. Rổ "chờ giao" định nghĩa bằng PHÉP TRỪ, không liệt kê dương.
- **Bất biến bắt buộc:** `delivered + awaiting + returned + return_risk = total`, đúng cả tiền lẫn số đơn.
- Không import `apps.customers` từ module production của `apps.orders` (`.importlinter`: cùng tầng, độc lập; chỉ `apps.orders.tests.*` được miễn).
- Money qua JSON là **chuỗi decimal** (`str(v or Decimal("0"))`), không phải float.
- Quyền giữ nguyên `require_section("dashboard")`. Không thêm quyền, không thêm env, không sửa docker.
- `schema.yaml` sửa TAY, KHÔNG regen (master đã cũ sẵn, regen kéo nhiễu feature khác).
- i18n: `t("saleReport.<key>", "<fallback tiếng Việt>")` inline; namespace này KHÔNG có file dịch.
- Python ≥ 3.12, ruff sạch. Commit message tiếng Anh, ngắn, KHÔNG kèm `Co-Authored-By`.
- Không chạy `npm ci` — worktree đã có junction `node_modules` hoạt động.
- prettier chỉ chạy trên đúng file mình chạm, không `--write` cả thư mục.

### Lệnh chuẩn

**Backend test** — `POSTGRES_DB` và `REDIS_CACHE_DB` phải RIÊNG mỗi lần chạy; KHÔNG chạy hai container pytest song song (dùng chung DB sinh lỗi giả). Chạy FOREGROUND với timeout rộng (~3 phút), đừng đẩy nền rồi dừng:

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report" && \
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=<unique> \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=<unique 0-15> \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend://app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <PATHS> -q --create-db'
```

**Backend lint:**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report" && \
docker run --rm -v "$(pwd -W)/backend://app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache apps/orders/
```

**Frontend** (chạy foreground, nhanh):

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report/frontend"
npx vitest run src/features/sale-report src/pages/__tests__/DashboardPage.tabs.test.tsx
npx tsc --noEmit
npx eslint src/features/sale-report src/pages/DashboardPage.tsx
```

### Baseline đã đo trong chính worktree này (2026-08-26)

- `apps/orders/tests/test_sale_reports.py` → **62 passed** in 175s. Task 1 và Task 2 viết lại 2 class resale trong file này, nên con số cuối sẽ khác — điều phải giữ là **không class nào NGOÀI hai class resale bị đỏ**.
- `npx vitest run src/features/sale-report` → 3 file. Task 3 và Task 4 viết lại 2 file test trong đó.

### Số prod để đối chiếu (đo 2026-08-26, kênh id 24, kỳ 01–25/08/2026)

| Rổ | Đơn | Tiền |
|---|---:|---:|
| Tổng | 82 | 142.889.000 |
| Giao thành công | 70 | 122.760.000 |
| Chờ giao | 9 | 16.340.000 |
| Hoàn | 1 | 1.999.000 |
| Rủi ro hoàn | 2 | 1.790.000 |

Kênh 24 có **0 đơn lead** trong kỳ. Sau khi deploy, tab tụt từ 148 đơn/258,65tr xuống các số trên, và bảng còn 5 nhân viên thay vì 7.

---

## File Structure

| File | Trách nhiệm |
|---|---|
| `backend/apps/orders/reporting/shared.py` | thêm helper `resale_channel_ids()` (monkeypatch-safe) |
| `backend/apps/orders/reporting/resale_reports.py` | **viết lại** — chỉ gộp 5 rổ theo kênh Resale, không biết gì về HTTP |
| `backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py` | view mỏng, payload `{kpis, by_staff}` |
| `backend/schema.yaml` | sửa TAY |
| `backend/apps/orders/tests/test_sale_reports.py` | viết lại 2 class resale |
| `frontend/src/features/sale-report/queries.ts` | type payload mới |
| `frontend/src/features/sale-report/resale-selectors.ts` | **viết lại** — hàm thuần |
| `frontend/src/features/sale-report/components/ResaleKpiTiles.tsx` | **mới** — 5 ô KPI |
| `frontend/src/features/sale-report/components/ResaleRevenueChart.tsx` | đổi trục sang doanh thu giao thành công |
| `frontend/src/features/sale-report/components/SaleResaleTab.tsx` | KPI → chart → bảng |
| `frontend/src/features/sale-report/__tests__/*` | cập nhật theo shape mới |

**Phụ thuộc:** T1 → T2. T3 → T4. T1 và T3 độc lập. T5 sau tất cả.

---

## Task 1: Backend — helper + viết lại aggregation

**Files:**
- Modify: `backend/apps/orders/reporting/shared.py` (thêm 1 hàm)
- Rewrite: `backend/apps/orders/reporting/resale_reports.py`
- Test: `backend/apps/orders/tests/test_sale_reports.py` (thay class `SaleResaleByStaffTests`)

**Interfaces:**
- Consumes: `cls._apply_created_at_date_filters`, `cls._exclude_hidden_branches` (cả hai resolve qua MRO của `OrderRepository` — KHÔNG import trực tiếp, sẽ vòng import).
- Produces:
  ```python
  OrderRepository.summary_resale_by_staff(
      date_from: str | None = None,
      date_to: str | None = None,
  ) -> dict
  ```
  ```python
  {
    "kpis": {<10 khoá rổ>},
    "by_staff": [{"staff_id", "staff__username", "staff__display_name", <10 khoá rổ>}],
  }
  ```
  10 khoá rổ = 5 cặp: `total_revenue`/`total_orders`, `delivered_revenue`/`delivered_orders`, `awaiting_revenue`/`awaiting_orders`, `returned_revenue`/`returned_orders`, `return_risk_revenue`/`return_risk_orders`. Cột `*_revenue` là chuỗi decimal, `*_orders` là int. `by_staff` sắp giảm dần theo `delivered_revenue`.
  Cũng produces: `apps.orders.reporting.shared.resale_channel_ids() -> tuple[int, ...]`.

- [ ] **Step 1: Thêm helper vào `shared.py`**

Đặt NGAY TRÊN `def sale_report_channel_ids()`:

```python
def resale_channel_ids() -> tuple[int, ...]:
    """Ids kênh Resale — hiện DÙNG CHUNG với ``SALE_FB_CHANNEL_IDS``.

    Kênh id 24 mang tên "Resale" nhưng ``code = 'sale-fb'``, và biến env
    ``SALE_FB_CHANNEL_IDS`` đang nuôi cả báo cáo "doanh thu Sale FB".

    ⚠️ THÊM KÊNH VÀO ``SALE_FB_CHANNEL_IDS`` SẼ ĐỔI LUÔN SỐ CỦA TAB RESALE.
    Hai báo cáo dùng chung một công tắc là quyết định có chủ ý (user chốt
    2026-08-26), không phải sơ suất. Cần tách thì thêm env ``RESALE_CHANNEL_IDS``
    riêng và chỉ sửa hàm này.

    Đọc biến module TẠI THỜI ĐIỂM GỌI chứ không bind lúc import: ``conftest.py``
    có fixture autouse patch hằng số này thành ``(9_999_000_000,)`` cho cả suite
    (để pk ``Channel`` tự tăng không tình cờ chạm 24). Module nào
    ``from .shared import SALE_FB_CHANNEL_IDS`` sẽ giữ giá trị cũ và test hoá ra
    XANH GIẢ.
    """
    return SALE_FB_CHANNEL_IDS
```

- [ ] **Step 2: Viết test thất bại**

Trong `backend/apps/orders/tests/test_sale_reports.py`, **XOÁ toàn bộ class `SaleResaleByStaffTests` cũ** và thay bằng class dưới đây. Giữ nguyên `SaleResaleReportApiTests` ở chỗ cũ — Task 2 sẽ thay nó.

Thêm import ở đầu file nếu chưa có: `from unittest import mock`.

```python
class SaleResaleByStaffTests(_ReportFixtureMixin, TestCase):
    """Tab Resale — đơn của KÊNH Resale, chia 4 rổ như Báo cáo tổng.

    Xem docs/superpowers/specs/2026-08-26-resale-channel-report-design.md.
    """

    def setUp(self):
        super().setUp()
        self.resale_channel = Channel.objects.create(code="resale-test", name="Resale Test")
        self.other_channel = Channel.objects.create(code="other-test", name="Other Test")
        # BẮT BUỘC: conftest có fixture autouse ép SALE_FB_CHANNEL_IDS =
        # (9_999_000_000,) cho cả suite. Không patch lại thì mọi rổ ra 0 và test
        # "xanh" vì lý do hoàn toàn sai.
        patcher = mock.patch(
            "apps.orders.reporting.shared.SALE_FB_CHANNEL_IDS",
            (self.resale_channel.id,),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _order(self, total, *, status=3, channel=None, staff=None, invoice_status=1):
        return Order.objects.create(
            channel=channel or self.resale_channel,
            customer=self.customer,
            staff=staff or self.sale,
            name="X",
            phone="0900000009",
            has_address=True,
            status=status,
            invoice_status=invoice_status,
            order_source="landing",
            total_after_discount=Decimal(total),
            landing_confirmed_amount=Decimal("0"),
        )

    def _kpis(self):
        return OrderRepository.summary_resale_by_staff()["kpis"]

    def test_order_outside_resale_channel_is_ignored(self):
        self._order("100000", channel=self.other_channel)

        kpis = self._kpis()
        self.assertEqual(kpis["total_orders"], 0)
        self.assertEqual(kpis["total_revenue"], "0")

    def test_delivered_order_lands_in_delivered_bucket(self):
        self._order("100000", status=3)

        kpis = self._kpis()
        self.assertEqual(kpis["delivered_orders"], 1)
        self.assertEqual(kpis["delivered_revenue"], "100000.00")
        self.assertEqual(kpis["total_revenue"], "100000.00")

    def test_in_delivery_order_lands_in_awaiting_bucket(self):
        self._order("80000", status=2)

        kpis = self._kpis()
        self.assertEqual(kpis["awaiting_orders"], 1)
        self.assertEqual(kpis["awaiting_revenue"], "80000.00")

    def test_unknown_status_falls_into_awaiting_not_out_of_every_bucket(self):
        # Rổ "chờ giao" là PHÉP TRỪ, nên mã lạ (KiotViet thêm mới) phải rơi vào
        # đây thay vì biến mất khỏi cả 4 rổ và làm tổng lệch.
        self._order("70000", status=99)

        kpis = self._kpis()
        self.assertEqual(kpis["awaiting_orders"], 1)
        self.assertEqual(kpis["total_orders"], 1)

    def test_returned_order_lands_in_returned_bucket(self):
        self._order("60000", status=4)

        kpis = self._kpis()
        self.assertEqual(kpis["returned_orders"], 1)
        self.assertEqual(kpis["returned_revenue"], "60000.00")

    def test_return_risk_order_lands_in_its_own_bucket(self):
        self._order("50000", status=10)

        kpis = self._kpis()
        self.assertEqual(kpis["return_risk_orders"], 1)
        self.assertEqual(kpis["returned_orders"], 0)

    def test_cancelled_orders_are_in_no_bucket(self):
        self._order("100000", status=6)
        self._order("90000", status=3, invoice_status=2)

        self.assertEqual(self._kpis()["total_orders"], 0)

    def test_lead_order_without_invoice_is_in_no_bucket(self):
        self._order("100000", status=3, invoice_status=None)

        self.assertEqual(self._kpis()["total_orders"], 0)

    def test_four_buckets_always_sum_to_total(self):
        for status, total in ((3, "100000"), (2, "80000"), (4, "60000"), (10, "50000"), (99, "40000")):
            self._order(total, status=status)

        kpis = self._kpis()
        self.assertEqual(
            kpis["delivered_orders"] + kpis["awaiting_orders"]
            + kpis["returned_orders"] + kpis["return_risk_orders"],
            kpis["total_orders"],
        )
        self.assertEqual(
            Decimal(kpis["delivered_revenue"]) + Decimal(kpis["awaiting_revenue"])
            + Decimal(kpis["returned_revenue"]) + Decimal(kpis["return_risk_revenue"]),
            Decimal(kpis["total_revenue"]),
        )

    def test_order_with_live_restore_clone_is_excluded_once(self):
        original = self._order("100000", status=3)
        clone = self._order("100000", status=3)
        Order.objects.filter(pk=clone.pk).update(restored_from_id=original.pk)

        # Đơn gốc bị loại (đã có bản khôi phục còn sống); chỉ clone được tính.
        kpis = self._kpis()
        self.assertEqual(kpis["total_orders"], 1)
        self.assertEqual(kpis["total_revenue"], "100000.00")

    def test_order_without_staff_still_counted_and_gets_its_own_row(self):
        order = self._order("100000", status=3)
        Order.objects.filter(pk=order.pk).update(staff=None)

        payload = OrderRepository.summary_resale_by_staff()
        self.assertEqual(payload["kpis"]["total_orders"], 1)
        self.assertEqual([row["staff_id"] for row in payload["by_staff"]], [None])

    def test_kpis_equal_the_sum_of_by_staff_rows(self):
        other = User.objects.create_user(username=f"sale2-{id(self)}", password="x")
        User.objects.filter(pk=other.pk).update(department_id=DepartmentID.SALES)
        self._order("100000", status=3)
        self._order("80000", status=2, staff=other)
        self._order("60000", status=4, staff=other)

        payload = OrderRepository.summary_resale_by_staff()
        for field in ("total_orders", "delivered_orders", "awaiting_orders", "returned_orders"):
            self.assertEqual(
                sum(row[field] for row in payload["by_staff"]),
                payload["kpis"][field],
                f"cot {field} khong khop giua kpis va by_staff",
            )
        self.assertEqual(
            sum(Decimal(row["total_revenue"]) for row in payload["by_staff"]),
            Decimal(payload["kpis"]["total_revenue"]),
        )

    def test_by_staff_sorted_by_delivered_revenue_desc(self):
        other = User.objects.create_user(username=f"sale3-{id(self)}", password="x")
        User.objects.filter(pk=other.pk).update(department_id=DepartmentID.SALES)
        self._order("50000", status=3)
        self._order("90000", status=3, staff=other)

        rows = OrderRepository.summary_resale_by_staff()["by_staff"]
        self.assertEqual([row["staff_id"] for row in rows], [other.id, self.sale.id])
```

- [ ] **Step 3: Chạy test để xác nhận nó ĐỎ**

Lệnh chuẩn, `POSTGRES_DB=desi_rs2_t1`, `REDIS_CACHE_DB=3`, paths `apps/orders/tests/test_sale_reports.py::SaleResaleByStaffTests`.
Kỳ vọng: **FAIL** — `TypeError` (chữ ký cũ còn tham số `sale_channel_ids`) hoặc `KeyError: 'kpis'`.

- [ ] **Step 4: Viết lại `resale_reports.py`**

Thay TOÀN BỘ nội dung file:

```python
"""Báo cáo Resale — đơn của KÊNH Resale, chia rổ theo kết quả giao hàng.

Nuôi tab "Resale" của trang Báo cáo Sale. Thiết kế:
docs/superpowers/specs/2026-08-26-resale-channel-report-design.md

Bản trước (MR !1366) SUY LUẬN đơn resale từ lịch sử mua (khách đã có đơn giao
thành công trước đó) trong phạm vi kênh ad-driven. Đo prod cho thấy cách đó chỉ
phủ 31% doanh thu resale thật, và mỉa mai nhất là loại luôn chính kênh tên
"Resale". Công ty KHÔNG định nghĩa resale bằng lịch sử mua — sale tạo đơn resale
trên một kênh riêng, nên kênh mới là nguồn sự thật.

Năm rổ dùng lại NGUYÊN VĂN định nghĩa của ``summary_overview`` (cùng hằng số,
cùng phép trừ) để hai trang không thể lệch định nghĩa trong im lặng.
"""
from decimal import Decimal

from django.db.models import Count, Exists, OuterRef, Q, Sum

from apps.orders.models import Order

from .shared import (
    CANCELLED_ORDER_Q,
    DELIVERED_SUCCESS_ORDER_STATUS,
    LEAD_ORDER_Q,
    OVERVIEW_EXCLUDED_BRANCH_IDS,
    RETURN_ORDER_STATUSES,
    RETURN_RISK_ORDER_STATUSES,
    resale_channel_ids,
)

# 5 cặp (tiền, số đơn). Tên khớp từng chữ với summary_overview để hai báo cáo
# đối chiếu được mà không phải dịch tên cột.
_MONEY_FIELDS = (
    "total_revenue",
    "delivered_revenue",
    "awaiting_revenue",
    "returned_revenue",
    "return_risk_revenue",
)


def _bucket_aggregates():
    """Năm cặp aggregate dùng chung cho cả `kpis` lẫn từng hàng `by_staff`.

    Một chỗ định nghĩa duy nhất: hai lượt gộp không thể lệch mệnh đề.
    """
    total_q = ~LEAD_ORDER_Q & ~CANCELLED_ORDER_Q
    delivered_q = total_q & Q(status=DELIVERED_SUCCESS_ORDER_STATUS)
    # PHỦ ĐỊNH, không liệt kê dương: Order.status là PositiveSmallIntegerField
    # không ép choices ở tầng DB, và luồng import KiotViet ghi thẳng int từ
    # payload API. Mã trạng thái lạ phải rơi vào MỘT rổ, không được rớt khỏi cả
    # bốn rổ và làm tổng lệch.
    awaiting_q = total_q & ~Q(
        status__in=(
            DELIVERED_SUCCESS_ORDER_STATUS,
            *RETURN_ORDER_STATUSES,
            *RETURN_RISK_ORDER_STATUSES,
        )
    )
    returned_q = total_q & Q(status__in=RETURN_ORDER_STATUSES)
    return_risk_q = total_q & Q(status__in=RETURN_RISK_ORDER_STATUSES)
    return {
        "total_revenue": Sum("total_after_discount", filter=total_q),
        "total_orders": Count("id", filter=total_q),
        "delivered_revenue": Sum("total_after_discount", filter=delivered_q),
        "delivered_orders": Count("id", filter=delivered_q),
        "awaiting_revenue": Sum("total_after_discount", filter=awaiting_q),
        "awaiting_orders": Count("id", filter=awaiting_q),
        "returned_revenue": Sum("total_after_discount", filter=returned_q),
        "returned_orders": Count("id", filter=returned_q),
        "return_risk_revenue": Sum("total_after_discount", filter=return_risk_q),
        "return_risk_orders": Count("id", filter=return_risk_q),
    }


def _stringify_money(row: dict) -> dict:
    for field in _MONEY_FIELDS:
        row[field] = str(row[field] or Decimal("0"))
    return row


class ResaleReportsMixin:
    """summary_resale_* — ghép vào ``OrderRepository`` qua ``OrderReportsMixin``."""

    @classmethod
    def summary_resale_by_staff(
        cls,
        date_from: str | None = None,
        date_to: str | None = None,
    ):
        """Đơn của kênh Resale trong kỳ, chia 4 rổ, kèm bảng theo nhân viên.

        ĐƠN RESALE = đơn thuộc kênh Resale (``resale_channel_ids()``). KHÔNG suy
        luận từ lịch sử mua nữa — xem docstring đầu module.

        DOANH THU = mọi đơn ĐÃ LÊN HÓA ĐƠN KiotViet trừ đơn huỷ
        (``~LEAD_ORDER_Q & ~CANCELLED_ORDER_Q``), giống hệt Báo cáo tổng. Tổng đó
        chia đúng 4 rổ RỜI NHAU nên ``delivered + awaiting + returned +
        return_risk = total``, cả tiền lẫn số đơn — có test khoá bất biến này.

        KHÔNG lọc theo phòng ban nhân viên: phạm vi đã do kênh quyết định, thêm
        điều kiện phòng ban chỉ tạo rủi ro âm thầm mất người khi CSKH tham gia
        bán resale. Đơn chưa gán sale rơi vào hàng ``staff_id = None``.
        """
        base = cls._apply_created_at_date_filters(
            cls._exclude_hidden_branches(Order.objects.filter(deleted_at__isnull=True)),
            date_from,
            date_to,
        ).exclude(branch_id__in=OVERVIEW_EXCLUDED_BRANCH_IDS)
        # Clone khôi phục kế thừa created_at của đơn gốc nên rơi vào CÙNG kỳ; không
        # loại đơn gốc thì cả hai cùng cộng vào tổng.
        #
        # DÙNG Exists(), KHÔNG `.exclude(restored_orders__deleted_at__isnull=True)`:
        # lookup `__isnull=True` xuyên quan hệ to-many dùng LEFT OUTER JOIN nên nó
        # khớp CẢ đơn không có bản khôi phục nào — exclude() khi đó xoá gần sạch bảng.
        base = base.exclude(
            Exists(Order.objects.filter(restored_from_id=OuterRef("pk"), deleted_at__isnull=True))
        )
        # Đọc qua helper, KHÔNG import hằng số: conftest patch nó cho cả suite.
        # Index ord_act_ch_st_cr_idx — (channel_id, status, created_at DESC)
        # WHERE deleted_at IS NULL — phủ đúng lát cắt này.
        base = base.filter(channel_id__in=resale_channel_ids())

        aggregates = _bucket_aggregates()
        kpis = _stringify_money(base.aggregate(**aggregates))
        for field, value in kpis.items():
            if field.endswith("_orders"):
                kpis[field] = value or 0

        rows = [
            _stringify_money(row)
            for row in base.values("staff_id", "staff__username", "staff__display_name").annotate(
                **aggregates
            )
        ]
        for row in rows:
            for field, value in row.items():
                if field.endswith("_orders"):
                    row[field] = value or 0
        # Sắp ở Python: order_by trên alias của Sum có filter buộc Postgres gói
        # thêm một lớp subquery, mà số hàng ở đây chỉ cỡ số nhân viên.
        rows.sort(key=lambda row: Decimal(row["delivered_revenue"]), reverse=True)
        return {"kpis": kpis, "by_staff": rows}
```

- [ ] **Step 5: Chạy test để xác nhận XANH**

Lệnh chuẩn, `POSTGRES_DB=desi_rs2_t1b`, `REDIS_CACHE_DB=3`, paths `apps/orders/tests/test_sale_reports.py::SaleResaleByStaffTests`.
Kỳ vọng: **13 passed**.

⚠️ `SaleResaleReportApiTests` (class API cũ) SẼ ĐỎ ở bước này vì payload đổi shape — đó là hệ quả biết trước, Task 2 sửa. Đừng vá nó ở đây.

- [ ] **Step 6: Lint**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report" && \
docker run --rm -v "$(pwd -W)/backend://app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache apps/orders/reporting/ apps/orders/tests/test_sale_reports.py
```

Kỳ vọng: `All checks passed!` — chú ý import thừa còn sót từ bản cũ (`CLOSED_SALES_STATUS_CODES` không còn dùng).

- [ ] **Step 7: Commit**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report"
git add backend/apps/orders/reporting/shared.py backend/apps/orders/reporting/resale_reports.py backend/apps/orders/tests/test_sale_reports.py
git commit -m "feat(reports): resale report scoped by channel with overview buckets"
```

---

## Task 2: Backend — endpoint payload mới

**Files:**
- Modify: `backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py`
- Modify: `backend/schema.yaml`
- Test: `backend/apps/orders/tests/test_sale_reports.py` (thay class `SaleResaleReportApiTests`)

**Interfaces:**
- Consumes: `OrderRepository.summary_resale_by_staff(date_from, date_to)` → `{"kpis": {...}, "by_staff": [...]}` (Task 1).
- Produces: `GET /api/orders/reports/resale-by-staff?date_from=&date_to=` → đúng dict đó. URL, route name `orders-resale-report` và quyền GIỮ NGUYÊN.

- [ ] **Step 1: Viết test thất bại**

Thay TOÀN BỘ class `SaleResaleReportApiTests` bằng:

```python
class SaleResaleReportApiTests(_ReportFixtureMixin, TestCase):
    URL = "/api/orders/reports/resale-by-staff"

    BUCKET_FIELDS = {
        "total_revenue", "total_orders",
        "delivered_revenue", "delivered_orders",
        "awaiting_revenue", "awaiting_orders",
        "returned_revenue", "returned_orders",
        "return_risk_revenue", "return_risk_orders",
    }

    def setUp(self):
        super().setUp()
        self.resale_channel = Channel.objects.create(code="resale-api", name="Resale API")
        patcher = mock.patch(
            "apps.orders.reporting.shared.SALE_FB_CHANNEL_IDS",
            (self.resale_channel.id,),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _payload(self, **params):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.URL, params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _order(self, total, *, status=3):
        return Order.objects.create(
            channel=self.resale_channel, customer=self.customer, staff=self.sale,
            name="X", phone="0900000009", has_address=True, status=status,
            invoice_status=1, order_source="landing",
            total_after_discount=Decimal(total), landing_confirmed_amount=Decimal("0"),
        )

    def test_payload_has_kpis_and_by_staff_with_bucket_fields(self):
        self._order("100000", status=3)
        self._order("80000", status=2)

        body = self._payload()
        self.assertEqual(set(body), {"kpis", "by_staff"})
        self.assertEqual(set(body["kpis"]), self.BUCKET_FIELDS)
        row = body["by_staff"][0]
        self.assertEqual(
            set(row),
            self.BUCKET_FIELDS | {"staff_id", "staff__username", "staff__display_name"},
        )
        self.assertEqual(body["kpis"]["delivered_revenue"], "100000.00")
        self.assertEqual(body["kpis"]["awaiting_revenue"], "80000.00")
        self.assertEqual(body["kpis"]["total_revenue"], "180000.00")

    def test_empty_range_returns_zeroed_kpis_not_error(self):
        body = self._payload(date_from="2020-01-01", date_to="2020-01-02")
        self.assertEqual(body["by_staff"], [])
        self.assertEqual(body["kpis"]["total_revenue"], "0")
        self.assertEqual(body["kpis"]["total_orders"], 0)

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(self.URL).status_code, (401, 403))

    def test_user_without_dashboard_section_is_denied(self):
        outsider = User.objects.create_user(username=f"outsider-{id(self)}", password="x")
        self.client.force_authenticate(user=outsider)
        self.assertEqual(self.client.get(self.URL).status_code, 403)
```

- [ ] **Step 2: Chạy test để xác nhận nó ĐỎ**

Lệnh chuẩn, `POSTGRES_DB=desi_rs2_t2`, `REDIS_CACHE_DB=4`, paths `apps/orders/tests/test_sale_reports.py::SaleResaleReportApiTests`.
Kỳ vọng: **FAIL** — `set(body)` ra `{"by_staff"}` chứ chưa có `kpis`.

- [ ] **Step 3: Sửa view**

Trong `dashboard_report_views.py`, thay TOÀN BỘ khối `@extend_schema ... def resale_report_summary(...)` bằng:

```python
_RESALE_BUCKET_FIELDS = {
    "total_revenue": drf_serializers.CharField(),
    "total_orders": drf_serializers.IntegerField(),
    "delivered_revenue": drf_serializers.CharField(),
    "delivered_orders": drf_serializers.IntegerField(),
    "awaiting_revenue": drf_serializers.CharField(),
    "awaiting_orders": drf_serializers.IntegerField(),
    "returned_revenue": drf_serializers.CharField(),
    "returned_orders": drf_serializers.IntegerField(),
    "return_risk_revenue": drf_serializers.CharField(),
    "return_risk_orders": drf_serializers.IntegerField(),
}


@extend_schema(
    responses={
        200: inline_serializer(
            "SaleResaleReportResponse",
            fields={
                "kpis": inline_serializer("SaleResaleKpis", fields=dict(_RESALE_BUCKET_FIELDS)),
                "by_staff": inline_serializer(
                    "SaleResaleStaffRow",
                    fields={
                        "staff_id": drf_serializers.IntegerField(allow_null=True),
                        "staff__username": drf_serializers.CharField(allow_null=True),
                        "staff__display_name": drf_serializers.CharField(allow_null=True),
                        **_RESALE_BUCKET_FIELDS,
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
    """Tab "Resale" — đơn của KÊNH Resale, chia 4 rổ như Báo cáo tổng.

    Phạm vi là kênh (``resale_channel_ids()``), KHÔNG còn suy luận từ lịch sử
    mua như bản trước. Bốn rổ cộng lại đúng bằng tổng, cả tiền lẫn số đơn.
    """
    return Response(
        OrderRepository.summary_resale_by_staff(
            date_from=request.query_params.get("date_from", ""),
            date_to=request.query_params.get("date_to", ""),
        )
    )
```

⚠️ Nếu `dict(_RESALE_BUCKET_FIELDS)` gây lỗi "serializer field đã bị bind" khi drf-spectacular dựng schema (một instance field dùng ở hai nơi), đổi `_RESALE_BUCKET_FIELDS` thành **hàm** `_resale_bucket_fields()` trả dict mới mỗi lần gọi, và gọi nó ở cả hai chỗ. Kiểm bằng cách chạy test — nếu schema dựng được thì giữ bản dict.

Bỏ dòng `sale_channel_ids, _excluded_ids = OrderRepository.ads_channel_scope()` — không còn dùng.

- [ ] **Step 4: Chạy test để xác nhận XANH**

Lệnh chuẩn, `POSTGRES_DB=desi_rs2_t2b`, `REDIS_CACHE_DB=4`, paths `apps/orders/tests/test_sale_reports.py`.
Kỳ vọng: cả file xanh — 13 (Task 1) + 4 (Task 2) + các class cũ không đổi.

- [ ] **Step 5: Cập nhật `schema.yaml` bằng TAY**

**KHÔNG regen.** Mở `backend/schema.yaml`, tìm 2 component `SaleResaleReportResponse` và `SaleResaleStaffRow` đang có, rồi:

1. Thay `SaleResaleStaffRow` — bỏ `resale_orders` / `resale_customers` / `resale_revenue`, thêm 10 khoá rổ. `required` sort ALPHABET:

```yaml
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
        total_revenue:
          type: string
        total_orders:
          type: integer
        delivered_revenue:
          type: string
        delivered_orders:
          type: integer
        awaiting_revenue:
          type: string
        awaiting_orders:
          type: integer
        returned_revenue:
          type: string
        returned_orders:
          type: integer
        return_risk_revenue:
          type: string
        return_risk_orders:
          type: integer
      required:
      - awaiting_orders
      - awaiting_revenue
      - delivered_orders
      - delivered_revenue
      - return_risk_orders
      - return_risk_revenue
      - returned_orders
      - returned_revenue
      - staff__display_name
      - staff__username
      - staff_id
      - total_orders
      - total_revenue
```

2. Thêm component `SaleResaleKpis` (đúng 10 khoá trên, bỏ 3 khoá staff, `required` sort alphabet), đặt theo thứ tự alphabet trong `components: schemas:`.

3. Sửa `SaleResaleReportResponse` để có cả `kpis` và `by_staff`:

```yaml
    SaleResaleReportResponse:
      type: object
      properties:
        kpis:
          $ref: '#/components/schemas/SaleResaleKpis'
        by_staff:
          type: array
          items:
            $ref: '#/components/schemas/SaleResaleStaffRow'
      required:
      - by_staff
      - kpis
```

Block path `/api/orders/reports/resale-by-staff` giữ nguyên (vẫn trỏ `SaleResaleReportResponse`); chỉ cập nhật `description` cho khớp docstring mới.

- [ ] **Step 6: Lint + commit**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report"
docker run --rm -v "$(pwd -W)/backend://app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache apps/orders/
git add backend/apps/orders/transport/ backend/schema.yaml backend/apps/orders/tests/test_sale_reports.py
git commit -m "feat(reports): resale endpoint returns kpis and staff buckets"
```

---

## Task 3: Frontend — types + selectors

**Files:**
- Modify: `frontend/src/features/sale-report/queries.ts`
- Rewrite: `frontend/src/features/sale-report/resale-selectors.ts`
- Test: `frontend/src/features/sale-report/__tests__/resale-selectors.test.ts` (viết lại)

**Interfaces:**
- Consumes: hợp đồng payload của Task 2 (không cần backend chạy).
- Produces:
  ```ts
  export type ResaleBucketsApi = {
    total_revenue: string; total_orders: number;
    delivered_revenue: string; delivered_orders: number;
    awaiting_revenue: string; awaiting_orders: number;
    returned_revenue: string; returned_orders: number;
    return_risk_revenue: string; return_risk_orders: number;
  };
  export type ResaleStaffApiRow = ResaleBucketsApi & {
    staff_id: number | null; staff__username: string | null; staff__display_name: string | null;
  };
  export type SaleResaleReportPayload = { kpis: ResaleBucketsApi; by_staff: ResaleStaffApiRow[] };

  export type ResaleBuckets = {
    totalRevenue: number; totalOrders: number;
    deliveredRevenue: number; deliveredOrders: number;
    awaitingRevenue: number; awaitingOrders: number;
    returnedRevenue: number; returnedOrders: number;
    returnRiskRevenue: number; returnRiskOrders: number;
    /** Doanh thu giao thành công / tổng doanh thu (%); null khi tổng = 0. */
    deliveredPct: number | null;
  };
  export type ResaleStaffRow = ResaleBuckets & { key: string; label: string };

  export function buildResaleKpis(kpis: ResaleBucketsApi | undefined): ResaleBuckets;
  export function buildResaleRows(rows: readonly ResaleStaffApiRow[], unknownLabel: string): ResaleStaffRow[];
  export function sumResaleRows(rows: readonly ResaleStaffRow[], totalLabel: string): ResaleStaffRow;
  ```
  `useSaleResaleReport` giữ nguyên tên và chính sách cache; chỉ đổi kiểu trả về.

- [ ] **Step 1: Viết test thất bại**

Thay TOÀN BỘ `frontend/src/features/sale-report/__tests__/resale-selectors.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { ResaleBucketsApi, ResaleStaffApiRow } from "../queries";
import { buildResaleKpis, buildResaleRows, sumResaleRows } from "../resale-selectors";

const UNKNOWN = "Không xác định";

function buckets(overrides: Partial<ResaleBucketsApi> = {}): ResaleBucketsApi {
  return {
    total_revenue: "100000",
    total_orders: 10,
    delivered_revenue: "60000",
    delivered_orders: 6,
    awaiting_revenue: "20000",
    awaiting_orders: 2,
    returned_revenue: "15000",
    returned_orders: 1,
    return_risk_revenue: "5000",
    return_risk_orders: 1,
    ...overrides,
  };
}

function apiRow(overrides: Partial<ResaleStaffApiRow> = {}): ResaleStaffApiRow {
  return {
    ...buckets(),
    staff_id: 1,
    staff__username: "sale1",
    staff__display_name: "Sale Một",
    ...overrides,
  };
}

describe("buildResaleKpis", () => {
  it("parses decimal strings into numbers", () => {
    const kpis = buildResaleKpis(buckets());
    expect(kpis.totalRevenue).toBe(100000);
    expect(kpis.deliveredRevenue).toBe(60000);
    expect(kpis.returnRiskOrders).toBe(1);
  });

  it("computes delivered percent against total revenue", () => {
    expect(buildResaleKpis(buckets()).deliveredPct).toBe(60);
  });

  it("returns null percent instead of NaN when total is zero", () => {
    const kpis = buildResaleKpis(buckets({ total_revenue: "0", delivered_revenue: "0" }));
    expect(kpis.deliveredPct).toBeNull();
  });

  it("returns a zeroed shape for undefined input", () => {
    const kpis = buildResaleKpis(undefined);
    expect(kpis.totalRevenue).toBe(0);
    expect(kpis.totalOrders).toBe(0);
    expect(kpis.deliveredPct).toBeNull();
  });

  it("keeps the four buckets summing to the total", () => {
    const k = buildResaleKpis(buckets());
    expect(k.deliveredRevenue + k.awaitingRevenue + k.returnedRevenue + k.returnRiskRevenue).toBe(
      k.totalRevenue,
    );
    expect(k.deliveredOrders + k.awaitingOrders + k.returnedOrders + k.returnRiskOrders).toBe(
      k.totalOrders,
    );
  });
});

describe("buildResaleRows", () => {
  it("prefers display name, falls back to username then unknown label", () => {
    const rows = buildResaleRows(
      [
        apiRow({ staff_id: 1, delivered_revenue: "90000" }),
        apiRow({ staff_id: 2, staff__display_name: null, staff__username: "sale2", delivered_revenue: "80000" }),
        apiRow({ staff_id: null, staff__display_name: null, staff__username: null, delivered_revenue: "70000" }),
      ],
      UNKNOWN,
    );
    expect(rows.map((row) => row.label)).toEqual(["Sale Một", "sale2", UNKNOWN]);
  });

  it("gives unassigned staff a stable distinct key", () => {
    const [row] = buildResaleRows(
      [apiRow({ staff_id: null, staff__display_name: null, staff__username: null })],
      UNKNOWN,
    );
    expect(row.key).toBe("__unassigned__");
  });

  it("sorts by delivered revenue descending", () => {
    const rows = buildResaleRows(
      [
        apiRow({ staff_id: 1, staff__display_name: "A", delivered_revenue: "10000" }),
        apiRow({ staff_id: 2, staff__display_name: "B", delivered_revenue: "90000" }),
        apiRow({ staff_id: 3, staff__display_name: "C", delivered_revenue: "50000" }),
      ],
      UNKNOWN,
    );
    expect(rows.map((row) => row.label)).toEqual(["B", "C", "A"]);
  });

  it("treats malformed money strings as zero rather than NaN", () => {
    const [row] = buildResaleRows([apiRow({ delivered_revenue: "" })], UNKNOWN);
    expect(row.deliveredRevenue).toBe(0);
  });

  it("returns an empty array for empty input", () => {
    expect(buildResaleRows([], UNKNOWN)).toEqual([]);
  });
});

describe("sumResaleRows", () => {
  it("sums every bucket and recomputes the percent from the totals", () => {
    const rows = buildResaleRows(
      [apiRow({ staff_id: 1 }), apiRow({ staff_id: 2 })],
      UNKNOWN,
    );
    const total = sumResaleRows(rows, "Tổng");

    expect(total.label).toBe("Tổng");
    expect(total.totalRevenue).toBe(200000);
    expect(total.deliveredRevenue).toBe(120000);
    expect(total.returnRiskOrders).toBe(2);
    expect(total.deliveredPct).toBe(60);
  });

  it("handles an empty row list without dividing by zero", () => {
    const total = sumResaleRows([], "Tổng");
    expect(total.totalRevenue).toBe(0);
    expect(total.deliveredPct).toBeNull();
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận nó ĐỎ**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report/frontend"
npx vitest run src/features/sale-report/__tests__/resale-selectors.test.ts
```

Kỳ vọng: **FAIL** — không export `buildResaleKpis`, và `ResaleBucketsApi` chưa tồn tại.

- [ ] **Step 3: Sửa `queries.ts`**

Thay khối type resale hiện có (`ResaleStaffApiRow` + `SaleResaleReportPayload`) bằng:

```ts
/** Năm cặp rổ của tab Resale — tên khớp từng chữ với Báo cáo tổng. */
export type ResaleBucketsApi = {
  /** Mọi đơn đã lên hóa đơn KiotViet, trừ đơn huỷ. Mẫu số của mọi ô %. */
  total_revenue: string;
  total_orders: number;
  delivered_revenue: string;
  delivered_orders: number;
  /** Phần bù: chưa có kết quả giao (không phải giao xong / hoàn / rủi ro hoàn). */
  awaiting_revenue: string;
  awaiting_orders: number;
  returned_revenue: string;
  returned_orders: number;
  /** Đơn đã quay đầu một lần (mã 10) — nguy cơ thành hoàn cao hơn đơn đang giao. */
  return_risk_revenue: string;
  return_risk_orders: number;
};

export type ResaleStaffApiRow = ResaleBucketsApi & {
  staff_id: number | null;
  staff__username: string | null;
  staff__display_name: string | null;
};

export type SaleResaleReportPayload = {
  kpis: ResaleBucketsApi;
  by_staff: ResaleStaffApiRow[];
};
```

`useSaleResaleReport` giữ nguyên hoàn toàn (URL, cache, refetch) — chỉ kiểu trả về đổi theo.

- [ ] **Step 4: Viết lại `resale-selectors.ts`**

```ts
/**
 * Selector thuần cho tab "Resale".
 *
 * Backend trả `{kpis, by_staff}` với tiền dạng chuỗi decimal. Ở đây parse sang
 * số và tính % giao thành công. Bốn rổ (giao xong / chờ giao / hoàn / rủi ro
 * hoàn) cộng lại đúng bằng tổng — quan hệ đó do backend bảo đảm, FE chỉ cộng
 * ngang các hàng nên không phá được.
 */

import { type ResaleBucketsApi, type ResaleStaffApiRow } from "./queries";
import { saleKey, saleLabel } from "./sale-report-selectors";

export type ResaleBuckets = {
  totalRevenue: number;
  totalOrders: number;
  deliveredRevenue: number;
  deliveredOrders: number;
  awaitingRevenue: number;
  awaitingOrders: number;
  returnedRevenue: number;
  returnedOrders: number;
  returnRiskRevenue: number;
  returnRiskOrders: number;
  /** Doanh thu giao thành công / tổng doanh thu (%); `null` khi tổng = 0. */
  deliveredPct: number | null;
};

export type ResaleStaffRow = ResaleBuckets & {
  key: string;
  label: string;
};

/** Chuỗi rỗng / rác -> 0, không để lọt NaN vào phép cộng của dòng tổng. */
function toNumber(value: string | undefined): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function divide(numerator: number, denominator: number): number | null {
  return denominator > 0 ? numerator / denominator : null;
}

type Sums = Omit<ResaleBuckets, "deliveredPct">;

const EMPTY_SUMS: Sums = {
  totalRevenue: 0,
  totalOrders: 0,
  deliveredRevenue: 0,
  deliveredOrders: 0,
  awaitingRevenue: 0,
  awaitingOrders: 0,
  returnedRevenue: 0,
  returnedOrders: 0,
  returnRiskRevenue: 0,
  returnRiskOrders: 0,
};

function parse(api: ResaleBucketsApi | undefined): Sums {
  if (!api) return { ...EMPTY_SUMS };
  return {
    totalRevenue: toNumber(api.total_revenue),
    totalOrders: api.total_orders || 0,
    deliveredRevenue: toNumber(api.delivered_revenue),
    deliveredOrders: api.delivered_orders || 0,
    awaitingRevenue: toNumber(api.awaiting_revenue),
    awaitingOrders: api.awaiting_orders || 0,
    returnedRevenue: toNumber(api.returned_revenue),
    returnedOrders: api.returned_orders || 0,
    returnRiskRevenue: toNumber(api.return_risk_revenue),
    returnRiskOrders: api.return_risk_orders || 0,
  };
}

function withPct(sums: Sums): ResaleBuckets {
  const share = divide(sums.deliveredRevenue, sums.totalRevenue);
  return { ...sums, deliveredPct: share === null ? null : share * 100 };
}

/** Hàng KPI của tab. `undefined` (chưa tải xong) -> mọi ô 0, % là `null`. */
export function buildResaleKpis(kpis: ResaleBucketsApi | undefined): ResaleBuckets {
  return withPct(parse(kpis));
}

/** Một hàng cho mỗi nhân viên, sắp doanh thu GIAO THÀNH CÔNG giảm dần. */
export function buildResaleRows(
  rows: readonly ResaleStaffApiRow[],
  unknownLabel: string,
): ResaleStaffRow[] {
  return rows
    .map((row) => ({
      key: saleKey(row),
      label: saleLabel(row, unknownLabel),
      ...withPct(parse(row)),
    }))
    .sort((left, right) => right.deliveredRevenue - left.deliveredRevenue);
}

/**
 * Dòng tổng. `deliveredPct` được tính LẠI từ tổng chứ không cộng ngang —
 * cộng phần trăm của các hàng có mẫu số khác nhau là sai.
 */
export function sumResaleRows(
  rows: readonly ResaleStaffRow[],
  totalLabel: string,
): ResaleStaffRow {
  const sums = rows.reduce<Sums>(
    (acc, row) => ({
      totalRevenue: acc.totalRevenue + row.totalRevenue,
      totalOrders: acc.totalOrders + row.totalOrders,
      deliveredRevenue: acc.deliveredRevenue + row.deliveredRevenue,
      deliveredOrders: acc.deliveredOrders + row.deliveredOrders,
      awaitingRevenue: acc.awaitingRevenue + row.awaitingRevenue,
      awaitingOrders: acc.awaitingOrders + row.awaitingOrders,
      returnedRevenue: acc.returnedRevenue + row.returnedRevenue,
      returnedOrders: acc.returnedOrders + row.returnedOrders,
      returnRiskRevenue: acc.returnRiskRevenue + row.returnRiskRevenue,
      returnRiskOrders: acc.returnRiskOrders + row.returnRiskOrders,
    }),
    { ...EMPTY_SUMS },
  );
  return { key: "__total__", label: totalLabel, ...withPct(sums) };
}
```

- [ ] **Step 5: Chạy test + type-check**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report/frontend"
npx vitest run src/features/sale-report/__tests__/resale-selectors.test.ts
npx tsc --noEmit
```

Kỳ vọng: test **15 passed**. `tsc` sẽ báo lỗi ở `SaleResaleTab.tsx` / `ResaleRevenueChart.tsx` vì chúng còn dùng shape cũ — **đó là hệ quả biết trước, Task 4 sửa**. Mọi lỗi khác phải sạch.

- [ ] **Step 6: Commit**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report/frontend"
npx prettier --write src/features/sale-report/queries.ts src/features/sale-report/resale-selectors.ts src/features/sale-report/__tests__/resale-selectors.test.ts
cd .. && git add frontend/src/features/sale-report/ && git commit -m "feat(sale-report): resale bucket types and selectors"
```

---

## Task 4: Frontend — KPI tiles, chart, tab

**Files:**
- Create: `frontend/src/features/sale-report/components/ResaleKpiTiles.tsx`
- Modify: `frontend/src/features/sale-report/components/ResaleRevenueChart.tsx`
- Rewrite: `frontend/src/features/sale-report/components/SaleResaleTab.tsx`
- Test: `frontend/src/features/sale-report/__tests__/SaleResaleTab.test.tsx` (viết lại)

**Interfaces:**
- Consumes: `useSaleResaleReport`, `SaleResaleReportPayload`, `ResaleBucketsApi`, `ResaleStaffApiRow` (Task 3); `buildResaleKpis`, `buildResaleRows`, `sumResaleRows`, `ResaleBuckets`, `ResaleStaffRow` (Task 3); `formatCurrency` từ `@/lib/format`; `formatAdsCurrency` / `formatRatioPct` từ `@/features/ads/report-grouping`; `formatRevenueShort` từ `@/lib/metrics`; `ChartTooltip` từ `@/features/dashboard/components/DashboardPrimitives`; `CHART_COLORS` từ `@/features/dashboard/dashboard-constants`.
- Produces: `<SaleResaleTab dateFrom? dateTo? />` — tab key `"resale"` đã có sẵn trong `DashboardPage.tsx`, KHÔNG phải thêm lại.

- [ ] **Step 1: Viết test thất bại**

Thay TOÀN BỘ `frontend/src/features/sale-report/__tests__/SaleResaleTab.test.tsx`:

```tsx
import { cleanup, render, screen, within } from "@testing-library/react";
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

const PAYLOAD: SaleResaleReportPayload = {
  kpis: {
    total_revenue: "100000",
    total_orders: 10,
    delivered_revenue: "60000",
    delivered_orders: 6,
    awaiting_revenue: "20000",
    awaiting_orders: 2,
    returned_revenue: "15000",
    returned_orders: 1,
    return_risk_revenue: "5000",
    return_risk_orders: 1,
  },
  by_staff: [
    {
      staff_id: 1,
      staff__username: "sale1",
      staff__display_name: "Sale Một",
      total_revenue: "100000",
      total_orders: 10,
      delivered_revenue: "60000",
      delivered_orders: 6,
      awaiting_revenue: "20000",
      awaiting_orders: 2,
      returned_revenue: "15000",
      returned_orders: 1,
      return_risk_revenue: "5000",
      return_risk_orders: 1,
    },
  ],
};

describe("SaleResaleTab", () => {
  it("renders the five KPI tiles", () => {
    mockQuery({ data: PAYLOAD });
    renderTab();

    for (const label of [
      "Tổng doanh thu",
      "Giao thành công",
      "Chờ giao",
      "Hoàn",
      "Rủi ro hoàn",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("renders a staff row with its delivered revenue and percent", () => {
    mockQuery({ data: PAYLOAD });
    renderTab();

    const staffCell = screen.getByText("Sale Một");
    const row = staffCell.closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("60.0%")).toBeInTheDocument();
  });

  it("shows an empty state instead of a table when there is no data", () => {
    mockQuery({ data: { kpis: PAYLOAD.kpis, by_staff: [] } });
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
    mockQuery({ data: PAYLOAD });
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
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report/frontend"
npx vitest run src/features/sale-report/__tests__/SaleResaleTab.test.tsx
```

Kỳ vọng: **FAIL** — không tìm thấy nhãn KPI (component chưa render tiles).

- [ ] **Step 3: Viết `ResaleKpiTiles.tsx`**

```tsx
import { useI18n } from "@/i18n";
import { formatCurrency } from "@/lib/format";

import { type ResaleBuckets } from "../resale-selectors";

function money(value: number): string {
  return formatCurrency(value, { fallback: "0", onInvalid: "fallback", maximumFractionDigits: 0 });
}

function Tile({
  label,
  value,
  sub,
  tone = "default",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "default" | "danger";
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 text-center">
      <div
        className={`text-xl font-semibold tabular-nums ${
          tone === "danger" ? "text-rose-500" : "text-foreground"
        }`}
      >
        {value}
      </div>
      <div className="mt-1 text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      {sub ? <div className="mt-0.5 text-xs tabular-nums text-muted-foreground">{sub}</div> : null}
    </div>
  );
}

/**
 * Hàng KPI của tab Resale — bám khuôn `OverviewKpiTiles` của Báo cáo tổng.
 *
 * Quan hệ số học KHÔNG nhìn thấy được từ layout, phải nhớ: Giao thành công +
 * Chờ giao + Hoàn + Rủi ro hoàn = Tổng doanh thu, đúng cả tiền lẫn số đơn.
 * Hai ô cuối mang `tone="danger"` vì đó là tiền đang mất hoặc sắp mất.
 */
export function ResaleKpiTiles({ kpis }: { kpis: ResaleBuckets }) {
  const { t } = useI18n();
  const unit = t("saleReport.ordersUnit", "đơn");
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
      <Tile
        label={t("saleReport.kpi.totalRevenue", "Tổng doanh thu")}
        value={money(kpis.totalRevenue)}
        sub={`${kpis.totalOrders} ${unit}`}
      />
      <Tile
        label={t("saleReport.kpi.delivered", "Giao thành công")}
        value={money(kpis.deliveredRevenue)}
        sub={`${kpis.deliveredOrders} ${unit}`}
      />
      <Tile
        label={t("saleReport.kpi.awaiting", "Chờ giao")}
        value={money(kpis.awaitingRevenue)}
        sub={`${kpis.awaitingOrders} ${unit}`}
      />
      <Tile
        label={t("saleReport.kpi.returned", "Hoàn")}
        value={money(kpis.returnedRevenue)}
        sub={`${kpis.returnedOrders} ${unit}`}
        tone="danger"
      />
      <Tile
        label={t("saleReport.kpi.returnRisk", "Rủi ro hoàn")}
        value={money(kpis.returnRiskRevenue)}
        sub={`${kpis.returnRiskOrders} ${unit}`}
        tone="danger"
      />
    </div>
  );
}
```

- [ ] **Step 4: Sửa `ResaleRevenueChart.tsx`**

Chỉ đổi phần dựng `data` và nhãn — GIỮ NGUYÊN cách key theo `row.key` + `tickFormatter` + tooltip map ngược (đã sửa ở MR !1366 để hai sale trùng `display_name` không chồng lên một tick):

```tsx
  const data = rows
    .filter((row) => row.deliveredRevenue > 0)
    .map((row) => ({ key: row.key, name: row.label, value: row.deliveredRevenue }));
```

Đổi mọi tham chiếu `resaleRevenue` còn lại trong file sang `deliveredRevenue`. Prop `seriesName` giữ nguyên; nơi gọi sẽ truyền nhãn "Doanh thu giao thành công".

- [ ] **Step 5: Viết lại `SaleResaleTab.tsx`**

```tsx
import { useMemo } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { LogoSpinner } from "@/components/ui/logo-spinner";
import { formatAdsCurrency, formatRatioPct } from "@/features/ads/report-grouping";
import { useI18n } from "@/i18n";

import { useSaleResaleReport } from "../queries";
import { type ResaleStaffRow, buildResaleKpis, buildResaleRows, sumResaleRows } from "../resale-selectors";
import { ResaleKpiTiles } from "./ResaleKpiTiles";
import { ResaleRevenueChart } from "./ResaleRevenueChart";

type SaleResaleTabProps = {
  dateFrom?: string;
  dateTo?: string;
};

function formatInt(value: number): string {
  return value.toLocaleString("vi-VN");
}

/**
 * Tab "Resale" — đơn của KÊNH Resale, chia 4 rổ như Báo cáo tổng.
 *
 * Dùng bộ lọc ngày của trang. `Tabs` chỉ mount panel đang mở nên query chỉ chạy
 * khi người dùng vào tab này.
 */
export function SaleResaleTab({ dateFrom, dateTo }: SaleResaleTabProps) {
  const { t } = useI18n();
  const query = useSaleResaleReport({ dateFrom, dateTo });
  const unknownLabel = t("dashboard.unknownStaff", "Không xác định");
  const totalLabel = t("saleReport.totalRow", "Tổng");

  const failed = query.isError;
  const kpis = useMemo(
    () => buildResaleKpis(failed ? undefined : query.data?.kpis),
    [failed, query.data],
  );
  const rows = useMemo(
    () => buildResaleRows(failed ? [] : (query.data?.by_staff ?? []), unknownLabel),
    [failed, query.data, unknownLabel],
  );
  const totals = useMemo(() => sumResaleRows(rows, totalLabel), [rows, totalLabel]);

  const error = query.error
    ? query.error instanceof Error
      ? query.error.message
      : String(query.error)
    : null;

  const cells = (row: ResaleStaffRow) => (
    <>
      <td className="px-3 py-2 text-right tabular-nums">{formatInt(row.totalOrders)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{formatAdsCurrency(row.totalRevenue)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{formatInt(row.deliveredOrders)}</td>
      <td className="px-3 py-2 text-right tabular-nums">
        {formatAdsCurrency(row.deliveredRevenue)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{formatAdsCurrency(row.awaitingRevenue)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{formatAdsCurrency(row.returnedRevenue)}</td>
      <td className="px-3 py-2 text-right tabular-nums">
        {formatAdsCurrency(row.returnRiskRevenue)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{formatRatioPct(row.deliveredPct)}</td>
    </>
  );

  // Tiêu đề cột CỐ Ý khác chữ với nhãn ô KPI phía trên ("Tổng doanh thu",
  // "Giao thành công", "Chờ giao", "Hoàn", "Rủi ro hoàn"). Trùng chữ thì
  // `screen.getByText` trong test tìm ra 2 phần tử và ném lỗi, mà quan trọng hơn
  // là người đọc không phân biệt được ô tổng toàn kỳ với cột của từng nhân viên.
  const headers = [
    t("saleReport.totalOrdersHeader", "Tổng đơn"),
    t("saleReport.totalRevenueHeader", "Tổng DT"),
    t("saleReport.deliveredOrdersHeader", "Đơn giao TC"),
    t("saleReport.deliveredRevenueHeader", "DT giao TC"),
    t("saleReport.awaitingHeader", "DT chờ giao"),
    t("saleReport.returnedHeader", "DT hoàn"),
    t("saleReport.returnRiskHeader", "DT rủi ro hoàn"),
    t("saleReport.deliveredPctHeader", "% giao TC"),
  ];

  return (
    <div className="space-y-4">
      {error && (
        <Card className="rounded-xl border-destructive/50 bg-destructive/10">
          <CardContent className="py-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}
      {query.isLoading ? (
        <Card className="rounded-xl border-border bg-card">
          <CardContent className="pt-6">
            <div className="flex min-h-[160px] items-center justify-center">
              <LogoSpinner size={36} />
            </div>
          </CardContent>
        </Card>
      ) : (
        <>
          <ResaleKpiTiles kpis={kpis} />
          <Card className="rounded-xl border-border bg-card">
            <CardContent className="pt-6">
              {rows.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {t("saleReport.empty", "Chưa có dữ liệu.")}
                </p>
              ) : (
                <div className="space-y-6">
                  <ResaleRevenueChart
                    rows={rows}
                    seriesName={t("saleReport.deliveredRevenueHeader", "DT giao TC")}
                  />
                  <div className="overflow-x-auto">
                    <table data-slot="table" className="w-full min-w-[980px] text-sm">
                      <thead>
                        <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
                          <th className="px-3 py-2 text-left font-medium">
                            {t("saleReport.staffHeader", "Sale")}
                          </th>
                          {headers.map((header) => (
                            <th key={header} className="px-3 py-2 text-right font-medium">
                              {header}
                            </th>
                          ))}
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
                        "Đơn resale = đơn thuộc kênh Resale. Doanh thu tính mọi đơn đã lên hóa đơn KiotViet trừ đơn huỷ, chia 4 rổ rời nhau như Báo cáo tổng: giao thành công + chờ giao + hoàn + rủi ro hoàn = tổng. Đơn đang chờ giao khi giao xong sẽ chuyển sang rổ giao thành công của chính kỳ đã tạo đơn, nên số của kỳ gần đây còn tăng.",
                      )}
                    </p>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 6: Chạy test + type-check + lint**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report/frontend"
npx vitest run src/features/sale-report src/pages/__tests__/DashboardPage.tabs.test.tsx
npx tsc --noEmit
npx eslint src/features/sale-report src/pages/DashboardPage.tsx
```

Kỳ vọng: tất cả xanh, `tsc` sạch.

`DashboardPage.tabs.test.tsx` **không cần sửa** — đã kiểm chứng: mock của nó (từ MR !1366) trả `data: { by_staff: [] }`, và `buildResaleKpis(undefined)` trả về hình dạng toàn 0 nên component không nổ. Đó chính là lý do `buildResaleKpis` nhận `undefined` thay vì bắt buộc có `kpis`.

Vẫn nên cập nhật mock đó cho đúng thực tế (thêm `kpis` đủ 10 khoá, giá trị 0 / `"0"`) — một dòng, và giữ cho file test không mô tả sai payload của backend. Nếu làm, chạy lại file này để chắc vẫn xanh.

- [ ] **Step 7: Commit**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report/frontend"
npx prettier --write \
  src/features/sale-report/components/ResaleKpiTiles.tsx \
  src/features/sale-report/components/ResaleRevenueChart.tsx \
  src/features/sale-report/components/SaleResaleTab.tsx \
  src/features/sale-report/__tests__/SaleResaleTab.test.tsx
cd .. && git add frontend/src/ && git commit -m "feat(sale-report): resale tab with overview-style kpi tiles"
```

---

## Task 5: Chốt — suite đầy đủ, ghi nhận, push, MR

**Files:**
- Modify: `docs/superpowers/specs/2026-08-26-resale-channel-report-design.md` (bảng Sprint)

- [ ] **Step 1: Chạy TRỌN suite frontend**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report/frontend"
npx vitest run 2>&1 | tail -20
```

Baseline master đo 2026-08-25: **192 file / 1485 test XANH 100%**, không có nợ đỏ. File đỏ nào cũng là hồi quy của nhánh này — đừng đổ cho baseline mà không kiểm chứng lại trên worktree sạch.

- [ ] **Step 2: Chạy suite backend `apps/orders`**

Chạy detached rồi chờ (mất ~20 phút; shell nền của harness timeout 10 phút nhưng container vẫn chạy tiếp):

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report" && \
MSYS_NO_PATHCONV=1 docker run -d --name rs2-final --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_rs2_final \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=6 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend://app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders -q --create-db'
# rồi chờ: until [ "$(docker inspect -f '{{.State.Running}}' rs2-final)" = "false" ]; do sleep 20; done
docker logs rs2-final 2>&1 | tail -20
```

Baseline master đo 2026-08-25: **1595 passed + 69 subtests, 0 failed**, không có nợ đỏ.

- [ ] **Step 3: Chạy import-linter**

Phải đặt `HOME=/tmp`, nếu không `pip install --user` ghi `.local/` vào chính worktree đang mount rồi lọt vào `git status`:

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report" && \
MSYS_NO_PATHCONV=1 docker run --rm -e HOME=/tmp \
  -v "$(pwd -W)/backend://app" -w //app desi-backend-test:latest \
  sh -c 'pip install -q --user import-linter && /tmp/.local/bin/lint-imports --config .importlinter' 2>&1 | tail -20
```

Kỳ vọng: không có vi phạm mới.

- [ ] **Step 4: Lint toàn bộ + `git status` sạch**

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report"
docker run --rm -v "$(pwd -W)/backend://app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
cd frontend && npx eslint src/features/sale-report src/pages/DashboardPage.tsx && npx tsc --noEmit
cd .. && git status --porcelain
```

`git status` phải RỖNG. Thấy `backend/.local/` nghĩa là thiếu `HOME=/tmp` ở Step 3 — xoá đi.

- [ ] **Step 5: Cập nhật spec**

Đổi cả 5 dòng bảng Sprint từ `⬜ chưa làm` sang `✅ xong`, và thêm ngay dưới bảng:

```markdown
### Ghi nhận sau khi thực hiện (2026-08-26)

**Đã làm:** S1–S5. Helper `resale_channel_ids()`; viết lại `summary_resale_by_staff`
trả `{kpis, by_staff}` với 5 rổ theo kênh Resale; endpoint đổi payload; FE có KPI tiles
+ chart doanh thu giao thành công + bảng 9 cột. Test: <N> test backend, <M> test frontend.

**Chưa làm (cố ý, xem "Ngoài phạm vi"):** env `RESALE_CHANNEL_IDS` riêng, xuất Excel,
biểu đồ diễn biến theo ngày.

**Còn nợ sau deploy:** 4 mục ở phần "Rủi ro & việc phải làm sau deploy" — quan trọng
nhất là BÁO PHÒNG SALE TRƯỚC vì số tụt từ 148 đơn/258,65tr xuống 82 đơn/142,89tr và
bảng còn 5 nhân viên thay vì 7.
```

Thay `<N>` / `<M>` bằng số đếm được thật.

- [ ] **Step 6: Commit, push, tạo MR**

Không có `glab`/token — tạo MR bằng push options:

```bash
cd "c:/Users/MYPC/Desktop/workspace/desi-order/.claude/worktrees/resale-channel-report"
git add docs/superpowers/specs/2026-08-26-resale-channel-report-design.md
git commit -m "docs: mark resale channel report sprints done"
git push -u origin feat/resale-channel-report \
  -o merge_request.create \
  -o merge_request.title="feat(sale-report): tab Resale lay theo kenh Resale, 4 ro theo Bao cao tong" \
  -o merge_request.remove_source_branch
```

Push báo "Everything up-to-date" mà MR chưa tạo → xoá remote branch rồi push lại kèm push options.

- [ ] **Step 7: Gửi link MR**

Kèm cảnh báo deploy: số tụt hơn một nửa và bảng mất 2 nhân viên — **phải báo phòng Sale trước**, nếu không sẽ bị coi là sự cố mất số.

⚠️ Sau khi gửi link MR, ĐÓNG BĂNG nhánh này. Thay đổi mới đi MR mới trên nhánh mới.

---

## Self-Review

**Spec coverage:**

| Mục spec | Task phủ |
|---|---|
| Helper `resale_channel_ids()` monkeypatch-safe | T1 Step 1 |
| Bỏ suy luận lịch sử mua, lọc theo kênh | T1 Step 4 |
| 4 rổ + tổng, dùng lại hằng số Overview | T1 Step 4 + test `test_four_buckets_always_sum_to_total` |
| Rổ "chờ giao" bằng phép trừ | T1 Step 4 + test `test_unknown_status_falls_into_awaiting_...` |
| Ranh giới doanh thu = đã lên hoá đơn, trừ huỷ | T1 test `test_cancelled_orders...`, `test_lead_order...` |
| Loại clone khôi phục còn sống | T1 test `test_order_with_live_restore_clone...` |
| Không lọc phòng ban, đơn không có sale vẫn tính | T1 test `test_order_without_staff...` |
| Payload `{kpis, by_staff}` | T1 Interfaces + T2 |
| Endpoint giữ URL/quyền | T2 Step 3 + test quyền |
| `schema.yaml` sửa tay | T2 Step 5 |
| FE types + selectors | T3 |
| KPI tiles theo khuôn Overview | T4 Step 3 |
| Chart đổi sang doanh thu giao thành công | T4 Step 4 |
| Bỏ cột "Khách quay lại" + footnote đếm trùng | T4 Step 5 (bảng mới không có cột đó) |
| Không test chart bằng jsdom | T4 Step 1 |
| Cập nhật Sprint + ghi nhận | T5 Step 5 |
| 4 việc sau deploy | T5 Step 5 + Step 7 |

Không mục nào của spec thiếu task.

**Placeholder scan:** không có "TBD"/"TODO"/"tương tự Task N". `<N>`/`<M>` ở T5 Step 5 là chỗ điền số đếm được, đã nói rõ cách lấy. `<unique>` / `<PATHS>` trong lệnh mẫu ở Global Constraints là tham số template; mọi Step dùng đều ghi giá trị cụ thể.

**Type consistency:**
- 10 khoá rổ khai ở T1 Interfaces → dùng nguyên vẹn ở T1 Step 4, T2 Step 1 (`BUCKET_FIELDS`), T2 Step 3 (`_RESALE_BUCKET_FIELDS`), T2 Step 5 (`schema.yaml`), T3 (`ResaleBucketsApi`). Khớp.
- `ResaleBuckets` (11 trường: 10 + `deliveredPct`) khai T3 → dùng T4 (`ResaleKpiTiles`, `cells`). Khớp.
- `buildResaleKpis` / `buildResaleRows` / `sumResaleRows` cùng chữ ký ở T3 Interfaces, T3 Step 4, T4 Step 5. Khớp.
- `ResaleRevenueChart` prop `rows: ResaleStaffRow[]` + `seriesName` giữ nguyên từ MR !1366; T4 Step 4 chỉ đổi trường đọc bên trong. Khớp.
- `summary_resale_by_staff` mất tham số `sale_channel_ids` — chỉ có một call site (view), sửa ở T2 Step 3. Khớp.
