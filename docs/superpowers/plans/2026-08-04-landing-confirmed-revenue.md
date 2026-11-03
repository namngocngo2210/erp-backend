# Landing Confirmed Revenue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tách "Doanh thu xác nhận đơn" (giá trị khách tự đăng ký lúc đơn landing đổ về) khỏi "Doanh thu chốt đơn" (phần sale tư vấn thêm) trong tab Báo cáo theo sale.

**Architecture:** Thêm field snapshot bất biến `Order.landing_confirmed_amount` ghi một lần lúc ingest (tái dùng logic bóc `product_raw`/bảng giá sẵn có), đổi 2 aggregate trong `summary_sale_daily_by_staff`, backfill đơn cũ bằng management command. Spec: `docs/superpowers/specs/2026-08-04-landing-confirmed-revenue-design.md`.

**Tech Stack:** Django 5 + DRF + pytest (backend), React + TypeScript + vitest (frontend).

## Global Constraints

- Worktree: `c:\Users\MYPC\Desktop\workspace\desi-order\.claude\worktrees\feat-landing-confirmed-revenue`, nhánh `feat/landing-confirmed-revenue`.
- Tên field API `confirmed_revenue`/`closed_revenue` GIỮ NGUYÊN — chỉ đổi ngữ nghĩa; không sửa schema.yaml ngoài mô tả nếu cần.
- Không đụng các báo cáo khác (Nhóm 1–9 dashboard, Ads, Khách hàng) — chỉ `summary_sale_daily_by_staff`.
- Backend test chạy bằng Docker (xem lệnh ở từng task; đổi `POSTGRES_DB`/`REDIS_CACHE_DB` riêng cho mỗi lần chạy đồng thời, REDIS_CACHE_DB chỉ 0–15).
- Lint bắt buộc: `ruff check .` (ruleset trong `backend/pyproject.toml`).
- Commit message tiếng Anh, ngắn, KHÔNG kèm Co-Authored-By.

**Lệnh test backend (Git Bash, chạy từ thư mục worktree):**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=test_lcr_task<N> \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=9 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

---

### Task 1: Model field + migration

**Files:**
- Modify: `backend/apps/orders/models.py` (class `Order`, ngay dưới `total_after_discount`, ~dòng 340)
- Create: `backend/apps/orders/migrations/0114_order_landing_confirmed_amount.py`

**Interfaces:**
- Produces: `Order.landing_confirmed_amount: Decimal` — mặc định 0, các task sau đọc/ghi field này.

- [ ] **Step 1: Thêm field vào model**

Trong `backend/apps/orders/models.py`, ngay sau dòng `total_after_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)`:

```python
    # Tiền khách TỰ ĐĂNG KÝ trên landing lúc đơn đổ về (trước khi sale tư vấn).
    # Ghi một lần duy nhất ở create_order_from_landing, không bao giờ sửa lại
    # (cùng họ bất biến với has_address). Đơn không qua landing giữ 0.
    landing_confirmed_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, db_default=0
    )
```

- [ ] **Step 2: Viết migration**

`backend/apps/orders/migrations/0114_order_landing_confirmed_amount.py`:

```python
from django.db import migrations, models


class Migration(migrations.Migration):
    """Snapshot tiền khách tự đăng ký trên landing (spec 2026-08-04).

    ADD COLUMN ... DEFAULT 0 thuần — không rewrite bảng, an toàn giờ hành chính.
    """

    dependencies = [
        ("orders", "0113_seed_dynamic_field_defs"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="landing_confirmed_amount",
            field=models.DecimalField(
                max_digits=12, decimal_places=2, default=0, db_default=0
            ),
        ),
    ]
```

- [ ] **Step 3: Chạy check migration**

Chạy lệnh test backend (Global Constraints) với `<paths>` = `apps/orders/tests/test_ingest_api.py -k accepts_new_payload` và `POSTGRES_DB=test_lcr_task1` — `--create-db` áp toàn bộ migration; PASS nghĩa là migration + model khớp nhau. Đồng thời chạy `python manage.py makemigrations --check --dry-run` trong cùng container (thêm vào sh -c, ngăn cách `&&`) để chắc không còn diff model↔migration.

Expected: pytest PASS, makemigrations báo "No changes detected".

- [ ] **Step 4: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations/0114_order_landing_confirmed_amount.py
git commit -m "feat: add landing_confirmed_amount snapshot field"
```

---

### Task 2: Ghi snapshot lúc ingest + copy khi clone

**Files:**
- Modify: `backend/apps/orders/application/services/order_service_commands.py` (`create_order_from_landing`, khối `Order.objects.create(...)` ~dòng 211–250)
- Modify: `backend/apps/orders/application/services/order_service_helpers.py` (dict `create_kwargs` ~dòng 56–121)
- Test: `backend/apps/orders/tests/test_ingest_api.py` (class mới cuối file)

**Interfaces:**
- Consumes: `Order.landing_confirmed_amount` (Task 1).
- Produces: mọi đơn tạo qua `OrderService.create_order_from_landing(payload)` có `landing_confirmed_amount` = giá trị `total_amount` tính được lúc ingest; đơn clone/khôi phục copy nguyên giá trị từ đơn gốc.

- [ ] **Step 1: Viết test fail**

Thêm cuối `backend/apps/orders/tests/test_ingest_api.py`:

```python
class LandingConfirmedAmountTests(TestCase):
    """Snapshot tiền khách tự đăng ký: ghi 1 lần lúc ingest, bất biến về sau."""

    def setUp(self):
        self.channel = Channel.objects.create(
            code="fb-lca", name="FB LCA", owner="FB LCA"
        )

    def _create(self, phone: str, **extra):
        payload = {"channel_id": self.channel.id, "phone": phone, **extra}
        return OrderService.create_order_from_landing(payload)

    def test_snapshot_from_product_id_and_quantity(self):
        Product.objects.create(name="Bồn ngâm chân", sku="BNC09", price="1500000")
        order = self._create("0911000001", product_id="BNC09", quantity=1)
        self.assertEqual(order.landing_confirmed_amount, Decimal("1500000"))

    def test_snapshot_from_product_raw_text(self):
        order = self._create(
            "0911000002", product="Mua 1 Thảm massage: 1.290.000 VNĐ"
        )
        self.assertEqual(order.landing_confirmed_amount, Decimal("1290000"))

    def test_snapshot_from_explicit_total_amount(self):
        order = self._create("0911000003", total_amount=2550000)
        self.assertEqual(order.landing_confirmed_amount, Decimal("2550000"))

    def test_snapshot_zero_when_nothing_matches(self):
        order = self._create("0911000004", product="hàng lạ không giá")
        self.assertEqual(order.landing_confirmed_amount, Decimal("0"))

    def test_snapshot_immutable_when_order_edited(self):
        Product.objects.create(name="Đai lưng", sku="DTL09", price="1500000")
        order = self._create("0911000005", product_id="DTL09", quantity=1)
        order.total_amount = Decimal("4200000")
        order.total_after_discount = Decimal("4200000")
        order.save()
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("1500000"))

    def test_clone_copies_snapshot(self):
        Product.objects.create(name="Tủ", sku="SQA09", price="1500000")
        order = self._create("0911000006", product_id="SQA09", quantity=1)
        clone = OrderService.duplicate_order(order)
        self.assertEqual(clone.landing_confirmed_amount, Decimal("1500000"))
```

Lưu ý cho người triển khai: mỗi test dùng phone khác nhau vì `create_order_from_landing` có cổng chống trùng theo số điện thoại. `OrderService.duplicate_order(order)` là method public đã xác nhận tồn tại (`order_service_helpers.py:35`).

- [ ] **Step 2: Chạy test, xác nhận fail**

Lệnh Global Constraints, `<paths>` = `apps/orders/tests/test_ingest_api.py::LandingConfirmedAmountTests`, `POSTGRES_DB=test_lcr_task2`.
Expected: FAIL — `landing_confirmed_amount` = 0 (chưa ghi) và/hoặc lỗi thiếu method.

- [ ] **Step 3: Implement**

Trong `create_order_from_landing`, khối `Order.objects.create(...)` (ngay cạnh `total_amount=derived_total_amount`):

```python
            total_amount=derived_total_amount,
            # Snapshot bất biến: đúng số tiền ingest tính được (payload tường minh
            # → parse product_raw → số lượng × giá bảng giá kênh). Spec 2026-08-04.
            landing_confirmed_amount=derived_total_amount,
```

Trong `order_service_helpers.py`, dict `create_kwargs` (cạnh `product_raw=order.product_raw`):

```python
            product_raw=order.product_raw,
            landing_confirmed_amount=order.landing_confirmed_amount,
```

- [ ] **Step 4: Chạy lại test, xác nhận pass**

Cùng lệnh Step 2. Expected: PASS cả 6 test.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/application/services/order_service_commands.py backend/apps/orders/application/services/order_service_helpers.py backend/apps/orders/tests/test_ingest_api.py
git commit -m "feat: capture landing confirmed amount at ingest"
```

---

### Task 3: Đổi aggregate báo cáo sale-by-staff

**Files:**
- Modify: `backend/apps/orders/_repositories_reports.py` (`summary_sale_daily_by_staff`, ~dòng 409–470)
- Modify: `backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py` (docstring `sale_staff_report_summary`, ~dòng 130)
- Test: `backend/apps/orders/tests/test_digital_and_sale_staff_reports.py`

**Interfaces:**
- Consumes: `Order.landing_confirmed_amount` (Task 1).
- Produces: response `by_staff_daily[]` giữ nguyên key (`confirmed_revenue`, `closed_revenue` — string decimal) với ngữ nghĩa mới: cả hai trên tập đơn `CLOSED_SALES_STATUS_CODES` (4 mã); `confirmed = SUM(LEAST(landing_confirmed_amount, total_after_discount))`, `closed = SUM(total_after_discount − LEAST(...))`.

- [ ] **Step 1: Sửa test theo ngữ nghĩa mới (fail trước)**

Trong `test_digital_and_sale_staff_reports.py`:

1. Mở rộng helper `_order` của `_ReportFixtureMixin` thêm tham số snapshot:

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
    ):
        order = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            staff=staff or self.sale,
            name="X",
            phone="0900000009",
            status=status,
            order_source=order_source,
            total_after_discount=Decimal(total),
            landing_confirmed_amount=Decimal(landing_confirmed),
        )
        if sales_status_code is not None:
            OrderAssessment.objects.create(order=order, sales_status_code=sales_status_code)
        return order
```

2. Thay `test_three_revenue_buckets_split_by_status` trong `SaleStaffReportTests` bằng:

```python
    def test_confirmed_is_landing_snapshot_closed_is_upsell(self):
        # khách đăng ký 1tr5, sale chốt 4tr2 -> xác nhận 1tr5, chốt 2tr7
        self._order("4200000", "closed", landing_confirmed="1500000")
        # đơn hẹn ngày cùng tập 4 mã -> vẫn tính
        self._order("200000", "closed_scheduled_date", landing_confirmed="200000")
        # chưa chốt -> chỉ vào doanh thu tổng
        self._order("300000", "not_buying", landing_confirmed="300000")

        rows = self._rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(Decimal(row["total_revenue"]), Decimal("4700000"))
        self.assertEqual(Decimal(row["confirmed_revenue"]), Decimal("1700000"))
        self.assertEqual(Decimal(row["closed_revenue"]), Decimal("2700000"))
        self.assertEqual(row["data_count"], 3)
        self.assertEqual(row["orders"], 2)
        self.assertEqual(row["staff_id"], self.sale.id)

    def test_order_without_snapshot_counts_fully_as_closed(self):
        # đơn tạo tay/fanpage: xác nhận 0, toàn bộ vào chốt
        self._order("3000000", "closed", landing_confirmed="0")

        row = self._rows()[0]
        self.assertEqual(Decimal(row["confirmed_revenue"]), Decimal("0"))
        self.assertEqual(Decimal(row["closed_revenue"]), Decimal("3000000"))

    def test_downsell_clamps_confirmed_no_negative_closed(self):
        # khách đăng ký 3tr, sale chốt xuống 1tr5 -> xác nhận cắt về 1tr5, chốt 0
        self._order("1500000", "closed", landing_confirmed="3000000")

        row = self._rows()[0]
        self.assertEqual(Decimal(row["confirmed_revenue"]), Decimal("1500000"))
        self.assertEqual(Decimal(row["closed_revenue"]), Decimal("0"))

    def test_confirmed_plus_closed_equals_closed_orders_revenue(self):
        self._order("4200000", "closed", landing_confirmed="1500000")
        self._order("3000000", "closed_express", landing_confirmed="0")
        self._order("1500000", "created_order", landing_confirmed="3000000")
        self._order("999000", "not_buying", landing_confirmed="999000")

        row = self._rows()[0]
        self.assertEqual(
            Decimal(row["confirmed_revenue"]) + Decimal(row["closed_revenue"]),
            Decimal("8700000"),
        )
```

Giữ nguyên `test_rows_split_per_day` và `test_requires_dashboard_section`.

- [ ] **Step 2: Chạy test, xác nhận fail**

Lệnh Global Constraints, `<paths>` = `apps/orders/tests/test_digital_and_sale_staff_reports.py::SaleStaffReportTests`, `POSTGRES_DB=test_lcr_task3`.
Expected: 4 test mới FAIL (số cũ tính theo trạng thái, không theo snapshot).

- [ ] **Step 3: Implement**

Trong `_repositories_reports.py`, method `summary_sale_daily_by_staff`:

1. Import bổ sung ở đầu file (nếu chưa có): `from django.db.models import DecimalField, ExpressionWrapper, F` và `from django.db.models.functions import Least` (gộp vào dòng import hiện có của file, theo ruff).
2. Thay 2 annotate `closed_revenue`/`confirmed_revenue`:

```python
        # Snapshot bị cắt trần về giá trị cuối (Least) để ca down-sell không sinh
        # số âm; nhờ đó confirmed + closed luôn = SUM(total_after_discount) của
        # đúng tập đơn chốt. Cả hai cột CÙNG tập 4 mã CLOSED_SALES_STATUS_CODES
        # (spec 2026-08-04 — phân biệt 3-mã/4-mã cũ của tab này bị bỏ).
        confirmed_expr = Least(F("landing_confirmed_amount"), F("total_after_discount"))
        upsell_expr = ExpressionWrapper(
            F("total_after_discount") - confirmed_expr,
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
```

(đặt ngay trước `rows = list(...)`), rồi trong `.annotate(...)`:

```python
                closed_revenue=Sum(
                    upsell_expr,
                    filter=Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES),
                ),
                confirmed_revenue=Sum(
                    confirmed_expr,
                    filter=Q(assessment__sales_status_code__in=CLOSED_SALES_STATUS_CODES),
                ),
```

3. Cập nhật docstring của method: ba mốc doanh thu thành — `total_revenue` (mọi data, giữ nguyên); `confirmed_revenue` = tiền khách tự đăng ký landing (snapshot, cắt trần theo Least); `closed_revenue` = phần sale tư vấn thêm; cả hai trên tập 4 mã. Xoá đoạn nói `closed_revenue` dùng `CLOSED_REVENUE_SALES_STATUS_CODES`. Nếu sau đó `CLOSED_REVENUE_SALES_STATUS_CODES` không còn được import/dùng trong file này thì giữ nguyên import nếu method khác (`summary_sale_performance_by_staff`) vẫn dùng — kiểm tra bằng grep trước khi xoá.
4. Cập nhật docstring `sale_staff_report_summary` trong `dashboard_report_views.py` thêm 1 dòng: "confirmed_revenue = tiền khách tự đăng ký trên landing (snapshot lúc ingest); closed_revenue = phần sale tư vấn thêm."

- [ ] **Step 4: Chạy lại test, xác nhận pass**

Cùng lệnh Step 2, thêm cả `apps/orders/tests/test_ingest_api.py::LandingConfirmedAmountTests` để chắc không vỡ Task 2.
Expected: PASS toàn bộ.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/_repositories_reports.py backend/apps/orders/transport/views/order_views_impl/dashboard_report_views.py backend/apps/orders/tests/test_digital_and_sale_staff_reports.py
git commit -m "feat: split confirmed vs closed revenue by landing snapshot"
```

---

### Task 4: Backfill command cho đơn cũ

**Files:**
- Create: `backend/apps/orders/management/commands/backfill_landing_confirmed_amount.py`
- Test: `backend/apps/orders/tests/test_backfill_landing_confirmed_amount.py`

**Interfaces:**
- Consumes: `derive_total_amount`, `derive_quantity` (`apps.orders.product_parser`); `resolve_price_book`, `unit_price_for` (`apps.orders.application.services.price_book_service`); `Order.landing_confirmed_amount`.
- Produces: lệnh `python manage.py backfill_landing_confirmed_amount [--apply] [--batch-size N]`, idempotent, mặc định dry-run.

- [ ] **Step 1: Viết test fail**

`backend/apps/orders/tests/test_backfill_landing_confirmed_amount.py`:

```python
"""Backfill snapshot tiền khách tự đăng ký cho đơn landing cũ.

Fallback đúng thứ tự ingest: tiền parse từ product_raw -> quantity × giá bảng
giá kênh theo sku -> giữ 0. Idempotent: chỉ đụng đơn đang = 0.
"""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, Product


class BackfillLandingConfirmedAmountTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="fb-bf", name="FB BF")
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000010", phone_normalized="0900000010"
        )

    def _order(self, **kwargs):
        return Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="X",
            phone="0900000010",
            order_source="landing",
            **kwargs,
        )

    def _run(self, *args):
        out = StringIO()
        call_command("backfill_landing_confirmed_amount", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        order = self._order(product_raw="Mua 1 Thảm: 1.290.000 VNĐ")
        output = self._run()
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("0"))
        self.assertIn("DRY-RUN", output)

    def test_apply_uses_amount_parsed_from_product_raw(self):
        order = self._order(product_raw="Mua 1 Thảm: 1.290.000 VNĐ")
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("1290000"))

    def test_apply_falls_back_to_quantity_times_price_book(self):
        Product.objects.create(name="Bồn ngâm chân", sku="BNC08", price="1500000")
        order = self._order(product_raw="đăng ký bồn ngâm chân", sku="BNC08", quantity=2)
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("3000000"))

    def test_apply_keeps_zero_when_nothing_derivable(self):
        order = self._order(product_raw="text lạ không bóc được")
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("0"))

    def test_apply_skips_orders_already_backfilled(self):
        order = self._order(
            product_raw="Mua 1 Thảm: 1.290.000 VNĐ",
            landing_confirmed_amount=Decimal("999000"),
        )
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("999000"))

    def test_skips_non_landing_orders(self):
        order = self._order(product_raw="Mua 1 Thảm: 1.290.000 VNĐ")
        Order.objects.filter(pk=order.pk).update(order_source="zalo")
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.landing_confirmed_amount, Decimal("0"))
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Lệnh Global Constraints, `<paths>` = `apps/orders/tests/test_backfill_landing_confirmed_amount.py`, `POSTGRES_DB=test_lcr_task4`.
Expected: FAIL — `Unknown command: 'backfill_landing_confirmed_amount'`.

- [ ] **Step 3: Implement command**

`backend/apps/orders/management/commands/backfill_landing_confirmed_amount.py`:

```python
"""Backfill Order.landing_confirmed_amount cho đơn landing tạo trước khi có field.

Tính lại theo đúng thứ tự fallback của ingest (bỏ bước payload vì không còn):
  1. Số tiền parse được từ product_raw (derive_total_amount).
  2. quantity đã lưu (fallback derive_quantity) × đơn giá bảng giá kênh theo sku
     đã lưu (unit_price_for — giá CATALOG HIỆN TẠI, chấp nhận sai số nếu giá đổi).
  3. Không ra gì -> giữ 0 (toàn bộ tiền đơn tính vào cột "chốt" theo spec).

Idempotent: chỉ đụng đơn landing_confirmed_amount = 0. Mặc định DRY-RUN,
--apply để ghi thật. Update bằng queryset.update() — không bắn signal/WS event.
Chạy tay giờ thấp điểm (tiền lệ migration 0032).
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.orders.application.services.price_book_service import (
    resolve_price_book,
    unit_price_for,
)
from apps.orders.models import Order, OrderSource, Product
from apps.orders.product_parser import derive_quantity, derive_total_amount


class Command(BaseCommand):
    help = (
        "Backfill landing_confirmed_amount cho đơn landing cũ từ product_raw. "
        "Mặc định dry-run; --apply để ghi."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Ghi thật vào DB.")
        parser.add_argument(
            "--batch-size", type=int, default=500, help="Số đơn xử lý mỗi lượt iterator."
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        batch_size = options["batch_size"]

        qs = (
            Order.objects.filter(
                deleted_at__isnull=True,
                order_source=OrderSource.LANDING,
                landing_confirmed_amount=0,
            )
            .exclude(product_raw="")
            .select_related("channel")
            .only(
                "id", "product_raw", "sku", "quantity",
                "landing_confirmed_amount", "channel",
            )
        )

        stats = {"from_text": 0, "from_price_book": 0, "unresolved": 0}
        products_by_sku = {p.sku: p for p in Product.objects.all()}
        book_by_channel: dict[int, object] = {}
        updates: list[tuple[int, Decimal]] = []

        for order in qs.iterator(chunk_size=batch_size):
            amount = Decimal(derive_total_amount(order.product_raw))
            if amount > 0:
                stats["from_text"] += 1
            else:
                # Đơn ingest cũ có thể chứa nhiều SKU nối chuỗi — chỉ backfill được
                # ca 1 SKU khớp catalog; ca nhiều SKU không tách được số lượng
                # từng dòng nên xếp vào unresolved (giữ 0).
                product = products_by_sku.get(order.sku)
                if product is not None:
                    if order.channel_id not in book_by_channel:
                        book_by_channel[order.channel_id] = resolve_price_book(order.channel)
                    quantity = order.quantity or derive_quantity(order.product_raw) or 1
                    amount = unit_price_for(product, book_by_channel[order.channel_id]) * quantity
                    stats["from_price_book"] += 1
            if amount > 0:
                updates.append((order.id, amount))
            else:
                stats["unresolved"] += 1

        self.stdout.write(f"Đơn landing cần backfill: {qs.count()}")
        self.stdout.write(f"  - bóc được tiền từ product_raw: {stats['from_text']}")
        self.stdout.write(f"  - tính từ quantity × bảng giá: {stats['from_price_book']}")
        self.stdout.write(f"  - không bóc được (giữ 0): {stats['unresolved']}")

        if not apply:
            self.stdout.write("DRY-RUN: chưa ghi gì. Thêm --apply để cập nhật.")
            return

        updated = 0
        for order_id, amount in updates:
            updated += Order.objects.filter(
                id=order_id, landing_confirmed_amount=0
            ).update(landing_confirmed_amount=amount)
        self.stdout.write(self.style.SUCCESS(f"Đã backfill {updated} đơn."))
```

Lưu ý: `Order.deleted_at` đã xác nhận tồn tại (`models.py:404`) — giữ nguyên điều kiện lọc.

- [ ] **Step 4: Chạy lại test, xác nhận pass**

Cùng lệnh Step 2. Expected: PASS cả 6 test.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/management/commands/backfill_landing_confirmed_amount.py backend/apps/orders/tests/test_backfill_landing_confirmed_amount.py
git commit -m "feat: add backfill command for landing confirmed amount"
```

---

### Task 5: Frontend — AOV + chú thích ngữ nghĩa mới

**Files:**
- Modify: `frontend/src/features/sale-report/sale-report-selectors.ts`
- Modify: `frontend/src/features/sale-report/components/SaleMetricTable.tsx`
- Test: `frontend/src/features/sale-report/__tests__/sale-report-selectors.test.ts`

**Interfaces:**
- Consumes: payload `by_staff_daily[]` (key không đổi — Task 3).
- Produces: `SaleMetricRow.aov = (confirmedRevenue + closedRevenue) / orders`; chú thích dưới bảng giải thích 2 cột.

- [ ] **Step 0: Chuẩn bị node_modules cho worktree (nếu chưa có)**

```powershell
cmd /c mklink /J "c:\Users\MYPC\Desktop\workspace\desi-order\.claude\worktrees\feat-landing-confirmed-revenue\frontend\node_modules" "c:\Users\MYPC\Desktop\workspace\desi-order\frontend\node_modules"
```

- [ ] **Step 1: Sửa test selector (fail trước)**

Trong `sale-report-selectors.test.ts`, tìm test đang assert `aov` (grep `aov`). Cập nhật kỳ vọng theo công thức mới và thêm test:

```typescript
it("aov dùng giá trị thực của đơn chốt (confirmed + closed)", () => {
  const rows = buildSaleStaffRows(
    [
      {
        date: "2026-08-01",
        staff_id: 1,
        staff__username: "sale1",
        staff__display_name: "Sale 1",
        data_count: 3,
        orders: 2,
        total_revenue: "5000000",
        closed_revenue: "2700000",
        confirmed_revenue: "1500000",
      },
    ],
    "Không xác định",
  );
  expect(rows[0].aov).toBe((2700000 + 1500000) / 2);
});
```

(Shape hàng lấy đúng type `SaleStaffDailyRow` trong `queries.ts` — nếu test hiện có helper dựng row thì dùng lại helper.)

- [ ] **Step 2: Chạy test, xác nhận fail**

```powershell
cd "c:\Users\MYPC\Desktop\workspace\desi-order\.claude\worktrees\feat-landing-confirmed-revenue\frontend"; npx vitest run src/features/sale-report
```

Expected: test mới FAIL (aov cũ = confirmed/orders = 750000).

- [ ] **Step 3: Implement**

`sale-report-selectors.ts`:

1. Trong `toRow`, đổi `aov: divide(acc.confirmedRevenue, acc.orders)` thành:

```typescript
    aov: divide(acc.confirmedRevenue + acc.closedRevenue, acc.orders),
```

2. Cập nhật comment của type `SaleMetricRow`:

```typescript
  /** Doanh thu chốt đơn — phần sale tư vấn thêm (giá trị cuối − tiền khách tự đăng ký). */
  closedRevenue: number;
  /** Doanh thu xác nhận đơn — tiền khách TỰ đăng ký trên landing lúc đơn đổ về. */
  confirmedRevenue: number;
  ...
  /** AOV = giá trị thực đơn chốt (xác nhận + chốt) / số đơn. */
  aov: number | null;
```

`SaleMetricTable.tsx` — thêm chú thích ngay sau `</table>` (trong div overflow):

```tsx
      </table>
      <p className="mt-2 text-xs text-muted-foreground">
        {t(
          "saleReport.revenueNote",
          "Xác nhận đơn = tiền khách tự đăng ký trên landing lúc đơn đổ về; Chốt đơn = phần sale tư vấn thêm. Đơn không qua landing (tạo tay, fanpage) tính toàn bộ vào Chốt đơn.",
        )}
      </p>
```

- [ ] **Step 4: Chạy lại test, xác nhận pass**

Cùng lệnh Step 2 (chạy cả `SaleMetricTable.test.tsx` trong cùng thư mục). Expected: PASS; nếu snapshot/text assertion của `SaleMetricTable.test.tsx` vỡ vì thêm chú thích thì cập nhật assertion theo UI mới.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/sale-report
git commit -m "feat: update sale report aov and revenue notes"
```

---

### Task 6: Verify toàn cục + push + MR

**Files:** không sửa code mới (chỉ fix nếu verify lòi lỗi).

- [ ] **Step 1: Ruff**

```bash
cd "c:\Users\MYPC\Desktop\workspace\desi-order\.claude\worktrees\feat-landing-confirmed-revenue"
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

Expected: sạch (hoặc chỉ nợ cũ đã ignore trong pyproject).

- [ ] **Step 2: Backend — chạy các file test đã đụng**

Lệnh Global Constraints, `POSTGRES_DB=test_lcr_final`, `<paths>` =
`apps/orders/tests/test_ingest_api.py apps/orders/tests/test_digital_and_sale_staff_reports.py apps/orders/tests/test_backfill_landing_confirmed_amount.py apps/orders/tests/test_dashboard_summary_api.py apps/orders/tests/test_sale_source_closed_revenue.py apps/orders/tests/test_order_duplicate_helper.py`.
Expected: PASS toàn bộ (2 file cuối là láng giềng dùng chung repository/clone — canh hồi quy).

- [ ] **Step 3: Frontend — vitest sale-report + dashboard tabs**

```powershell
cd "...\feat-landing-confirmed-revenue\frontend"; npx vitest run src/features/sale-report src/pages/__tests__/DashboardPage.tabs.test.tsx
```

Expected: PASS (lưu ý `RequireAuth.test.tsx` đỏ sẵn trên master — không thuộc phạm vi).

- [ ] **Step 4: Push + MR**

```bash
git push -u origin feat/landing-confirmed-revenue
```

Lấy link MR từ output push (GitLab in sẵn "create merge request") và gửi cho user, kèm ghi chú vận hành: sau khi merge + deploy, chạy tay `python manage.py backfill_landing_confirmed_amount` (dry-run trước, `--apply` giờ thấp điểm).
