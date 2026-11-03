# Customer Portrait Dashboard Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mở rộng dashboard `/customers/report` với đầy đủ tiêu chí chân dung khách hàng (nhân khẩu, sức khỏe/nhu cầu, nguồn, sale, độ phủ dữ liệu) tổ chức theo section, cộng 3 phân tích chéo (cross-tab).

**Architecture:** Refactor `CustomerReportService.summary()` theo cấu hình khai báo (config-driven) để sinh phân bố 1 chiều, thêm các trường đặc biệt (gender/province/potential/used_massage/brand) + độ phủ dữ liệu, và 3 cross-tab ở module riêng. Frontend tổ chức trang theo `<ReportSection>`, thêm `<DistributionTable>` và `<StackedBarChart>`.

**Tech Stack:** Django + DRF (ORM aggregation, Python Counter), React 18 + Vite SPA, Recharts 2.13.

**Spec:** `docs/superpowers/specs/2026-05-26-customer-portrait-dashboard-expansion-design.md`

**Nguyên tắc đếm:** trường đơn trị → theo đơn (rỗng/None → `unknown`); trường mảng → theo lượt; phân khúc gán theo khách; tỉnh top 10 + "Khác". **Counter keys luôn là chuỗi** (tránh lỗi so sánh int↔str khi sort).

---

## Backend test runner (pytest hỏng — dùng Django runner)

Interpreter là venv repo-root; từ thư mục `backend` gọi `../.venv/bin/python`. Cần Postgres + Redis tạm:

```bash
docker run -d --name desi-test-pg -e POSTGRES_DB=desi_order -e POSTGRES_USER=desi_order -e POSTGRES_PASSWORD=desi_order -p 5432:5432 public.ecr.aws/docker/library/postgres:16-alpine
docker run -d --name desi-test-redis -p 6379:6379 public.ecr.aws/docker/library/redis:7-alpine redis-server --requirepass 'Desi@2026'
```

Lệnh test:
```bash
cd /Users/desi/Desktop/workspace/desi-order/backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 REDIS_PASSWORD='Desi@2026' ../.venv/bin/python manage.py test <dotted.path> -v2
```

Frontend: `cd /Users/desi/Desktop/workspace/desi-order/frontend && npx tsc --noEmit` và `npm run build`.

---

## File Structure

**Backend:**
- Create `apps/customers/report_fields.py` — config `CODE_FIELDS`, `ARRAY_FIELDS`, label maps, helpers.
- Modify `apps/customers/report_service.py` — refactor `summary()` dùng config + trường đặc biệt + completeness; orchestrate cross-tab.
- Create `apps/customers/report_crosstabs.py` — 3 hàm cross-tab (Phase 2).
- Modify tests: `test_customer_report_service.py`, `test_customer_report_api.py`; create `test_report_crosstabs.py`.

**Frontend:**
- Modify `lib/api/customers.ts` — mở rộng `CustomerReportSummary` + type `CrossTabData`.
- Create `components/report/ReportSection.tsx`, `components/report/DistributionTable.tsx`, `components/report/StackedBarChart.tsx` (hoặc đặt trong `pages/` nếu codebase chưa có `components/report` — kiểm tra trước).
- Modify `pages/CustomerReportPage.tsx` — tổ chức theo section + chart mới.

---

# PHASE 1 — Phân bố 1 chiều + bố cục section + độ phủ

## Task 1: Config trường phân bố (`report_fields.py`)

**Files:**
- Create: `backend/apps/customers/report_fields.py`
- Test: `backend/apps/customers/tests/test_report_fields.py`

Bối cảnh: `apps/orders/crm_catalog.py` có sẵn các `*_LABELS` (dict code→nhãn) và `*_OPTIONS` (list `CatalogOption(code,label)`) cho: OCCUPATION, CUSTOMER_CLASSIFICATION, PAIN_LEVEL, PAIN_DURATION, NOT_CLOSED_REASON, CALL_DURATION, POST_PURCHASE_RATING, LIFESTYLE_HABIT, RELAX_TIME, DELAX_SOURCE, PURCHASE_BARRIER, USED_MASSAGE_BEFORE; và `POTENTIAL_LEVEL_LABELS` (dict int→nhãn).

- [ ] **Step 1: Viết test thất bại**

```python
# backend/apps/customers/tests/test_report_fields.py
from django.test import SimpleTestCase

from apps.customers.report_fields import (
    ARRAY_FIELDS,
    CODE_FIELDS,
    UNKNOWN_CODE,
    UNKNOWN_LABEL,
)


class ReportFieldsTests(SimpleTestCase):
    def test_code_fields_have_unknown_label(self):
        for f in CODE_FIELDS:
            self.assertEqual(f.label_map[UNKNOWN_CODE], UNKNOWN_LABEL)
            self.assertTrue(f.value_key.startswith("assessment__"))
            self.assertTrue(f.key.endswith("_distribution"))

    def test_array_fields_marked(self):
        keys = {f.key for f in ARRAY_FIELDS}
        self.assertIn("delax_source_distribution", keys)
        self.assertIn("lifestyle_distribution", keys)
        for f in ARRAY_FIELDS:
            self.assertTrue(f.is_array)

    def test_fixed_order_non_empty(self):
        for f in CODE_FIELDS + ARRAY_FIELDS:
            self.assertGreater(len(f.fixed_order), 0)
```

- [ ] **Step 2: Run, confirm FAIL** (`ModuleNotFoundError: report_fields`).
Run: `cd /Users/desi/Desktop/workspace/desi-order/backend && ../.venv/bin/python manage.py test apps.customers.tests.test_report_fields -v2`

- [ ] **Step 3: Implement**

```python
# backend/apps/customers/report_fields.py
"""Cấu hình các trường phân bố cho dashboard chân dung khách hàng."""
from __future__ import annotations

from dataclasses import dataclass, field

from apps.orders import crm_catalog as cat

UNKNOWN_CODE = "unknown"
UNKNOWN_LABEL = "Chưa xác định"

# Nhãn cố định không có sẵn trong crm_catalog.
GENDER_LABELS = {"0": "Nữ", "1": "Nam"}


def codes(options) -> tuple[str, ...]:
    """Thứ tự code theo danh mục (CatalogOption.code)."""
    return tuple(o.code for o in options)


def with_unknown(label_map: dict) -> dict:
    """Chuẩn hóa key về str và thêm nhãn 'Chưa xác định'."""
    return {**{str(k): v for k, v in label_map.items()}, UNKNOWN_CODE: UNKNOWN_LABEL}


@dataclass(frozen=True)
class DistField:
    key: str             # key trả về trong response
    value_key: str       # key trong dict .values()
    label_map: dict
    is_array: bool = False
    fixed_order: tuple = field(default_factory=tuple)


# Trường mã đơn trị — đếm theo đơn.
CODE_FIELDS = [
    DistField("occupation_distribution", "assessment__occupation_code",
              with_unknown(cat.OCCUPATION_LABELS), fixed_order=codes(cat.OCCUPATION_OPTIONS)),
    DistField("classification_distribution", "assessment__customer_classification_code",
              with_unknown(cat.CUSTOMER_CLASSIFICATION_LABELS), fixed_order=codes(cat.CUSTOMER_CLASSIFICATION_OPTIONS)),
    DistField("pain_level_distribution", "assessment__pain_level_code",
              with_unknown(cat.PAIN_LEVEL_LABELS), fixed_order=codes(cat.PAIN_LEVEL_OPTIONS)),
    DistField("pain_duration_distribution", "assessment__pain_duration_code",
              with_unknown(cat.PAIN_DURATION_LABELS), fixed_order=codes(cat.PAIN_DURATION_OPTIONS)),
    DistField("not_closed_reason_distribution", "assessment__not_closed_reason_code",
              with_unknown(cat.NOT_CLOSED_REASON_LABELS), fixed_order=codes(cat.NOT_CLOSED_REASON_OPTIONS)),
    DistField("call_duration_distribution", "assessment__call_duration_code",
              with_unknown(cat.CALL_DURATION_LABELS), fixed_order=codes(cat.CALL_DURATION_OPTIONS)),
    DistField("feedback_rating_distribution", "assessment__post_purchase_rating_code",
              with_unknown(cat.POST_PURCHASE_RATING_LABELS), fixed_order=codes(cat.POST_PURCHASE_RATING_OPTIONS)),
]

# Trường mảng — đếm theo lượt.
ARRAY_FIELDS = [
    DistField("lifestyle_distribution", "assessment__lifestyle_habit_codes",
              dict(cat.LIFESTYLE_HABIT_LABELS), is_array=True, fixed_order=codes(cat.LIFESTYLE_HABIT_OPTIONS)),
    DistField("relax_time_distribution", "assessment__relax_time_codes",
              dict(cat.RELAX_TIME_LABELS), is_array=True, fixed_order=codes(cat.RELAX_TIME_OPTIONS)),
    DistField("delax_source_distribution", "assessment__delax_source_codes",
              dict(cat.DELAX_SOURCE_LABELS), is_array=True, fixed_order=codes(cat.DELAX_SOURCE_OPTIONS)),
    DistField("barrier_distribution", "assessment__purchase_barrier_codes",
              dict(cat.PURCHASE_BARRIER_LABELS), is_array=True, fixed_order=codes(cat.PURCHASE_BARRIER_OPTIONS)),
]
```

- [ ] **Step 4: Run, confirm PASS** (3 tests OK).
- [ ] **Step 5: Commit**
```bash
cd /Users/desi/Desktop/workspace/desi-order && git add backend/apps/customers/report_fields.py backend/apps/customers/tests/test_report_fields.py && git commit -m "Add report field config for customer portrait dashboard"
```

---

## Task 2: Refactor `summary()` dùng config + trường đặc biệt

**Files:**
- Modify: `backend/apps/customers/report_service.py` (toàn bộ method `summary` + thêm helper)
- Test: `backend/apps/customers/tests/test_customer_report_service.py`

Lưu ý: giữ nguyên `_segment_map`, `_distribution`, `_feedback_samples`. Việc cần: thay phần thân `summary` cũ bằng bản config-driven; `barrier`/`feedback_rating` giờ do config sinh (bỏ counter thủ công cũ). `need_type_distribution` giữ query OrderNeed như cũ.

- [ ] **Step 1: Cập nhật test (thêm assertion trường mới)**

Mở `backend/apps/customers/tests/test_customer_report_service.py`. Sửa factory `_make_order` để nhận thêm field, và bổ sung 1 test mới. Thay signature + body assessment:

```python
    def _make_order(self, *, customer, channel=None, status=3, created_at=None,
                    age_text="", occupation_code=None, classification_code=None, rating_code=None,
                    barrier_codes=None, feedback_text="", total=Decimal("0"), needs=False,
                    province="", gender=None, potential_level=None, used_massage=None,
                    pain_level_code=None, lifestyle_codes=None, relax_codes=None,
                    source_codes=None, not_closed_reason_code=None, call_duration_code=None,
                    comparing_brand_text="", sales_status_code=None):
        if gender is not None:
            customer.gender = gender
            customer.save(update_fields=["gender"])
        order = Order.objects.create(
            customer=customer, channel=channel or self.channel, status=status,
            total_after_discount=total, province=province,
        )
        if created_at is not None:
            Order.objects.filter(pk=order.pk).update(created_at=created_at)
            order.refresh_from_db()
        assessment = OrderAssessment.objects.create(
            order=order, age_text=age_text, occupation_code=occupation_code,
            customer_classification_code=classification_code,
            post_purchase_rating_code=rating_code,
            purchase_barrier_codes=barrier_codes or [],
            post_purchase_feedback_text=feedback_text,
            potential_level=potential_level, used_massage_before=used_massage,
            pain_level_code=pain_level_code,
            lifestyle_habit_codes=lifestyle_codes or [],
            relax_time_codes=relax_codes or [],
            delax_source_codes=source_codes or [],
            not_closed_reason_code=not_closed_reason_code,
            call_duration_code=call_duration_code,
            comparing_brand_text=comparing_brand_text,
            sales_status_code=sales_status_code,
        )
        if needs:
            OrderNeed.objects.create(order_assessment=assessment, need_type=self.ntype)
        return order
```

Thêm test mới (cuối lớp test):

```python
    def test_new_portrait_distributions(self):
        c1 = Customer.objects.create(full_name="P1")
        c2 = Customer.objects.create(full_name="P2")
        self._make_order(customer=c1, gender=1, province="Hà Nội",
                         potential_level=5, used_massage=True, pain_level_code="severe",
                         lifestyle_codes=["sit_a_lot", "low_activity"], relax_codes=["evening"],
                         source_codes=["facebook", "tiktok"], not_closed_reason_code="too_expensive",
                         call_duration_code="3_to_5m", comparing_brand_text="Mar6")
        self._make_order(customer=c2, gender=0, province="Hà Nội",
                         potential_level=3, used_massage=False, pain_level_code="severe",
                         lifestyle_codes=["sit_a_lot"], source_codes=["facebook"],
                         comparing_brand_text="mar6")

        data = CustomerReportService.summary()

        gender = {r["code"]: r["count"] for r in data["gender_distribution"]}
        self.assertEqual(gender["1"], 1)
        self.assertEqual(gender["0"], 1)

        prov = {r["code"]: r["count"] for r in data["province_distribution"]}
        self.assertEqual(prov["Hà Nội"], 2)

        pot = {r["code"]: r["count"] for r in data["potential_distribution"]}
        self.assertEqual(pot["5"], 1)
        self.assertEqual(pot["3"], 1)

        used = {r["code"]: r["count"] for r in data["used_massage_distribution"]}
        self.assertEqual(used["yes"], 1)
        self.assertEqual(used["no"], 1)

        pain = {r["code"]: r["count"] for r in data["pain_level_distribution"]}
        self.assertEqual(pain["severe"], 2)

        # Mảng đếm theo lượt.
        life = {r["code"]: r["count"] for r in data["lifestyle_distribution"]}
        self.assertEqual(life["sit_a_lot"], 2)
        self.assertEqual(life["low_activity"], 1)

        source = {r["code"]: r["count"] for r in data["delax_source_distribution"]}
        self.assertEqual(source["facebook"], 2)
        self.assertEqual(source["tiktok"], 1)

        # Brand chuẩn hóa lower → "mar6" gộp 2.
        brand = {r["value"].lower(): r["count"] for r in data["comparing_brand_top"]}
        self.assertEqual(brand["mar6"], 2)
```

- [ ] **Step 2: Run, confirm FAIL** (KeyError / missing keys).
Run: `cd /Users/desi/Desktop/workspace/desi-order/backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 REDIS_PASSWORD='Desi@2026' ../.venv/bin/python manage.py test apps.customers.tests.test_customer_report_service.CustomerReportServiceTests.test_new_portrait_distributions -v2`

- [ ] **Step 3: Refactor `summary()`**

Trong `report_service.py`: cập nhật imports và thay method `summary`. Imports mới (thêm vào khối hiện có):

```python
from apps.orders.crm_catalog import (
    POST_PURCHASE_RATING_LABELS,
    POTENTIAL_LEVEL_LABELS,
    USED_MASSAGE_BEFORE_LABELS,
)
from .report_fields import (
    ARRAY_FIELDS,
    CODE_FIELDS,
    GENDER_LABELS,
    UNKNOWN_CODE,
    UNKNOWN_LABEL,
    with_unknown,
)
```

(Xóa các import label cũ không còn dùng trực tiếp nếu trùng: `OCCUPATION_LABELS`, `CUSTOMER_CLASSIFICATION_LABELS`, `PURCHASE_BARRIER_LABELS` giờ nằm trong config — có thể bỏ khỏi import của service. Giữ `POST_PURCHASE_RATING_LABELS` vì `_feedback_samples` dùng. Bỏ định nghĩa `UNKNOWN_CODE`/`UNKNOWN_LABEL` cũ ở đầu file vì đã import từ `report_fields`.)

Thay toàn bộ method `summary` bằng:

```python
    BRAND_TOP_N = 15

    @staticmethod
    def summary(*, date_from: str | None = None, date_to: str | None = None,
                channel: str | None = None, segment: str | None = None) -> dict:
        base = (
            Order.objects.filter(deleted_at__isnull=True, assessment__isnull=False)
            .exclude(branch_id__in=EXCLUDED_BRANCH_IDS)
        )
        base = OrderRepository._apply_created_at_date_filters(base, date_from, date_to)
        if channel:
            base = base.filter(channel_id=channel)

        customer_ids = list(base.values_list("customer_id", flat=True).distinct())
        segment_by_customer = CustomerReportService._segment_map(customer_ids)
        if segment:
            allowed = {cid for cid, code in segment_by_customer.items() if code == segment}
            base = base.filter(customer_id__in=allowed)

        special_keys = [
            "id", "customer_id", "province", "customer__gender",
            "assessment__age_text", "assessment__used_massage_before",
            "assessment__potential_level", "assessment__comparing_brand_text",
            "assessment__sales_status_code",
        ]
        config_keys = [f.value_key for f in CODE_FIELDS + ARRAY_FIELDS]
        rows = list(base.values(*special_keys, *config_keys))
        total_records = len(rows)
        total_customers = len({r["customer_id"] for r in rows})

        counters = {f.key: Counter() for f in CODE_FIELDS + ARRAY_FIELDS}
        seg_counter: Counter = Counter()
        age_counter: Counter = Counter()
        gender_counter: Counter = Counter()
        province_counter: Counter = Counter()
        potential_counter: Counter = Counter()
        used_counter: Counter = Counter()
        brand_counter: Counter = Counter()
        brand_display: dict[str, str] = {}
        for r in rows:
            seg_counter[segment_by_customer.get(r["customer_id"]) or UNKNOWN_CODE] += 1
            age_counter[bucket_age(r["assessment__age_text"])] += 1
            g = r["customer__gender"]
            gender_counter[str(g) if g is not None else UNKNOWN_CODE] += 1
            prov = (r["province"] or "").strip()
            province_counter[prov or UNKNOWN_CODE] += 1
            pl = r["assessment__potential_level"]
            potential_counter[str(pl) if pl else UNKNOWN_CODE] += 1
            um = r["assessment__used_massage_before"]
            used_counter["yes" if um is True else "no" if um is False else UNKNOWN_CODE] += 1
            brand = (r["assessment__comparing_brand_text"] or "").strip()
            if brand:
                norm = brand.lower()
                brand_counter[norm] += 1
                brand_display.setdefault(norm, brand)
            for f in CODE_FIELDS:
                counters[f.key][r[f.value_key] or UNKNOWN_CODE] += 1
            for f in ARRAY_FIELDS:
                for code in (r[f.value_key] or []):
                    if code:
                        counters[f.key][code] += 1

        segment_labels = dict(CustomerSegmentCategory.objects.values_list("code", "name"))
        segment_labels[UNKNOWN_CODE] = UNKNOWN_LABEL

        result = {
            "totals": {"total_records": total_records, "total_customers": total_customers},
            "segment_distribution": CustomerReportService._distribution(
                seg_counter, segment_labels, total_records,
                fixed_order=[item["code"] for item in segmentation.SEGMENT_CATALOG_SEED],
            ),
            "age_distribution": CustomerReportService._distribution(
                age_counter, AGE_BUCKET_LABELS, total_records, fixed_order=AGE_BUCKET_ORDER,
            ),
            "gender_distribution": CustomerReportService._distribution(
                gender_counter, {**GENDER_LABELS, UNKNOWN_CODE: UNKNOWN_LABEL}, total_records,
                fixed_order=["0", "1"],
            ),
            "potential_distribution": CustomerReportService._distribution(
                potential_counter, with_unknown(POTENTIAL_LEVEL_LABELS), total_records,
                fixed_order=["1", "2", "3", "4", "5"],
            ),
            "used_massage_distribution": CustomerReportService._distribution(
                used_counter, {**USED_MASSAGE_BEFORE_LABELS, UNKNOWN_CODE: UNKNOWN_LABEL}, total_records,
                fixed_order=["yes", "no"],
            ),
            "province_distribution": CustomerReportService._province_distribution(
                province_counter, total_records,
            ),
            "need_type_distribution": CustomerReportService._need_distribution(
                [r["id"] for r in rows], total_records,
            ),
            "comparing_brand_top": [
                {"value": brand_display[norm], "count": count}
                for norm, count in brand_counter.most_common(CustomerReportService.BRAND_TOP_N)
            ],
            "feedback_samples": CustomerReportService._feedback_samples(base),
            "completeness": CustomerReportService._completeness(rows, total_records),
        }
        for f in CODE_FIELDS:
            result[f.key] = CustomerReportService._distribution(
                counters[f.key], f.label_map, total_records, fixed_order=list(f.fixed_order),
            )
        for f in ARRAY_FIELDS:
            occ_total = sum(counters[f.key].values())
            result[f.key] = CustomerReportService._distribution(
                counters[f.key], f.label_map, occ_total, fixed_order=list(f.fixed_order),
            )
        return result
```

Thêm 3 helper mới vào lớp (đặt cạnh `_distribution`):

```python
    @staticmethod
    def _province_distribution(counter: Counter, total: int, top_n: int = 10) -> list[dict]:
        def pct(c: int) -> float:
            return round(c * 100 / total, 1) if total else 0.0

        unknown = counter.pop(UNKNOWN_CODE, 0)
        ranked = counter.most_common()
        items = [
            {"code": name, "label": name, "count": c, "percentage": pct(c)}
            for name, c in ranked[:top_n]
        ]
        rest = sum(c for _, c in ranked[top_n:])
        if rest:
            items.append({"code": "__other__", "label": "Khác", "count": rest, "percentage": pct(rest)})
        if unknown:
            items.append({"code": UNKNOWN_CODE, "label": UNKNOWN_LABEL, "count": unknown, "percentage": pct(unknown)})
        return items

    @staticmethod
    def _need_distribution(order_ids: list[int], total: int) -> list[dict]:
        need_counter: Counter = Counter()
        need_labels: dict[str, str] = {}
        if order_ids:
            need_rows = (
                OrderNeed.objects.filter(order_assessment__order_id__in=order_ids)
                .values("order_assessment__order_id", "need_type__code", "need_type__name_vi")
                .distinct()
            )
            for nr in need_rows:
                code = nr["need_type__code"]
                need_counter[code] += 1
                need_labels[code] = nr["need_type__name_vi"]
        return CustomerReportService._distribution(need_counter, need_labels, total)

    @staticmethod
    def _completeness(rows: list[dict], total: int) -> list[dict]:
        specs = [
            ("assessment__age_text", "Tuổi", "text"),
            ("customer__gender", "Giới tính", "scalar"),
            ("assessment__occupation_code", "Nghề nghiệp", "scalar"),
            ("assessment__pain_level_code", "Mức độ đau", "scalar"),
            ("assessment__potential_level", "Mức tiềm năng", "scalar"),
            ("assessment__delax_source_codes", "Nguồn biết đến", "array"),
            ("assessment__post_purchase_rating_code", "Phản hồi SP", "scalar"),
            ("assessment__customer_classification_code", "Phân loại KH", "scalar"),
        ]
        out = []
        for value_key, label, kind in specs:
            filled = 0
            for r in rows:
                v = r.get(value_key)
                if kind == "array":
                    ok = bool(v)
                elif kind == "text":
                    ok = bool((v or "").strip())
                else:
                    ok = v is not None and v != ""
                if ok:
                    filled += 1
            out.append({
                "field": value_key, "label": label, "filled": filled, "total": total,
                "percentage": round(filled * 100 / total, 1) if total else 0.0,
            })
        return out
```

Lưu ý: phần `need_type_distribution` cũ trong method bị thay bằng gọi `_need_distribution`. Đảm bảo không còn code `need_counter`/`need_rows` lặp trong `summary`.

- [ ] **Step 4: Run test mới + test cũ của service, confirm PASS**
Run: `cd /Users/desi/Desktop/workspace/desi-order/backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 REDIS_PASSWORD='Desi@2026' ../.venv/bin/python manage.py test apps.customers.tests.test_customer_report_service -v2`
Expected: tất cả PASS (gồm các test cũ: occupation/age/segment/barrier/need/feedback vẫn đúng).

- [ ] **Step 5: Commit**
```bash
cd /Users/desi/Desktop/workspace/desi-order && git add backend/apps/customers/report_service.py backend/apps/customers/tests/test_customer_report_service.py && git commit -m "Refactor report summary with config-driven portrait distributions"
```

---

## Task 3: Cập nhật API test shape

**Files:**
- Test: `backend/apps/customers/tests/test_customer_report_api.py`

- [ ] **Step 1: Cập nhật danh sách key kiểm tra**

Thay khối `for key in (...)` trong `test_admin_gets_summary_shape` thành:

```python
        for key in (
            "totals", "segment_distribution", "age_distribution", "gender_distribution",
            "province_distribution", "occupation_distribution", "classification_distribution",
            "potential_distribution", "pain_level_distribution", "pain_duration_distribution",
            "lifestyle_distribution", "used_massage_distribution", "relax_time_distribution",
            "delax_source_distribution", "need_type_distribution", "barrier_distribution",
            "not_closed_reason_distribution", "call_duration_distribution",
            "feedback_rating_distribution", "comparing_brand_top", "feedback_samples",
            "completeness",
        ):
            self.assertIn(key, resp.data)
```

- [ ] **Step 2: Run, confirm PASS**
Run: `cd /Users/desi/Desktop/workspace/desi-order/backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 REDIS_PASSWORD='Desi@2026' ../.venv/bin/python manage.py test apps.customers.tests.test_customer_report_api -v2`

- [ ] **Step 3: Commit**
```bash
cd /Users/desi/Desktop/workspace/desi-order && git add backend/apps/customers/tests/test_customer_report_api.py && git commit -m "Assert new portrait keys in report API shape test"
```

---

## Task 4: Frontend API types (Phase 1 keys)

**Files:**
- Modify: `frontend/src/lib/api/customers.ts`

- [ ] **Step 1: Mở rộng `CustomerReportSummary`**

Thêm interface `ReportBrandItem` và `ReportCompletenessItem`, và thêm các field vào `CustomerReportSummary` (giữ các field cũ). Thay khối interface `CustomerReportSummary` hiện có bằng:

```typescript
export interface ReportBrandItem {
  value: string;
  count: number;
}

export interface ReportCompletenessItem {
  field: string;
  label: string;
  filled: number;
  total: number;
  percentage: number;
}

export interface CustomerReportSummary {
  totals: { total_records: number; total_customers: number };
  segment_distribution: ReportDistributionItem[];
  age_distribution: ReportDistributionItem[];
  gender_distribution: ReportDistributionItem[];
  province_distribution: ReportDistributionItem[];
  occupation_distribution: ReportDistributionItem[];
  classification_distribution: ReportDistributionItem[];
  potential_distribution: ReportDistributionItem[];
  pain_level_distribution: ReportDistributionItem[];
  pain_duration_distribution: ReportDistributionItem[];
  lifestyle_distribution: ReportDistributionItem[];
  used_massage_distribution: ReportDistributionItem[];
  relax_time_distribution: ReportDistributionItem[];
  delax_source_distribution: ReportDistributionItem[];
  need_type_distribution: ReportDistributionItem[];
  barrier_distribution: ReportDistributionItem[];
  not_closed_reason_distribution: ReportDistributionItem[];
  call_duration_distribution: ReportDistributionItem[];
  feedback_rating_distribution: ReportDistributionItem[];
  comparing_brand_top: ReportBrandItem[];
  feedback_samples: ReportFeedbackSample[];
  completeness: ReportCompletenessItem[];
}
```

- [ ] **Step 2: Verify compile**
Run: `cd /Users/desi/Desktop/workspace/desi-order/frontend && npx tsc --noEmit 2>&1 | grep "customers.ts" || echo "no customers.ts errors"`

- [ ] **Step 3: Commit**
```bash
cd /Users/desi/Desktop/workspace/desi-order && git add frontend/src/lib/api/customers.ts && git commit -m "Extend customer report summary types with portrait fields"
```

---

## Task 5: Frontend — bố cục section + chart mới

**Files:**
- Create: `frontend/src/components/report/ReportSection.tsx`
- Create: `frontend/src/components/report/DistributionTable.tsx`
- Modify: `frontend/src/pages/CustomerReportPage.tsx`

Bối cảnh: `CustomerReportPage.tsx` đã có 2 component cục bộ `DistributionBar` và `DistributionDonut`, KPI cards, filter bar, panel feedback. Giữ nguyên chúng. Việc cần: (a) tạo `ReportSection` (heading + lưới), `DistributionTable` (bảng value/count); (b) tổ chức lại phần render các chart thành các section theo spec; (c) thêm các chart mới dùng đúng key API mới.

- [ ] **Step 1: Tạo `ReportSection`**

```tsx
// frontend/src/components/report/ReportSection.tsx
import type { ReactNode } from "react";

export function ReportSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-slate-800">{title}</h2>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">{children}</div>
    </section>
  );
}
```

- [ ] **Step 2: Tạo `DistributionTable`** (cho brand-top + completeness)

```tsx
// frontend/src/components/report/DistributionTable.tsx
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export interface TableRow {
  label: string;
  count: number;
  extra?: string;
}

export function DistributionTable({
  title,
  rows,
  countHeader = "Số lượng",
  note,
}: {
  title: string;
  rows: TableRow[];
  countHeader?: string;
  note?: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {note ? <p className="text-xs text-muted-foreground">{note}</p> : null}
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">Không có dữ liệu</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="py-1 font-medium">Giá trị</th>
                <th className="py-1 text-right font-medium">{countHeader}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.label}-${i}`} className="border-t">
                  <td className="py-1.5">{r.label}</td>
                  <td className="py-1.5 text-right tabular-nums">
                    {r.count.toLocaleString("vi-VN")}
                    {r.extra ? <span className="ml-1 text-muted-foreground">{r.extra}</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 3: Tổ chức lại render trong `CustomerReportPage`**

Thêm import:
```tsx
import { ReportSection } from "@/components/report/ReportSection";
import { DistributionTable } from "@/components/report/DistributionTable";
```

Thay khối `<div className="grid grid-cols-1 gap-4 lg:grid-cols-2"> ... </div>` (lưới chart hiện tại) bằng các section sau (đặt `s` = `summary`):

```tsx
      <ReportSection title="Nhân khẩu học">
        <DistributionDonut title="Giới tính" data={summary?.gender_distribution ?? []} />
        <DistributionBar title="Độ tuổi" data={summary?.age_distribution ?? []} />
        <DistributionDonut title="Nghề nghiệp" data={summary?.occupation_distribution ?? []} />
        <DistributionBar title="Tỉnh/thành (Top 10)" data={summary?.province_distribution ?? []} horizontal />
      </ReportSection>

      <ReportSection title="Phân khúc & phân loại">
        <DistributionDonut title="Phân khúc khách hàng" data={summary?.segment_distribution ?? []} />
        <DistributionBar title="Phân loại khách hàng" data={summary?.classification_distribution ?? []} horizontal />
        <DistributionBar title="Mức tiềm năng (1-5)" data={summary?.potential_distribution ?? []} />
      </ReportSection>

      <ReportSection title="Sức khỏe & nhu cầu">
        <DistributionBar title="Mức độ đau" data={summary?.pain_level_distribution ?? []} />
        <DistributionBar title="Thời gian bị đau" data={summary?.pain_duration_distribution ?? []} />
        <DistributionBar title="Thói quen sinh hoạt" data={summary?.lifestyle_distribution ?? []} horizontal note="Đếm theo lượt." />
        <DistributionDonut title="Đã từng dùng massage" data={summary?.used_massage_distribution ?? []} />
        <DistributionBar title="Thời điểm thư giãn" data={summary?.relax_time_distribution ?? []} horizontal note="Đếm theo lượt." />
        <DistributionBar title="Mục đích mua (nhu cầu chi tiết)" data={summary?.need_type_distribution ?? []} horizontal />
      </ReportSection>

      <ReportSection title="Nguồn & marketing">
        <DistributionBar title="Nguồn biết đến sản phẩm" data={summary?.delax_source_distribution ?? []} horizontal note="Đếm theo lượt." />
      </ReportSection>

      <ReportSection title="Sale & rào cản">
        <DistributionBar title="Rào cản mua hàng" data={summary?.barrier_distribution ?? []} horizontal note="Đếm theo lượt." />
        <DistributionBar title="Lý do không chốt" data={summary?.not_closed_reason_distribution ?? []} horizontal />
        <DistributionBar title="Thời lượng cuộc gọi" data={summary?.call_duration_distribution ?? []} />
        <DistributionTable
          title="Thương hiệu đang so sánh (Top)"
          rows={(summary?.comparing_brand_top ?? []).map((b) => ({ label: b.value, count: b.count }))}
          countHeader="Lượt nhắc"
        />
      </ReportSection>

      <ReportSection title="Phản hồi sau mua">
        <DistributionDonut title="Phản hồi về sản phẩm" data={summary?.feedback_rating_distribution ?? []} />
      </ReportSection>

      <ReportSection title="Độ phủ dữ liệu chân dung">
        <DistributionTable
          title="% đơn đã nhập từng trường"
          rows={(summary?.completeness ?? []).map((c) => ({
            label: c.label,
            count: c.filled,
            extra: `(${c.percentage}%)`,
          }))}
          countHeader="Đã nhập"
          note="Trên tổng số đơn có đánh giá trong kỳ."
        />
      </ReportSection>
```

Giữ panel "Phản hồi gần đây của khách" (feedback_samples) như cũ, đặt ngay sau các section (hoặc trong section "Phản hồi sau mua" — tùy, miễn hợp lệ).

- [ ] **Step 4: Đối chiếu trước khi tin** — xác nhận `Card`/`CardHeader`/`CardContent`/`CardTitle` export từ `@/components/ui/card`; `DistributionBar`/`DistributionDonut` vẫn nằm trong `CustomerReportPage.tsx` và nhận props `{title, data, horizontal?, note?}`. Nếu các component này được tách ra file khác trong các task trước thì import cho đúng.

- [ ] **Step 5: Verify compile + build**
Run: `cd /Users/desi/Desktop/workspace/desi-order/frontend && npx tsc --noEmit 2>&1 | grep -E "CustomerReportPage|ReportSection|DistributionTable" || echo "no errors"; npm run build 2>&1 | tail -5`
Expected: build thành công.

- [ ] **Step 6: Commit**
```bash
cd /Users/desi/Desktop/workspace/desi-order && git add frontend/src/components/report/ReportSection.tsx frontend/src/components/report/DistributionTable.tsx frontend/src/pages/CustomerReportPage.tsx && git commit -m "Organize customer report into themed sections with new portrait charts"
```

---

# PHASE 2 — Cross-tab (phân tích chéo)

## Task 6: `report_crosstabs.py` (3 cross-tab)

**Files:**
- Create: `backend/apps/customers/report_crosstabs.py`
- Test: `backend/apps/customers/tests/test_report_crosstabs.py`

Định dạng trả về mỗi cross-tab:
```python
{"rows": [{"code","label"}...], "columns": [{"code","label"}...], "cells": {row_code: {col_code: count}}}
```

Hàm nhận dữ liệu đã có sẵn từ `summary` để tránh query lại:
- `rows_data`: list dict từ `base.values(...)` (đã có `id`, `customer_id`, `assessment__pain_level_code`, `assessment__age_text`, `assessment__delax_source_codes`, `assessment__sales_status_code`).
- `segment_by_customer`: dict.

- [ ] **Step 1: Viết test thất bại**

```python
# backend/apps/customers/tests/test_report_crosstabs.py
from django.test import SimpleTestCase

from apps.customers.report_crosstabs import (
    pain_by_age_crosstab,
    source_by_close_crosstab,
)


class CrossTabTests(SimpleTestCase):
    def test_pain_by_age(self):
        rows_data = [
            {"assessment__pain_level_code": "severe", "assessment__age_text": "30"},
            {"assessment__pain_level_code": "severe", "assessment__age_text": "50"},
            {"assessment__pain_level_code": "light", "assessment__age_text": "30"},
        ]
        ct = pain_by_age_crosstab(rows_data)
        self.assertEqual(ct["cells"]["severe"]["18-30"], 1)
        self.assertEqual(ct["cells"]["severe"]["46-60"], 1)
        self.assertEqual(ct["cells"]["light"]["18-30"], 1)

    def test_source_by_close(self):
        rows_data = [
            {"assessment__delax_source_codes": ["facebook"], "assessment__sales_status_code": "closed"},
            {"assessment__delax_source_codes": ["facebook", "tiktok"], "assessment__sales_status_code": "not_buying"},
        ]
        ct = source_by_close_crosstab(rows_data)
        self.assertEqual(ct["cells"]["facebook"]["closed"], 1)
        self.assertEqual(ct["cells"]["facebook"]["not_closed"], 1)
        self.assertEqual(ct["cells"]["tiktok"]["not_closed"], 1)
```

- [ ] **Step 2: Run, confirm FAIL** (`ModuleNotFoundError: report_crosstabs`).
Run: `cd /Users/desi/Desktop/workspace/desi-order/backend && ../.venv/bin/python manage.py test apps.customers.tests.test_report_crosstabs -v2`

- [ ] **Step 3: Implement**

```python
# backend/apps/customers/report_crosstabs.py
"""Phân tích chéo (cross-tab) cho dashboard chân dung khách hàng."""
from __future__ import annotations

from collections import Counter, defaultdict

from apps.orders import crm_catalog as cat
from apps.orders._repositories_reports import CLOSED_SALES_STATUS_CODES
from apps.orders.models import OrderNeed

from .age_buckets import AGE_BUCKET_LABELS, AGE_BUCKET_ORDER, bucket_age
from .report_fields import UNKNOWN_CODE, UNKNOWN_LABEL


def _axis(codes_labels: list[tuple[str, str]]) -> list[dict]:
    return [{"code": c, "label": l} for c, l in codes_labels]


def _emptyset_cells() -> dict:
    return defaultdict(lambda: defaultdict(int))


def pain_by_age_crosstab(rows_data: list[dict]) -> dict:
    pain_labels = {**{o.code: o.label for o in cat.PAIN_LEVEL_OPTIONS}, UNKNOWN_CODE: UNKNOWN_LABEL}
    cells = _emptyset_cells()
    seen_rows: Counter = Counter()
    for r in rows_data:
        pain = r["assessment__pain_level_code"] or UNKNOWN_CODE
        age = bucket_age(r["assessment__age_text"])
        cells[pain][age] += 1
        seen_rows[pain] += 1
    row_codes = [c for c in list(pain_labels) if c in seen_rows]
    return {
        "rows": _axis([(c, pain_labels[c]) for c in row_codes]),
        "columns": _axis([(c, AGE_BUCKET_LABELS[c]) for c in AGE_BUCKET_ORDER]),
        "cells": {r: dict(cols) for r, cols in cells.items()},
    }


def source_by_close_crosstab(rows_data: list[dict]) -> dict:
    source_labels = {o.code: o.label for o in cat.DELAX_SOURCE_OPTIONS}
    cells = _emptyset_cells()
    seen: Counter = Counter()
    for r in rows_data:
        closed = (r["assessment__sales_status_code"] in CLOSED_SALES_STATUS_CODES)
        col = "closed" if closed else "not_closed"
        for code in (r["assessment__delax_source_codes"] or []):
            if code:
                cells[code][col] += 1
                seen[code] += 1
    row_codes = [c for c in source_labels if c in seen]
    return {
        "rows": _axis([(c, source_labels[c]) for c in row_codes]),
        "columns": _axis([("closed", "Đã chốt"), ("not_closed", "Chưa chốt")]),
        "cells": {r: dict(cols) for r, cols in cells.items()},
    }


def need_by_segment_crosstab(rows_data: list[dict], segment_by_customer: dict, segment_labels: dict) -> dict:
    order_to_customer = {r["id"]: r["customer_id"] for r in rows_data}
    order_ids = list(order_to_customer)
    cells = _emptyset_cells()
    need_labels: dict[str, str] = {}
    seg_seen: Counter = Counter()
    if order_ids:
        need_rows = (
            OrderNeed.objects.filter(order_assessment__order_id__in=order_ids)
            .values("order_assessment__order_id", "need_type__code", "need_type__name_vi")
            .distinct()
        )
        for nr in need_rows:
            oid = nr["order_assessment__order_id"]
            need_code = nr["need_type__code"]
            need_labels[need_code] = nr["need_type__name_vi"]
            seg = segment_by_customer.get(order_to_customer.get(oid)) or UNKNOWN_CODE
            cells[need_code][seg] += 1
            seg_seen[seg] += 1
    col_codes = [c for c in list(segment_labels) if c in seg_seen]
    return {
        "rows": _axis([(c, need_labels[c]) for c in need_labels]),
        "columns": _axis([(c, segment_labels.get(c, c)) for c in col_codes]),
        "cells": {r: dict(cols) for r, cols in cells.items()},
    }
```

- [ ] **Step 4: Run, confirm PASS** (2 tests OK).
- [ ] **Step 5: Commit**
```bash
cd /Users/desi/Desktop/workspace/desi-order && git add backend/apps/customers/report_crosstabs.py backend/apps/customers/tests/test_report_crosstabs.py && git commit -m "Add cross-tab analytics functions for customer report"
```

---

## Task 7: Gắn cross-tab vào response + test API

**Files:**
- Modify: `backend/apps/customers/report_service.py`
- Test: `backend/apps/customers/tests/test_customer_report_service.py`, `test_customer_report_api.py`

- [ ] **Step 1: Viết test service cho crosstabs**

Thêm test (cuối lớp `CustomerReportServiceTests`):

```python
    def test_crosstabs_present(self):
        c = Customer.objects.create(full_name="X")
        self._make_order(customer=c, pain_level_code="severe", age_text="30",
                         source_codes=["facebook"], sales_status_code="closed")
        data = CustomerReportService.summary()
        self.assertIn("crosstabs", data)
        for key in ("need_by_segment", "pain_by_age", "source_by_close"):
            self.assertIn(key, data["crosstabs"])
            self.assertIn("rows", data["crosstabs"][key])
            self.assertIn("cells", data["crosstabs"][key])
        self.assertEqual(data["crosstabs"]["source_by_close"]["cells"]["facebook"]["closed"], 1)
```

- [ ] **Step 2: Run, confirm FAIL** (KeyError 'crosstabs').

- [ ] **Step 3: Gắn vào `summary()`**

Thêm import trong `report_service.py`:
```python
from . import report_crosstabs
```

Trong `summary()`, ngay trước `return result` (sau khi `result` đã build xong và `segment_labels` đã có), thêm:
```python
        result["crosstabs"] = {
            "need_by_segment": report_crosstabs.need_by_segment_crosstab(
                rows, segment_by_customer, segment_labels,
            ),
            "pain_by_age": report_crosstabs.pain_by_age_crosstab(rows),
            "source_by_close": report_crosstabs.source_by_close_crosstab(rows),
        }
```
(Bảo đảm dòng `result[...] = ...` cho CODE_FIELDS/ARRAY_FIELDS nằm TRƯỚC dòng gán `crosstabs`, và `return result` ở cuối.)

- [ ] **Step 4: Thêm 'crosstabs' vào API shape test**

Trong `test_customer_report_api.py`, thêm `"crosstabs"` vào tuple key kiểm tra.

- [ ] **Step 5: Run cả 2 test, confirm PASS**
Run: `cd /Users/desi/Desktop/workspace/desi-order/backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 REDIS_PASSWORD='Desi@2026' ../.venv/bin/python manage.py test apps.customers.tests.test_customer_report_service apps.customers.tests.test_customer_report_api -v2`

- [ ] **Step 6: Commit**
```bash
cd /Users/desi/Desktop/workspace/desi-order && git add backend/apps/customers/report_service.py backend/apps/customers/tests/test_customer_report_service.py backend/apps/customers/tests/test_customer_report_api.py && git commit -m "Include cross-tabs in customer report summary response"
```

---

## Task 8: Frontend cross-tab (StackedBarChart + section)

**Files:**
- Create: `frontend/src/components/report/StackedBarChart.tsx`
- Modify: `frontend/src/lib/api/customers.ts` (type `CrossTabData` + `crosstabs`)
- Modify: `frontend/src/pages/CustomerReportPage.tsx` (section "Phân tích chéo")

- [ ] **Step 1: Type cross-tab trong `customers.ts`**

Thêm:
```typescript
export interface CrossTabAxisItem {
  code: string;
  label: string;
}

export interface CrossTabData {
  rows: CrossTabAxisItem[];
  columns: CrossTabAxisItem[];
  cells: Record<string, Record<string, number>>;
}
```
Và thêm field vào `CustomerReportSummary`:
```typescript
  crosstabs: {
    need_by_segment: CrossTabData;
    pain_by_age: CrossTabData;
    source_by_close: CrossTabData;
  };
```

- [ ] **Step 2: Tạo `StackedBarChart`**

```tsx
// frontend/src/components/report/StackedBarChart.tsx
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { CrossTabData } from "@/lib/api/customers";

const COLORS = ["#3b82f6", "#22c55e", "#f59e0b", "#8b5cf6", "#06b6d4", "#ef4444", "#64748b", "#0ea5e9"];

export function StackedBarChart({ title, data, note }: { title: string; data?: CrossTabData; note?: string }) {
  const rows = data?.rows ?? [];
  const columns = data?.columns ?? [];
  const chartData = rows.map((r) => {
    const entry: Record<string, number | string> = { label: r.label };
    for (const col of columns) entry[col.code] = data?.cells?.[r.code]?.[col.code] ?? 0;
    return entry;
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {note ? <p className="text-xs text-muted-foreground">{note}</p> : null}
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">Không có dữ liệu</p>
        ) : (
          <ResponsiveContainer width="100%" height={Math.max(280, rows.length * 36 + 80)}>
            <BarChart data={chartData} layout="vertical" margin={{ top: 8, right: 24, left: 16, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis type="number" allowDecimals={false} />
              <YAxis type="category" dataKey="label" width={130} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Legend />
              {columns.map((col, i) => (
                <Bar key={col.code} dataKey={col.code} name={col.label} stackId="a" fill={COLORS[i % COLORS.length]} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 3: Thêm section "Phân tích chéo" vào `CustomerReportPage`**

Import:
```tsx
import { StackedBarChart } from "@/components/report/StackedBarChart";
```
Thêm section (đặt trước section "Độ phủ dữ liệu"):
```tsx
      <ReportSection title="Phân tích chéo">
        <StackedBarChart title="Nhu cầu × Phân khúc" data={summary?.crosstabs?.need_by_segment} />
        <StackedBarChart title="Mức đau × Độ tuổi" data={summary?.crosstabs?.pain_by_age} />
        <StackedBarChart title="Nguồn × Tỉ lệ chốt" data={summary?.crosstabs?.source_by_close} note="Theo lượt nguồn." />
      </ReportSection>
```

- [ ] **Step 4: Verify compile + build**
Run: `cd /Users/desi/Desktop/workspace/desi-order/frontend && npx tsc --noEmit 2>&1 | grep -E "StackedBarChart|customers.ts|CustomerReportPage" || echo "no errors"; npm run build 2>&1 | tail -5`

- [ ] **Step 5: Commit**
```bash
cd /Users/desi/Desktop/workspace/desi-order && git add frontend/src/components/report/StackedBarChart.tsx frontend/src/lib/api/customers.ts frontend/src/pages/CustomerReportPage.tsx && git commit -m "Add cross-tab stacked bar charts to customer report"
```

---

## Task 9: Xác minh tổng thể

- [ ] **Step 1: Chạy toàn bộ test customers**
Run: `cd /Users/desi/Desktop/workspace/desi-order/backend && POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=desi_order POSTGRES_USER=desi_order POSTGRES_PASSWORD=desi_order REDIS_HOST=localhost REDIS_PORT=6379 REDIS_PASSWORD='Desi@2026' ../.venv/bin/python manage.py test apps.customers -v1`
Expected: chỉ test `test_customer_order_aggregate_api` fail (pre-existing trên master); mọi test report PASS.

- [ ] **Step 2: Build frontend lần cuối**
Run: `cd /Users/desi/Desktop/workspace/desi-order/frontend && npm run build 2>&1 | tail -5`

- [ ] **Step 3: Cập nhật spec ghi hoàn thành** vào cuối `docs/superpowers/specs/2026-05-26-customer-portrait-dashboard-expansion-design.md`, commit.

- [ ] **Step 4: Push**
```bash
cd /Users/desi/Desktop/workspace/desi-order && git push
```

---

## Self-Review Notes

- **Spec coverage:** Nhóm A (pain_level, pain_duration, lifestyle, used_massage, relax_time → Task 2/5); Nhóm B (delax_source, gender, province → Task 2/5); Nhóm C (potential, not_closed_reason, call_duration, comparing_brand → Task 2/5); Nhóm D (completeness → Task 2/3/5; 3 cross-tab → Task 6/7/8). Bố cục section → Task 5. ✅
- **Đơn vị đếm:** array theo lượt (lifestyle/relax/source/barrier — base = occurrences), code theo đơn (base = total_records); province top10+Khác; brand chuẩn hóa lower. Test khẳng định.
- **Counter keys là str** (gender/potential ép `str()`) → tránh lỗi sort int↔str.
- **Type khớp:** key backend ↔ `CustomerReportSummary`; `CrossTabData` ↔ format `{rows,columns,cells}`.
- **Điểm cần đối chiếu khi code (không giả định):** field bắt buộc khi tạo `Order`/`OrderAssessment` trong factory mở rộng (Task 2 Step 1); vị trí thật của `DistributionBar/Donut`/`Card` (Task 5 Step 4). Plan đã ghi rõ phải verify.
```
