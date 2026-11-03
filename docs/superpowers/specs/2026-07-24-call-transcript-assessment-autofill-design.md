# Auto-fill OrderAssessment từ transcript cuộc gọi (Gemini)

> Trạng thái: **APPROVED 24/07/2026 — code xong 24/07 (S1–S3), chờ S4 vận hành (bật billing, smoke prod, bật kill switch)**
> Branch: `feat/call-transcript-assessment-autofill`
> Liên quan: `docs/zenify-call-transcripts.md` (pipeline transcribe đã có), `plans/order_assessment.md`

## 1. Bối cảnh & mục tiêu

Pipeline Zenify → Gemini đã chạy: cuộc gọi được tải về, transcribe và lưu vào
`callcenter_call_records.transcript`, khớp sẵn với đơn hàng. Song song đó sale
phải **gõ tay** toàn bộ khối chân dung khách trong `OrderAssessment` (tuổi, nghề,
mức đau, thời gian đau, thói quen, nhu cầu...) — đúng những thông tin khách vừa
nói ra trong chính cuộc gọi đó.

Mục tiêu: thêm bước **transcript → structured extraction → điền vào assessment**,
để sale chỉ còn phải kiểm chứng thay vì nhập từ đầu.

Ngoài phạm vi: xử lý file upload thủ công, extract từ chat/Zalo, chấm điểm tiềm
năng tự động, gợi ý chờ duyệt.

## 2. Quyết định đã chốt (24/07/2026)

1. **Nguồn input**: tái dùng `CallRecord.transcript` đã có. Không upload file.
2. **Cách ghi**: tự động ghi thẳng vào field **đang trống**. Không có bước duyệt.
   Truy vết bằng `OrderJourneyEvent` + cột `ai_filled_fields`.
3. **Phạm vi field**: chỉ khối *chân dung & nhu cầu khách*. **KHÔNG** cho AI đụng
   field vận hành (`sales_status_code`, `delivery_business_status_code`,
   `potential_level`, `bomb_reason_*`) — sai số của LLM ở đó làm lệch chia đơn,
   xuất kho và report doanh thu.
4. **Quota**: bật billing Google AI Studio, extract chạy bằng **task riêng**
   (text-only, rẻ hơn audio nhiều) chứ không gộp vào request transcribe. Đổi lại
   được: chạy lại bất kỳ lúc nào mà không cần audio gốc (URL Zenify hết hạn theo
   retention), và extract được cả backlog transcript cũ.
5. **Đơn vị extract**: **từng `CallRecord`, cộng dồn**. Mỗi transcript extract
   đúng 1 lần; cuộc gọi sau bổ sung field cuộc trước chưa điền được.
6. **UI**: badge "AI" cạnh field kèm tooltip trích dẫn transcript; sale sửa đè →
   badge biến mất.

## 3. Kiến trúc

```
CallRecord: transcribe_status=done AND order_id IS NOT NULL AND extract_status=pending
   │  beat 5' — task extract_pending_call_insights (queue `default`)
   ▼
apps/callcenter/extract.py                    ← biết cách nói chuyện với Gemini
   ├─ dựng prompt: transcript + code/nhãn sinh động từ crm_catalog
   ├─ Gemini structured output (response_schema) → JSON
   ├─ lưu raw vào CallRecord.extract_payload, extract_status=done
   └─ gọi 1 hàm public duy nhất của orders ↓
apps/orders/application/services/assessment_ai_fill_service.py
   │                                          ← sở hữu luật ghi assessment
   ├─ lọc: chỉ giữ field đang trống + có evidence
   ├─ coerce_and_validate()                   (tái dùng assessment_fields.py)
   ├─ _upsert_order_assessment(order, payload, actor=None)  (tái dùng helper)
   ├─ cập nhật ai_filled_fields (JSONB)
   └─ ghi OrderJourneyEvent("assessment_ai_filled")
```

**Ranh giới**: `callcenter` không import `OrderAssessment`; `orders` không biết
Gemini tồn tại. Hai bên gặp nhau ở một dict đã validate. `callcenter` đã có tiền
lệ import `apps.orders.models` (`sync_service.py`) nên chiều phụ thuộc này không
mới.

**Vì sao không viết đường ghi mới**: `order_service_helpers._upsert_order_assessment`
đã lo `get_or_create`, validate, sync needs, ghi journey event và auto-block spam.
AI đi qua đúng cửa sale đang đi → không có nhánh logic thứ hai để lệch nhau.

## 4. Data model

### 4.1 `callcenter_call_records` — 4 cột mới

Đối xứng hoàn toàn với cụm `transcribe_*` đang chạy ổn định.

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `extract_status` | SmallInt choices | 0 pending / 1 processing / 2 done / 3 failed / 4 skipped |
| `extract_payload` | JSONField default dict | raw output Gemini + phần bị loại (`rejected`) |
| `extract_error` | TextField blank | lỗi lần cuối, **nguyên văn** (chuẩn error-passthrough) |
| `extract_attempts` | PositiveSmallInt | quá `GEMINI_EXTRACT_MAX_ATTEMPTS` → `failed` |

Index: `(extract_status, id)` cho picker của task.

`skipped` (4) = `len(transcript) < GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS` — cuộc gọi
quá ngắn không có nội dung tư vấn, lưu trạng thái để không quét lại mãi.

Record chưa khớp đơn (`order_id IS NULL`) **không** được claim: extract xong không
có chỗ đổ. Vòng re-match của Task 1 sẽ gán đơn sau, tick kế tiếp mới tới lượt.

**Backlog khi bật**: `extract_status` default `pending` nghĩa là mọi transcript đã
có từ trước vào hàng đợi ngay lúc bật kill switch. Chấp nhận — batch 20/5' ≈
5.760 record/ngày, thừa sức tiêu hoá backlog hiện tại trong vài giờ. Muốn kiểm
soát nhịp thì hạ `GEMINI_EXTRACT_BATCH` trong lần bật đầu.

### 4.2 `order_assessments` — 1 cột mới

`ai_filled_fields = JSONField(default=dict, blank=True)`

```json
{
  "pain_level_code": {
    "call_record_id": 812,
    "quote": "dạo này vai gáy đau lắm, mỏi cả ngày",
    "at": "2026-07-24T09:12:00+07:00"
  }
}
```

Key = tên field (hoặc `"needs"` cho khối nhu cầu). Sale sửa field nào → service
update assessment hiện có xoá key đó. Không thêm cột `source` cho từng field
trong 25 cột — một JSONB đủ và không đụng schema mỗi lần thêm field CRM.

### 4.3 Catalog

Thêm `CatalogOption("assessment_ai_filled", "AI điền từ cuộc gọi")` vào
`EVENT_TYPE_OPTIONS` (`crm_catalog.py`) để timeline FE không hiện mã raw — đúng
bài học đã ghi trong chính file đó.

## 5. Phạm vi field AI được điền

| Field | Kiểu | Nguồn whitelist |
|---|---|---|
| `age_text` | text (≤32) | không whitelist; chỉ chặn độ dài bằng `SHORT_TEXT_MAX_LEN` sẵn có |
| `occupation_code` | code | `OCCUPATION_LABELS` |
| `pain_level_code` | code | `PAIN_LEVEL_LABELS` |
| `pain_duration_code` | code | `PAIN_DURATION_LABELS` |
| `lifestyle_habit_codes` | code_array | `LIFESTYLE_HABIT_LABELS` |
| `relax_time_codes` | code_array | `RELAX_TIME_LABELS` |
| `delax_source_codes` | code_array | `DELAX_SOURCE_LABELS` |
| `used_massage_before` | bool | — |
| `needs[]` | list type_code | `NEED_TYPE_SEED` |

Danh sách này là hằng số `AI_FILLABLE_FIELDS` đặt cạnh `ASSESSMENT_FIELDS` trong
`assessment_fields.py` — cùng chỗ với single source of truth hiện có.

**Lưu ý `occupation_code`**: `assessment_fields.py` đang khai nó là `kind="text"`
không whitelist (sale gõ tự do được). Riêng đường AI **ép whitelist**
`OCCUPATION_LABELS` trước khi đưa vào payload, không để LLM tự chế chuỗi làm bẩn
report. Không đổi khai báo chung để khỏi phá luồng nhập tay đang chạy.

## 6. Prompt & structured output

Gọi Gemini với `response_mime_type="application/json"` + `response_schema` **sinh
động từ catalog**:

```python
def build_extract_schema() -> dict:
    # enum lấy trực tiếp từ *_LABELS trong crm_catalog
    # → thêm option mới trong catalog là prompt tự cập nhật, không có chỗ nào lệch
```

Shape trả về:

```jsonc
{
  "age_text": "string|null",
  "occupation_code": "enum|null",
  "pain_level_code": "enum|null",
  "pain_duration_code": "enum|null",
  "lifestyle_habit_codes": ["enum"],
  "relax_time_codes": ["enum"],
  "delax_source_codes": ["enum"],
  "used_massage_before": "bool|null",
  "need_type_codes": ["enum"],
  "evidence": { "<field_name>": "trích dẫn nguyên văn từ transcript" }
}
```

Prompt liệt kê **code kèm nhãn tiếng Việt** để LLM map đúng ngữ nghĩa, và chỉ thị
cứng: *chỉ điền khi khách nói rõ; không suy đoán; không chắc thì để null; mỗi
field điền phải kèm trích dẫn nguyên văn trong `evidence`*.

**Validate lại bằng Python — không tin enum của LLM.** Kết quả đẩy qua
`coerce_and_validate()`; code không thuộc whitelist bị loại và ghi vào
`extract_payload["rejected"]` để soi chất lượng prompt theo thời gian.

## 7. Luật merge

- **Scalar** (`age_text`, `occupation_code`, `pain_level_code`,
  `pain_duration_code`): chỉ ghi khi giá trị hiện tại là `None` hoặc `""`.
- **Array** (`*_codes`): chỉ ghi khi đang `[]` — **không union**. Union sẽ làm sale
  xoá không được: xoá xong lần extract sau AI điền lại.
- **`needs[]`**: chỉ ghi khi assessment chưa có `OrderNeed` nào. Payload theo shape
  `_sync_assessment_needs` đang nhận: `[{"type_code": "..."}]`.
- **`used_massage_before`**: chỉ ghi khi đang `None`.
- **Thiếu `evidence` cho field → không ghi field đó.** Đây là chốt chặn chống
  hallucination, không phải tuỳ chọn.
- Field nào không có trong payload gửi xuống `_upsert_order_assessment` thì
  helper bỏ qua (`if spec.name not in crm_payload: continue`) → field sale đã nhập
  không bao giờ bị đụng tới.
- Toàn bộ trong `transaction.atomic()` + `select_for_update` trên assessment để
  không đua với sale đang lưu form.
- Assessment chưa tồn tại → `get_or_create` tạo mới; helper tự ghi event
  `assessment_created` với `actor=None`.
- **`updated_by` phải giữ nguyên.** `_save_assessment` gán `assessment.updated_by
  = actor` vô điều kiện và đưa cột này vào `update_fields` — truyền `actor=None`
  sẽ **xoá mất** thông tin sale nào sửa cuối. AI không phải người dùng, nên
  service gọi helper với `actor = assessment.updated_by` (assessment mới thì
  `None`). Riêng `OrderJourneyEvent` vẫn ghi `actor=None` để timeline phân biệt
  được đâu là AI.
- Ghi đúng **1** `OrderJourneyEvent("assessment_ai_filled")` cho mỗi lần fill:
  `actor=None`, `payload_json = {"call_record_id": …, "fields": {field: {value, quote}}}`.
  Không ghi event nếu không field nào được điền.
- Các `event_mappings` sẵn có trong helper chỉ phủ 5 field vận hành — không giao
  với 9 field AI điền, nên không sinh event trùng.

## 8. Cấu hình

| Env | Default | Ghi chú |
|---|---|---|
| `GEMINI_EXTRACT_ENABLED` | `false` | kill switch; bật SAU smoke prod |
| `GEMINI_EXTRACT_MODEL` | `gemini-flash-latest` | |
| `GEMINI_EXTRACT_BATCH` | `20` | số record tối đa 1 tick |
| `GEMINI_EXTRACT_MAX_ATTEMPTS` | `3` | |
| `GEMINI_EXTRACT_MIN_TRANSCRIPT_CHARS` | `200` | ngắn hơn → `skipped` |

Dùng chung `GEMINI_API_KEY` đã có. Không thêm quota guard RPM/RPD: quyết định #4
là bật billing, và counter local chính là thứ đã tự khoá pipeline hồi 22/07 (§8
`zenify-call-transcripts.md`). Vẫn giữ **cooldown khi Gemini trả 429** theo đúng
cơ chế `transcribe.py` (key riêng `gemini:extract:cooldown`) để phòng sự cố
billing/hạn mức phía Google.

Triết lý retry giữ nguyên: lỗi **transient** (429/5xx/mạng/timeout) **không đốt
attempt**; lỗi **nội dung** (JSON hỏng, transcript rỗng, response rỗng) mới đếm.

## 9. Vận hành

- Management command `extract_call_insights --order <id> --call-record <id>
  --re-extract --dry-run` để backfill transcript cũ và soi tay kết quả.
- Chạy lại 1 record: `--re-extract` đặt `extract_status=pending` rồi để task xử lý.
  Luật merge "chỉ điền field trống" nghĩa là chạy lại **không** đè kết quả cũ —
  muốn thay thế phải xoá field trên UI trước.
- Không log nội dung transcript hay giá trị extract (PII) — chỉ log `call_id`,
  `order_id`, danh sách tên field đã điền và status.
- `ai_filled_fields` đi theo scope RBAC của order như phần còn lại của assessment.

## 10. Frontend

- Regen `schema.d.ts`; `crm` response thêm `ai_filled_fields`.
- Thực tế lúc code: field chân dung/nhu cầu khách nằm ở `BlockCrmRating.tsx`
  (không phải `Sheet3AssessmentPanel.tsx` như phỏng đoán ban đầu). Thêm component
  **`AiFilledBadge` ở file riêng** — badge nhỏ cạnh nhãn field, tooltip hiện trích
  dẫn transcript. Wiring qua prop `labelAdornment` mới trên `ERow` dùng chung
  (`order-detail-shared.tsx`) để không phải sửa từng field row một.
- Sale sửa field → badge ẩn ngay (optimistic); BE xoá key khi lưu.
- Timeline đơn render event `assessment_ai_filled` bằng nhãn từ catalog.

## 11. Test plan

Backend:
- `extract.py`: JSON hợp lệ; JSON hỏng → đếm attempt; code lạ bị loại vào
  `rejected`; transcript ngắn → `skipped`; record chưa khớp đơn không bị claim;
  lỗi transient không đốt attempt; đủ attempt → `failed`; 429 → cooldown.
- `assessment_ai_fill_service`: field trống được ghi; field sale đã nhập không bị
  đè; array đã có không bị union; needs đã có không bị thêm; thiếu evidence →
  không ghi; không field nào điền → không sinh event; đúng 1 journey event với
  payload đúng; assessment chưa có → tạo mới.
- Serializer trả `ai_filled_fields`; sale sửa field → key bị xoá.
- Chạy test theo runbook `desi-order-web-test`.

Frontend:
- Badge hiện đúng field có trong `ai_filled_fields`, ẩn khi sale sửa.
- Tooltip render trích dẫn.
- `ai_filled_fields = {}` (đơn cũ) không vỡ render.

## 12. Sprint plan

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| S1 | Migration 2 app; `AI_FILLABLE_FIELDS`; `extract.py` (schema sinh từ catalog, prompt, validate); unit test | ✅ xong 24/07 |
| S2 | `assessment_ai_fill_service`; task + beat; journey event + catalog label; serializer `ai_filled_fields`; test | ✅ xong 24/07 |
| S3 | FE `AiFilledBadge` + tooltip + timeline label; test | ✅ xong 24/07 |
| S4 | Command backfill; bật billing Google; smoke prod; bật `GEMINI_EXTRACT_ENABLED` | ⬜ |

Deploy notes: chạy migration `callcenter` + `orders`; thêm env `GEMINI_EXTRACT_*`;
bật `GEMINI_EXTRACT_ENABLED=true` sau khi soi tay kết quả bằng `--dry-run` trên
vài chục transcript thật.

## 13. Giả định

- Transcript hiện có đủ chất lượng để trích xuất chân dung khách. Nếu `--dry-run`
  trên dữ liệu thật cho kết quả kém, việc cần làm là chỉnh prompt/model chứ không
  phải đổi kiến trúc.
- Billing Google AI Studio được bật trước S4; trước đó chỉ chạy tay số lượng nhỏ.
- Sale chấp nhận kiểm chứng field có badge thay vì được duyệt trước khi ghi.
- Đơn không có cuộc gọi nào khớp thì không có gì thay đổi — tính năng này không
  thay thế việc nhập tay, chỉ giảm tải.
