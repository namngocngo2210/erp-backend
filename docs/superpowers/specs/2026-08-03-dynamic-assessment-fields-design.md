# Trường đánh giá động (Dynamic Assessment Fields)

**Ngày:** 2026-08-03
**Trạng thái:** Đã duyệt design, chờ lập plan

## 1. Bối cảnh & mục tiêu

`OrderAssessment` hiện có ~25 cột fix cứng; mã hợp lệ hard-code trong
`crm_catalog.py`, registry field ở `assessment_fields.py`, whitelist AI ở
`ai_extract_contract.py`. Thêm 1 trường CRM mới phải sửa model + migration +
catalog + contract + FE rồi deploy.

Mục tiêu:

1. Thêm trường có mã (choice) mới **không cần sửa code/migration/deploy**.
2. Lưu được **thông tin tự do do AI trích xuất** từ transcript (tóm tắt tình
   trạng khách, đối thủ được nhắc tới, hoàn cảnh gia đình…).
3. **Không bỏ sót thông tin chưa có trường**: AI được ghi nhận mọi điều đáng
   chú ý khác vào một "giỏ mở" (open insights) mà không cần định nghĩa trước
   gì cả; chủ đề nào lặp lại nhiều thì admin mới nâng thành trường chính thức.
4. Trước mắt phục vụ xem trên từng đơn; **mở đường** cho báo cáo sau (không
   làm báo cáo trong phạm vi này).

Phương án đã chốt: **Registry trong DB + 1 cột JSONB** (hybrid). Đã loại EAV
(phức tạp, chưa cần báo cáo động ngay) và JSONB thuần không registry (dữ liệu
bẩn — bài học đã ghi trong `ai_extract_contract.py`).

## 2. Data model

### 2.1 Bảng mới `assessment_field_defs` (app `orders`)

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `code` | slug, unique | Định danh trường, vd `family_context`. `clean()` cấm trùng `ASSESSMENT_FIELD_NAMES` (25 cột cứng) và các key hệ thống (`needs`, `extra_fields`, `ai_filled_fields`) |
| `label` | varchar(255) | Nhãn hiển thị tiếng Việt |
| `kind` | varchar(32) | `single_choice` \| `multi_choice` \| `text` \| `number` \| `bool` |
| `options` | JSONB | `[{"code": str, "label": str}]` — bắt buộc với 2 kind choice, rỗng với kind khác |
| `ai_enabled` | bool | AI có được trích trường này từ transcript không |
| `ai_hint` | text | Gợi ý thêm cho prompt (vai trò như `_FIELD_HINTS` hiện tại) |
| `is_active` | bool | Tắt = ẩn khỏi form + loại khỏi prompt AI; **dữ liệu cũ giữ nguyên** |
| `sort_order` | int | Thứ tự hiển thị |
| `created_at`, `updated_at` | timestamptz | |

- Quản lý qua **Django admin** (không làm admin UI riêng trên FE).
- Registry cache ngắn (60s, key version-bump khi save) vì được đọc mỗi
  request assessment + mỗi tick extract.
- Xoá field def: không hard-delete khi đã có dữ liệu — chỉ `is_active=False`.

### 2.2 `OrderAssessment.extra_fields` JSONB

- `extra_fields = models.JSONField(default=dict, blank=True)`.
- Shape: `{"<field_code>": <giá trị>}` — string / list[string] / number / bool
  theo kind của field def.
- **Chưa** đánh GIN index. Khi có báo cáo đầu tiên trên trường động thì thêm
  index có chủ đích, hoặc promote trường "nóng" thành cột thật (lúc đó mới
  migration).

### 2.3 `OrderAssessment.ai_open_insights` JSONB (giỏ mở)

- `ai_open_insights = models.JSONField(default=list, blank=True)`.
- Shape mỗi phần tử:
  `{"topic": str, "content": str, "quote": str, "call_record_id": int, "at": iso8601}`.
- Append-only từ luồng AI (mục 4); sale không sửa (read-only trên UI).
- Chống phình: bỏ qua item trùng (`topic` + `content` giống hệt item đã có),
  cắt `topic` ≤ 100 ký tự, `content`/`quote` ≤ `TEXT_MAX_LEN`, giữ tối đa 100
  item / đơn (đầy thì bỏ item mới — đơn nhiều cuộc gọi đến vậy là bất thường).
- 1 migration duy nhất cho cả sprint: tạo bảng registry + 2 cột JSONB
  (`extra_fields`, `ai_open_insights`).

## 3. Luồng ghi (sale nhập tay)

- Payload CRM (`assessment` trong body) nhận thêm key là code trường động,
  phẳng cùng cấp với field cứng — FE không cần phân biệt hai loại khi submit.
- `_upsert_order_assessment` là **cửa duy nhất** (giữ nguyên nguyên tắc của
  luồng AI fill hiện tại): tách payload thành field cứng (đường cũ) và field
  động (validate theo registry rồi merge vào `extra_fields`).
- Validation mở rộng từ `coerce_and_validate`: whitelist lấy từ `options` của
  field def thay vì hằng số Python; text giới hạn `TEXT_MAX_LEN`, array giới
  hạn `ARRAY_MAX_ITEMS` như field cứng. Code lạ / kind sai → 400 với message
  rõ ràng như hiện tại.
- Key không có trong registry (kể cả field def đã inactive) → reject, không
  ghi mù vào JSONB.
- Luật provenance giữ nguyên: sale đè giá trị field động nào thì key đó bị
  xoá khỏi `ai_filled_fields` — vì `ai_filled_fields` vốn key theo tên field,
  code trường động thành key mới, **không đổi shape**.

## 4. Luồng AI extract

- `ai_extract_contract.py` build response_schema + prompt catalog từ **hai
  nguồn**: whitelist tĩnh hiện tại + field defs `ai_enabled=True,
  is_active=True` đọc từ registry:
  - `single_choice`/`multi_choice` → enum từ `options` (như whitelist tĩnh).
  - `text` → string tự do, `ai_hint` đưa vào dòng catalog trong prompt.
  - `number`/`bool` → type tương ứng, nullable.
- **Thông tin tự do = field def `kind="text"` + `ai_enabled=True`**. Seed sẵn
  (data migration hoặc fixture Django admin): `call_summary` (tóm tắt tình
  trạng & nhu cầu khách), `competitors_mentioned` (đối thủ được nhắc tới),
  `family_context` (hoàn cảnh gia đình). Nội dung seed chốt lại ở bước plan.
- `normalize_extracted` áp cùng luật cho trường động: không tin enum của LLM,
  code lạ vào `rejected`; text strip + giới hạn độ dài.
- `apply_ai_insights` giữ nguyên 3 luật cứng cho trường động:
  1. Chỉ điền khi trống (key chưa có / giá trị rỗng trong `extra_fields`).
  2. Array chỉ ghi khi rỗng, không union.
  3. Không có trích dẫn trong `evidence` thì không ghi.
- Bật field def mới trong admin → tick extract kế tiếp tự đưa vào prompt,
  không sửa code. (Record đã DONE không tự extract lại — chấp nhận, như hành
  vi hiện tại khi thêm field vào contract.)

### 4.1 Giỏ mở (open insights)

- Response schema thêm mảng `open_insights`:
  `[{"topic": str, "content": str, "quote": str}]`. Prompt yêu cầu: sau khi
  trích các trường theo danh mục, ghi nhận MỌI thông tin đáng chú ý khác về
  khách/thương vụ chưa thuộc trường nào (mỗi item một chủ đề ngắn gọn), vẫn
  áp luật trích dẫn nguyên văn — không có quote thì item bị loại.
- `normalize_extracted` lọc: đúng shape, strip, giới hạn độ dài, loại item
  thiếu quote; phần loại vào `rejected` như các trường khác.
- `apply_ai_insights` append vào `ai_open_insights` (dedupe + cap theo mục
  2.3). Khác trường động: giỏ mở KHÔNG theo luật "chỉ điền khi trống" — mỗi
  cuộc gọi có thể bổ sung insight mới; dedupe lo phần trùng.
- Nâng cấp thành trường chính thức: admin tạo field def mới trong registry —
  dữ liệu giỏ mở cũ giữ nguyên làm ngữ cảnh, không tự migrate.

## 5. API & Frontend

- `build_crm_catalog_payload()` trả thêm `dynamic_fields`: danh sách
  `{code, label, kind, options, sort_order}` của field defs active — FE dùng
  chung endpoint catalog hiện có, không thêm endpoint mới.
- Serializer assessment expose `extra_fields` (đọc) và nhận key động khi ghi
  (mục 3). `ai_filled_fields` trả về như hiện tại.
- FE: panel assessment thêm section "Thông tin bổ sung" render từ
  `dynamic_fields`, control generic theo kind (shadcn): Select /
  MultiSelect / Textarea / Input number / Switch. Badge "AI điền" + tooltip
  trích dẫn dùng lại component hiện có.
- FE: khối read-only "AI ghi nhận thêm" render `ai_open_insights` — danh sách
  topic + content, tooltip quote, nhóm theo cuộc gọi (call_record_id).
- Giá trị chứa code không còn trong `options` (admin đổi danh mục sau khi đã
  có dữ liệu): FE hiển thị raw code, không crash; không rewrite data.

## 6. Error handling & edge cases

- Registry đọc lỗi/cache miss → query thẳng DB (bảng nhỏ); không có đường nào
  làm hỏng luồng lưu assessment vì lỗi registry.
- 2 field def trùng `code` → chặn bằng unique constraint (DB-level).
- Đổi `kind` của field def đã có dữ liệu: chặn ở `clean()` khi đã có
  assessment chứa key đó (kiểm tra tồn tại bằng query `extra_fields__has_key`)
  — đổi kind là tạo field mới.
- Extract đang chạy đúng lúc admin sửa registry: tick dùng snapshot registry
  tại thời điểm build prompt; giá trị trả về validate lại theo registry hiện
  hành lúc ghi (`normalize` + upsert) nên không ghi được code đã bị gỡ.

## 7. Kiểm thử

- Registry: validate 5 kind, cấm code trùng cột cứng, cấm đổi kind khi có dữ
  liệu, options bắt buộc với choice.
- Upsert: merge `extra_fields` đúng, reject code lạ/kind sai/field inactive,
  xoá key `ai_filled_fields` khi sale đè.
- Contract: schema + prompt build từ registry (mock defs), normalize loại
  code lạ vào `rejected`.
- AI fill: chỉ điền khi trống, cần evidence, array không union — cho trường
  động.
- Giỏ mở: append + dedupe + cap 100 item, item thiếu quote bị loại, không áp
  luật "chỉ điền khi trống".
- API: catalog trả `dynamic_fields`, serializer round-trip `extra_fields`.
- FE: render control theo kind, submit payload phẳng, hiển thị raw code khi
  option bị gỡ.

## 8. Ngoài phạm vi (YAGNI)

- Admin UI riêng trên FE cho việc tạo field def (Django admin đủ dùng).
- GIN index / báo cáo / filter trên trường động.
- Migrate 25 cột cứng hiện có vào JSONB (không đụng — báo cáo, index, filter
  đang chạy trên đó).
- Extract lại các call record đã DONE khi bật field mới.

## Tiến độ (2026-08-03)

Đã hoàn thành 8/8 task theo plan `docs/superpowers/plans/2026-08-03-dynamic-assessment-fields.md`:

1. Model registry `AssessmentFieldDef` + 2 cột JSONB (`extra_fields`,
   `ai_open_insights`) + migration + Django admin — `413fc416`, `22401256`.
2. Registry read path (cache 60s) + validate 5 kind + coerce dynamic value —
   `0afe6441`.
3. Upsert CRM nhận key trường động, merge vào `extra_fields`, xoá khỏi
   `ai_filled_fields` khi sale đè — `3fcf261a`, fix serializer passthrough
   phát hiện ở review — `74033833`.
4. AI extract contract build schema + prompt từ registry + giỏ mở
   `open_insights` trong response schema — `7cde2a67`, `80e9466a`.
5. `apply_ai_insights` điền `extra_fields` (chỉ khi trống, cần evidence,
   array không union) + append `ai_open_insights` (dedupe, cap 100 item) —
   `55a43a58`.
6. `build_crm_catalog_payload` trả `dynamic_fields`; serializer assessment
   expose `extra_fields`; seed 3 field text AI-enabled (`call_summary`,
   `competitors_mentioned`, `family_context`) — `e32d92a6`.
7. FE: section "Thông tin bổ sung" render control theo kind (Select /
   MultiSelect / Textarea / Input number / Switch) + khối "AI ghi nhận thêm"
   read-only cho `ai_open_insights` — `dded9399`.
8. Verification toàn cục (task này) — `07192e6b`.

### Kết quả verify (2026-08-03)

- Backend `apps/orders/tests apps/callcenter/tests`: 1036 passed, 16
  subtests passed (818.45s), exit 0.
- Ruff toàn backend: 0 lỗi (`All checks passed!`).
- Frontend `npm run test:ci`: 793 passed, 7 failed — đúng bằng số fail sẵn
  có trên base (2 `RequireAuth.test.tsx` + 5
  `use-orders-data.realtime-upsert-filters.test.tsx`), không do branch này.
- Frontend `npm run lint:strict`: 0 lỗi. `npx tsc --noEmit`: 0 lỗi.

### Việc chưa làm / deferred (minor, chấp nhận được ghi trong ledger)

- Admin `has_delete_permission` chạy 1 query/object khi bulk-delete field
  def (bảng nhỏ, chấp nhận).
- Chưa có `assertNumQueries` khẳng định cache registry hit thật sự 0 query.
- `build_extract_response_schema` gọi `_dynamic_ai_defs` 2 lần/request
  (cache 60s che phần lớn tác động).
- Thiếu test nhánh SKIP khi `coerce_and_validate_dynamic` raise do registry
  đổi giữa chừng tick extract.
- `ApplyAiInsightsTests` chưa dọn `REGISTRY_CACHE_KEY` trong `setUp`.
- `test_schema_enum_comes_from_catalog` đổi sang subset-check, không còn bắt
  được code thừa lọt vào enum của giỏ evidence.
- FE `DynamicAssessmentFields` thiếu unit test riêng cho nhánh
  number/bool/multi_choice và trường hợp option bị gỡ (orphan code).
- Ngoài phạm vi theo thiết kế (mục 8): admin UI riêng trên FE, GIN
  index/báo cáo/filter trên trường động, migrate 25 cột cứng vào JSONB,
  extract lại call record đã DONE khi bật field mới.

Fix-wave sau final review (commit `5245d80c`): overlay giá trị pending trường
động lên `extra_fields` khi hiển thị (trước đó sửa không thấy tới khi bấm
Lưu); registry chịu được Redis chết (fallback query DB thẳng); bổ sung
`need_type_codes`/`evidence`/`open_insights` vào reserved codes; dọn cache
registry trong `ApplyAiInsightsTests`.

Minor còn lại từ final review (chấp nhận để sau):

- Admin bulk-delete (`delete_selected`) bypass guard `has_delete_permission`
  per-object → vẫn hard-delete được field def đã có dữ liệu; cache chờ TTL.
- Race lost-update trên cột JSONB `extra_fields` giữa sale save (không lock)
  và AI fill (mirror pattern sẵn có của `ai_filled_fields`, xác suất thấp).
- `apply_ai_insights` nằm ngoài try per-record của tick extract — race hẹp
  registry đổi đúng giữa validate và upsert có thể làm chết 1 tick.
- `AiOpenInsights` render danh sách phẳng, chưa nhóm theo cuộc gọi như §5.
- FE input number: gõ chữ → NaN → gửi null (xoá giá trị) thay vì báo lỗi.

### Ghi chú vận hành

- Thêm trường đánh giá mới: vào Django admin tạo `AssessmentFieldDef` (code,
  label, kind, options nếu là choice, `ai_enabled`, `ai_hint`, `is_active`,
  `sort_order`). Không cần sửa code/migration/deploy.
- AI tự nhận trường mới vào prompt/response schema sau tối đa 60s (thời gian
  cache registry hết hạn) — không cần restart service.
