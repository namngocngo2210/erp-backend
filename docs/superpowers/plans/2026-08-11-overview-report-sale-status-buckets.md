# Báo cáo tổng — doanh thu theo trạng thái sale: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đổi gốc doanh thu của Báo cáo tổng từ "đã có hóa đơn KiotViet" sang "trạng thái sale đã chốt", chia thành 4 rổ kết quả cộng lại đúng bằng tổng.

**Architecture:** Chỉ đổi các `Q` lọc trong `OrderRepository.summary_overview` — số lượng query giữ nguyên (1 aggregate + 1 group-by kênh + 1 group-by địa lý + 2 query spend). FE đổi view model và hàng KPI theo response mới. Không có migration.

**Tech Stack:** Django 5 + DRF + drf-spectacular (backend), React 19 + TypeScript + vitest + shadcn/ui (frontend).

**Spec:** `docs/superpowers/specs/2026-08-11-overview-report-sale-status-buckets-design.md`

## Global Constraints

- Worktree: `c:\Users\MYPC\Desktop\workspace\desi-order-wt-overview-buckets`, nhánh `feat/overview-report-sale-status-buckets`.
- Commit message tiếng Anh, ngắn, KHÔNG kèm dòng `Co-Authored-By`.
- Backend lint bắt buộc: `ruff check .` phải sạch theo `backend/pyproject.toml`.
- Không tạo migration — thay đổi thuần truy vấn.
- Mọi số tiền trong response là `str(Decimal)`, số đơn là `int` — giữ đúng khuôn cũ.
- Tên rổ trong code: `total` / `delivered` / `awaiting` / `returned` / `return_risk`.
- Nhãn tiếng Việt cố định: `Tổng doanh thu`, `Doanh thu giao thành công`, `Doanh thu chờ giao`, `Hoàn`, `Rủi ro hoàn`, `Chi phí FB + Google Ads`.
- Bất biến phải giữ ở mọi task: `delivered + awaiting + returned + return_risk == total`, cả tiền lẫn số đơn; và tổng mọi khối donut/địa lý = ô KPI tương ứng.

**Lệnh chạy test backend** (mỗi lần chạy đổi `POSTGRES_DB` để không giẫm nhau):

```bash
cd /c/Users/MYPC/Desktop/workspace/desi-order-wt-overview-buckets && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=ovbuckets1 \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest apps/orders/tests/test_overview_report.py -q --create-db'
```

Nếu container hạ tầng đã mất (Docker Desktop restart): dựng lại trước
```bash
docker network create desi-test-net 2>/dev/null; \
docker run -d --name desi-test-pg --network desi-test-net -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -e POSTGRES_DB=desi_order public.ecr.aws/docker/library/postgres:16-alpine; \
docker run -d --name desi-test-redis --network desi-test-net public.ecr.aws/docker/library/redis:7-alpine
```

**Lệnh chạy test frontend** — worktree anh em nên dùng junction, chạy MỘT LẦN trước Task 4:

```powershell
New-Item -ItemType Junction -Path "c:\Users\MYPC\Desktop\workspace\desi-order-wt-overview-buckets\frontend\node_modules" -Target "c:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```

Rồi trong `frontend/`: `npx vitest run <file>`, `npx tsc --noEmit`, `npm run lint:strict`.

---

## File Structure

**Backend**
- `backend/apps/orders/_repositories_reports.py` — hằng số rổ mới (~dòng 112-162) + viết lại phần lọc của `summary_overview` (dòng 1505-1770). Trái tim của thay đổi.
- `backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py:233-308` — đổi field của `inline_serializer` `OverviewReportSummaryResponse` cho khớp response.
- `backend/apps/orders/tests/test_overview_report.py` — cập nhật test cũ + thêm test bất biến/phạm vi.
- `backend/schema.yaml` — regen OpenAPI.

**Frontend**
- `frontend/src/api/schema.d.ts` — regen từ `schema.yaml` (`npm run gen:api`).
- `frontend/src/features/overview-report/overview-selectors.ts` — `OverviewKpis` + `computeOverviewKpis` theo response mới.
- `frontend/src/features/overview-report/components/OverviewKpiTiles.tsx` — 6 ô.
- `frontend/src/features/overview-report/pages/OverviewReportPage.tsx` — mặc định Tháng này, đổi khối donut thứ 3, sửa chữ.
- `frontend/src/features/overview-report/__tests__/overview-selectors.test.ts` — payload mẫu + assert mới.

**Docs**
- `docs/overview-report.md` — viết lại mục "Định nghĩa số liệu", layout, response, sprint.

---

### Task 1: Hằng số 4 rổ trạng thái

**Files:**
- Modify: `backend/apps/orders/_repositories_reports.py:112-162`
- Test: `backend/apps/orders/tests/test_overview_report.py`

**Interfaces:**
- Produces: `RETURN_RISK_ORDER_STATUSES: tuple[int, ...]`, `AWAITING_DELIVERY_ORDER_STATUSES: tuple[int, ...]`, `NO_SALES_STATUS_Q: Q`, `OVERVIEW_CLOSED_SCOPE_Q: Q` — Task 2 và 3 import trực tiếp từ module này.
- Consumes: `DELIVERED_SUCCESS_ORDER_STATUS`, `RETURN_ORDER_STATUSES`, `CLOSED_SALES_STATUS_CODES`, `LEAD_ORDER_Q` (đã có sẵn trong file).

- [ ] **Step 1: Viết test bất biến phân hoạch (chưa cần DB)**

Thêm vào cuối `backend/apps/orders/tests/test_overview_report.py`:

```python
class OverviewStatusBucketConstantsTests(SimpleTestCase):
    """4 rổ của Báo cáo tổng phải phủ kín mọi mã trạng thái, không chồng nhau.

    Thêm một mã trạng thái mới vào OrderStatus mà quên xếp rổ sẽ làm test này đỏ
    ngay, thay vì âm thầm rơi khỏi doanh thu trên prod.
    """

    def test_four_buckets_partition_every_order_status(self):
        from apps.orders.models import OrderStatus
        from apps.orders._repositories_reports import (
            AWAITING_DELIVERY_ORDER_STATUSES,
            DELIVERED_SUCCESS_ORDER_STATUS,
            RETURN_ORDER_STATUSES,
            RETURN_RISK_ORDER_STATUSES,
        )

        buckets = [
            {DELIVERED_SUCCESS_ORDER_STATUS},
            set(AWAITING_DELIVERY_ORDER_STATUSES),
            set(RETURN_ORDER_STATUSES),
            set(RETURN_RISK_ORDER_STATUSES),
        ]
        union = set().union(*buckets)
        # Đơn hủy (6) bị loại khỏi tổng nên KHÔNG thuộc rổ nào.
        self.assertEqual(union, {s.value for s in OrderStatus} - {OrderStatus.CANCELLED.value})
        self.assertEqual(sum(len(b) for b in buckets), len(union), "hai rổ chồng nhau")

    def test_awaiting_bucket_excludes_redelivery(self):
        from apps.orders._repositories_reports import (
            AWAITING_DELIVERY_ORDER_STATUSES,
            RETURN_RISK_ORDER_STATUSES,
        )

        # "Chờ giao lại" (10) là rủi ro hoàn, KHÔNG phải chờ giao — khác với
        # IN_DELIVERY_ORDER_STATUSES của nhóm 5a vốn gộp chung.
        self.assertEqual(RETURN_RISK_ORDER_STATUSES, (10,))
        self.assertNotIn(10, AWAITING_DELIVERY_ORDER_STATUSES)
        self.assertEqual(AWAITING_DELIVERY_ORDER_STATUSES, (0, 1, 2, 7, 8, 9, 11))
```

Sửa dòng import đầu file, thêm `SimpleTestCase`:

```python
from django.test import SimpleTestCase, TestCase
```

- [ ] **Step 2: Chạy test — phải đỏ**

Chạy lệnh test backend ở Global Constraints với `-k OverviewStatusBucketConstants`.
Kỳ vọng: FAIL với `ImportError: cannot import name 'AWAITING_DELIVERY_ORDER_STATUSES'`.

- [ ] **Step 3: Thêm hằng số**

Trong `backend/apps/orders/_repositories_reports.py`, thêm `OrderStatus` vào khối import từ `.models` (dòng 42-49):

```python
from .models import (
    INVOICE_CODE_SOURCE_PREFIXES,
    MARKETPLACE_ORDER_SOURCES,
    Order,
    OrderItem,
    OrderJourneyEvent,
    OrderSource,
    OrderStatus,
)
```

Thêm ngay SAU khối `RETURN_ORDER_STATUSES` (dòng 159-162):

```python
# BÁO CÁO TỔNG — 4 rổ kết quả của "tiền đã chốt" (user chốt 2026-08-11).
# "Chờ giao lại" (10) tách riêng thành RỦI RO HOÀN: đơn đã quay đầu một lần,
# nguy cơ thành hoàn cao hơn hẳn đơn đang giao bình thường. CỐ Ý không tái dùng
# IN_DELIVERY_ORDER_STATUSES (nhóm 5a) vì bộ đó gộp mã 10 vào "đang giao".
RETURN_RISK_ORDER_STATUSES = (10,)
# Rổ "chờ giao" định nghĩa bằng PHÉP TRỪ trên toàn bộ OrderStatus, không liệt kê
# tay: thêm trạng thái mới vào enum thì nó tự vào rổ này thay vì rơi khỏi doanh
# thu. Đơn hủy (6) đã bị loại khỏi tổng nên không thuộc rổ nào.
AWAITING_DELIVERY_ORDER_STATUSES = tuple(
    sorted(
        {status.value for status in OrderStatus}
        - {DELIVERED_SUCCESS_ORDER_STATUS, OrderStatus.CANCELLED.value}
        - set(RETURN_ORDER_STATUSES)
        - set(RETURN_RISK_ORDER_STATUSES)
    )
)

# Đơn CHƯA gắn trạng thái sale — 3 dạng dữ liệu thật đều phải khớp: không có bản
# đánh giá, có nhưng cột NULL, có nhưng chuỗi rỗng.
NO_SALES_STATUS_Q = (
    Q(assessment__isnull=True)
    | Q(assessment__sales_status_code__isnull=True)
    | Q(assessment__sales_status_code="")
)

# Phạm vi đơn của Báo cáo tổng: tiền đội sale đã chốt trong kỳ, CỘNG đơn đã lên
# hóa đơn KiotViet mà không đi qua luồng CRM (Shopee/TikTok, hóa đơn tạo thẳng
# trên KiotViet) — bỏ vế hai thì khối donut "Nội sàn" thành rỗng.
#
# Đơn đã có hóa đơn nhưng mang trạng thái sale NGOÀI 4 mã chốt (vd "Khách bom
# hàng") CỐ Ý bị loại (user chốt 2026-08-11). Nếu ô Hoàn trên prod thấp bất
# thường trong khi tab Hoàn vẫn nhiều đơn thì đó là dấu hiệu sale đang đổi trạng
# thái sau khi đơn hoàn — khi đó nới vế hai thành `~LEAD_ORDER_Q` là xong.
OVERVIEW_CLOSED_SCOPE_Q = Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES) | (
    ~LEAD_ORDER_Q & NO_SALES_STATUS_Q
)
```

- [ ] **Step 4: Chạy lại test — phải xanh**

Cùng lệnh Step 2. Kỳ vọng: 2 passed.

- [ ] **Step 5: Lint + commit**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/apps/orders/_repositories_reports.py backend/apps/orders/tests/test_overview_report.py
git commit -m "feat(overview): add sale-status revenue scope and delivery result buckets"
```

---

### Task 2: `summary_overview` — phạm vi mới + 4 rổ ở mức tổng

**Files:**
- Modify: `backend/apps/orders/_repositories_reports.py:1505-1770` (thân `summary_overview`)
- Modify: `backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py:233-308`
- Test: `backend/apps/orders/tests/test_overview_report.py`

**Interfaces:**
- Consumes: `OVERVIEW_CLOSED_SCOPE_Q`, `AWAITING_DELIVERY_ORDER_STATUSES`, `RETURN_RISK_ORDER_STATUSES` (Task 1).
- Produces: response keys `total_revenue`, `total_orders`, `delivered_revenue`, `delivered_orders`, `awaiting_revenue`, `awaiting_orders`, `returned_revenue`, `returned_orders`, `return_risk_revenue`, `return_risk_orders`, `ads_cost` — Task 3 giữ nguyên các key này và thêm mảng breakdown; Task 4 đọc chúng ở FE.

- [ ] **Step 1: Viết test cho phạm vi + bất biến cộng dồn**

Trong `OverviewReportTests`, thêm helper tạo trạng thái sale ngay dưới `_order` (dòng 39-50):

```python
    def _closed(self, total, *, status=0, invoice_status=3, sales_status="closed", **kwargs):
        """Đơn kèm bản đánh giá CRM — `sales_status=None` nghĩa là KHÔNG tạo đánh giá."""
        order = self._order(total, status=status, invoice_status=invoice_status, **kwargs)
        if sales_status is not None:
            OrderAssessment.objects.create(order=order, sales_status_code=sales_status)
        return order
```

Thêm `OrderAssessment` vào import ở dòng 25:

```python
from apps.orders.models import Order, OrderAssessment, OrderItem, Product
```

Rồi thêm 3 test mới vào cuối `OverviewReportTests`:

```python
    def test_four_buckets_sum_to_total_revenue(self):
        self._closed("100000", status=3)   # giao thành công
        self._closed("200000", status=0)   # chờ giao (Mới)
        self._closed("300000", status=2)   # chờ giao (Đang giao)
        self._closed("50000", status=4)    # hoàn
        self._closed("30000", status=13)   # hoàn (chờ xác nhận hoàn)
        self._closed("70000", status=10)   # rủi ro hoàn (chờ giao lại)

        data = self._payload()
        self.assertEqual(data["total_revenue"], "750000.00")
        self.assertEqual(data["total_orders"], 6)
        self.assertEqual(data["delivered_revenue"], "100000.00")
        self.assertEqual(data["awaiting_revenue"], "500000.00")
        self.assertEqual(data["awaiting_orders"], 2)
        self.assertEqual(data["returned_revenue"], "80000.00")
        self.assertEqual(data["return_risk_revenue"], "70000.00")
        self.assertEqual(data["return_risk_orders"], 1)
        # Bất biến: 4 rổ cộng lại = tổng, cả tiền lẫn số đơn.
        parts = ("delivered", "awaiting", "returned", "return_risk")
        self.assertEqual(
            sum(Decimal(data[f"{p}_revenue"]) for p in parts), Decimal(data["total_revenue"])
        )
        self.assertEqual(sum(data[f"{p}_orders"] for p in parts), data["total_orders"])

    def test_scope_takes_closed_sale_status_or_invoiced_without_status(self):
        # 4 mã chốt đều vào tổng, kể cả lead chưa lên hóa đơn.
        for code in ("closed", "closed_express", "closed_scheduled_date", "created_order"):
            self._closed("100000", status=3, sales_status=code)
        self._closed("400000", status=3, sales_status="closed", invoice_status=None)
        # Đã có hóa đơn nhưng chưa gắn trạng thái sale — 3 dạng dữ liệu thật.
        self._closed("10000", status=3, sales_status=None)
        self._closed("20000", status=3, sales_status="")
        order = self._order("30000", status=3, invoice_status=3)
        OrderAssessment.objects.create(order=order, sales_status_code=None)

        data = self._payload()
        self.assertEqual(data["total_revenue"], "860000.00")
        self.assertEqual(data["total_orders"], 8)

    def test_scope_drops_non_closed_sale_status_and_cancelled(self):
        self._closed("100000", status=3)                                   # đếm
        self._closed("900000", status=4, sales_status="customer_bombed")   # loại: ngoài 4 mã chốt
        self._closed("800000", status=0, sales_status="not_buying")        # loại
        self._closed("700000", status=0, sales_status="closed", invoice_status=2)  # loại: hóa đơn hủy
        self._closed("600000", status=6)                                   # loại: đơn đã hủy
        self._order("500000", status=0)                                    # loại: lead chưa chốt

        data = self._payload()
        self.assertEqual(data["total_revenue"], "100000.00")
        self.assertEqual(data["total_orders"], 1)
        self.assertEqual(data["returned_revenue"], "0")
        self.assertNotIn("cancelled_revenue", data)
        self.assertNotIn("pending_revenue", data)
        self.assertNotIn("gross_revenue", data)
```

- [ ] **Step 2: Chạy test — phải đỏ**

Lệnh test backend với `-k "four_buckets_sum or scope_takes or scope_drops"`.
Kỳ vọng: FAIL với `KeyError: 'total_revenue'`.

- [ ] **Step 3: Đổi phần lọc trong `summary_overview`**

Thay khối dòng 1588-1606 (`pending_q` … `agg = base.aggregate(...)`) bằng:

```python
        total_q = OVERVIEW_CLOSED_SCOPE_Q & ~CANCELLED_ORDER_Q
        delivered_q = total_q & Q(status=DELIVERED_SUCCESS_ORDER_STATUS)
        awaiting_q = total_q & Q(status__in=AWAITING_DELIVERY_ORDER_STATUSES)
        returned_q = total_q & Q(status__in=RETURN_ORDER_STATUSES)
        return_risk_q = total_q & Q(status__in=RETURN_RISK_ORDER_STATUSES)
        agg = base.aggregate(
            total_revenue=Sum("amount", filter=total_q),
            total_orders=Count("id", filter=total_q),
            delivered_revenue=Sum("amount", filter=delivered_q),
            delivered_orders=Count("id", filter=delivered_q),
            awaiting_revenue=Sum("amount", filter=awaiting_q),
            awaiting_orders=Count("id", filter=awaiting_q),
            returned_revenue=Sum("amount", filter=returned_q),
            returned_orders=Count("id", filter=returned_q),
            return_risk_revenue=Sum("amount", filter=return_risk_q),
            return_risk_orders=Count("id", filter=return_risk_q),
        )
```

Rồi thay 11 dòng đầu của `return {...}` (dòng 1747-1758) bằng:

```python
            "total_revenue": str(agg["total_revenue"] or Decimal("0")),
            "total_orders": agg["total_orders"] or 0,
            "delivered_revenue": str(agg["delivered_revenue"] or Decimal("0")),
            "delivered_orders": agg["delivered_orders"] or 0,
            "awaiting_revenue": str(agg["awaiting_revenue"] or Decimal("0")),
            "awaiting_orders": agg["awaiting_orders"] or 0,
            "returned_revenue": str(agg["returned_revenue"] or Decimal("0")),
            "returned_orders": agg["returned_orders"] or 0,
            "return_risk_revenue": str(agg["return_risk_revenue"] or Decimal("0")),
            "return_risk_orders": agg["return_risk_orders"] or 0,
            "ads_cost": str(ads_cost),
```

Thay docstring của method (dòng 1513-1543) — bỏ đoạn nói về lead/gross/net, thay bằng:

```python
        """Báo cáo tổng toàn công ty theo khoảng ngày tạo đơn.

        Tập đơn cơ sở: chưa xóa, loại chi nhánh nội bộ + kho Đại Mỗ, loại clone
        khôi phục còn sống, MỌI kênh/nguồn — khác các summary_ads_* vốn giới hạn
        scope kênh Ads.

        DOANH THU TÍNH THEO TRẠNG THÁI SALE (user chốt 2026-08-11):
        ``OVERVIEW_CLOSED_SCOPE_Q`` trừ đơn hủy (``CANCELLED_ORDER_Q``). Tổng đó
        chia đúng 4 rổ RỜI NHAU theo trạng thái giao hàng — giao thành công (3),
        chờ giao (``AWAITING_DELIVERY_ORDER_STATUSES``), hoàn
        (``RETURN_ORDER_STATUSES``), rủi ro hoàn (``RETURN_RISK_ORDER_STATUSES``)
        — nên 4 rổ cộng lại LUÔN bằng tổng, cả tiền lẫn số đơn. Mẫu số của mọi ô
        % trên FE là tổng này.

        Chi phí Ads: tổng spend FB (×1.1) + Google (×1.08 — cùng hệ số hiển thị
        với Báo cáo Ads) của TOÀN BỘ ad account theo ``spend.date``, không giới
        hạn theo kênh gán NV. Đây là tiền chi ra, KHÔNG tham gia phép cộng 4 rổ.

        ``sale_channel``: lọc nhóm kênh bán — nội sàn (Shopee/TikTok) hoặc
        ngoại sàn (phần còn lại).

        ``product_ids``: lọc theo sản phẩm. Khi có, MỌI số tiền trên trang là
        tiền CỦA RIÊNG các sản phẩm đó (tổng ``OrderItem.line_total`` khớp sản
        phẩm), không phải giá trị cả đơn — đơn mua kèm SP khác chỉ đóng góp phần
        của SP được lọc, nên cộng số liệu của mọi SP lại đúng bằng tổng. Số ĐƠN
        vẫn đếm theo đơn (đơn có chứa SP). Chi phí Ads KHÔNG tách được theo sản
        phẩm nên giữ nguyên tổng chi tiêu — FE phải nói rõ điều này.
        """
```

- [ ] **Step 4: Đổi serializer OpenAPI**

Trong `dashboard_report_views.py`, thay dòng 238-251 (từ `"gross_revenue"` đến `"ads_cost"`) bằng:

```python
                # Tổng = 4 rổ dưới cộng lại (giao thành công + chờ giao + hoàn +
                # rủi ro hoàn); ads_cost là tiền chi ra, ngoài phép cộng.
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
                "ads_cost": drf_serializers.CharField(),
```

- [ ] **Step 5: Chạy test — 3 test mới xanh**

Lệnh test backend với `-k "four_buckets_sum or scope_takes or scope_drops"`. Kỳ vọng: 3 passed.
Các test CŨ trong file vẫn đỏ ở bước này (chúng assert `net_revenue`/`cancelled_*`) — Task 3 dọn.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/_repositories_reports.py backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py backend/apps/orders/tests/test_overview_report.py
git commit -m "feat(overview): compute revenue buckets from sale status"
```

---

### Task 3: Khối phân bổ theo kênh bán và địa lý dùng cơ sở mới

**Files:**
- Modify: `backend/apps/orders/_repositories_reports.py` (khối `sale_channel_rows`, `geo_rows`, cuối `return`)
- Modify: `backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py:274-282`
- Test: `backend/apps/orders/tests/test_overview_report.py` (cập nhật toàn bộ test cũ)

**Interfaces:**
- Consumes: `total_q`, `delivered_q`, `returned_q`, `return_risk_q` (biến cục bộ trong `summary_overview`, Task 2).
- Produces: response keys `revenue_by_sale_channel`, `returned_by_sale_channel`, `return_risk_by_sale_channel`, `revenue_by_region`, `revenue_by_province` — Task 4/5 đọc ở FE.

- [ ] **Step 1: Viết test khối mới**

Thêm vào `OverviewReportTests`:

```python
    def test_blocks_follow_total_revenue_basis(self):
        self._closed("100000", status=3, source="landing")
        self._closed("200000", status=0, source="shopee")
        self._closed("50000", status=4, source="landing")
        self._closed("70000", status=10, source="tiktok")

        data = self._payload()
        # Khối doanh thu = TỔNG (gồm cả hoàn + rủi ro hoàn), không còn là "net".
        self.assertEqual(
            data["revenue_by_sale_channel"],
            [
                {
                    "sale_channel": "marketplace",
                    "revenue": "270000.00",
                    "orders": 2,
                    "delivered_revenue": "0",
                    "delivered_orders": 0,
                },
                {
                    "sale_channel": "off_marketplace",
                    "revenue": "150000.00",
                    "orders": 2,
                    "delivered_revenue": "100000.00",
                    "delivered_orders": 1,
                },
            ],
        )
        self.assertEqual(
            data["return_risk_by_sale_channel"],
            [{"sale_channel": "marketplace", "revenue": "70000.00", "orders": 1}],
        )
        self.assertNotIn("cancelled_by_sale_channel", data)
        # Bất biến: tổng mọi khối = ô KPI tương ứng.
        self.assertEqual(
            sum(Decimal(r["revenue"]) for r in data["revenue_by_sale_channel"]),
            Decimal(data["total_revenue"]),
        )
        self.assertEqual(
            sum(Decimal(r["revenue"]) for r in data["revenue_by_region"]),
            Decimal(data["total_revenue"]),
        )
        self.assertEqual(
            sum(Decimal(r["revenue"]) for r in data["revenue_by_province"]),
            Decimal(data["total_revenue"]),
        )
```

- [ ] **Step 2: Chạy test — phải đỏ**

Lệnh test backend với `-k blocks_follow_total`. Kỳ vọng: FAIL vì `revenue_by_sale_channel` còn dùng `net_q`.

- [ ] **Step 3: Đổi group-by kênh bán**

Trong `summary_overview`, thay 8 dòng `annotate` của `sale_channel_rows` (dòng 1622-1631) bằng:

```python
            .annotate(
                revenue=Sum("amount", filter=total_q),
                orders=Count("id", filter=total_q),
                delivered_revenue=Sum("amount", filter=delivered_q),
                delivered_orders=Count("id", filter=delivered_q),
                returned_revenue=Sum("amount", filter=returned_q),
                returned_orders=Count("id", filter=returned_q),
                return_risk_revenue=Sum("amount", filter=return_risk_q),
                return_risk_orders=Count("id", filter=return_risk_q),
            )
```

- [ ] **Step 4: Đổi khối địa lý sang cơ sở tổng**

Thay `base.filter(net_q)` (dòng 1652) bằng `base.filter(total_q)`, và sửa comment ngay trên nó: `Chỉ bucket net (doanh thu sau hoàn hủy).` → `Cơ sở là TỔNG doanh thu (gồm cả hoàn/rủi ro hoàn) — khớp ô KPI đầu.`

- [ ] **Step 5: Đổi 3 mảng breakdown ở `return`**

```python
            "revenue_by_sale_channel": _bucket_rows("revenue", "orders", with_delivered=True),
            "returned_by_sale_channel": _bucket_rows(
                "returned_revenue", "returned_orders", with_delivered=False
            ),
            "return_risk_by_sale_channel": _bucket_rows(
                "return_risk_revenue", "return_risk_orders", with_delivered=False
            ),
```

Và trong `dashboard_report_views.py` đổi key `"cancelled_by_sale_channel"` (dòng 274) thành `"return_risk_by_sale_channel"` (giữ nguyên `inline_serializer("OverviewSaleChannelAmount", ...)` bên trong).

- [ ] **Step 6: Cập nhật toàn bộ test cũ trong file**

Chạy full file test và sửa từng test đỏ theo quy tắc:
- `net_revenue`/`net_orders` → `total_revenue`/`total_orders`, và giá trị kỳ vọng phải CỘNG thêm phần hoàn + rủi ro hoàn.
- Bỏ mọi assert `gross_revenue`, `cancelled_*`, `pending_*`.
- Đơn trong fixture phải có trạng thái sale chốt (dùng `_closed`) hoặc chủ ý không có (dùng `_order` + `invoice_status`), nếu không sẽ rơi khỏi tổng.
- `test_pending_leads_never_reach_revenue_buckets` đổi tên thành `test_lead_without_closed_sale_status_stays_out` và assert lead chưa chốt không vào tổng, lead ĐÃ chốt thì vào.
- `test_returned_and_cancelled_by_sale_channel` đổi thành `test_returned_and_return_risk_by_sale_channel`.
- `test_buckets_partition_and_sources` viết lại theo 4 rổ mới.

- [ ] **Step 7: Chạy TOÀN BỘ file test — phải xanh**

Lệnh test backend (không `-k`), `POSTGRES_DB=ovbuckets2`. Kỳ vọng: tất cả passed.

- [ ] **Step 8: Chạy các suite liên quan để chắc không vỡ chỗ khác**

```bash
... python -m pytest apps/orders/tests/test_sale_reports.py apps/orders/tests/test_ads_reports.py -q --create-db
```
Kỳ vọng: passed (các báo cáo này dùng chung hằng số nhưng không dùng `summary_overview`).

- [ ] **Step 9: Regen OpenAPI + lint + commit**

```bash
... sh -c 'python manage.py spectacular --file schema.yaml'
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
git add backend/
git commit -m "feat(overview): switch breakdown blocks to total revenue basis"
```

Lưu ý: `schema.yaml` trên master vốn đã lệch sẵn nhiều dòng. Chỉ `git add` phần diff thuộc các schema `Overview*`; nếu regen kéo theo nhiễu của feature khác thì `git checkout -p` bỏ phần nhiễu.

---

### Task 4: FE — view model và hàng KPI

**Files:**
- Modify: `frontend/src/api/schema.d.ts` (regen)
- Modify: `frontend/src/features/overview-report/overview-selectors.ts:11-80`
- Modify: `frontend/src/features/overview-report/components/OverviewKpiTiles.tsx`
- Test: `frontend/src/features/overview-report/__tests__/overview-selectors.test.ts`

**Interfaces:**
- Consumes: response keys từ Task 2/3.
- Produces: type `OverviewKpis` với các field `totalRevenue`, `totalOrders`, `deliveredRevenue`, `deliveredOrders`, `deliveredPct`, `awaitingRevenue`, `awaitingOrders`, `awaitingPct`, `returnedRevenue`, `returnedOrders`, `returnedPct`, `returnRiskRevenue`, `returnRiskOrders`, `returnRiskPct`, `adsCost`, `adsCostPct` — Task 5 dùng trong page.

- [ ] **Step 1: Tạo junction node_modules + regen typed schema**

```powershell
New-Item -ItemType Junction -Path "c:\Users\MYPC\Desktop\workspace\desi-order-wt-overview-buckets\frontend\node_modules" -Target "c:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```
Rồi trong `frontend/`: `npm run gen:api` và `npx prettier --write src/api/schema.d.ts`.

- [ ] **Step 2: Sửa payload mẫu + assert trong test selectors**

Trong `overview-selectors.test.ts`, thay `const payload` (dòng 14-26, phần trước `revenue_by_sale_channel`) bằng:

```typescript
const payload: OverviewReportSummary = {
  total_revenue: "1000000",
  total_orders: 10,
  delivered_revenue: "600000",
  delivered_orders: 6,
  awaiting_revenue: "200000",
  awaiting_orders: 2,
  returned_revenue: "150000",
  returned_orders: 1,
  return_risk_revenue: "50000",
  return_risk_orders: 1,
  ads_cost: "250000",
```

Đổi `cancelled_by_sale_channel:` (dòng 48) thành:

```typescript
  return_risk_by_sale_channel: [{ sale_channel: "marketplace", revenue: "50000", orders: 1 }],
```

Thay cả block `describe("computeOverviewKpis", ...)` (dòng 84-108) bằng:

```typescript
describe("computeOverviewKpis", () => {
  it("mọi % tính trên tổng doanh thu, 4 rổ cộng lại đúng 100%", () => {
    const kpis = computeOverviewKpis(payload);
    expect(kpis.totalRevenue).toBe(1000000);
    expect(kpis.totalOrders).toBe(10);
    expect(kpis.deliveredPct).toBeCloseTo(60);
    expect(kpis.awaitingPct).toBeCloseTo(20);
    expect(kpis.returnedPct).toBeCloseTo(15);
    expect(kpis.returnRiskPct).toBeCloseTo(5);
    const sum =
      (kpis.deliveredPct ?? 0) +
      (kpis.awaitingPct ?? 0) +
      (kpis.returnedPct ?? 0) +
      (kpis.returnRiskPct ?? 0);
    expect(sum).toBeCloseTo(100);
    // Chi phí Ads đứng NGOÀI phép cộng, vẫn lấy tổng doanh thu làm mẫu số.
    expect(kpis.adsCostPct).toBeCloseTo(25);
  });

  it("4 rổ cộng lại đúng bằng tổng doanh thu", () => {
    const kpis = computeOverviewKpis(payload);
    expect(
      kpis.deliveredRevenue + kpis.awaitingRevenue + kpis.returnedRevenue + kpis.returnRiskRevenue,
    ).toBe(kpis.totalRevenue);
    expect(
      kpis.deliveredOrders + kpis.awaitingOrders + kpis.returnedOrders + kpis.returnRiskOrders,
    ).toBe(kpis.totalOrders);
  });

  it("null payload → số 0 và % null (không chia 0)", () => {
    const kpis = computeOverviewKpis(null);
    expect(kpis.totalRevenue).toBe(0);
    expect(kpis.deliveredPct).toBeNull();
    expect(kpis.awaitingPct).toBeNull();
    expect(kpis.returnedPct).toBeNull();
    expect(kpis.returnRiskPct).toBeNull();
    expect(kpis.adsCostPct).toBeNull();
  });
});
```

`describe("sumBreakdownRows")` và các `describe` về breakdown giữ nguyên: mảng `revenue_by_sale_channel` / `revenue_by_region` / `revenue_by_province` trong payload mẫu không đổi.

- [ ] **Step 3: Chạy test — phải đỏ**

Trong `frontend/`: `npx vitest run src/features/overview-report/__tests__/overview-selectors.test.ts`
Kỳ vọng: FAIL — `kpis.totalRevenue` là `undefined`.

- [ ] **Step 4: Viết lại `OverviewKpis` + `computeOverviewKpis`**

Thay dòng 11-80 của `overview-selectors.ts` bằng:

```typescript
export type OverviewKpis = {
  /** Tiền đội sale đã chốt trong kỳ — mẫu số của mọi ô %. */
  totalRevenue: number;
  totalOrders: number;
  /** Đơn giao thành công (status 3). */
  deliveredRevenue: number;
  deliveredOrders: number;
  deliveredPct: number | null;
  /** Đơn đã chốt còn trên đường, chưa có kết quả cuối. */
  awaitingRevenue: number;
  awaitingOrders: number;
  awaitingPct: number | null;
  /** Đơn đang/đã chuyển hoàn (status 4/5/12/13). */
  returnedRevenue: number;
  returnedOrders: number;
  returnedPct: number | null;
  /** Đơn chờ giao lại (status 10) — đã quay đầu một lần, nguy cơ thành hoàn. */
  returnRiskRevenue: number;
  returnRiskOrders: number;
  returnRiskPct: number | null;
  /** Tiền chi quảng cáo — KHÔNG nằm trong 4 rổ trên. */
  adsCost: number;
  adsCostPct: number | null;
};

/** Một dòng của khối phân bổ (dùng cho cả donut lẫn bảng). */
export type BreakdownRow = {
  key: string;
  label: string;
  orders: number;
  revenue: number;
  /** % trên tổng của chính khối đó. */
  sharePct: number;
  /** Chỉ khối doanh thu có — khối hoàn/rủi ro hoàn để undefined. */
  deliveredOrders?: number;
  deliveredRevenue?: number;
};

function pctOf(value: number, base: number): number | null {
  return base > 0 ? (value / base) * 100 : null;
}

/**
 * Payload → view model các ô KPI. Mẫu số của MỌI ô % là tổng doanh thu, nên 4 rổ
 * kết quả cộng lại đúng 100% — xem docs/overview-report.md.
 */
export function computeOverviewKpis(data: OverviewReportSummary | null): OverviewKpis {
  const totalRevenue = Number(data?.total_revenue || 0);
  const deliveredRevenue = Number(data?.delivered_revenue || 0);
  const awaitingRevenue = Number(data?.awaiting_revenue || 0);
  const returnedRevenue = Number(data?.returned_revenue || 0);
  const returnRiskRevenue = Number(data?.return_risk_revenue || 0);
  const adsCost = Number(data?.ads_cost || 0);

  return {
    totalRevenue,
    totalOrders: data?.total_orders ?? 0,
    deliveredRevenue,
    deliveredOrders: data?.delivered_orders ?? 0,
    deliveredPct: pctOf(deliveredRevenue, totalRevenue),
    awaitingRevenue,
    awaitingOrders: data?.awaiting_orders ?? 0,
    awaitingPct: pctOf(awaitingRevenue, totalRevenue),
    returnedRevenue,
    returnedOrders: data?.returned_orders ?? 0,
    returnedPct: pctOf(returnedRevenue, totalRevenue),
    returnRiskRevenue,
    returnRiskOrders: data?.return_risk_orders ?? 0,
    returnRiskPct: pctOf(returnRiskRevenue, totalRevenue),
    adsCost,
    adsCostPct: pctOf(adsCost, totalRevenue),
  };
}
```

- [ ] **Step 5: Chạy test — phải xanh**

`npx vitest run src/features/overview-report/__tests__/overview-selectors.test.ts`. Kỳ vọng: all passed.

- [ ] **Step 6: Viết lại hàng KPI**

Thay phần từ dòng 39 tới hết `OverviewKpiTiles.tsx` bằng:

```tsx
/**
 * Hàng KPI của trang. Bốn ô giữa (giao thành công / chờ giao / hoàn / rủi ro
 * hoàn) là 4 rổ RỜI NHAU của ô Tổng doanh thu nên % của chúng cộng lại đúng
 * 100%. Ô chi phí Ads là tiền chi ra — đứng NGOÀI phép cộng, chỉ lấy tổng doanh
 * thu làm mẫu số để biết tỉ lệ. Phân bổ theo kênh/địa lý nằm ở các khối bên
 * dưới, không lặp lại ở đây.
 */
export function OverviewKpiTiles({ kpis }: { kpis: OverviewKpis }) {
  const { t } = useI18n();
  const ordersUnit = t("overviewReport.kpi.ordersUnit", "đơn");
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      <Tile
        label={t("overviewReport.kpi.totalRevenue", "Tổng doanh thu")}
        value={money(kpis.totalRevenue)}
        sub={`${kpis.totalOrders} ${ordersUnit}`}
      />
      <Tile
        label={t("overviewReport.kpi.deliveredRevenue", "Doanh thu giao thành công")}
        value={money(kpis.deliveredRevenue)}
        sub={`${pct(kpis.deliveredPct)} · ${kpis.deliveredOrders} ${ordersUnit}`}
      />
      <Tile
        label={t("overviewReport.kpi.awaitingRevenue", "Doanh thu chờ giao")}
        value={money(kpis.awaitingRevenue)}
        sub={`${pct(kpis.awaitingPct)} · ${kpis.awaitingOrders} ${ordersUnit}`}
      />
      <Tile
        label={t("overviewReport.kpi.returned", "Hoàn")}
        value={money(kpis.returnedRevenue)}
        sub={`${pct(kpis.returnedPct)} · ${kpis.returnedOrders} ${ordersUnit}`}
        tone="danger"
      />
      <Tile
        label={t("overviewReport.kpi.returnRisk", "Rủi ro hoàn")}
        value={money(kpis.returnRiskRevenue)}
        sub={`${pct(kpis.returnRiskPct)} · ${kpis.returnRiskOrders} ${ordersUnit}`}
        tone="danger"
      />
      <Tile
        label={t("overviewReport.kpi.adsCost", "Chi phí FB + Google Ads")}
        value={money(kpis.adsCost)}
        sub={pct(kpis.adsCostPct)}
        tone="danger"
      />
    </div>
  );
}
```

- [ ] **Step 7: Chưa commit — sang thẳng Task 5**

`npx tsc --noEmit` ở bước này CHẮC CHẮN đỏ vì `OverviewReportPage.tsx` còn tham
chiếu `cancelled_by_sale_channel` và `kpis.netRevenue`. Đó là chuyện bình
thường: Task 4 và Task 5 sửa hai nửa của cùng một đổi kiểu, nên commit một lần ở
cuối Task 5 khi `tsc` sạch. Không commit code không build được.

---

### Task 5: FE — trang báo cáo (khối donut, mặc định Tháng này, chữ hiển thị)

**Files:**
- Modify: `frontend/src/features/overview-report/pages/OverviewReportPage.tsx`

**Interfaces:**
- Consumes: `computeOverviewKpis`, `buildSaleChannelBreakdown` (không đổi chữ ký), response key `return_risk_by_sale_channel`.

- [ ] **Step 1: Đổi mặc định bộ lọc sang Tháng này**

Thay dòng 30-35:

```typescript
// Mặc định "Tháng này" (user chốt 2026-08-11): trang này để lãnh đạo nhìn cả
// tháng đang chạy, không phải chỉ hôm nay.
const THIS_MONTH_RANGE = getDateRange("this_month");
const DEFAULT_DATE_FILTER: DateFilterValue = {
  option: "this_month",
  from: THIS_MONTH_RANGE.from,
  to: THIS_MONTH_RANGE.to,
};
```

- [ ] **Step 2: Đổi mảng dữ liệu khối thứ 3**

Thay dòng 84-87 (`cancelledRows`) bằng:

```typescript
  const returnRiskRows = useMemo(
    () => buildSaleChannelBreakdown(data?.return_risk_by_sale_channel),
    [data],
  );
```

- [ ] **Step 3: Đổi mô tả trang + tiêu đề 3 khối donut**

`PageHeader` description (dòng 95-98):

```typescript
        description={t(
          "overviewReport.hint",
          "Số liệu toàn công ty theo ngày tạo đơn — tổng doanh thu đã chốt và kết quả giao hàng của số tiền đó, kèm chi phí Ads.",
        )}
```

Khối 1 (dòng 179-188) đổi tiêu đề và nhãn tâm donut:

```tsx
            <OverviewBreakdownCard
              title={t("overviewReport.revenueByChannelTitle", "Doanh thu theo kênh bán")}
              valueHeader={t("overviewReport.revenueHeader", "Doanh thu")}
              centerLabel={t("overviewReport.kpi.totalRevenueShort", "Doanh thu")}
              rows={revenueRows}
              variant="revenue"
            />
```

Khối 3 (dòng 196-202) thay hẳn khối Hủy:

```tsx
            <OverviewBreakdownCard
              title={t("overviewReport.returnRiskByChannelTitle", "Rủi ro hoàn theo kênh bán")}
              valueHeader={t("overviewReport.returnRiskHeader", "Giá trị rủi ro hoàn")}
              centerLabel={t("overviewReport.kpi.returnRisk", "Rủi ro hoàn")}
              rows={returnRiskRows}
              variant="amount"
            />
```

Khối vùng miền (dòng 206-214) đổi `centerLabel` key `overviewReport.kpi.netRevenueShort` → `overviewReport.kpi.totalRevenueShort` (fallback giữ `"Doanh thu"`).

- [ ] **Step 4: Kiểm tra type + lint**

```bash
npx tsc --noEmit
npm run lint:strict
```
Kỳ vọng: sạch cả hai.

- [ ] **Step 5: Chạy toàn bộ test FE của feature**

```bash
npx vitest run src/features/overview-report
```
Kỳ vọng: all passed. Nếu `overview-report-api.test.ts` đỏ vì payload mẫu, sửa payload theo key mới.

- [ ] **Step 6: Chạy full suite FE để bắt hồi quy ngoài feature**

```bash
npm run test:ci
```
Baseline master 2026-08-11 là XANH 100% (140 file / 1053 test) — mọi test đỏ đều là do thay đổi này, không được bỏ qua.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/overview-report/
git commit -m "feat(overview): swap cancelled block for return risk, default to this month"
```

---

### Task 6: Cập nhật tài liệu

**Files:**
- Modify: `docs/overview-report.md`

- [ ] **Step 1: Viết lại mục "Định nghĩa số liệu"**

Thay toàn bộ đoạn từ `Tập đơn cơ sở:` đến hết bảng bucket + các gạch đầu dòng định nghĩa (dòng 63-127) bằng nội dung của mục "Định nghĩa số liệu" trong spec `docs/superpowers/specs/2026-08-11-overview-report-sale-status-buckets-design.md`, giữ nguyên phần "Bộ lọc nhóm kênh bán", "Bộ lọc sản phẩm", "Bất biến phải giữ".

Bổ sung vào "Bất biến phải giữ" một gạch đầu dòng:

```markdown
**Bốn rổ kết quả cộng lại đúng bằng ô Tổng doanh thu** (tiền và số đơn). Test
`test_four_buckets_sum_to_total_revenue` và
`test_four_buckets_partition_every_order_status` chốt bất biến này — cái sau bắt
được cả trường hợp thêm mã `OrderStatus` mới mà quên xếp rổ.
```

- [ ] **Step 2: Cập nhật layout, response JSON, mục Frontend**

- Sơ đồ layout: hàng KPI thành `Tổng DT │ Giao TC │ Chờ giao │ Hoàn │ Rủi ro hoàn │ Chi phí`; khối donut thứ 3 đổi `Hủy` → `Rủi ro hoàn`.
- Khối JSON response: chép nguyên từ spec.
- Mục Frontend: `OverviewKpiTiles.tsx — 6 tile`, ghi rõ mặc định `DateFilter` là **Tháng này**.

- [ ] **Step 3: Thêm dòng Sprint + note**

```markdown
| 6 | Đổi gốc doanh thu sang trạng thái sale: 4 rổ kết quả + ô Rủi ro hoàn, bỏ ô Hủy/Đơn chờ chốt, mặc định Tháng này | ✅ Done 2026-08-11 |
```

Và một mục note cuối file:

```markdown
### Note sau Sprint 6 (2026-08-11)

Doanh thu không còn tính theo hóa đơn KiotViet mà theo TRẠNG THÁI SALE. Hệ quả
phải theo dõi trên prod: đơn đã có hóa đơn nhưng sale đổi trạng thái sang mã
ngoài 4 mã chốt (vd "Khách bom hàng") bị loại khỏi báo cáo theo quyết định
2026-08-11. Dấu hiệu sai: ô Hoàn gần 0 trong khi tab Hoàn ở màn đơn hàng vẫn
nhiều đơn — khi đó nới `OVERVIEW_CLOSED_SCOPE_Q` vế hai thành `~LEAD_ORDER_Q`.

Đối chiếu sau deploy: so `total_revenue` của một kỳ với `SELECT sum(...)` trên
DB theo cùng điều kiện phạm vi; và kiểm tra 4 rổ cộng lại đúng bằng tổng.
```

- [ ] **Step 4: Commit + push + tạo MR**

```bash
git add docs/overview-report.md
git commit -m "docs: update overview report metric definitions"
git push -u origin feat/overview-report-sale-status-buckets \
  -o merge_request.create -o merge_request.title="Overview report: revenue by sale status with 4 result buckets"
```

Gửi link MR cho user.

---

## Self-Review

**Spec coverage:**
- Phạm vi đơn (4 mã chốt HOẶC đã có hóa đơn chưa gắn trạng thái sale) → Task 1 Step 3, test Task 2 Step 1.
- Loại đơn hủy khỏi tổng → Task 2 Step 3, test `test_scope_drops_non_closed_sale_status_and_cancelled`.
- 4 rổ phân hoạch kín → Task 1 (hằng số + test thuần Python), Task 2 (test qua API).
- % trên tổng doanh thu → Task 4 Step 4 + test Step 2.
- Bỏ ô Hủy / Đơn chờ chốt → Task 2 (response), Task 4 (tiles).
- Khối donut theo cơ sở mới + thay khối Hủy bằng Rủi ro hoàn → Task 3, Task 5.
- Mặc định Tháng này → Task 5 Step 1.
- Đổi tên field API → Task 2 Step 3-4, Task 3 Step 5.
- Cập nhật docs → Task 6.

**Type consistency:** tên rổ dùng nhất quán `total`/`delivered`/`awaiting`/`returned`/`return_risk` ở BE và `totalRevenue`/`awaitingRevenue`/`returnRiskRevenue` ở FE; key JSON `return_risk_*` khớp giữa serializer, repository, test BE và payload mẫu FE.
