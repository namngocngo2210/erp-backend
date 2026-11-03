# Auto-fill OrderAssessment từ transcript cuộc gọi — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transcript cuộc gọi đã có trong `callcenter_call_records` được Gemini trích xuất thành dữ liệu có cấu trúc và tự điền vào các field chân dung/nhu cầu khách đang trống của `OrderAssessment`, có badge "AI" + trích dẫn để sale kiểm chứng.

**Architecture:** Task Celery mới quét `CallRecord` đã transcribe xong → `apps/callcenter/extract.py` gọi Gemini với structured output → payload đi qua `apps/orders/ai_extract_contract.py` để validate theo catalog → `assessment_ai_fill_service` ghi vào assessment bằng chính đường ghi mà sale đang dùng (`_upsert_order_assessment`). `callcenter` không import `OrderAssessment`; `orders` không biết Gemini tồn tại.

**Tech Stack:** Django 5 + DRF, Celery + Redis, PostgreSQL, `google-genai` SDK, React + TypeScript + shadcn/ui.

Spec: `docs/superpowers/specs/2026-07-24-call-transcript-assessment-autofill-design.md`

## Global Constraints

- Python 3.12+, code theo clean architecture đang có trong `backend/apps/*`.
- Worktree: `C:/Users/MYPC/Desktop/workspace/desi-order-wt-assessment-autofill`, branch `feat/call-transcript-assessment-autofill`.
- Toàn bộ comment/docstring mới viết **tiếng Việt**, giải thích *vì sao* chứ không mô tả lại code — bám đúng giọng của `apps/callcenter/transcribe.py`.
- Commit message **tiếng Anh**, ngắn, không có trailer `Co-Authored-By`.
- **KHÔNG** cho AI ghi vào `sales_status_code`, `delivery_business_status_code`, `potential_level`, `bomb_reason_*`, `not_closed_reason_code`, `internal_note`, `customer_attitude_raw`, `purchase_barrier_codes`, `comparing_brand_text`, `call_duration_code`, `post_purchase_*`, `next_followup_at`.
- Field AI được phép ghi, đúng 9 mục: `age_text`, `occupation_code`, `pain_level_code`, `pain_duration_code`, `lifestyle_habit_codes`, `relax_time_codes`, `delax_source_codes`, `used_massage_before`, `needs`.
- Không log nội dung transcript hoặc giá trị extract (PII). Chỉ log `call_id`, `order_id`, tên field, status.
- **KHÔNG BAO GIỜ** gọi `cache.clear()` trong test (FLUSHDB đè Redis thật).
- `Order.channel` là FK **NOT NULL** (`PROTECT`). Mọi test tạo đơn phải tạo `Channel` trước:
  `Channel.objects.create(code="lp-ai", name="Landing AI")` rồi `Order.objects.create(channel=channel, name=..., phone=..., status=0)`.
- **CI chặn drift `schema.yaml`** (`.github/workflows/backend-ci.yml:70-76`): đổi serializer mà quên regen là CI đỏ.
  Regen bằng `python manage.py spectacular --file schema.yaml` (chạy trong container test, xem Task 3 Step 7).

**Lệnh chạy test backend** (dùng nguyên văn, `POSTGRES_DB` riêng cho worktree này):

```bash
cd "C:/Users/MYPC/Desktop/workspace/desi-order-wt-assessment-autofill" && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_autofill \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -e PYTEST_ADDOPTS="-p no:cacheprovider -o cache_dir=/tmp/pc" \
  -v "$PWD/backend:/app" -w /app desi-backend-test:latest \
  sh -c 'mkdir -p /tmp/prom_multiproc && python -m pytest <PATHS> -q --create-db'
```

Lần chạy đầu tiên phải làm một lần: `docker network connect desi-test-net desi-test-redis`.

**Lệnh chạy test frontend:**

```bash
cd "C:/Users/MYPC/Desktop/workspace/desi-order-wt-assessment-autofill/frontend" && npm run test -- <PATH>
```

---

## File Structure

| File | Trách nhiệm |
|---|---|
| `backend/apps/callcenter/models.py` (sửa) | `ExtractStatus` + 4 cột `extract_*` trên `CallRecord` |
| `backend/apps/callcenter/migrations/0002_call_record_extract_fields.py` (mới) | migration cột + index |
| `backend/config/settings_parts/base.py` (sửa) | 5 env `GEMINI_EXTRACT_*` |
| `backend/apps/orders/ai_extract_contract.py` (mới) | **Hợp đồng dữ liệu**: danh sách field AI được ghi, JSON schema sinh từ catalog, chuẩn hoá + loại code lạ |
| `backend/apps/callcenter/extract.py` (mới) | Gọi Gemini cho 1 transcript, phân loại lỗi, vòng lặp batch |
| `backend/apps/orders/application/services/assessment_ai_fill_service.py` (mới) | Luật merge + ghi assessment + journey event |
| `backend/apps/orders/models.py` (sửa) | `OrderAssessment.ai_filled_fields` |
| `backend/apps/orders/migrations/0108_orderassessment_ai_filled_fields.py` (mới) | migration cột |
| `backend/apps/orders/crm_catalog.py` (sửa) | nhãn event `assessment_ai_filled` |
| `backend/apps/orders/application/services/order_service_helpers.py` (sửa) | xoá key `ai_filled_fields` khi sale sửa đè |
| `backend/apps/orders/transport/serializers/.../product_item_crm_serializers.py` (sửa) | trả `ai_filled_fields` (read-only) |
| `backend/apps/callcenter/tasks.py` (sửa) | task `extract_pending_call_insights` |
| `backend/config/settings_parts/celery.py` (sửa) | beat 5' |
| `backend/apps/callcenter/management/commands/extract_call_insights.py` (mới) | backfill + `--dry-run` |
| `backend/apps/callcenter/tests/test_call_insights.py` (mới) | test extract + task |
| `backend/apps/orders/tests/test_assessment_ai_fill.py` (mới) | test contract + merge + serializer |
| `frontend/src/components/orders/AiFilledBadge.tsx` (mới) | badge + tooltip trích dẫn |
| `frontend/src/components/orders/Sheet3AssessmentPanel.tsx` (sửa) | gắn badge vào nhãn field |
| `frontend/src/components/orders/__tests__/AiFilledBadge.test.tsx` (mới) | test FE |

**Lưu ý về ranh giới**: spec §3 nói prompt sinh từ `crm_catalog`. Trong plan này *hợp đồng* (schema + whitelist + chuẩn hoá) đặt ở `apps/orders/ai_extract_contract.py` để `orders` giữ quyền sở hữu luật dữ liệu, còn `callcenter` chỉ dựng câu prompt và gọi API. Kết quả không đổi, nhưng `callcenter` không phải import `crm_catalog`.

**Lưu ý về `evidence`**: spec mô tả `evidence` là map `{field: quote}`. Gemini structured output không hỗ trợ object khoá động ổn định, nên schema gửi đi dùng **mảng** `[{field, quote}]` rồi Python quy về map. Hành vi không đổi.

---

### Task 1: Cột `extract_*` trên CallRecord + settings

**Files:**
- Modify: `backend/apps/callcenter/models.py:12-19` (thêm `ExtractStatus` cạnh `TranscribeStatus`), `:47-52` (thêm 4 cột), `:59-68` (thêm index)
- Create: `backend/apps/callcenter/migrations/0002_call_record_extract_fields.py`
- Modify: `backend/config/settings_parts/base.py:289` (thêm sau `GEMINI_QUOTA_COOLDOWN_MINUTES`)
- Modify: `.env.example`
- Create: `backend/apps/callcenter/tests/test_call_insights.py`

**Interfaces:**
- Consumes: `CallRecord`, `TranscribeStatus` đã có.
- Produces: `ExtractStatus` (IntegerChoices: `PENDING=0, PROCESSING=1, DONE=2, FAILED=3, SKIPPED=4`); `CallRecord.extract_status/extract_payload/extract_error/extract_attempts`; settings `GEMINI_EXTRACT_ENABLED: bool`, `GEMINI_EXTRACT_MODEL: str`, `GEMINI_EXTRACT_BATCH: int`, `GEMINI_EXTRACT_MAX_ATTEMPTS: int`, `GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS: int`.

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/callcenter/tests/test_call_insights.py`:

```python
"""Test luồng extract chân dung khách từ transcript (Gemini structured output).

Mock toàn bộ Gemini — không gọi mạng thật.
"""

from django.test import TestCase

from apps.callcenter.models import CallRecord, ExtractStatus, TranscribeStatus


class ExtractStatusFieldsTests(TestCase):
    def test_new_record_defaults_to_pending_extract(self):
        record = CallRecord.objects.create(
            call_id="c-extract-1",
            transcribe_status=TranscribeStatus.DONE,
            transcript="Nhân viên: alo ạ",
        )
        record.refresh_from_db()
        self.assertEqual(record.extract_status, ExtractStatus.PENDING)
        self.assertEqual(record.extract_payload, {})
        self.assertEqual(record.extract_error, "")
        self.assertEqual(record.extract_attempts, 0)
```

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: lệnh docker ở Global Constraints với `<PATHS>` = `apps/callcenter/tests/test_call_insights.py`
Expected: FAIL — `ImportError: cannot import name 'ExtractStatus' from 'apps.callcenter.models'`

- [ ] **Step 3: Thêm `ExtractStatus` và 4 cột vào model**

Trong `backend/apps/callcenter/models.py`, thêm ngay sau class `TranscribeStatus`:

```python
class ExtractStatus(models.IntegerChoices):
    """Trạng thái trích xuất chân dung khách từ transcript.

    Tách hẳn khỏi transcribe_status: 1 transcript có thể transcribe xong từ lâu
    mà chưa extract (bật tính năng sau), và ngược lại extract lỗi không được
    kéo transcript về pending làm mất công gọi Gemini lần nữa với audio.
    """

    PENDING = 0, "Chờ extract"
    PROCESSING = 1, "Đang extract"
    DONE = 2, "Hoàn thành"
    FAILED = 3, "Thất bại"
    # Transcript quá ngắn (không có nội dung tư vấn) — lưu để không quét lại mãi.
    SKIPPED = 4, "Bỏ qua"
```

Thêm vào `CallRecord`, ngay sau `transcribe_attempts`:

```python
    extract_status = models.PositiveSmallIntegerField(
        choices=ExtractStatus.choices, default=ExtractStatus.PENDING
    )
    # Raw output Gemini + phần bị loại (key "rejected") — giữ để soi chất lượng
    # prompt theo thời gian mà không phải gọi lại API.
    extract_payload = models.JSONField(default=dict, blank=True)
    extract_error = models.TextField(blank=True, default="")
    extract_attempts = models.PositiveSmallIntegerField(default=0)
```

Thêm vào `Meta.indexes`:

```python
            models.Index(fields=["extract_status", "id"], name="callrec_extract_status_idx"),
```

- [ ] **Step 4: Sinh migration**

```bash
cd "C:/Users/MYPC/Desktop/workspace/desi-order-wt-assessment-autofill" && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_autofill \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$PWD/backend:/app" -w /app desi-backend-test:latest \
  python manage.py makemigrations callcenter -n call_record_extract_fields
```

Expected: tạo `apps/callcenter/migrations/0002_call_record_extract_fields.py` với 4 `AddField` + 1 `AddIndex`.

- [ ] **Step 5: Thêm settings**

Trong `backend/config/settings_parts/base.py`, thêm ngay sau dòng `GEMINI_QUOTA_COOLDOWN_MINUTES = ...`:

```python
# Extract chân dung khách từ transcript (docs/superpowers/specs/2026-07-24-...-design.md).
# Kill switch riêng với ZENIFY_CALL_SYNC_ENABLED: transcribe có thể chạy mà chưa
# muốn ghi gì vào assessment.
GEMINI_EXTRACT_ENABLED = os.getenv("GEMINI_EXTRACT_ENABLED", "0") in ("1", "true", "True")
GEMINI_EXTRACT_MODEL = os.getenv("GEMINI_EXTRACT_MODEL", "gemini-flash-latest").strip()
GEMINI_EXTRACT_BATCH = int(os.getenv("GEMINI_EXTRACT_BATCH", "20"))
GEMINI_EXTRACT_MAX_ATTEMPTS = int(os.getenv("GEMINI_EXTRACT_MAX_ATTEMPTS", "3"))
# Ngắn hơn ngưỡng = cuộc gọi không có nội dung tư vấn → SKIPPED, không đốt request.
GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS = int(os.getenv("GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS", "200"))
```

Trong `.env.example`, thêm cạnh block `GEMINI_*` đã có:

```
GEMINI_EXTRACT_ENABLED=0
GEMINI_EXTRACT_MODEL=gemini-flash-latest
GEMINI_EXTRACT_BATCH=20
GEMINI_EXTRACT_MAX_ATTEMPTS=3
GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS=200
```

- [ ] **Step 6: Chạy test để chắc chắn nó pass**

Run: lệnh docker với `<PATHS>` = `apps/callcenter/tests/test_call_insights.py`
Expected: PASS — 1 passed

- [ ] **Step 7: Commit**

```bash
cd "C:/Users/MYPC/Desktop/workspace/desi-order-wt-assessment-autofill"
git add backend/apps/callcenter/models.py backend/apps/callcenter/migrations/0002_call_record_extract_fields.py \
  backend/config/settings_parts/base.py .env.example backend/apps/callcenter/tests/test_call_insights.py
git commit -m "feat(callcenter): add extract state machine fields to call records"
```

---

### Task 2: Hợp đồng dữ liệu AI (`ai_extract_contract.py`)

**Files:**
- Create: `backend/apps/orders/ai_extract_contract.py`
- Create: `backend/apps/orders/tests/test_assessment_ai_fill.py`

**Interfaces:**
- Consumes: `apps.orders.crm_catalog` (`OCCUPATION_LABELS`, `PAIN_LEVEL_LABELS`, `PAIN_DURATION_LABELS`, `LIFESTYLE_HABIT_LABELS`, `RELAX_TIME_LABELS`, `DELAX_SOURCE_LABELS`, `NEED_TYPE_SEED`), `apps.orders.assessment_fields.SHORT_TEXT_MAX_LEN`.
- Produces:
  - `AI_SCALAR_FIELDS: tuple[str, ...]` = `("age_text", "occupation_code", "pain_level_code", "pain_duration_code", "used_massage_before")`
  - `AI_ARRAY_FIELDS: tuple[str, ...]` = `("lifestyle_habit_codes", "relax_time_codes", "delax_source_codes")`
  - `AI_EVIDENCE_KEYS: tuple[str, ...]` = scalar + array + `("needs",)`
  - `build_extract_response_schema() -> dict`
  - `build_prompt_catalog_text() -> str`
  - `normalize_extracted(raw: dict) -> tuple[dict, dict]` — trả `(clean, rejected)`

- [ ] **Step 1: Viết test thất bại**

Tạo `backend/apps/orders/tests/test_assessment_ai_fill.py`:

```python
"""Test hợp đồng dữ liệu AI extract + luật merge vào OrderAssessment."""

from django.test import TestCase

from apps.orders.ai_extract_contract import (
    AI_EVIDENCE_KEYS,
    build_extract_response_schema,
    normalize_extracted,
)


class ExtractContractTests(TestCase):
    def test_schema_enum_comes_from_catalog(self):
        schema = build_extract_response_schema()
        props = schema["properties"]
        self.assertIn("severe", props["pain_level_code"]["enum"])
        self.assertIn("sit_a_lot", props["lifestyle_habit_codes"]["items"]["enum"])
        self.assertIn("neck_shoulder_nape", props["need_type_codes"]["items"]["enum"])
        self.assertEqual(
            set(props["evidence"]["items"]["properties"]["field"]["enum"]),
            set(AI_EVIDENCE_KEYS),
        )

    def test_normalize_drops_codes_outside_catalog(self):
        clean, rejected = normalize_extracted(
            {
                "pain_level_code": "khong_co_ma_nay",
                "pain_duration_code": "over_1y",
                "lifestyle_habit_codes": ["sit_a_lot", "bay_len_troi", "sit_a_lot"],
                "need_type_codes": ["knee_pain", "khong_ton_tai"],
                "evidence": [{"field": "pain_duration_code", "quote": "đau hơn một năm rồi"}],
            }
        )
        self.assertNotIn("pain_level_code", clean)
        self.assertEqual(clean["pain_duration_code"], "over_1y")
        self.assertEqual(clean["lifestyle_habit_codes"], ["sit_a_lot"])
        self.assertEqual(clean["need_type_codes"], ["knee_pain"])
        self.assertEqual(clean["evidence"], {"pain_duration_code": "đau hơn một năm rồi"})
        self.assertIn("pain_level_code", rejected)
        self.assertIn("bay_len_troi", rejected["lifestyle_habit_codes"])

    def test_normalize_truncates_age_text_and_keeps_bool(self):
        clean, _ = normalize_extracted(
            {"age_text": "x" * 100, "used_massage_before": False, "evidence": []}
        )
        self.assertEqual(len(clean["age_text"]), 32)
        self.assertIs(clean["used_massage_before"], False)

    def test_normalize_drops_wrong_types(self):
        clean, rejected = normalize_extracted(
            {"age_text": 45, "used_massage_before": "có", "lifestyle_habit_codes": "sit_a_lot",
             "evidence": []}
        )
        self.assertNotIn("age_text", clean)
        self.assertNotIn("used_massage_before", clean)
        self.assertNotIn("lifestyle_habit_codes", clean)
        self.assertEqual(set(rejected), {"age_text", "used_massage_before", "lifestyle_habit_codes"})
```

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: lệnh docker với `<PATHS>` = `apps/orders/tests/test_assessment_ai_fill.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.orders.ai_extract_contract'`

- [ ] **Step 3: Viết `ai_extract_contract.py`**

```python
"""Hợp đồng dữ liệu giữa Gemini và OrderAssessment.

`orders` giữ quyền sở hữu luật: field nào AI được điền, code nào hợp lệ, chuẩn
hoá ra sao. `callcenter` chỉ dựng câu prompt và gọi API — không import catalog,
không tự quyết field nào được ghi.

Enum trong response_schema sinh THẲNG từ crm_catalog: thêm option mới trong
catalog là prompt tự cập nhật, không có chỗ nào lệch nhau.
"""

from __future__ import annotations

from apps.orders.assessment_fields import SHORT_TEXT_MAX_LEN
from apps.orders.crm_catalog import (
    DELAX_SOURCE_LABELS,
    LIFESTYLE_HABIT_LABELS,
    NEED_TYPE_SEED,
    OCCUPATION_LABELS,
    PAIN_DURATION_LABELS,
    PAIN_LEVEL_LABELS,
    RELAX_TIME_LABELS,
)

# occupation_code khai là kind="text" không whitelist trong assessment_fields
# (sale gõ tự do được). Đường AI thì ÉP whitelist — để LLM tự chế chuỗi sẽ làm
# bẩn report mà không ai phát hiện.
_SCALAR_WHITELIST: dict[str, dict[str, str]] = {
    "occupation_code": OCCUPATION_LABELS,
    "pain_level_code": PAIN_LEVEL_LABELS,
    "pain_duration_code": PAIN_DURATION_LABELS,
}
_ARRAY_WHITELIST: dict[str, dict[str, str]] = {
    "lifestyle_habit_codes": LIFESTYLE_HABIT_LABELS,
    "relax_time_codes": RELAX_TIME_LABELS,
    "delax_source_codes": DELAX_SOURCE_LABELS,
}
NEED_TYPE_LABELS: dict[str, str] = {item["code"]: item["name_vi"] for item in NEED_TYPE_SEED}

AI_SCALAR_FIELDS: tuple[str, ...] = (
    "age_text",
    "occupation_code",
    "pain_level_code",
    "pain_duration_code",
    "used_massage_before",
)
AI_ARRAY_FIELDS: tuple[str, ...] = tuple(_ARRAY_WHITELIST)
# "needs" không phải cột trên assessment (bảng OrderNeed riêng) nhưng vẫn cần 1
# khoá evidence để áp cùng luật "không trích dẫn thì không ghi".
AI_EVIDENCE_KEYS: tuple[str, ...] = AI_SCALAR_FIELDS + AI_ARRAY_FIELDS + ("needs",)


def build_extract_response_schema() -> dict:
    """Schema OpenAPI-subset cho `response_schema` của google-genai."""
    properties: dict[str, dict] = {
        "age_text": {"type": "string", "nullable": True},
        "used_massage_before": {"type": "boolean", "nullable": True},
    }
    for name, labels in _SCALAR_WHITELIST.items():
        properties[name] = {"type": "string", "enum": sorted(labels), "nullable": True}
    for name, labels in _ARRAY_WHITELIST.items():
        properties[name] = {"type": "array", "items": {"type": "string", "enum": sorted(labels)}}
    properties["need_type_codes"] = {
        "type": "array",
        "items": {"type": "string", "enum": sorted(NEED_TYPE_LABELS)},
    }
    # Mảng thay vì object khoá động: Gemini không giữ ổn định object có key tự do.
    properties["evidence"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": sorted(AI_EVIDENCE_KEYS)},
                "quote": {"type": "string"},
            },
            "required": ["field", "quote"],
        },
    }
    return {"type": "object", "properties": properties}


def build_prompt_catalog_text() -> str:
    """Liệt kê code kèm nhãn tiếng Việt để LLM map đúng ngữ nghĩa, không đoán mò."""
    blocks: list[str] = []
    for name, labels in {**_SCALAR_WHITELIST, **_ARRAY_WHITELIST}.items():
        options = "; ".join(f"{code} = {label}" for code, label in labels.items())
        blocks.append(f"- {name}: {options}")
    needs = "; ".join(f"{code} = {label}" for code, label in NEED_TYPE_LABELS.items())
    blocks.append(f"- need_type_codes: {needs}")
    return "\n".join(blocks)


def _clean_evidence(raw_evidence) -> dict[str, str]:
    if not isinstance(raw_evidence, list):
        return {}
    result: dict[str, str] = {}
    for item in raw_evidence:
        if not isinstance(item, dict):
            continue
        field = item.get("field")
        quote = item.get("quote")
        if field in AI_EVIDENCE_KEYS and isinstance(quote, str) and quote.strip():
            result.setdefault(field, quote.strip())
    return result


def normalize_extracted(raw: dict) -> tuple[dict, dict]:
    """Lọc output LLM về đúng hợp đồng. Trả ``(clean, rejected)``.

    KHÔNG tin enum của LLM: schema chỉ là gợi ý, model vẫn trả code lạ được.
    Phần bị loại giữ lại trong ``rejected`` để soi chất lượng prompt về sau.
    """
    clean: dict = {}
    rejected: dict = {}

    age = raw.get("age_text")
    if isinstance(age, str) and age.strip():
        clean["age_text"] = age.strip()[:SHORT_TEXT_MAX_LEN]
    elif age is not None:
        rejected["age_text"] = age

    used = raw.get("used_massage_before")
    if isinstance(used, bool):
        clean["used_massage_before"] = used
    elif used is not None:
        rejected["used_massage_before"] = used

    for name, labels in _SCALAR_WHITELIST.items():
        value = raw.get(name)
        if value is None:
            continue
        if isinstance(value, str) and value in labels:
            clean[name] = value
        else:
            rejected[name] = value

    for name, labels in {**_ARRAY_WHITELIST, "need_type_codes": NEED_TYPE_LABELS}.items():
        value = raw.get(name)
        if value is None:
            continue
        if not isinstance(value, list):
            rejected[name] = value
            continue
        kept: list[str] = []
        dropped: list = []
        for code in value:
            if isinstance(code, str) and code in labels and code not in kept:
                kept.append(code)
            elif code not in kept:
                dropped.append(code)
        if kept:
            clean[name] = kept
        if dropped:
            rejected[name] = dropped

    clean["evidence"] = _clean_evidence(raw.get("evidence"))
    return clean, rejected
```

- [ ] **Step 4: Chạy test để chắc chắn nó pass**

Run: lệnh docker với `<PATHS>` = `apps/orders/tests/test_assessment_ai_fill.py`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/apps/orders/ai_extract_contract.py backend/apps/orders/tests/test_assessment_ai_fill.py
git commit -m "feat(orders): add AI extraction data contract built from CRM catalog"
```

---

### Task 3: Cột `ai_filled_fields` + serializer + xoá key khi sale sửa

**Files:**
- Modify: `backend/apps/orders/models.py:543` (thêm cột trước `updated_by`)
- Create: `backend/apps/orders/migrations/0108_orderassessment_ai_filled_fields.py`
- Modify: `backend/apps/orders/crm_catalog.py:499` (thêm option event)
- Modify: `backend/apps/orders/application/services/order_service_helpers.py:582` (xoá key sau khi apply payload)
- Modify: `backend/apps/orders/transport/serializers/order_serializers_impl/product_item_crm_serializers.py:406-416`
- Modify: `backend/apps/orders/tests/test_assessment_ai_fill.py`

**Interfaces:**
- Consumes: `OrderAssessment`, `OrderServiceHelpers._upsert_order_assessment` đã có.
- Produces: `OrderAssessment.ai_filled_fields: dict`; event code `"assessment_ai_filled"`; serializer trả `ai_filled_fields` read-only.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `backend/apps/orders/tests/test_assessment_ai_fill.py`:

```python
from apps.channels_core.models import Channel
from apps.orders.application.services.order_service_helpers import OrderServiceHelpers
from apps.orders.crm_catalog import EVENT_TYPE_LABELS
from apps.orders.models import Order, OrderAssessment


def make_order(code: str) -> Order:
    """Order.channel là FK NOT NULL (PROTECT) — không tạo Channel là IntegrityError."""
    channel = Channel.objects.create(code=code, name=f"Landing {code}")
    return Order.objects.create(channel=channel, name="Khách A", phone="0912345678", status=0)


class AiFilledFieldsColumnTests(TestCase):
    def setUp(self):
        self.order = make_order("lp-ai-col")

    def test_event_type_has_label(self):
        self.assertEqual(EVENT_TYPE_LABELS["assessment_ai_filled"], "AI điền từ cuộc gọi")

    def test_column_defaults_to_empty_dict(self):
        assessment = OrderAssessment.objects.create(order=self.order)
        assessment.refresh_from_db()
        self.assertEqual(assessment.ai_filled_fields, {})

    def test_sale_edit_clears_ai_key_for_that_field(self):
        assessment = OrderAssessment.objects.create(
            order=self.order,
            pain_level_code="light",
            age_text="40",
            ai_filled_fields={
                "pain_level_code": {"call_record_id": 1, "quote": "hơi mỏi", "at": "2026-07-24T09:00:00+07:00"},
                "age_text": {"call_record_id": 1, "quote": "em 40", "at": "2026-07-24T09:00:00+07:00"},
            },
        )
        OrderServiceHelpers._upsert_order_assessment(
            self.order, {"pain_level_code": "severe"}, None
        )
        assessment.refresh_from_db()
        self.assertNotIn("pain_level_code", assessment.ai_filled_fields)
        self.assertIn("age_text", assessment.ai_filled_fields)

    def test_serializer_exposes_ai_filled_fields_read_only(self):
        from apps.orders.transport.serializers.order_serializers_impl.product_item_crm_serializers import (
            OrderCRMSerializer,
        )

        marker = {"age_text": {"call_record_id": 3, "quote": "em 40", "at": "2026-07-24T09:00:00+07:00"}}
        assessment = OrderAssessment.objects.create(order=self.order, ai_filled_fields=marker)
        data = OrderCRMSerializer(assessment).data
        self.assertEqual(data["ai_filled_fields"], marker)
        self.assertTrue(OrderCRMSerializer().fields["ai_filled_fields"].read_only)
```

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: lệnh docker với `<PATHS>` = `apps/orders/tests/test_assessment_ai_fill.py::AiFilledFieldsColumnTests`
Expected: FAIL — `KeyError: 'assessment_ai_filled'`

- [ ] **Step 3: Thêm cột + nhãn event**

Trong `backend/apps/orders/models.py`, thêm ngay trước `updated_by` của `OrderAssessment`:

```python
    # Field nào do AI điền từ transcript + trích dẫn làm căn cứ. Một JSONB thay vì
    # 25 cột `*_source`: thêm field CRM mới không phải đụng schema.
    # Shape: {"<field>": {"call_record_id": int, "quote": str, "at": iso8601}}
    # Sale sửa đè field nào thì key đó bị xoá (xem _upsert_order_assessment).
    ai_filled_fields = models.JSONField(default=dict, blank=True)
```

Trong `backend/apps/orders/crm_catalog.py`, thêm vào cuối `EVENT_TYPE_OPTIONS`:

```python
    # Auto-fill từ transcript cuộc gọi — actor=None để timeline phân biệt với người.
    CatalogOption("assessment_ai_filled", "AI điền từ cuộc gọi"),
```

- [ ] **Step 4: Sinh migration**

```bash
cd "C:/Users/MYPC/Desktop/workspace/desi-order-wt-assessment-autofill" && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_autofill \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$PWD/backend:/app" -w /app desi-backend-test:latest \
  python manage.py makemigrations orders -n orderassessment_ai_filled_fields
```

Expected: tạo `0108_orderassessment_ai_filled_fields.py` với 1 `AddField`.

- [ ] **Step 5: Xoá key khi sale sửa đè**

Trong `backend/apps/orders/application/services/order_service_helpers.py`, ngay SAU dòng
`OrderServiceHelpers._save_assessment(assessment, changed_fields, created, actor)` (dòng 582) và TRƯỚC block `if "needs" in crm_payload:`, chèn:

```python
        # Sale (hoặc bất kỳ ai) sửa đè field nào thì field đó không còn là "AI điền".
        # Đặt ở đây nên luồng AI cũng bị xoá key ngay sau khi ghi — assessment_ai_fill_service
        # cố ý set lại ai_filled_fields SAU khi gọi helper, không cần cờ phân biệt.
        stale_keys = [name for name in changed_fields if name in (assessment.ai_filled_fields or {})]
        if "needs" in crm_payload and "needs" in (assessment.ai_filled_fields or {}):
            stale_keys.append("needs")
        if stale_keys:
            for name in stale_keys:
                assessment.ai_filled_fields.pop(name, None)
            assessment.save(update_fields=["ai_filled_fields", "updated_at"])
```

- [ ] **Step 6: Trả `ai_filled_fields` qua API**

Trong `product_item_crm_serializers.py`, thêm `"ai_filled_fields"` vào list trong `_build_meta_fields()` (ngay trước `"created_at"`):

```python
        "ai_filled_fields",
        "created_at",
```

Và thêm vào `OrderCRMSerializer` (cạnh `needs = ...`) để chắc chắn read-only:

```python
    ai_filled_fields = serializers.JSONField(read_only=True)
```

- [ ] **Step 7: Regen `schema.yaml` (bắt buộc — CI chặn drift)**

```bash
cd "C:/Users/MYPC/Desktop/workspace/desi-order-wt-assessment-autofill" && MSYS_NO_PATHCONV=1 docker run --rm --network desi-test-net \
  -e POSTGRES_HOST=desi-test-pg -e POSTGRES_DB=desi_autofill \
  -e REDIS_HOST=desi-test-redis -e REDIS_PASSWORD= -e REDIS_CACHE_DB=7 \
  -e DJANGO_SECRET_KEY=test-secret-for-ci-only \
  -e ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" \
  -v "$PWD/backend:/app" -w /app desi-backend-test:latest \
  python manage.py spectacular --file schema.yaml
```

Expected: `backend/schema.yaml` xuất hiện `ai_filled_fields` trong schema `OrderCRM`.

- [ ] **Step 8: Chạy test để chắc chắn nó pass**

Run: lệnh docker với `<PATHS>` = `apps/orders/tests/test_assessment_ai_fill.py`
Expected: PASS — 8 passed

- [ ] **Step 9: Commit**

```bash
git add backend/apps/orders/models.py backend/apps/orders/migrations/0108_orderassessment_ai_filled_fields.py \
  backend/apps/orders/crm_catalog.py backend/apps/orders/application/services/order_service_helpers.py \
  backend/apps/orders/transport/serializers/order_serializers_impl/product_item_crm_serializers.py \
  backend/apps/orders/tests/test_assessment_ai_fill.py backend/schema.yaml
git commit -m "feat(orders): track AI-filled assessment fields and clear on manual edit"
```

---

### Task 4: Service ghi kết quả vào assessment

**Files:**
- Create: `backend/apps/orders/application/services/assessment_ai_fill_service.py`
- Modify: `backend/apps/orders/tests/test_assessment_ai_fill.py`

**Interfaces:**
- Consumes: `normalize_extracted` (Task 2), `OrderAssessment.ai_filled_fields` (Task 3), `OrderServiceHelpers._upsert_order_assessment`, `OrderServiceHelpers._create_journey_event`.
- Produces: `apply_ai_insights(*, order: Order, clean: dict, call_record_id: int) -> list[str]` — trả danh sách tên field đã điền (đã sort), rỗng nếu không điền gì.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `backend/apps/orders/tests/test_assessment_ai_fill.py`:

```python
from apps.orders.application.services.assessment_ai_fill_service import apply_ai_insights
from apps.orders.models import NeedGroup, NeedType, OrderJourneyEvent, OrderNeed
from apps.users.test_helpers import make_admin


def _clean(**overrides):
    payload = {
        "age_text": "45",
        "pain_level_code": "severe",
        "lifestyle_habit_codes": ["sit_a_lot"],
        "need_type_codes": ["knee_pain"],
        "evidence": {
            "age_text": "em 45 tuổi",
            "pain_level_code": "đau nặng lắm",
            "lifestyle_habit_codes": "ngồi văn phòng cả ngày",
            "needs": "đầu gối đau",
        },
    }
    payload.update(overrides)
    return payload


class ApplyAiInsightsTests(TestCase):
    def setUp(self):
        self.order = make_order("lp-ai-apply")  # helper ở Task 3, cùng file
        group = NeedGroup.objects.create(code="pain_relief", name_vi="Giảm đau nhức", sort_order=10)
        NeedType.objects.create(code="knee_pain", group=group, name_vi="Đau đầu gối", sort_order=80)

    def test_fills_empty_fields_and_creates_needs(self):
        filled = apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.age_text, "45")
        self.assertEqual(assessment.pain_level_code, "severe")
        self.assertEqual(assessment.lifestyle_habit_codes, ["sit_a_lot"])
        self.assertEqual(
            list(OrderNeed.objects.filter(order_assessment=assessment).values_list("need_type__code", flat=True)),
            ["knee_pain"],
        )
        self.assertEqual(filled, ["age_text", "lifestyle_habit_codes", "needs", "pain_level_code"])
        self.assertEqual(assessment.ai_filled_fields["pain_level_code"]["call_record_id"], 7)
        self.assertEqual(assessment.ai_filled_fields["pain_level_code"]["quote"], "đau nặng lắm")

    def test_never_overwrites_values_entered_by_sale(self):
        OrderAssessment.objects.create(order=self.order, pain_level_code="light", age_text="")
        apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.pain_level_code, "light")
        self.assertEqual(assessment.age_text, "45")
        self.assertNotIn("pain_level_code", assessment.ai_filled_fields)

    def test_does_not_union_non_empty_arrays(self):
        OrderAssessment.objects.create(order=self.order, lifestyle_habit_codes=["sports"])
        apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.lifestyle_habit_codes, ["sports"])

    def test_does_not_add_needs_when_assessment_already_has_some(self):
        assessment = OrderAssessment.objects.create(order=self.order)
        other = NeedType.objects.create(
            code="calf_pain", group=NeedGroup.objects.get(code="pain_relief"),
            name_vi="Đau bắp chân", sort_order=90,
        )
        OrderNeed.objects.create(order_assessment=assessment, need_type=other)
        apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        self.assertEqual(
            list(OrderNeed.objects.filter(order_assessment=assessment).values_list("need_type__code", flat=True)),
            ["calf_pain"],
        )

    def test_skips_fields_without_evidence(self):
        clean = _clean(evidence={"age_text": "em 45 tuổi"})
        apply_ai_insights(order=self.order, clean=clean, call_record_id=7)
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.age_text, "45")
        self.assertIsNone(assessment.pain_level_code)
        self.assertEqual(assessment.lifestyle_habit_codes, [])

    def test_no_field_filled_creates_no_event(self):
        OrderAssessment.objects.create(
            order=self.order, age_text="30", pain_level_code="light",
            lifestyle_habit_codes=["sports"],
        )
        OrderNeed.objects.create(
            order_assessment=OrderAssessment.objects.get(order=self.order),
            need_type=NeedType.objects.get(code="knee_pain"),
        )
        filled = apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        self.assertEqual(filled, [])
        self.assertFalse(
            OrderJourneyEvent.objects.filter(order=self.order, event_type="assessment_ai_filled").exists()
        )

    def test_writes_exactly_one_journey_event_with_actor_none(self):
        apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        events = OrderJourneyEvent.objects.filter(order=self.order, event_type="assessment_ai_filled")
        self.assertEqual(events.count(), 1)
        event = events.first()
        self.assertIsNone(event.actor)
        self.assertEqual(event.payload_json["call_record_id"], 7)
        self.assertEqual(event.payload_json["fields"]["pain_level_code"]["value"], "severe")

    def test_preserves_updated_by(self):
        sale = make_admin()
        OrderAssessment.objects.create(order=self.order, updated_by=sale)
        apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.updated_by_id, sale.id)
```

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: lệnh docker với `<PATHS>` = `apps/orders/tests/test_assessment_ai_fill.py::ApplyAiInsightsTests`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.orders.application.services.assessment_ai_fill_service'`

- [ ] **Step 3: Viết service**

Tạo `backend/apps/orders/application/services/assessment_ai_fill_service.py`:

```python
"""Ghi kết quả AI extract vào OrderAssessment — CHỈ điền field đang trống.

Đi qua đúng cửa mà sale đang đi (`_upsert_order_assessment`) để không tồn tại
nhánh logic thứ hai lệch với luồng nhập tay: validate, sync needs, auto-block
spam, journey event đều dùng chung.

Ba luật cứng (docs/superpowers/specs/2026-07-24-...-design.md §7):
1. Chỉ ghi field đang trống — không bao giờ đè giá trị sale đã nhập.
2. Array chỉ ghi khi rỗng, KHÔNG union: union thì sale xoá xong lần sau AI điền lại.
3. Không có trích dẫn transcript thì không ghi — chốt chặn chống hallucination.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.orders.ai_extract_contract import AI_ARRAY_FIELDS, AI_SCALAR_FIELDS
from apps.orders.models import Order, OrderAssessment, OrderNeed


def _is_empty(value) -> bool:
    """False/0 là giá trị THẬT (used_massage_before=False), không phải trống."""
    return value is None or value == "" or value == []


@transaction.atomic
def apply_ai_insights(*, order: Order, clean: dict, call_record_id: int) -> list[str]:
    """Điền field trống của assessment từ payload đã normalize. Trả tên field đã điền."""
    from apps.orders.application.services.order_service_helpers import OrderServiceHelpers

    # select_for_update: sale có thể đang bấm Lưu form đúng lúc tick chạy.
    assessment = OrderAssessment.objects.select_for_update().filter(order=order).first()
    evidence = clean.get("evidence") or {}
    now_iso = timezone.now().isoformat()

    crm_payload: dict = {}
    filled: dict[str, dict] = {}

    for name in AI_SCALAR_FIELDS + AI_ARRAY_FIELDS:
        value = clean.get(name)
        if _is_empty(value) or name not in evidence:
            continue
        if assessment is not None and not _is_empty(getattr(assessment, name)):
            continue
        crm_payload[name] = value
        filled[name] = {"call_record_id": call_record_id, "quote": evidence[name], "at": now_iso}

    need_codes = clean.get("need_type_codes") or []
    if need_codes and "needs" in evidence:
        already_has_needs = assessment is not None and OrderNeed.objects.filter(
            order_assessment=assessment
        ).exists()
        if not already_has_needs:
            crm_payload["needs"] = [{"type_code": code} for code in need_codes]
            filled["needs"] = {
                "call_record_id": call_record_id,
                "quote": evidence["needs"],
                "at": now_iso,
            }

    if not crm_payload:
        return []

    # actor = người sửa cuối hiện tại, KHÔNG phải None: _save_assessment gán
    # updated_by = actor vô điều kiện, truyền None sẽ xoá mất dấu vết sale nào
    # chạm assessment lần cuối. AI không phải người dùng — dấu vết AI nằm ở
    # journey event bên dưới (actor=None) và ở ai_filled_fields.
    actor = assessment.updated_by if assessment is not None else None
    assessment, _changed = OrderServiceHelpers._upsert_order_assessment(order, crm_payload, actor)

    # Set SAU khi gọi helper: helper vừa xoá key của mọi field changed (luật
    # "sale sửa đè thì hết là AI điền"), giờ mới đánh dấu lại đúng phần AI ghi.
    assessment.ai_filled_fields = {**(assessment.ai_filled_fields or {}), **filled}
    assessment.save(update_fields=["ai_filled_fields", "updated_at"])

    OrderServiceHelpers._create_journey_event(
        order=order,
        event_type="assessment_ai_filled",
        actor=None,
        payload_json={
            "call_record_id": call_record_id,
            "fields": {
                name: {"value": crm_payload[name], "quote": meta["quote"]}
                for name, meta in filled.items()
            },
        },
    )
    return sorted(filled)
```

- [ ] **Step 4: Chạy test để chắc chắn nó pass**

Run: lệnh docker với `<PATHS>` = `apps/orders/tests/test_assessment_ai_fill.py`
Expected: PASS — 16 passed

- [ ] **Step 5: Chạy toàn bộ test orders để chắc không phá luồng nhập tay**

Run: lệnh docker với `<PATHS>` = `apps/orders`
Expected: PASS, không có test nào mới fail so với trước Task 3.

- [ ] **Step 6: Commit**

```bash
git add backend/apps/orders/application/services/assessment_ai_fill_service.py \
  backend/apps/orders/tests/test_assessment_ai_fill.py
git commit -m "feat(orders): apply AI-extracted insights into empty assessment fields"
```

---

### Task 5: Gọi Gemini cho 1 transcript

**Files:**
- Create: `backend/apps/callcenter/extract.py`
- Modify: `backend/apps/callcenter/tests/test_call_insights.py`

**Interfaces:**
- Consumes: `build_extract_response_schema`, `build_prompt_catalog_text`, `normalize_extracted` (Task 2); settings từ Task 1.
- Produces:
  - `class ExtractError(Exception)` với `.transient: bool`
  - `EXTRACT_COOLDOWN_KEY = "gemini:extract:cooldown"`
  - `extract_insights(transcript: str) -> tuple[dict, dict, dict]` — trả `(raw, clean, rejected)`

- [ ] **Step 1: Viết test thất bại**

Thêm vào `backend/apps/callcenter/tests/test_call_insights.py`:

```python
import json
from unittest.mock import patch

from django.test import override_settings

from apps.callcenter.extract import ExtractError, extract_insights

GEMINI_JSON = json.dumps(
    {
        "age_text": "45",
        "pain_level_code": "severe",
        "lifestyle_habit_codes": ["sit_a_lot", "ma_khong_ton_tai"],
        "need_type_codes": ["knee_pain"],
        "evidence": [
            {"field": "age_text", "quote": "em 45 tuổi"},
            {"field": "pain_level_code", "quote": "đau nặng lắm"},
        ],
    }
)


class _FakeResponse:
    def __init__(self, text):
        self.text = text


@override_settings(GEMINI_API_KEY="k", GEMINI_EXTRACT_MODEL="gemini-flash-latest")
class ExtractInsightsTests(TestCase):
    def _run(self, response_text):
        with patch("apps.callcenter.extract._generate_json", return_value=response_text) as mock:
            return extract_insights("Nhân viên: alo ạ" * 30), mock

    def test_parses_and_normalizes(self):
        (raw, clean, rejected), _ = self._run(GEMINI_JSON)
        self.assertEqual(raw["age_text"], "45")
        self.assertEqual(clean["pain_level_code"], "severe")
        self.assertEqual(clean["lifestyle_habit_codes"], ["sit_a_lot"])
        self.assertEqual(clean["evidence"]["age_text"], "em 45 tuổi")
        self.assertIn("ma_khong_ton_tai", rejected["lifestyle_habit_codes"])

    def test_invalid_json_is_content_error(self):
        with patch("apps.callcenter.extract._generate_json", return_value="khong phai json"):
            with self.assertRaises(ExtractError) as ctx:
                extract_insights("x" * 300)
        self.assertFalse(ctx.exception.transient)

    def test_empty_response_is_content_error(self):
        with patch("apps.callcenter.extract._generate_json", return_value="  "):
            with self.assertRaises(ExtractError) as ctx:
                extract_insights("x" * 300)
        self.assertFalse(ctx.exception.transient)

    def test_api_429_is_transient(self):
        with patch(
            "apps.callcenter.extract._generate_json",
            side_effect=RuntimeError("429 RESOURCE_EXHAUSTED"),
        ):
            with self.assertRaises(ExtractError) as ctx:
                extract_insights("x" * 300)
        self.assertTrue(ctx.exception.transient)
        self.assertTrue(ctx.exception.quota_exhausted)
```

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: lệnh docker với `<PATHS>` = `apps/callcenter/tests/test_call_insights.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.callcenter.extract'`

- [ ] **Step 3: Viết `extract.py` (phần gọi Gemini)**

Tạo `backend/apps/callcenter/extract.py`:

```python
"""Trích xuất chân dung & nhu cầu khách từ transcript bằng Gemini.

Luồng 3 của pipeline cuộc gọi (sau sync + transcribe). Text-only nên rẻ và
nhanh hơn transcribe nhiều, và chạy lại được bất kỳ lúc nào mà không cần audio
gốc — URL ghi âm Zenify hết hạn theo retention của họ.

Hợp đồng dữ liệu (field nào, code nào hợp lệ) thuộc về apps.orders — module này
chỉ biết cách nói chuyện với Gemini. Xem
docs/superpowers/specs/2026-07-24-call-transcript-assessment-autofill-design.md
"""

from __future__ import annotations

import json
import logging

from django.conf import settings

from apps.orders.ai_extract_contract import (
    build_extract_response_schema,
    build_prompt_catalog_text,
    normalize_extracted,
)

logger = logging.getLogger(__name__)

# Cờ cooldown riêng của luồng extract — không dùng chung với transcribe để 429
# của luồng này không khoá luôn việc transcribe (và ngược lại).
EXTRACT_COOLDOWN_KEY = "gemini:extract:cooldown"

_TRANSIENT_MARKERS = (
    "429",
    "500",
    "503",
    "resource_exhausted",
    "unavailable",
    "deadline",
    "timeout",
    "timed out",
    "connection",
)
_QUOTA_MARKERS = ("429", "resource_exhausted")

_PROMPT_HEADER = (
    "Đây là transcript một cuộc gọi giữa nhân viên tư vấn và khách hàng, bằng "
    "tiếng Việt, về sản phẩm ghế/máy massage. Hãy trích xuất thông tin chân dung "
    "và nhu cầu của KHÁCH HÀNG.\n\n"
    "QUY TẮC BẮT BUỘC:\n"
    "- CHỈ điền khi khách nói rõ trong transcript. TUYỆT ĐỐI không suy đoán.\n"
    "- Không chắc chắn thì để null (với mảng thì để mảng rỗng).\n"
    "- Mỗi field có điền giá trị PHẢI kèm một mục trong `evidence` với trích dẫn "
    "NGUYÊN VĂN câu nói của khách làm căn cứ. Field không có trích dẫn sẽ bị bỏ.\n"
    "- Chỉ dùng đúng các mã dưới đây, không tự chế mã mới.\n\n"
    "DANH MỤC MÃ HỢP LỆ (mã = ý nghĩa):\n"
)


class ExtractError(Exception):
    """Lỗi extract 1 record. Message giữ nguyên văn để lưu vào record.

    ``transient=True`` = lỗi hạ tầng nhất thời (mạng, 429/5xx): KHÔNG đốt lượt
    attempt — cùng bài học audit 2026-07-18 của transcribe, Gemini chập chờn
    15 phút mà đốt attempt là hàng loạt record FAILED oan.

    ``quota_exhausted=True`` = Google trả 429: nghỉ trọn chu kỳ cooldown thay vì
    dội request vào tường.
    """

    def __init__(self, message: str, *, transient: bool = False, quota_exhausted: bool = False):
        super().__init__(message)
        self.transient = transient
        self.quota_exhausted = quota_exhausted


def build_prompt(transcript: str) -> str:
    return f"{_PROMPT_HEADER}{build_prompt_catalog_text()}\n\nTRANSCRIPT:\n{transcript}"


def _generate_json(prompt: str) -> str:
    """Gọi Gemini với structured output. Tách hàm riêng để test mock được."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - thiếu dependency là lỗi deploy
        raise ExtractError(f"Thiếu thư viện google-genai: {exc}") from exc

    client = genai.Client(
        api_key=settings.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=120_000),
    )
    response = client.models.generate_content(
        model=settings.GEMINI_EXTRACT_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=build_extract_response_schema(),
            # temperature=0: trích xuất là việc đọc hiểu, không cần sáng tạo.
            temperature=0,
        ),
    )
    return getattr(response, "text", None) or ""


def extract_insights(transcript: str) -> tuple[dict, dict, dict]:
    """Trả ``(raw, clean, rejected)`` cho 1 transcript."""
    try:
        text = _generate_json(build_prompt(transcript))
    except ExtractError:
        raise
    except Exception as exc:  # noqa: BLE001 — lỗi SDK/API giữ nguyên văn cho record
        message = str(exc)
        lowered = message.lower()
        raise ExtractError(
            f"Gemini lỗi: {message}",
            transient=any(marker in lowered for marker in _TRANSIENT_MARKERS),
            quota_exhausted=any(marker in lowered for marker in _QUOTA_MARKERS),
        ) from exc

    text = (text or "").strip()
    if not text:
        raise ExtractError("Gemini trả về response rỗng.")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractError(f"Gemini trả về JSON không hợp lệ: {exc}") from exc
    if not isinstance(raw, dict):
        raise ExtractError("Gemini trả về JSON không phải object.")

    clean, rejected = normalize_extracted(raw)
    return raw, clean, rejected
```

- [ ] **Step 4: Chạy test để chắc chắn nó pass**

Run: lệnh docker với `<PATHS>` = `apps/callcenter/tests/test_call_insights.py`
Expected: PASS — 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/apps/callcenter/extract.py backend/apps/callcenter/tests/test_call_insights.py
git commit -m "feat(callcenter): call Gemini with structured output to extract call insights"
```

---

### Task 6: Vòng lặp batch + Celery task + beat

**Files:**
- Modify: `backend/apps/callcenter/extract.py` (thêm phần batch ở cuối file)
- Modify: `backend/apps/callcenter/tasks.py`
- Modify: `backend/config/settings_parts/celery.py:295` (thêm entry sau `callcenter-transcribe-pending`)
- Modify: `backend/apps/callcenter/tests/test_call_insights.py`

**Interfaces:**
- Consumes: `extract_insights`, `ExtractError`, `EXTRACT_COOLDOWN_KEY` (Task 5); `apply_ai_insights` (Task 4); `ExtractStatus` (Task 1).
- Produces: `extract_pending_insights(limit: int | None = None) -> dict` với keys `claimed/done/filled/errors/skipped/quota_stopped`; Celery task `apps.callcenter.tasks.extract_pending_call_insights`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `backend/apps/callcenter/tests/test_call_insights.py`:

```python
from apps.callcenter.extract import extract_pending_insights
from apps.channels_core.models import Channel
from apps.orders.models import NeedGroup, NeedType, Order, OrderAssessment


def make_order(code: str) -> Order:
    """Order.channel là FK NOT NULL (PROTECT) — không tạo Channel là IntegrityError."""
    channel = Channel.objects.create(code=code, name=f"Landing {code}")
    return Order.objects.create(channel=channel, name="Khách A", phone="0912345678", status=0)


@override_settings(
    GEMINI_API_KEY="k",
    GEMINI_EXTRACT_ENABLED=True,
    GEMINI_EXTRACT_BATCH=10,
    GEMINI_EXTRACT_MAX_ATTEMPTS=3,
    GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS=200,
)
class ExtractBatchTests(TestCase):
    def setUp(self):
        self.order = make_order("lp-ai-batch")
        group = NeedGroup.objects.create(code="pain_relief", name_vi="Giảm đau nhức", sort_order=10)
        NeedType.objects.create(code="knee_pain", group=group, name_vi="Đau đầu gối", sort_order=80)

    def _record(self, **kwargs):
        defaults = {
            "call_id": "c-batch-1",
            "transcribe_status": TranscribeStatus.DONE,
            "transcript": "Khách: em 45 tuổi, đau nặng lắm. " * 20,
            "order": self.order,
        }
        defaults.update(kwargs)
        return CallRecord.objects.create(**defaults)

    def test_done_record_fills_assessment(self):
        record = self._record()
        with patch("apps.callcenter.extract._generate_json", return_value=GEMINI_JSON):
            summary = extract_pending_insights()
        record.refresh_from_db()
        self.assertEqual(summary["done"], 1)
        self.assertEqual(record.extract_status, ExtractStatus.DONE)
        self.assertEqual(record.extract_payload["clean"]["pain_level_code"], "severe")
        self.assertIn("ma_khong_ton_tai", record.extract_payload["rejected"]["lifestyle_habit_codes"])
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.pain_level_code, "severe")

    def test_unmatched_record_is_not_claimed(self):
        self._record(call_id="c-nomatch", order=None)
        with patch("apps.callcenter.extract._generate_json", return_value=GEMINI_JSON) as mock:
            summary = extract_pending_insights()
        self.assertEqual(summary["claimed"], 0)
        mock.assert_not_called()

    def test_short_transcript_is_skipped_without_calling_gemini(self):
        record = self._record(call_id="c-short", transcript="alo")
        with patch("apps.callcenter.extract._generate_json") as mock:
            extract_pending_insights()
        record.refresh_from_db()
        self.assertEqual(record.extract_status, ExtractStatus.SKIPPED)
        mock.assert_not_called()

    def test_transient_error_does_not_burn_attempt(self):
        record = self._record(call_id="c-transient")
        with patch(
            "apps.callcenter.extract._generate_json", side_effect=RuntimeError("503 unavailable")
        ):
            extract_pending_insights()
        record.refresh_from_db()
        self.assertEqual(record.extract_attempts, 0)
        self.assertEqual(record.extract_status, ExtractStatus.PENDING)

    def test_content_error_burns_attempt_and_fails_at_limit(self):
        record = self._record(call_id="c-bad-json", extract_attempts=2)
        with patch("apps.callcenter.extract._generate_json", return_value="not json"):
            extract_pending_insights()
        record.refresh_from_db()
        self.assertEqual(record.extract_attempts, 3)
        self.assertEqual(record.extract_status, ExtractStatus.FAILED)

    def test_quota_error_enters_cooldown_and_requeues(self):
        record = self._record(call_id="c-quota")
        with patch(
            "apps.callcenter.extract._generate_json",
            side_effect=RuntimeError("429 RESOURCE_EXHAUSTED"),
        ):
            summary = extract_pending_insights()
        record.refresh_from_db()
        self.assertTrue(summary["quota_stopped"])
        self.assertEqual(record.extract_status, ExtractStatus.PENDING)
        self.assertEqual(record.extract_attempts, 0)
        self.assertIsNotNone(cache.get(EXTRACT_COOLDOWN_KEY))
        cache.delete(EXTRACT_COOLDOWN_KEY)

    def test_disabled_flag_is_noop(self):
        self._record(call_id="c-disabled")
        with override_settings(GEMINI_EXTRACT_ENABLED=False):
            with patch("apps.callcenter.extract._generate_json") as mock:
                summary = extract_pending_insights()
        self.assertEqual(summary["claimed"], 0)
        mock.assert_not_called()
```

Thêm import ở đầu file: `from django.core.cache import cache` và `from apps.callcenter.extract import EXTRACT_COOLDOWN_KEY`.

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: lệnh docker với `<PATHS>` = `apps/callcenter/tests/test_call_insights.py::ExtractBatchTests`
Expected: FAIL — `ImportError: cannot import name 'extract_pending_insights'`

- [ ] **Step 3: Thêm vòng lặp batch vào `extract.py`**

Thêm import ở đầu file:

```python
from django.core.cache import cache
from django.db import transaction

from .models import CallRecord, ExtractStatus, TranscribeStatus
```

Thêm vào cuối file:

```python
# Record kẹt PROCESSING quá lâu (worker bị kill giữa chừng) được trả về PENDING.
STUCK_PROCESSING_MINUTES = 30


def _requeue_stuck_processing() -> int:
    from datetime import timedelta

    from django.utils import timezone

    threshold = timezone.now() - timedelta(minutes=STUCK_PROCESSING_MINUTES)
    return CallRecord.objects.filter(
        extract_status=ExtractStatus.PROCESSING, updated_at__lt=threshold
    ).update(extract_status=ExtractStatus.PENDING)


def _claim_pending_batch(limit: int) -> list[int]:
    """Khoá dòng (skip_locked — 2 worker không giành nhau), set PROCESSING rồi trả
    id để xử lý NGOÀI transaction: phần gọi Gemini chậm không được ôm lock.

    order_id NOT NULL: extract xong mà chưa khớp đơn thì không có chỗ đổ. Vòng
    re-match của Task 1 sync sẽ gán đơn sau, tick kế tiếp mới tới lượt.
    """
    with transaction.atomic():
        ids = list(
            CallRecord.objects.select_for_update(skip_locked=True)
            .filter(
                extract_status=ExtractStatus.PENDING,
                transcribe_status=TranscribeStatus.DONE,
                order__isnull=False,
            )
            .order_by("-id")  # mới trước, cùng lý do ưu tiên của transcribe
            .values_list("id", flat=True)[:limit]
        )
        if ids:
            CallRecord.objects.filter(id__in=ids).update(extract_status=ExtractStatus.PROCESSING)
    return ids


def _fail_record(record: CallRecord, error: str, *, transient: bool = False) -> None:
    if not transient:
        record.extract_attempts += 1
    record.extract_error = error
    record.extract_status = (
        ExtractStatus.FAILED
        if record.extract_attempts >= settings.GEMINI_EXTRACT_MAX_ATTEMPTS
        else ExtractStatus.PENDING
    )
    record.save(
        update_fields=["extract_attempts", "extract_error", "extract_status", "updated_at"]
    )


def _requeue_batch(record_ids: list[int]) -> int:
    return CallRecord.objects.filter(
        id__in=record_ids, extract_status=ExtractStatus.PROCESSING
    ).update(extract_status=ExtractStatus.PENDING)


def extract_pending_insights(limit: int | None = None) -> dict:
    """Xử lý tối đa ``limit`` record trong 1 tick. Lỗi 1 record không chặn record khác."""
    from apps.orders.application.services.assessment_ai_fill_service import apply_ai_insights

    summary = {
        "claimed": 0,
        "done": 0,
        "filled": 0,
        "errors": 0,
        "skipped": 0,
        "quota_stopped": False,
        "requeued_stuck": 0,
    }
    if not settings.GEMINI_EXTRACT_ENABLED or not settings.GEMINI_API_KEY:
        return summary

    summary["requeued_stuck"] = _requeue_stuck_processing()
    # Chặn TRƯỚC khi claim: hết quota mà vẫn claim thì mỗi tick ghi 2 lượt UPDATE
    # cả batch để rồi vứt (sự cố transcribe 2026-07-22).
    if cache.get(EXTRACT_COOLDOWN_KEY) is not None:
        summary["quota_stopped"] = True
        return summary

    batch = _claim_pending_batch(limit or settings.GEMINI_EXTRACT_BATCH)
    summary["claimed"] = len(batch)

    for index, record_id in enumerate(batch):
        record = CallRecord.objects.filter(id=record_id).select_related("order").first()
        if record is None:  # pragma: no cover - record bị xoá giữa chừng
            continue
        transcript = record.transcript or ""
        if len(transcript) < settings.GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS:
            record.extract_status = ExtractStatus.SKIPPED
            record.save(update_fields=["extract_status", "updated_at"])
            summary["skipped"] += 1
            continue

        try:
            raw, clean, rejected = extract_insights(transcript)
        except ExtractError as exc:
            if exc.quota_exhausted:
                cache.set(
                    EXTRACT_COOLDOWN_KEY, 1, timeout=settings.GEMINI_QUOTA_COOLDOWN_MINUTES * 60
                )
                remaining = _requeue_batch(batch[index:])
                summary["quota_stopped"] = True
                logger.warning(
                    "extract nghỉ %s phút vì Gemini hết quota: %s (%s record chờ lại)",
                    settings.GEMINI_QUOTA_COOLDOWN_MINUTES,
                    exc,
                    remaining,
                )
                break
            _fail_record(record, str(exc), transient=exc.transient)
            summary["errors"] += 1
            logger.warning("extract lỗi call_id=%s: %s", record.call_id, exc)
            continue

        filled = apply_ai_insights(order=record.order, clean=clean, call_record_id=record.id)
        record.extract_payload = {"raw": raw, "clean": clean, "rejected": rejected, "filled": filled}
        record.extract_status = ExtractStatus.DONE
        record.extract_error = ""
        record.save(
            update_fields=["extract_payload", "extract_status", "extract_error", "updated_at"]
        )
        summary["done"] += 1
        summary["filled"] += len(filled)
        # KHÔNG log nội dung transcript/giá trị extract (PII) — chỉ tên field.
        logger.info(
            "extract xong call_id=%s order_id=%s fields=%s", record.call_id, record.order_id, filled
        )
    return summary
```

- [ ] **Step 4: Chạy test để chắc chắn nó pass**

Run: lệnh docker với `<PATHS>` = `apps/callcenter/tests/test_call_insights.py`
Expected: PASS — 12 passed

- [ ] **Step 5: Thêm Celery task**

Trong `backend/apps/callcenter/tasks.py`, thêm import và task:

```python
from .extract import extract_pending_insights
```

```python
_EXTRACT_LOCK_KEY = "callcenter:extract:lock"


@shared_task(
    bind=True,
    name="apps.callcenter.tasks.extract_pending_call_insights",
    soft_time_limit=480,
    time_limit=540,
)
def extract_pending_call_insights(self) -> dict:
    # Gate riêng GEMINI_EXTRACT_ENABLED, KHÔNG dùng ZENIFY_CALL_SYNC_ENABLED:
    # transcribe có thể đang chạy mà chưa muốn ghi gì vào assessment.
    if not settings.GEMINI_EXTRACT_ENABLED:
        return {"skipped": "disabled"}
    if not settings.GEMINI_API_KEY:
        return {"skipped": "gemini_not_configured"}
    if not cache.add(_EXTRACT_LOCK_KEY, 1, timeout=540):
        return {"skipped": "locked"}
    try:
        summary = extract_pending_insights()
        logger.info("extract_pending_call_insights: %s", summary)
        return summary
    finally:
        cache.delete(_EXTRACT_LOCK_KEY)
```

- [ ] **Step 6: Thêm beat entry**

Trong `backend/config/settings_parts/celery.py`, ngay sau block `"callcenter-transcribe-pending"`:

```python
    # Trích xuất chân dung khách từ transcript → điền OrderAssessment.
    "callcenter-extract-pending-insights": {
        "task": "apps.callcenter.tasks.extract_pending_call_insights",
        "schedule": crontab(minute="*/5"),
    },
```

- [ ] **Step 7: Chạy toàn bộ test callcenter**

Run: lệnh docker với `<PATHS>` = `apps/callcenter`
Expected: PASS, không test cũ nào fail.

- [ ] **Step 8: Commit**

```bash
git add backend/apps/callcenter/extract.py backend/apps/callcenter/tasks.py \
  backend/config/settings_parts/celery.py backend/apps/callcenter/tests/test_call_insights.py
git commit -m "feat(callcenter): add periodic task extracting insights into assessments"
```

---

### Task 7: Management command backfill

**Files:**
- Create: `backend/apps/callcenter/management/commands/extract_call_insights.py`
- Modify: `backend/apps/callcenter/tests/test_call_insights.py`

**Interfaces:**
- Consumes: `extract_pending_insights`, `extract_insights`, `ExtractStatus`.
- Produces: command `extract_call_insights` với flag `--order <id>`, `--call-record <id>`, `--limit <n>`, `--re-extract`, `--dry-run`.

- [ ] **Step 1: Viết test thất bại**

Thêm vào `backend/apps/callcenter/tests/test_call_insights.py`:

```python
from io import StringIO

from django.core.management import call_command


@override_settings(GEMINI_API_KEY="k", GEMINI_EXTRACT_ENABLED=True,
                   GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS=200)
class ExtractCommandTests(TestCase):
    def setUp(self):
        self.order = make_order("lp-ai-cmd")
        group = NeedGroup.objects.create(code="pain_relief", name_vi="Giảm đau nhức", sort_order=10)
        NeedType.objects.create(code="knee_pain", group=group, name_vi="Đau đầu gối", sort_order=80)
        self.record = CallRecord.objects.create(
            call_id="c-cmd-1",
            transcribe_status=TranscribeStatus.DONE,
            transcript="Khách: em 45 tuổi, đau nặng lắm. " * 20,
            order=self.order,
        )

    def test_dry_run_does_not_write_anything(self):
        out = StringIO()
        with patch("apps.callcenter.extract._generate_json", return_value=GEMINI_JSON):
            call_command("extract_call_insights", "--call-record", str(self.record.id),
                         "--dry-run", stdout=out)
        self.record.refresh_from_db()
        self.assertEqual(self.record.extract_status, ExtractStatus.PENDING)
        self.assertFalse(OrderAssessment.objects.filter(order=self.order).exists())
        self.assertIn("pain_level_code", out.getvalue())

    def test_re_extract_resets_done_record_to_pending(self):
        self.record.extract_status = ExtractStatus.DONE
        self.record.save(update_fields=["extract_status"])
        with patch("apps.callcenter.extract._generate_json", return_value=GEMINI_JSON):
            call_command("extract_call_insights", "--order", str(self.order.id), "--re-extract",
                         stdout=StringIO())
        self.record.refresh_from_db()
        self.assertEqual(self.record.extract_status, ExtractStatus.DONE)
        self.assertEqual(OrderAssessment.objects.get(order=self.order).pain_level_code, "severe")
```

- [ ] **Step 2: Chạy test để chắc chắn nó fail**

Run: lệnh docker với `<PATHS>` = `apps/callcenter/tests/test_call_insights.py::ExtractCommandTests`
Expected: FAIL — `CommandError: Unknown command: 'extract_call_insights'`

- [ ] **Step 3: Viết command**

Tạo `backend/apps/callcenter/management/commands/extract_call_insights.py`:

```python
"""Chạy tay bước extract chân dung khách từ transcript.

Dùng để backfill transcript cũ và để SOI kết quả trước khi bật kill switch
trên prod: `--dry-run` in ra đúng thứ Gemini trả về mà không ghi gì.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.callcenter.extract import ExtractError, extract_insights, extract_pending_insights
from apps.callcenter.models import CallRecord, ExtractStatus, TranscribeStatus


class Command(BaseCommand):
    help = "Trích xuất chân dung khách từ transcript cuộc gọi và điền vào OrderAssessment."

    def add_arguments(self, parser):
        parser.add_argument("--order", type=int, help="Chỉ xử lý cuộc gọi của đơn này")
        parser.add_argument("--call-record", type=int, help="Chỉ xử lý 1 cuộc gọi theo id")
        parser.add_argument("--limit", type=int, default=None, help="Số record tối đa")
        parser.add_argument(
            "--re-extract",
            action="store_true",
            help="Đưa record đã DONE/FAILED/SKIPPED về PENDING trước khi chạy",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Chỉ in kết quả Gemini, KHÔNG ghi assessment và không đổi trạng thái",
        )

    def handle(self, *args, **options):
        queryset = CallRecord.objects.filter(
            transcribe_status=TranscribeStatus.DONE, order__isnull=False
        )
        if options["order"]:
            queryset = queryset.filter(order_id=options["order"])
        if options["call_record"]:
            queryset = queryset.filter(id=options["call_record"])

        if options["dry_run"]:
            self._dry_run(queryset, options["limit"])
            return

        if options["re_extract"]:
            reset = queryset.exclude(extract_status=ExtractStatus.PENDING).update(
                extract_status=ExtractStatus.PENDING, extract_attempts=0, extract_error=""
            )
            self.stdout.write(f"Đã đưa {reset} record về PENDING.")

        summary = extract_pending_insights(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(json.dumps(summary, ensure_ascii=False)))

    def _dry_run(self, queryset, limit):
        records = list(queryset.filter(extract_status=ExtractStatus.PENDING)[: limit or 10])
        if not records:
            records = list(queryset[: limit or 10])
        for record in records:
            try:
                _raw, clean, rejected = extract_insights(record.transcript or "")
            except ExtractError as exc:
                self.stdout.write(self.style.ERROR(f"[{record.call_id}] {exc}"))
                continue
            self.stdout.write(f"[{record.call_id}] order={record.order_id}")
            self.stdout.write(f"  clean={json.dumps(clean, ensure_ascii=False)}")
            if rejected:
                self.stdout.write(
                    self.style.WARNING(f"  rejected={json.dumps(rejected, ensure_ascii=False)}")
                )
```

- [ ] **Step 4: Chạy test để chắc chắn nó pass**

Run: lệnh docker với `<PATHS>` = `apps/callcenter/tests/test_call_insights.py`
Expected: PASS — 14 passed

- [ ] **Step 5: Commit**

```bash
git add backend/apps/callcenter/management/commands/extract_call_insights.py \
  backend/apps/callcenter/tests/test_call_insights.py
git commit -m "feat(callcenter): add extract_call_insights management command"
```

---

### Task 8: Badge "AI" trên form assessment

**Files:**
- Create: `frontend/src/components/orders/AiFilledBadge.tsx`
- Create: `frontend/src/components/orders/__tests__/AiFilledBadge.test.tsx`
- Modify: `frontend/src/components/orders/Sheet3AssessmentPanel.tsx`
- Modify: `frontend/src/api/schema.d.ts` (regen)

**Interfaces:**
- Consumes: `crm.ai_filled_fields` từ API (Task 3).
- Produces: `AiFilledBadge` — props `{ info?: AiFilledInfo }`, `type AiFilledInfo = { call_record_id: number; quote: string; at: string }`, `type AiFilledMap = Record<string, AiFilledInfo>`.

- [ ] **Step 1: Regen schema TypeScript**

`backend/schema.yaml` đã được regen ở Task 3 Step 7. Giờ sinh lại type FE từ nó:

```bash
cd "C:/Users/MYPC/Desktop/workspace/desi-order-wt-assessment-autofill/frontend" && npm run gen:api
```

Expected: `src/api/schema.d.ts` có `ai_filled_fields` trong schema `OrderCRM`.

- [ ] **Step 2: Viết test thất bại**

Tạo `frontend/src/components/orders/__tests__/AiFilledBadge.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AiFilledBadge } from "../AiFilledBadge";

describe("AiFilledBadge", () => {
  it("không render gì khi field không do AI điền", () => {
    const { container } = render(<AiFilledBadge />);
    expect(container).toBeEmptyDOMElement();
  });

  it("hiện nhãn AI và trích dẫn làm căn cứ", () => {
    render(
      <AiFilledBadge
        info={{ call_record_id: 7, quote: "em 45 tuổi", at: "2026-07-24T09:00:00+07:00" }}
      />,
    );
    expect(screen.getByText("AI")).toBeInTheDocument();
    expect(screen.getByLabelText(/em 45 tuổi/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Chạy test để chắc chắn nó fail**

Run: `cd "C:/Users/MYPC/Desktop/workspace/desi-order-wt-assessment-autofill/frontend" && npm run test -- src/components/orders/__tests__/AiFilledBadge.test.tsx`
Expected: FAIL — không resolve được `../AiFilledBadge`

- [ ] **Step 4: Viết component**

Tạo `frontend/src/components/orders/AiFilledBadge.tsx`:

```tsx
import { Sparkles } from "lucide-react";

export type AiFilledInfo = {
  call_record_id: number;
  quote: string;
  at: string;
};

export type AiFilledMap = Record<string, AiFilledInfo>;

type Props = {
  info?: AiFilledInfo;
};

/**
 * Nhãn nhỏ cạnh field cho biết giá trị do AI trích từ transcript cuộc gọi,
 * kèm trích dẫn nguyên văn làm căn cứ để sale kiểm chứng nhanh.
 * Sale sửa đè → backend xoá key nên badge tự biến mất ở lần load sau.
 */
export function AiFilledBadge({ info }: Props) {
  if (!info) return null;
  return (
    <span
      aria-label={`AI điền từ cuộc gọi: "${info.quote}"`}
      title={`AI điền từ cuộc gọi: "${info.quote}"`}
      className="ml-1.5 inline-flex items-center gap-0.5 rounded-full bg-sky-50 px-1.5 py-0.5 text-[10px] font-medium text-sky-700 ring-1 ring-inset ring-sky-200"
    >
      <Sparkles className="h-2.5 w-2.5" aria-hidden />
      AI
    </span>
  );
}
```

- [ ] **Step 5: Chạy test để chắc chắn nó pass**

Run: `npm run test -- src/components/orders/__tests__/AiFilledBadge.test.tsx`
Expected: PASS — 2 passed

- [ ] **Step 6: Gắn badge vào panel**

Trong `frontend/src/components/orders/Sheet3AssessmentPanel.tsx`:

1. Thêm import: `import { AiFilledBadge, type AiFilledMap } from "./AiFilledBadge";`
2. Thêm vào props của panel: `aiFilledFields?: AiFilledMap;`
3. Với mỗi trong 9 field (`age_text`, `occupation_code`, `pain_level_code`, `pain_duration_code`, `lifestyle_habit_codes`, `relax_time_codes`, `delax_source_codes`, `used_massage_before`, `needs`), chèn ngay sau text của `<Label>`:

```tsx
<AiFilledBadge info={aiFilledFields?.["pain_level_code"]} />
```

(đổi khoá cho đúng từng field)

4. Nơi render panel truyền xuống: `aiFilledFields={crm?.ai_filled_fields}`.

- [ ] **Step 7: Typecheck + lint + test**

Run:
```bash
cd "C:/Users/MYPC/Desktop/workspace/desi-order-wt-assessment-autofill/frontend" && npm run typecheck && npm run lint && npm run test -- src/components/orders
```
Expected: typecheck sạch, lint sạch, test PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/orders/AiFilledBadge.tsx \
  frontend/src/components/orders/__tests__/AiFilledBadge.test.tsx \
  frontend/src/components/orders/Sheet3AssessmentPanel.tsx frontend/src/api/schema.d.ts
git commit -m "feat(orders): show AI badge with transcript quote on assessment fields"
```

---

### Task 9: Cập nhật tài liệu + đẩy nhánh

**Files:**
- Modify: `docs/zenify-call-transcripts.md` (thêm mục về luồng 3)
- Modify: `docs/superpowers/specs/2026-07-24-call-transcript-assessment-autofill-design.md` (đánh dấu sprint)

- [ ] **Step 1: Ghi luồng 3 vào tài liệu pipeline**

Trong `docs/zenify-call-transcripts.md`, thêm mục sau §5:

```markdown
## 5b. Luồng 3 — extract chân dung khách (Task `extract_pending_call_insights`)

Beat 5', queue `default`, gate riêng `GEMINI_EXTRACT_ENABLED`. Đọc transcript đã
`done` của record đã khớp đơn → Gemini structured output (text-only) → điền các
field chân dung/nhu cầu **đang trống** của `OrderAssessment`, đánh dấu trong
`ai_filled_fields` và ghi 1 `OrderJourneyEvent("assessment_ai_filled")`.

Thiết kế đầy đủ: `docs/superpowers/specs/2026-07-24-call-transcript-assessment-autofill-design.md`
```

- [ ] **Step 2: Đánh dấu sprint đã xong trong spec**

Trong bảng §12 của spec, đổi `⬜` thành `✅ xong <ngày>` cho S1–S3. S4 (bật billing, smoke prod) giữ `⬜` — là việc vận hành, không phải code.

- [ ] **Step 3: Chạy toàn bộ test backend liên quan lần cuối**

Run: lệnh docker với `<PATHS>` = `apps/callcenter apps/orders`
Expected: PASS toàn bộ.

- [ ] **Step 4: Commit và push**

```bash
cd "C:/Users/MYPC/Desktop/workspace/desi-order-wt-assessment-autofill"
git add docs/
git commit -m "docs: document call insight extraction flow"
git push -u origin feat/call-transcript-assessment-autofill
```

---

## Checklist vận hành S4 (không phải task code)

- [ ] Bật billing Google AI Studio cho `GEMINI_API_KEY` đang dùng.
- [ ] Chạy migration prod: `callcenter 0002`, `orders 0108`.
- [ ] Thêm env `GEMINI_EXTRACT_*` vào file env prod (giữ `GEMINI_EXTRACT_ENABLED=0`).
- [ ] Chạy `python manage.py extract_call_insights --dry-run --limit 30` trên prod, đọc tay kết quả `clean`/`rejected` của ~30 transcript thật.
- [ ] Nếu chất lượng đạt: đặt `GEMINI_EXTRACT_BATCH=5` cho lần bật đầu, `GEMINI_EXTRACT_ENABLED=1`, theo dõi log worker 1 giờ.
- [ ] Ổn định thì nâng `GEMINI_EXTRACT_BATCH` về 20 để tiêu hoá backlog.
