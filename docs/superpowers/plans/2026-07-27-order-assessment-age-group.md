# Order Assessment Age Group Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đổi `OrderAssessment.age_text` từ free text sang dropdown 7 nhóm tuổi cố định, khoá ràng buộc ở tầng ghi, và backfill 3.026 dòng dữ liệu cũ.

**Architecture:** Cột `age_text` giữ nguyên tên và nguyên kiểu `CharField(32) NOT NULL default ""` — chỉ đổi ngữ nghĩa từ text sang code. Ràng buộc đến từ việc đổi một entry trong registry `ASSESSMENT_FIELDS` (`assessment_fields.py`), vốn là single source of truth auto-sinh DRF serializer, validate và upsert. Một parser thuần riêng biệt lo việc map dữ liệu cũ, dùng chung cho data migration và import CSV.

**Tech Stack:** Django 5 + DRF, PostgreSQL, pytest, React + TypeScript (Vite), shadcn/ui.

## Global Constraints

- Spec nguồn: `docs/superpowers/specs/2026-07-27-order-assessment-age-group-design.md`. Đọc trước khi bắt đầu.
- Python 3.12+, ruff bắt buộc xanh (ruleset trong `backend/pyproject.toml`: E4,E7,E9,F,B,C4,DTZ,LOG,PIE,RET,SIM,T20,UP). CI đỏ nếu vi phạm mới.
- Code nhóm tuổi: `18_24`, `25_34`, `35_44`, `45_54`, `55_64`, `65_plus`, `not_disclosed`. **Không** dùng chuỗi `unknown` — đã bị `report_fields.UNKNOWN_CODE` chiếm cho nghĩa "chưa điền".
- Nhãn hiển thị: `18-24`, `25-34`, `35-44`, `45-54`, `55-64`, `65+`, `Không xác định`.
- Chuỗi rỗng `""` giữ nguyên nghĩa "sale chưa hỏi" — không bao giờ tự động đổi thành `not_disclosed`.
- Commit sau mỗi task. Message tiếng Anh, ngắn, không kèm dòng `Co-Authored-By`.

**Lệnh chạy test backend** (dùng nguyên văn, thay `<paths>`):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_age_group \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <paths> -q --create-db'
```

Bẫy đã biết: image tên `desi-backend-test:latest`; thiếu `ENCRYPTION_KEY` thì mọi test ERROR ở migration; Git Bash cần `$(pwd -W)` và `-w //app`; không bao giờ gọi `cache.clear()` trong test.

**Lệnh lint backend:**

```bash
docker run --rm -v "$(pwd -W)/backend:/app" -w //app ghcr.io/astral-sh/ruff:latest check --no-cache .
```

---

## File Structure

**Tạo mới:**
- `backend/apps/orders/age_group_backfill.py` — parser thuần, không import Django. Một trách nhiệm: chuỗi tự do → mã nhóm tuổi.
- `backend/apps/orders/tests/test_age_group_backfill.py` — test parser.
- `backend/apps/orders/migrations/0110_backfill_age_group.py` — data migration.
- `backend/apps/orders/tests/test_age_group_migration.py` — test hàm backfill của migration.

**Sửa:**
- `backend/apps/orders/crm_catalog.py` — danh mục + payload.
- `backend/apps/orders/assessment_fields.py` — registry.
- `backend/apps/orders/ai_extract_contract.py` — hợp đồng AI.
- `backend/apps/orders/export_excel.py`, `management/commands/import_assessment_csv.py`.
- `backend/apps/customers/report_fields.py`, `report_service.py`, `report_crosstabs.py`, `profile_service.py`, `serializers.py`.
- `backend/schema.yaml` và `frontend/src/api/schema.d.ts` — **sinh tự động**, không sửa tay. CI có drift-check cho cả hai (`.gitlab-ci.yml:132`, `:198`).
- `frontend/src/features/orders/BlockCrmRating.tsx`, `OrdersTable/cell-renderers.tsx`, `order-types.ts`.
- `frontend/src/features/customers/customer-utils.tsx`, `components/CustomerGroupCells.tsx`.

**Xoá:**
- `backend/apps/customers/age_buckets.py` và test của nó (nếu có).

---

### Task 1: Parser nhóm tuổi

Hàm thuần, chưa nối vào đâu. Không đổi hành vi hệ thống — toàn bộ test hiện có phải vẫn xanh.

**Files:**
- Create: `backend/apps/orders/age_group_backfill.py`
- Test: `backend/apps/orders/tests/test_age_group_backfill.py`

**Interfaces:**
- Consumes: không có.
- Produces:
  - `parse_age_group(raw: str | None) -> str` — trả mã nhóm, hoặc `""` nếu input rỗng.
  - `group_for_age(age: int) -> str` — trả mã nhóm cho một số tuổi.
  - `AGE_GROUP_NOT_DISCLOSED: str = "not_disclosed"`

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/orders/tests/test_age_group_backfill.py`. Bảng case lấy từ chính dữ liệu prod trong spec:

```python
from django.test import SimpleTestCase

from apps.orders.age_group_backfill import group_for_age, parse_age_group


class ParseAgeGroupTests(SimpleTestCase):
    def test_empty_stays_empty(self):
        """Rỗng = sale chưa hỏi, khác hẳn 'đã hỏi nhưng khách không nói'."""
        for raw in ("", "   ", None):
            self.assertEqual(parse_age_group(raw), "")

    def test_plain_number(self):
        cases = {"70": "65_plus", "60": "55_64", "50": "45_54",
                 "40": "35_44", "30": "25_34", "20": "18_24"}
        for raw, expected in cases.items():
            self.assertEqual(parse_age_group(raw), expected, raw)

    def test_t_suffix_is_stripped(self):
        """'t' = 'tuổi'. 305/2725 dòng prod dùng dạng này."""
        self.assertEqual(parse_age_group("70t"), "65_plus")
        self.assertEqual(parse_age_group("45T"), "45_54")

    def test_boundaries(self):
        cases = {"24": "18_24", "25": "25_34", "34": "25_34", "35": "35_44",
                 "44": "35_44", "45": "45_54", "54": "45_54", "55": "55_64",
                 "64": "55_64", "65": "65_plus", "100": "65_plus"}
        for raw, expected in cases.items():
            self.assertEqual(parse_age_group(raw), expected, raw)

    def test_out_of_range_is_not_disclosed(self):
        for raw in ("9", "17", "101", "150"):
            self.assertEqual(parse_age_group(raw), "not_disclosed", raw)

    def test_text_without_digits_is_not_disclosed(self):
        for raw in ("ko chia sẻ", "trung niên", "già", "n/a", "-"):
            self.assertEqual(parse_age_group(raw), "not_disclosed", raw)

    def test_range_uses_midpoint(self):
        cases = {"35-40": "35_44", "38-45": "35_44", "55-60": "55_64",
                 "49-55": "45_54", "60-70": "65_plus", "35-45t": "35_44"}
        for raw, expected in cases.items():
            self.assertEqual(parse_age_group(raw), expected, raw)

    def test_decade_shorthand(self):
        """'7x' = bảy mươi mấy → midpoint 74. '6x' → 64."""
        self.assertEqual(parse_age_group("7x"), "65_plus")
        self.assertEqual(parse_age_group("6x"), "55_64")

    def test_fuzzy_prefix_uses_first_number(self):
        cases = {"ngoài 40": "35_44", "ngoài  40": "35_44",
                 "ngoài 50": "45_54", "gần 70": "65_plus"}
        for raw, expected in cases.items():
            self.assertEqual(parse_age_group(raw), expected, raw)

    def test_birth_year(self):
        """Mốc cố định 2026 để kết quả tái lập được."""
        self.assertEqual(parse_age_group("1980"), "45_54")
        self.assertEqual(parse_age_group("1960"), "65_plus")

    def test_group_for_age_direct(self):
        self.assertEqual(group_for_age(47), "45_54")
        self.assertEqual(group_for_age(5), "not_disclosed")
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: lệnh test backend với `<paths>` = `apps/orders/tests/test_age_group_backfill.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.orders.age_group_backfill'`

- [ ] **Step 3: Viết implementation**

Tạo `backend/apps/orders/age_group_backfill.py`:

```python
"""Parse `age_text` tự do (dữ liệu cũ) → mã nhóm tuổi.

Dùng bởi data migration 0110 và bởi `import_assessment_csv` (file CSV cũ vẫn
ghi tuổi dạng số). Thuần Python, không import Django, để migration gọi được
mà không kéo theo app registry.
"""
from __future__ import annotations

import re

# Backfill chạy một lần trên dữ liệu chốt ngày 2026-07-27. Mốc cố định thay vì
# date.today() để chạy lại lúc nào cũng ra cùng kết quả.
REFERENCE_YEAR = 2026

MIN_AGE = 18
MAX_AGE = 100

# Không đặt tên "unknown": report_fields.UNKNOWN_CODE đã chiếm chuỗi đó cho
# nghĩa "chưa điền", còn đây là "đã hỏi nhưng khách không nói".
AGE_GROUP_NOT_DISCLOSED = "not_disclosed"

# (code, tuổi lớn nhất còn thuộc nhóm). Nhóm cuối không có trần.
_UPPER_BOUNDS: tuple[tuple[str, int], ...] = (
    ("18_24", 24),
    ("25_34", 34),
    ("35_44", 44),
    ("45_54", 54),
    ("55_64", 64),
)
_TOP_CODE = "65_plus"

_RANGE_RE = re.compile(r"^(\d{1,4})\s*[-–]\s*(\d{1,4})$")
_DECADE_RE = re.compile(r"^(\d)0?x$")
_DIGITS_RE = re.compile(r"\d+")
_T_SUFFIX_RE = re.compile(r"(?<=\d)t")


def _normalize(raw: str) -> str:
    """Lowercase, trim, gộp khoảng trắng, bỏ hậu tố 't' (= 'tuổi') sau chữ số."""
    text = " ".join(str(raw).strip().lower().split())
    return _T_SUFFIX_RE.sub("", text).strip()


def group_for_age(age: int) -> str:
    """Map một số tuổi vào nhóm. Ngoài [18, 100] coi như không xác định."""
    if age < MIN_AGE or age > MAX_AGE:
        return AGE_GROUP_NOT_DISCLOSED
    for code, upper in _UPPER_BOUNDS:
        if age <= upper:
            return code
    return _TOP_CODE


def parse_age_group(raw: str | None) -> str:
    """Chuỗi tự do → mã nhóm tuổi.

    Rỗng trả về rỗng: "sale chưa hỏi" là trạng thái riêng, không phải
    "khách không nói".
    """
    text = _normalize(raw or "")
    if not text:
        return ""

    digits = _DIGITS_RE.search(text)
    if digits is None:
        # Mọi cách diễn đạt bằng lời ("ko chia sẻ", "trung niên") rơi vào đây —
        # bắt theo "không có chữ số" thay vì liệt kê từ khoá để không bao giờ sót.
        return AGE_GROUP_NOT_DISCLOSED

    range_match = _RANGE_RE.match(text)
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        return group_for_age((low + high) // 2)

    decade_match = _DECADE_RE.match(text)
    if decade_match:
        # "7x" = 70..79 → midpoint 74.
        return group_for_age(int(decade_match.group(1)) * 10 + 4)

    number = int(digits.group())
    if 1900 <= number <= 2015:
        return group_for_age(REFERENCE_YEAR - number)
    return group_for_age(number)
```

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: lệnh test backend với `<paths>` = `apps/orders/tests/test_age_group_backfill.py`
Expected: PASS, 11 tests

- [ ] **Step 5: Lint**

Run: lệnh lint backend
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/age_group_backfill.py backend/apps/orders/tests/test_age_group_backfill.py
git commit -m "feat(orders): add age group parser for legacy age_text"
```

---

### Task 2: Danh mục nhóm tuổi trong catalog

Thêm danh mục và đẩy ra API catalog. Vẫn chưa khoá ràng buộc — `assessment_fields.py` chưa đụng, nên toàn bộ test hiện có vẫn xanh.

**Files:**
- Modify: `backend/apps/orders/crm_catalog.py`
- Test: `backend/apps/orders/tests/test_age_group_catalog.py` (create)

**Interfaces:**
- Consumes: `AGE_GROUP_NOT_DISCLOSED` từ Task 1 (chỉ để assert code khớp nhau trong test).
- Produces:
  - `AGE_GROUP_OPTIONS: list[CatalogOption]`
  - `AGE_GROUP_LABELS: dict[str, str]`
  - `build_crm_catalog_payload()` có thêm khoá `"age_group_options"`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/orders/tests/test_age_group_catalog.py`:

```python
from django.test import SimpleTestCase

from apps.orders.age_group_backfill import AGE_GROUP_NOT_DISCLOSED
from apps.orders.crm_catalog import (
    AGE_GROUP_LABELS,
    AGE_GROUP_OPTIONS,
    CRM_CODE_LABELS,
    build_crm_catalog_payload,
)


class AgeGroupCatalogTests(SimpleTestCase):
    def test_seven_options_in_order(self):
        self.assertEqual(
            [o.code for o in AGE_GROUP_OPTIONS],
            ["18_24", "25_34", "35_44", "45_54", "55_64", "65_plus", "not_disclosed"],
        )

    def test_labels(self):
        self.assertEqual(AGE_GROUP_LABELS["65_plus"], "65+")
        self.assertEqual(AGE_GROUP_LABELS["not_disclosed"], "Không xác định")

    def test_parser_code_matches_catalog(self):
        """Parser và catalog phải nói cùng một ngôn ngữ."""
        self.assertIn(AGE_GROUP_NOT_DISCLOSED, AGE_GROUP_LABELS)

    def test_never_uses_unknown_string(self):
        """'unknown' đã bị report_fields.UNKNOWN_CODE chiếm cho nghĩa 'chưa điền'."""
        self.assertNotIn("unknown", AGE_GROUP_LABELS)

    def test_registered_for_label_lookup(self):
        self.assertIs(CRM_CODE_LABELS["age_text"], AGE_GROUP_LABELS)

    def test_exposed_in_catalog_payload(self):
        payload = build_crm_catalog_payload()
        self.assertEqual(
            payload["age_group_options"][0], {"code": "18_24", "label": "18-24"},
        )
        self.assertEqual(len(payload["age_group_options"]), 7)
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: lệnh test backend với `<paths>` = `apps/orders/tests/test_age_group_catalog.py`
Expected: FAIL — `ImportError: cannot import name 'AGE_GROUP_LABELS'`

- [ ] **Step 3: Viết implementation**

Trong `backend/apps/orders/crm_catalog.py`, thêm khối options ngay sau `OCCUPATION_OPTIONS`:

```python
# Bộ nhóm tuổi chuẩn của Facebook Ads — giữ khớp để đối chiếu chân dung khách
# với báo cáo audience trong apps/facebook_ads.
AGE_GROUP_OPTIONS = [
    CatalogOption("18_24", "18-24"),
    CatalogOption("25_34", "25-34"),
    CatalogOption("35_44", "35-44"),
    CatalogOption("45_54", "45-54"),
    CatalogOption("55_64", "55-64"),
    CatalogOption("65_plus", "65+"),
    # "Đã hỏi, khách không nói" — khác với cột rỗng nghĩa là "sale chưa hỏi".
    CatalogOption("not_disclosed", "Không xác định"),
]
```

Thêm vào khối `_to_map` (cạnh `OCCUPATION_LABELS`):

```python
AGE_GROUP_LABELS = _to_map(AGE_GROUP_OPTIONS)
```

Thêm một dòng vào `CRM_CODE_LABELS`:

```python
    "age_text": AGE_GROUP_LABELS,
```

Thêm một dòng vào `build_crm_catalog_payload()` (cạnh `occupation_options`):

```python
        "age_group_options": [{"code": o.code, "label": o.label} for o in AGE_GROUP_OPTIONS],
```

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: lệnh test backend với `<paths>` = `apps/orders/tests/test_age_group_catalog.py`
Expected: PASS, 6 tests

- [ ] **Step 5: Xác nhận chưa có hành vi nào đổi**

`CRM_CODE_LABELS["age_text"]` chưa ảnh hưởng gì: `_LABELLED_CODE_FIELDS`
(`product_item_crm_serializers.py:373`) lọc thêm điều kiện `f.kind == "code"` mà
`age_text` vẫn đang là `"text"`; còn `_validate_code`
(`order_core_serializers.py:81`) chỉ chạy cho 4 field liệt kê tay.

Run: lệnh test backend với `<paths>` = `apps/orders/tests/ apps/customers/tests/`
Expected: PASS, không có test nào mới hỏng

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/crm_catalog.py backend/apps/orders/tests/test_age_group_catalog.py
git commit -m "feat(orders): add age group catalog options"
```

---

### Task 3: Khoá ràng buộc ghi + hợp đồng AI

Đây là task đổi hành vi. `age_text` thành code field, kéo theo: serializer sinh `age_text_label`, `coerce_and_validate` reject free text, AI phải trả code. Test hiện có dùng free text sẽ hỏng và được sửa trong chính task này.

**Files:**
- Modify: `backend/apps/orders/assessment_fields.py:51`
- Modify: `backend/apps/orders/models.py:532` (comment)
- Modify: `backend/apps/orders/ai_extract_contract.py`
- Modify: `backend/apps/orders/tests/test_assessment_ai_fill.py`
- Modify: `backend/apps/callcenter/tests/test_call_insights.py`
- Test: `backend/apps/orders/tests/test_age_group_validation.py` (create)

**Interfaces:**
- Consumes: `AGE_GROUP_LABELS` từ Task 2.
- Produces: `age_text` là code field; API trả thêm `age_text_label`; `normalize_extracted` chỉ nhận code hợp lệ cho `age_text`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/orders/tests/test_age_group_validation.py`:

```python
from django.test import SimpleTestCase

from apps.orders.ai_extract_contract import normalize_extracted
from apps.orders.assessment_fields import (
    ASSESSMENT_FIELDS_BY_NAME,
    AssessmentValidationError,
    coerce_and_validate,
)


class AgeGroupValidationTests(SimpleTestCase):
    def test_is_a_code_field(self):
        self.assertEqual(ASSESSMENT_FIELDS_BY_NAME["age_text"].kind, "code")

    def test_accepts_valid_code(self):
        self.assertEqual(coerce_and_validate("age_text", "45_54"), "45_54")

    def test_rejects_free_text(self):
        for value in ("45", "45-60", "ngoài 40"):
            with self.assertRaises(AssessmentValidationError, msg=value):
                coerce_and_validate("age_text", value)

    def test_null_becomes_empty_string(self):
        """Cột NOT NULL default '' nên null phải hoá rỗng, không phải None."""
        self.assertEqual(coerce_and_validate("age_text", None), "")

    def test_empty_string_allowed(self):
        self.assertEqual(coerce_and_validate("age_text", ""), "")


class AiContractAgeGroupTests(SimpleTestCase):
    def test_accepts_whitelisted_code(self):
        clean, rejected = normalize_extracted({"age_text": "45_54", "evidence": []})
        self.assertEqual(clean["age_text"], "45_54")
        self.assertNotIn("age_text", rejected)

    def test_rejects_free_text_from_llm(self):
        clean, rejected = normalize_extracted({"age_text": "45 tuổi", "evidence": []})
        self.assertNotIn("age_text", clean)
        self.assertEqual(rejected["age_text"], "45 tuổi")

    def test_schema_exposes_enum(self):
        from apps.orders.ai_extract_contract import build_extract_response_schema

        prop = build_extract_response_schema()["properties"]["age_text"]
        self.assertIn("65_plus", prop["enum"])
        self.assertEqual(prop["type"], "string")
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: lệnh test backend với `<paths>` = `apps/orders/tests/test_age_group_validation.py`
Expected: FAIL — `test_is_a_code_field` báo `'text' != 'code'`

- [ ] **Step 3: Đổi registry**

Trong `backend/apps/orders/assessment_fields.py`, sửa import (thêm `AGE_GROUP_LABELS` vào khối import từ `apps.orders.crm_catalog`) và thay dòng 51:

```python
    # Cột tên là age_text vì lý do lịch sử nhưng chứa MÃ nhóm tuổi, không phải
    # text tự do. Giữ tên để không reset cấu hình cột đã lưu phía client.
    AssessmentField("age_text", "code", null_default="", whitelist=AGE_GROUP_LABELS),
```

Xoá luôn hằng `SHORT_TEXT_MAX_LEN` ở `assessment_fields.py:27`. Nó chỉ có đúng hai chỗ
dùng — dòng 51 ngay trên và `ai_extract_contract.py:117` — mà Step 4 xoá nốt chỗ còn lại,
nên để lại là dead code. Xoá kèm dòng import ở `ai_extract_contract.py:13`.

Trong `backend/apps/orders/models.py`, thêm comment trên dòng 532:

```python
    # Mã nhóm tuổi (crm_catalog.AGE_GROUP_OPTIONS), KHÔNG phải text tự do.
    # Tên cột giữ nguyên vì là column key trong cấu hình bảng lưu ở client.
    age_text = models.CharField(max_length=32, blank=True, default="")
```

- [ ] **Step 4: Đổi hợp đồng AI**

Trong `backend/apps/orders/ai_extract_contract.py`, thêm `AGE_GROUP_LABELS` vào import từ `crm_catalog`, rồi thêm một dòng vào `_SCALAR_WHITELIST`:

```python
    "age_text": AGE_GROUP_LABELS,
```

Xoá khối xử lý riêng trong `normalize_extracted` (dòng ~115-119):

```python
    age = raw.get("age_text")
    if isinstance(age, str) and age.strip():
        clean["age_text"] = age.strip()[:SHORT_TEXT_MAX_LEN]
    elif age is not None:
        rejected["age_text"] = age
```

Xoá dòng khai báo tay trong `build_extract_response_schema`:

```python
        "age_text": {"type": "string", "nullable": True},
```

Vòng lặp `for name, labels in _SCALAR_WHITELIST.items()` ngay bên dưới sẽ tự sinh
`{"type": "string", "enum": [...], "nullable": True}` cho `age_text`.

Xoá dòng 13 `from apps.orders.assessment_fields import SHORT_TEXT_MAX_LEN` — sau khi bỏ
khối trên thì file này không còn dùng nó nữa, ruff `F401` sẽ báo nếu để lại.

- [ ] **Step 5: Chạy test mới**

Run: lệnh test backend với `<paths>` = `apps/orders/tests/test_age_group_validation.py`
Expected: PASS, 8 tests

- [ ] **Step 6: Sửa test hiện có bị ảnh hưởng**

Chạy trước để thấy chính xác cái nào đỏ:

Run: lệnh test backend với `<paths>` = `apps/orders/tests/test_assessment_ai_fill.py apps/callcenter/tests/test_call_insights.py`
Expected: FAIL

Sửa như sau.

`backend/apps/orders/tests/test_assessment_ai_fill.py`:
- Dòng ~61-65 `test_normalize_truncates_age_text_and_keeps_bool`: `age_text` không còn cắt độ dài mà là whitelist. Đổi thành:

```python
    def test_normalize_rejects_overlong_age_text(self):
        clean, rejected = normalize_extracted(
            {"age_text": "x" * 100, "used_massage_before": False, "evidence": []}
        )
        self.assertNotIn("age_text", clean)
        self.assertIs(clean["used_massage_before"], False)
```

- Dòng ~70-76: giữ nguyên ý (`age_text: 45` kiểu int vẫn bị reject), không cần sửa.
- Dòng ~95, ~122, ~152, ~168, ~199: đổi mọi giá trị `age_text` từ `"40"` / `"45"` sang `"35_44"` / `"45_54"` tương ứng, và đổi assert theo. Ví dụ dòng 152:

```python
        self.assertEqual(assessment.age_text, "45_54")
```

- Dòng ~205 `age_text="30"` → `age_text="25_34"`.
- Dòng ~164 `age_text=""` giữ nguyên (rỗng vẫn hợp lệ).

`backend/apps/orders/tests/test_portrait_completion.py` dòng 34: `age_text="35"` →
`age_text="35_44"`. Test này dựng dữ liệu qua ORM nên không đỏ (ORM không validate),
nhưng để giá trị ngoài whitelist trong fixture là bẫy cho người đọc sau.

`backend/apps/callcenter/tests/test_call_insights.py`:
- Dòng 42 `"age_text": "45"` → `"age_text": "45_54"`.
- Dòng 62 `self.assertEqual(raw["age_text"], "45")` → `"45_54"`.
- Dòng 47 và 65 là `quote` (`"em 45 tuổi"`) — giữ nguyên, quote là trích dẫn transcript không phải giá trị field.

- [ ] **Step 7: Chạy lại toàn bộ test bị ảnh hưởng**

Run: lệnh test backend với `<paths>` = `apps/orders/tests/ apps/callcenter/tests/`
Expected: PASS

- [ ] **Step 8: Lint + commit**

Run: lệnh lint backend
Expected: `All checks passed!`

```bash
git add backend/apps/orders/assessment_fields.py backend/apps/orders/models.py \
  backend/apps/orders/ai_extract_contract.py backend/apps/orders/tests/ \
  backend/apps/callcenter/tests/test_call_insights.py
git commit -m "feat(orders): enforce age group codes on assessment write path"
```

---

### Task 4: Data migration

**Files:**
- Create: `backend/apps/orders/migrations/0110_backfill_age_group.py`
- Test: `backend/apps/orders/tests/test_age_group_migration.py`

**Interfaces:**
- Consumes: `parse_age_group` từ Task 1.
- Produces: hàm module-level `backfill(apps, schema_editor)` để test gọi trực tiếp.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/orders/tests/test_age_group_migration.py`:

```python
import importlib
from decimal import Decimal

from django.apps import apps as django_apps
from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment

migration = importlib.import_module("apps.orders.migrations.0110_backfill_age_group")


class BackfillAgeGroupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(name="CH-AGE", code="ch-age")

    def _assessment(self, age_text: str) -> OrderAssessment:
        customer = Customer.objects.create(full_name=f"KH {age_text or 'blank'}")
        order = Order.objects.create(
            customer=customer, channel=self.channel, status=3,
            total_after_discount=Decimal("0"),
        )
        return OrderAssessment.objects.create(order=order, age_text=age_text)

    def test_converts_free_text_to_codes(self):
        cases = {
            "70": "65_plus",
            "70t": "65_plus",
            "ngoài 40": "35_44",
            "35-40": "35_44",
            "ko chia sẻ": "not_disclosed",
        }
        created = {raw: self._assessment(raw) for raw in cases}

        migration.backfill(django_apps, None)

        for raw, expected in cases.items():
            created[raw].refresh_from_db()
            self.assertEqual(created[raw].age_text, expected, raw)

    def test_leaves_empty_untouched(self):
        """Rỗng = sale chưa hỏi. Không được biến thành not_disclosed."""
        blank = self._assessment("")

        migration.backfill(django_apps, None)

        blank.refresh_from_db()
        self.assertEqual(blank.age_text, "")

    def test_is_idempotent(self):
        row = self._assessment("70")

        migration.backfill(django_apps, None)
        migration.backfill(django_apps, None)

        row.refresh_from_db()
        self.assertEqual(row.age_text, "65_plus")
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: lệnh test backend với `<paths>` = `apps/orders/tests/test_age_group_migration.py`
Expected: FAIL — `ModuleNotFoundError: apps.orders.migrations.0110_backfill_age_group`

- [ ] **Step 3: Viết migration**

Tạo `backend/apps/orders/migrations/0110_backfill_age_group.py`:

```python
"""Backfill age_text tự do → mã nhóm tuổi.

Import parser từ app thay vì copy vào migration: hàm này còn được
`import_assessment_csv` dùng lâu dài nên sẽ không biến mất, và migration chỉ
đụng dòng đã tồn tại — DB mới không có dòng nào để chạy sai.
"""
from django.db import migrations

from apps.orders.age_group_backfill import parse_age_group

BATCH_SIZE = 1000


def backfill(apps, schema_editor):
    OrderAssessment = apps.get_model("orders", "OrderAssessment")
    pending = []
    queryset = OrderAssessment.objects.exclude(age_text="").only("id", "age_text")
    for row in queryset.iterator(chunk_size=BATCH_SIZE):
        converted = parse_age_group(row.age_text)
        if converted != row.age_text:
            row.age_text = converted
            pending.append(row)
    for start in range(0, len(pending), BATCH_SIZE):
        OrderAssessment.objects.bulk_update(pending[start:start + BATCH_SIZE], ["age_text"])


class Migration(migrations.Migration):
    dependencies = [("orders", "0109_drop_dead_indexes")]

    # Không có reverse: giá trị gốc không suy ngược được từ mã nhóm. Lưới an
    # toàn là bản dump CSV chạy trước khi deploy (xem spec, mục Vận hành).
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
```

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: lệnh test backend với `<paths>` = `apps/orders/tests/test_age_group_migration.py`
Expected: PASS, 3 tests

- [ ] **Step 5: Xác nhận không sinh migration thừa**

Run: lệnh test backend, thay `python -m pytest ...` bằng `python manage.py makemigrations --check --dry-run`
Expected: `No changes detected` — xác nhận không cần schema migration như spec khẳng định.

- [ ] **Step 6: Lint + commit**

```bash
git add backend/apps/orders/migrations/0110_backfill_age_group.py backend/apps/orders/tests/test_age_group_migration.py
git commit -m "feat(orders): backfill legacy age_text into age group codes"
```

---

### Task 5: Đường đọc báo cáo khách hàng

Xoá hệ nhóm tuổi thứ hai. Đây là task làm đổi số trên dashboard.

**Files:**
- Delete: `backend/apps/customers/age_buckets.py`
- Modify: `backend/apps/customers/report_service.py`
- Modify: `backend/apps/customers/report_crosstabs.py`
- Modify: `backend/apps/customers/tests/test_customer_report_service.py`

**Interfaces:**
- Consumes: `AGE_GROUP_LABELS`, `AGE_GROUP_OPTIONS` từ Task 2; cột đã chứa code sau Task 3-4.
- Produces: `age_distribution` và crosstab `pain_by_age` dùng trục 6 nhóm + `not_disclosed` + `unknown`.

- [ ] **Step 1: Viết test thất bại**

Trong `backend/apps/customers/tests/test_customer_report_service.py`, đổi các giá trị dựng dữ liệu sang code (`age_text="30"` → `"25_34"`, `age_text="50"` → `"45_54"`) rồi thêm test:

```python
    def test_age_distribution_uses_catalog_groups(self):
        c1 = Customer.objects.create(full_name="Age-A")
        c2 = Customer.objects.create(full_name="Age-B")
        c3 = Customer.objects.create(full_name="Age-C")
        self._make_order(customer=c1, age_text="25_34")
        self._make_order(customer=c2, age_text="not_disclosed")
        self._make_order(customer=c3, age_text="")

        result = CustomerReportService.summary()
        by_code = {row["code"]: row for row in result["age_distribution"]}

        self.assertEqual(by_code["25_34"]["count"], 1)
        self.assertEqual(by_code["25_34"]["label"], "25-34")
        self.assertEqual(by_code["not_disclosed"]["count"], 1)
        self.assertEqual(by_code["not_disclosed"]["label"], "Không xác định")
        # Chưa điền là ô riêng, không gộp vào "Không xác định".
        self.assertEqual(by_code["unknown"]["count"], 1)
        self.assertEqual(by_code["unknown"]["label"], "Chưa điền")
        # Hệ bucket cũ phải biến mất hoàn toàn.
        self.assertNotIn("18-30", by_code)
        self.assertNotIn("other", by_code)
```

`Customer` đã được import sẵn trong file (dùng ở dòng ~71). `_make_order` là helper sẵn
có với tham số `age_text` (xem dòng ~34-51) — dùng đúng chữ ký hiện tại, không viết mới.

Lưu ý về `_distribution`: khi truyền `fixed_order`, code có count bằng 0 bị **bỏ khỏi**
kết quả (`report_service.py`, `if counter.get(code)`). Nên chỉ assert những code thực sự
có dữ liệu trong test.

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: lệnh test backend với `<paths>` = `apps/customers/tests/test_customer_report_service.py`
Expected: FAIL — `age_distribution` còn trả bucket `18-30` / `other`

- [ ] **Step 3: Thêm cấu hình trục tuổi vào report_fields.py**

`report_fields.py` là nhà của cấu hình phân bố, đã có sẵn `UNKNOWN_CODE` và
`from apps.orders import crm_catalog as cat`. Đặt hằng ở đây để cả `report_service`
lẫn `report_crosstabs` cùng import — tránh vòng lặp import giữa hai file đó
(`report_service` đã import `report_crosstabs`).

Thêm vào cuối `backend/apps/customers/report_fields.py`:

```python
# Trục tuổi có hai ô "không có số": not_disclosed (đã hỏi, khách không nói) và
# UNKNOWN_CODE (sale chưa điền). Nhãn ô sau đổi thành "Chưa điền" thay vì
# "Chưa xác định" mặc định, để không lẫn với "Không xác định".
AGE_DISTRIBUTION_LABELS = {**cat.AGE_GROUP_LABELS, UNKNOWN_CODE: "Chưa điền"}
AGE_DISTRIBUTION_ORDER = [*codes(cat.AGE_GROUP_OPTIONS), UNKNOWN_CODE]
```

- [ ] **Step 4: Sửa report_service.py**

Bỏ import `from .age_buckets import AGE_BUCKET_LABELS, AGE_BUCKET_ORDER, bucket_age` (dòng 24). Thêm `AGE_DISTRIBUTION_LABELS, AGE_DISTRIBUTION_ORDER` vào khối import sẵn có `from .report_fields import (...)` (dòng 27-34).

Sửa dòng 82:

```python
            age_counter[r["assessment__age_text"] or UNKNOWN_CODE] += 1
```

Sửa khối `age_distribution` (dòng ~112-114):

```python
            "age_distribution": CustomerReportService._distribution(
                age_counter, AGE_DISTRIBUTION_LABELS, total_records,
                fixed_order=AGE_DISTRIBUTION_ORDER,
            ),
```

Dòng 275 (`_completeness`) giữ nguyên: kind `"text"` kiểm tra `bool((v or "").strip())`,
đúng cho cả code lẫn text.

- [ ] **Step 5: Sửa report_crosstabs.py**

Bỏ import `from .age_buckets import AGE_BUCKET_LABELS, AGE_BUCKET_ORDER, bucket_age`
(dòng 10). Thêm hai hằng mới vào import sẵn có từ `.report_fields` (dòng 11):

```python
from .report_fields import (
    AGE_DISTRIBUTION_LABELS,
    AGE_DISTRIBUTION_ORDER,
    UNKNOWN_CODE,
    UNKNOWN_LABEL,
)
```

Trong `pain_by_age_crosstab`, sửa dòng 28 và 34:

```python
        age = r["assessment__age_text"] or UNKNOWN_CODE
```

```python
        "columns": _axis([(c, AGE_DISTRIBUTION_LABELS[c]) for c in AGE_DISTRIBUTION_ORDER]),
```

- [ ] **Step 6: Xoá age_buckets.py**

```bash
git rm backend/apps/customers/age_buckets.py
```

Kiểm tra không còn ai tham chiếu:

```bash
grep -rn "age_buckets\|bucket_age\|AGE_BUCKET" backend/apps
```
Expected: không có kết quả. Nếu còn file test riêng cho `age_buckets`, xoá luôn.

- [ ] **Step 7: Chạy test để xác nhận pass**

Run: lệnh test backend với `<paths>` = `apps/customers/tests/`
Expected: PASS

- [ ] **Step 8: Lint + commit**

```bash
git add -A backend/apps/customers
git commit -m "refactor(customers): use assessment age group codes in reports"
```

---

### Task 6: Export Excel, import CSV, hồ sơ khách

**Files:**
- Modify: `backend/apps/orders/export_excel.py:100`
- Modify: `backend/apps/orders/management/commands/import_assessment_csv.py`
- Modify: `backend/apps/customers/profile_service.py:129`
- Modify: `backend/apps/customers/serializers.py`
- Modify: `backend/apps/customers/tests/test_customer_profiles_api.py`

**Interfaces:**
- Consumes: `AGE_GROUP_LABELS` (Task 2), `parse_age_group` (Task 1).
- Produces: `demographics.age_group_label` trong API hồ sơ khách.

- [ ] **Step 1: Viết test thất bại**

Trong `backend/apps/customers/tests/test_customer_profiles_api.py`, đổi dòng 71
`age_text="45-60"` → `age_text="45_54"`, dòng 84 assert → `"45_54"`, và thêm:

```python
        self.assertEqual(row["demographics"]["age_group_label"], "45-54")
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: lệnh test backend với `<paths>` = `apps/customers/tests/test_customer_profiles_api.py`
Expected: FAIL — `KeyError: 'age_group_label'`

- [ ] **Step 3: Export Excel**

Trong `backend/apps/orders/export_excel.py`, thêm `AGE_GROUP_LABELS` vào import từ
`crm_catalog`, rồi sửa dòng 100:

```python
                    elif key == "age_text": val = AGE_GROUP_LABELS.get(order.assessment.age_text, order.assessment.age_text) or ""
```

Giữ đúng style một dòng của các nhánh xung quanh.

- [ ] **Step 4: Import CSV**

Trong `backend/apps/orders/management/commands/import_assessment_csv.py`, thêm import:

```python
from apps.orders.age_group_backfill import parse_age_group
```

Trong `_apply_scalar` (dòng ~271-292), thêm nhánh cạnh nhánh `used_massage_before`:

```python
            if field == "used_massage_before":
                new_value = _coerce_used_massage(new_raw)
                if new_value is None:
                    continue
            elif field == "age_text":
                # File CSV cũ ghi tuổi dạng số; chuyển sang mã nhóm để không
                # ghi giá trị ngoài whitelist vào cột.
                new_value = parse_age_group(new_raw)
                if not new_value:
                    continue
            else:
                new_value = new_raw
```

- [ ] **Step 5: Hồ sơ khách**

Trong `backend/apps/customers/profile_service.py`, thêm import
`from apps.orders.crm_catalog import AGE_GROUP_LABELS` và sửa dòng 129:

```python
                    "age_text": getattr(assessment, "age_text", "") if assessment else "",
                    "age_group_label": AGE_GROUP_LABELS.get(
                        getattr(assessment, "age_text", "") if assessment else "",
                    ),
```

Trong `backend/apps/customers/serializers.py`, thêm vào
`CustomerProfileDemographicsSerializer` (sau dòng 208):

```python
    age_group_label = serializers.CharField(allow_null=True)
```

- [ ] **Step 6: Chạy test để xác nhận pass**

Run: lệnh test backend với `<paths>` = `apps/customers/tests/ apps/orders/tests/`
Expected: PASS

- [ ] **Step 7: Lint + commit**

```bash
git add backend/apps/orders/export_excel.py \
  backend/apps/orders/management/commands/import_assessment_csv.py \
  backend/apps/customers/profile_service.py backend/apps/customers/serializers.py \
  backend/apps/customers/tests/ backend/apps/orders/tests/
git commit -m "feat: render age group labels in export, import and profile"
```

---

### Task 7: Frontend — đổi ô nhập thành dropdown

**Files:**
- Modify: `frontend/src/features/orders/BlockCrmRating.tsx`
- Modify: `frontend/src/features/orders/OrdersTable/cell-renderers.tsx:211`
- Modify: `frontend/src/features/orders/order-types.ts`
- Modify: `frontend/src/features/customers/customer-utils.tsx:72`
- Modify: `frontend/src/features/customers/components/CustomerGroupCells.tsx:170`
- Modify: `frontend/src/lib/api/customers.ts:185`
- Modify: `frontend/src/api/schema.d.ts` (regenerate)

**Interfaces:**
- Consumes: `crmCatalog.age_group_options` (Task 2), `crm.age_text_label` (Task 3), `demographics.age_group_label` (Task 6).
- Produces: không có task nào phụ thuộc sau.

- [ ] **Step 1: Cập nhật type**

Trong `frontend/src/features/orders/BlockCrmRating.tsx`, thêm vào `CrmCatalogPayload`
(cạnh `occupation_options`):

```ts
  age_group_options?: CrmCatalogOption[];
```

Trong `frontend/src/features/orders/order-types.ts`, cạnh dòng 62:

```ts
  age_text?: string | null;
  age_text_label?: string | null;
```

Trong `frontend/src/lib/api/customers.ts`, cạnh dòng 185:

```ts
    age_text: string;
    age_group_label: string | null;
```

- [ ] **Step 2: Đổi ERow trong BlockCrmRating**

Thêm memo cạnh `painLevelOptions` (dòng ~202):

```ts
  const ageGroupOptions = useMemo(() => toOpts(crmCatalog?.age_group_options), [crmCatalog]);
```

Thay `rowAge` (dòng ~419-426) — bám đúng pattern của `rowPainLevel`:

```tsx
  const rowAge = (
    <ERow
      key="age"
      label="Độ tuổi"
      labelAdornment={<AiFilledBadge info={aiFilled?.age_text} />}
      rawValue={crm?.age_text ?? ""}
      display={
        <span className="text-sm font-medium">
          {ageGroupOptions.find((o) => o.value === crm?.age_text)?.label ||
            crm?.age_text_label ||
            "—"}
        </span>
      }
      type="select"
      options={ageGroupOptions}
      onSave={(v) => patchCrm({ age_text: v ? String(v) : null })}
    />
  );
```

- [ ] **Step 3: Đổi cell renderer trong bảng**

Trong `frontend/src/features/orders/OrdersTable/cell-renderers.tsx`, thay case
`age_text` (dòng 211-212) — bám pattern case `occupation` ngay dưới:

```tsx
    case "age_text":
      return renderCrmSelect(row, onPatch, "age_text", toOpts(crmCatalog?.age_group_options));
```

- [ ] **Step 4: Hiển thị label ở màn khách hàng**

`frontend/src/features/customers/customer-utils.tsx` dòng ~72:

```ts
      age_text: crm.age_text || "",
      age_group_label: crm.age_text_label ?? null,
```

`frontend/src/features/customers/components/CustomerGroupCells.tsx` dòng ~170:

```tsx
            {labelOrDash(p.demographics.age_group_label, p.demographics.age_text)}
```

`labelOrDash` đã được dùng ngay dòng dưới cho `occupation` — dùng lại, không viết mới.

- [ ] **Step 5: Regenerate hợp đồng API (bắt buộc — CI có drift-check)**

`.gitlab-ci.yml:132` và `:198` kiểm tra hai file sinh tự động phải khớp code. Cả hai
đều đổi vì Task 3 thêm `age_text_label` và Task 6 thêm `age_group_label`. Sinh lại ở
cuối cho gọn, thay vì mỗi task một lần.

Trước hết sinh lại `backend/schema.yaml` — chạy trong container backend:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_age_group \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$(pwd -W)/backend:/app" -w //app desi-backend-test:latest \
  python manage.py spectacular --file schema.yaml
```

Kiểm tra không có dòng `Error [` trong output — CI fail nếu có.

Rồi sinh lại type frontend:

```bash
cd frontend && npm run gen:api
```

`gen:api` đã tự chạy `prettier --write` ngay sau `openapi-typescript`
(`frontend/package.json:19`). Đừng chạy `openapi-typescript` trực tiếp —
nó xuất thụt 4 space còn file trong repo dùng 2 space, drift-check sẽ đỏ dù nội dung
không đổi.

- [ ] **Step 6: Typecheck + build**

```bash
cd frontend && npm run typecheck && npm run build
```
Expected: không lỗi. Nếu `typecheck` không tồn tại, dùng `npx tsc --noEmit`.

- [ ] **Step 7: Chạy test frontend**

```bash
cd frontend && npm test
```
Expected: PASS. Nếu `DataCoverageBadge.test.tsx` hoặc test nào khác đụng `age_text`, sửa giá trị sang code mới.

- [ ] **Step 8: Commit**

```bash
git add frontend/src frontend/src/api/schema.d.ts backend/schema.yaml
git commit -m "feat(frontend): age group dropdown for assessment"
```

---

### Task 8: Kiểm chứng toàn hệ + tài liệu vận hành

**Files:**
- Modify: `docs/superpowers/specs/2026-07-27-order-assessment-age-group-design.md` (đánh dấu mục "Việc chưa chốt" đã xong)

- [ ] **Step 1: Chạy toàn bộ suite backend**

Run: lệnh test backend với `<paths>` = `apps`
Expected: PASS. Suite đầy đủ mất ~19 phút — chạy nền và redirect ra file log, vì shell nền bị kill nhưng container docker vẫn chạy tiếp:

```bash
<lệnh test backend với paths=apps> > /tmp/age-group-suite.log 2>&1
```

- [ ] **Step 2: Lint backend**

Run: lệnh lint backend
Expected: `All checks passed!`

- [ ] **Step 3: Rà không còn tàn dư**

```bash
grep -rn "age_buckets\|bucket_age\|AGE_BUCKET" backend frontend docs/superpowers/specs
```
Expected: chỉ còn nhắc trong spec (phần mô tả hiện trạng cũ), không còn trong code.

- [ ] **Step 4: Xác nhận hợp đồng API không drift**

Lặp lại đúng hai lệnh sinh ở Task 7 Step 5, rồi:

```bash
git status --porcelain backend/schema.yaml frontend/src/api/schema.d.ts
```
Expected: không có output. Có output nghĩa là Task 7 sinh thiếu — commit lại phần chênh.

- [ ] **Step 5: Cập nhật spec**

Trong spec, thay mục "Việc chưa chốt" bằng kết quả thực tế của query đuôi dữ liệu (nếu
đã chạy), hoặc ghi rõ là vẫn chưa chạy và đây là bước bắt buộc trước khi deploy prod.

- [ ] **Step 6: Commit + push**

```bash
git add docs/superpowers/specs
git commit -m "docs: close out age group spec"
git push -u origin worktree-feat-assessment-age-group
```

Gửi link MR cho người review.

---

## Trước khi deploy prod

Không nằm trong task nào vì là thao tác thủ công trên VPS. **Bắt buộc chạy trước
khi `migrate`**, vì backfill ghi đè mất giá trị gốc và migration không có reverse:

```
alias P="docker exec -i desi-postgres psql -U desi -d desi"
```
```
Q="copy (select id,age_text from order_assessments where age_text<>'')"
```
```
P -c "$Q to stdout csv header" > age_text_backup.csv
```

Giữ `age_text_backup.csv` cùng chỗ backup DB.

Sau khi migrate, đối chiếu phân bố với bảng trong spec:

```
Q2="select age_text,count(*) c from order_assessments where age_text<>'' group by 1 order by c desc"
```
```
P -tAc "$Q2"
```

Kỳ vọng: 7 dòng, `65_plus` khoảng 850, `45_54` khoảng 706, `55_64` khoảng 542,
`35_44` khoảng 489, `25_34` khoảng 54, `18_24` bằng 0, `not_disclosed` khoảng 385
(84 giá trị lộn xộn trong top-100 cộng phần đuôi ~300 dòng). Nếu `not_disclosed`
vượt xa 400, dừng lại và soi lại parser trước khi cho sale dùng.
