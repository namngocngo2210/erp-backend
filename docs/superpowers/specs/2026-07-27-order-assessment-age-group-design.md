# Chuẩn hoá cột tuổi ở Order Assessment thành nhóm tuổi

Ngày: 2026-07-27

## Bối cảnh

`OrderAssessment.age_text` hiện là `CharField(32)` free text. Sale gõ tay tuỳ ý, dẫn tới
ba vấn đề:

1. **Dữ liệu bẩn ngay từ khâu ghi.** 322 giá trị phân biệt cho một trường lẽ ra chỉ có
   vài nhóm: `70`, `70t`, `ngoài 40`, `gần 70`, `7x`, `35-40`, `ko chia sẻ`.
2. **Hai hệ nhóm tuổi song song và lệch nhau.** Input tự do, nhưng tầng báo cáo lại có
   `apps/customers/age_buckets.py` parse regex thành `18-30 / 31-45 / 46-60 / 61-75 /
   76-100 / other`. Không nhóm nào trong đó khớp nhóm nghiệp vụ đang cần.
3. **Không phân biệt được "sale chưa hỏi" với "khách không nói".** Cả hai đều ra chuỗi
   rỗng, nên chỉ số chấm độ đầy đủ hồ sơ của sale bị nhiễu.

Mục tiêu: đưa trường này về dropdown 7 lựa chọn cố định, khoá ràng buộc ở tầng ghi, và
backfill dữ liệu cũ.

## Hiện trạng dữ liệu prod (đo ngày 2026-07-27)

| Chỉ số | Giá trị |
|---|---|
| Tổng dòng `order_assessments` | 479.013 |
| Dòng có `age_text` khác rỗng | **3.026 (0,63%)** |
| Số giá trị phân biệt | 322 |

Phân loại trên 100 giá trị phổ biến nhất (phủ 2.725 dòng = 90% dữ liệu có giá trị):

| Dạng | Dòng | Tỷ lệ |
|---|---|---|
| Số thuần — `70`, `45` | 2.336 | 86% |
| Số + hậu tố `t` (= "tuổi") — `70t` | 305 | 11% |
| Lộn xộn — `ngoài 40`, `gần 70`, `7x`, `35-40`, `ko chia sẻ` | 84 | 3% |

Phân bố sau khi map vào bộ nhóm mới:

| Nhóm | Dòng | Tỷ lệ |
|---|---|---|
| 65+ | 850 | 31,2% |
| 45-54 | 706 | 25,9% |
| 55-64 | 542 | 19,9% |
| 35-44 | 489 | 17,9% |
| 25-34 | 54 | 2,0% |
| 18-24 | 0 | 0% |

Tổng cột này là 96,9%; 3,1% còn lại là các giá trị lộn xộn chưa parse được ở bảng trên.

Hai điều rút ra:

- **Backfill rất nhẹ.** 3.026 dòng chạy trong vài giây. Không cần batch, không cần downtime.
- **`ko chia sẻ` xuất hiện 8 lần** — sale đang tự chế cách ghi "khách không nói". Đây là
  bằng chứng nghiệp vụ cho thấy "Không xác định" phải là một lựa chọn thật.

Nhóm `18-24` trống hoàn toàn và `25-34` chỉ 2%, còn `65+` gộp từ 65 đến 90+ và là nhóm
lớn nhất. Đã cân nhắc tách `65-74 / 75+` nhưng quyết định **giữ nguyên 6 nhóm**, vì đây
đúng là bộ nhóm chuẩn của Facebook Ads — giữ khớp để còn đối chiếu chân dung khách với
báo cáo audience của `apps/facebook_ads`.

## Quyết định thiết kế

### 1. Danh mục nhóm tuổi

Thêm vào `backend/apps/orders/crm_catalog.py`, theo convention snake_code sẵn có của
`PAIN_DURATION_OPTIONS`:

```python
AGE_GROUP_OPTIONS = [
    CatalogOption("18_24",   "18-24"),
    CatalogOption("25_34",   "25-34"),
    CatalogOption("35_44",   "35-44"),
    CatalogOption("45_54",   "45-54"),
    CatalogOption("55_64",   "55-64"),
    CatalogOption("65_plus", "65+"),
    CatalogOption("not_disclosed", "Không xác định"),
]
AGE_GROUP_LABELS = _to_map(AGE_GROUP_OPTIONS)
```

Code là `not_disclosed` chứ không phải `unknown`, vì `apps/customers/report_fields.py:9` đã
chiếm hằng `UNKNOWN_CODE = "unknown"` cho ô "chưa điền". Dùng cùng một chuỗi sẽ làm hai
trạng thái đụng key nhau trong dict phân bố — đúng thứ mục 3 muốn tách bạch.

Đăng ký thêm ở `CRM_CODE_LABELS["age_text"]` và `build_crm_catalog_payload()` khoá
`age_group_options`.

### 2. Giữ nguyên tên cột `age_text`

Không thêm cột mới, không rename. Chỉ đổi ngữ nghĩa của cột hiện có từ free text sang
code, kèm comment trong model.

Lý do không rename: khoá `age_text` đang là column key trong cấu hình ẩn/hiện cột lưu
phía client (`frontend/src/features/orders/order-types.ts:328`). Đổi tên sẽ reset cấu
hình bảng của toàn bộ sale — cái giá đó lớn hơn lợi ích đặt tên đúng.

Hệ quả phải chấp nhận: tên cột không còn phản ánh đúng nội dung. Bù bằng docstring ở
model và ở `ASSESSMENT_FIELDS`.

### 3. Ba trạng thái phân biệt

| Giá trị lưu | Nghĩa | Tính là "đã khai thác"? |
|---|---|---|
| `""` | Sale chưa hỏi | Không |
| `not_disclosed` | Đã hỏi, khách không nói | **Có** |
| `18_24` … `65_plus` | Có dữ liệu | Có |

Nhờ tách `not_disclosed` khỏi rỗng, biểu thức `~Q(assessment__age_text="")` ở
`backend/apps/orders/_repositories_reports.py:611` (chấm độ đầy đủ hồ sơ của sale)
**tự động đúng, không cần sửa** — sale chọn "Không xác định" vẫn được ghi nhận là đã
khai thác thông tin.

### 4. Khoá ràng buộc ghi — sửa một dòng

`backend/apps/orders/assessment_fields.py:51`:

```python
# trước
AssessmentField("age_text", "text", null_default="", max_length=SHORT_TEXT_MAX_LEN)
# sau
AssessmentField("age_text", "code", null_default="", whitelist=AGE_GROUP_LABELS)
```

`ASSESSMENT_FIELDS` là single source of truth: DRF serializer
(`order_core_serializers.py:57`), validate (`coerce_and_validate`) và upsert
(`order_service_helpers.py:122`) đều auto-sinh từ registry này. Một dòng này khoá toàn
bộ đường ghi — REST API, inline edit, import CSV, AI autofill. Code lạ ném
`AssessmentValidationError`.

`null_default=""` giữ cột NOT NULL default `""` như hiện tại, nên **không cần schema
migration**, chỉ cần data migration.

### 5. Rule backfill

Parser thuần, không phụ thuộc Django, đặt ở `backend/apps/orders/age_group_backfill.py`.

**Bước 0 — chuẩn hoá:** lowercase, trim, gộp khoảng trắng liên tiếp (`ngoài  40` →
`ngoài 40`), bỏ hậu tố `t` sau chữ số (`70t` → `70`, `35-45t` → `35-45`).

**Bước 1 — chọn luật.** Áp theo thứ tự, dừng ở luật đầu tiên khớp. Bốn luật cuối cho ra
một con số tuổi, chưa phải kết quả cuối:

| # | Điều kiện | Ví dụ thật | Cho ra |
|---|---|---|---|
| 1 | Chuỗi rỗng | `""` | trả `""` ngay — **không** đổi thành `not_disclosed` |
| 2 | Không chứa chữ số nào | `ko chia sẻ`, `trung niên` | trả `not_disclosed` ngay |
| 3 | Khoảng `A-B` | `35-40`, `49-55`, `35-45` | tuổi = `(A+B)//2` |
| 4 | Thập niên `Nx` | `7x`, `6x` | tuổi = midpoint của `[N0, N9]` |
| 5 | Số 4 chữ số trong `[1900, 2015]` | `1980` | tuổi = `2026 - năm` |
| 6 | Số đầu tiên trong chuỗi | `70`, `ngoài 40`, `gần 70` | tuổi = số đó |
| — | Không luật nào khớp | | `not_disclosed` |

**Bước 2 — chốt nhóm.** Với con số tuổi từ luật 3–6: nếu `< 18` hoặc `> 100` thì
`not_disclosed`, ngược lại map vào nhóm chứa nó.

Luật 2 dùng "không chứa chữ số" thay vì danh sách từ khoá — phủ mọi cách diễn đạt bằng
lời mà không bao giờ sót.

Luật 3 và 4 chọn **midpoint** thay vì loại bỏ khoảng vắt qua hai nhóm: `49-55` → 52 →
`45_54`, `60-70` → 65 → `65_plus`. Mọi khoảng trong dữ liệu thật đều ≤ 10 năm nên sai số
chấp nhận được, và giữ được tín hiệu thay vì vứt đi.

Luật 6 xử lý gọn cả `ngoài 40` (→ `35_44`) lẫn `gần 70` (→ `65_plus`) mà không cần hiểu
tiền tố.

`REFERENCE_YEAR = 2026` là hằng số trong module, có comment giải thích: backfill chạy một
lần nên mốc cố định cho kết quả tái lập được, không phụ thuộc ngày chạy migration.

### 6. Xoá `age_buckets.py`

`backend/apps/customers/age_buckets.py` hết lý do tồn tại. `report_service.py:82` và
`report_crosstabs.py:28` đọc thẳng code từ cột thay vì gọi `bucket_age()`.

Trong `age_distribution`, `fixed_order` = 6 nhóm + `not_disclosed` + `UNKNOWN_CODE`, và
nhãn của `UNKNOWN_CODE` đổi riêng cho trục tuổi thành "Chưa điền" (thay vì "Chưa xác định"
mặc định của `with_unknown`) để không lẫn với "Không xác định".

## Phạm vi thay đổi

### Backend

| File | Việc |
|---|---|
| `apps/orders/crm_catalog.py` | Thêm `AGE_GROUP_OPTIONS` / `AGE_GROUP_LABELS`, đăng ký `CRM_CODE_LABELS` + payload |
| `apps/orders/assessment_fields.py:51` | Đổi `kind` sang `code` + whitelist |
| `apps/orders/age_group_backfill.py` | **Mới** — parser thuần |
| `apps/orders/migrations/0110_backfill_age_group.py` | **Mới** — data migration 3.026 dòng (kế tiếp `0109_drop_dead_indexes`) |
| `apps/orders/ai_extract_contract.py` | Chuyển `age_text` vào `_SCALAR_WHITELIST`; bỏ nhánh free-text ở `normalize_extracted:115`. Gemini `response_schema` tự nhận enum |
| `apps/orders/export_excel.py:100` | Xuất label thay vì code, theo đúng pattern các code field khác |
| `apps/orders/management/commands/import_assessment_csv.py` | Tái dùng parser để CSV cũ vẫn import được |
| `apps/customers/age_buckets.py` | **Xoá** |
| `apps/customers/report_service.py` | Bỏ `bucket_age`, đổi `fixed_order` |
| `apps/customers/report_crosstabs.py` | Bỏ `bucket_age` |
| `apps/customers/profile_service.py:129`, `serializers.py:208` | Trả kèm label |

Không đụng `_repositories_reports.py:611` — xem mục 3.

### Frontend

| File | Việc |
|---|---|
| `features/orders/BlockCrmRating.tsx:421` | `ERow` đổi sang `type="select"` + options từ catalog |
| `features/orders/OrdersTable/cell-renderers.tsx:211` | `renderCrmText` → `renderCrmSelect` |
| `features/orders/order-types.ts` | Type `age_text` thành union code; `CrmCatalogPayload` thêm `age_group_options` |
| `features/customers/customer-utils.tsx:72`, `components/CustomerGroupCells.tsx:170` | Hiển thị label thay vì raw |
| `api/schema.d.ts` | Regenerate từ `schema.yaml` |

### Test

- Unit test parser, bảng case dựng từ chính dữ liệu prod ở trên (gồm `70t`, `ngoài 40`,
  `gần 70`, `7x`, `6x`, `35-40`, `49-55`, `ko chia sẻ`, rỗng, `9`, `150`).
- Test data migration: dựng vài `OrderAssessment` với giá trị thật, chạy migration, assert
  code ra đúng.
- Test `coerce_and_validate` reject code lạ và chấp nhận `""`.
- Sửa test hiện có đang dùng giá trị cũ: `apps/customers/tests/test_customer_report_service.py`
  (`age_text="30"`, `"50"`), `test_customer_profiles_api.py` (`age_text="45-60"`),
  `apps/callcenter/tests/test_call_insights.py` (`age_text="45"`).

## Vận hành khi deploy

Vì backfill ghi đè mất giá trị gốc, **bắt buộc** dump trước khi chạy migration:

```
alias P="docker exec -i desi-postgres psql -U desi -d desi"
```
```
Q="copy (select id,age_text from order_assessments where age_text<>'')"
```
```
P -c "$Q to stdout csv header" > age_text_backup.csv
```

Giữ file cùng chỗ backup DB. Nếu rule backfill sai, còn dữ liệu gốc để chạy lại. Migration
không có reverse khôi phục được — đây là lưới an toàn duy nhất.

## Ảnh hưởng đã biết

**Báo cáo khách hàng sẽ đổi số.** Biểu đồ phân bố tuổi hiện chia
`18-30 / 31-45 / 46-60 / 61-75 / 76-100`, sau đổi thành 6 nhóm mới. Không so sánh trực
tiếp được với báo cáo đã xuất trước đây. Đây là hệ quả không tránh được của việc gộp về
một nguồn sự thật, và là đánh đổi có chủ đích.

**Sale mất khả năng ghi tuổi chính xác.** Trước gõ được `47`, giờ chỉ chọn `45-54`. Đúng
với mục tiêu chuẩn hoá; nếu sau này cần tuổi chính xác thì phải là một trường riêng, không
phải quay lại free text.

## Rủi ro

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| Backfill map sai nhóm | Thấp | Chỉ 3.026 dòng; dump CSV trước; unit test dựng từ dữ liệu thật |
| Client cũ còn gửi free text lên API | Thấp | Validate reject rõ ràng bằng `AssessmentValidationError`; FE deploy cùng lúc |
| AI autofill trả code ngoài whitelist | Thấp | `normalize_extracted` vốn đã không tin enum của LLM, giá trị lạ rơi vào `rejected` |

## Việc chưa chốt

**Trạng thái tại thời điểm chốt task 8 (2026-07-27): query này CHƯA được chạy trên prod.**
Đây không phải việc "để sau tuỳ chọn" — nó là bước bắt buộc phải chạy và soát kết quả
**trước khi** chạy migration `0110_backfill_age_group` trên prod.

Query cần chạy là truy vấn phần đuôi dữ liệu (222 giá trị còn lại, ~300 dòng, chưa nằm
trong top-100 đã phân loại ở bảng "Hiện trạng dữ liệu prod"):

```
Q1="select age_text,count(*) c from order_assessments"
```
```
Q2=" where age_text<>'' and age_text !~ '^[0-9]+t?$' group by 1 order by c desc"
```
```
P "$Q1$Q2"
```

Lý do bắt buộc: parser có nhánh catch-all (luật cuối trong bảng luật ở mục 5) nên phần
đuôi chưa soát **không chặn được việc chạy migration về mặt kỹ thuật** — mọi giá trị lạ
tự rơi về `not_disclosed` một cách an toàn. Nhưng an toàn về kỹ thuật không đồng nghĩa
đúng về nghiệp vụ: nếu phần đuôi này chứa nhiều giá trị đáng lẽ parse được thành một nhóm
tuổi cụ thể (ví dụ lỗi chính tả của một mẫu đã biết) mà lại rơi vào `not_disclosed`, tỷ lệ
"không xác định" sẽ bị thổi phồng so với thực tế, ảnh hưởng tới các báo cáo dùng trường
này.

Việc phải làm trước khi deploy prod, theo đúng thứ tự:

1. Chạy `Q1$Q2` ở trên qua `psql` trên prod (xem alias `P` ở mục "Vận hành khi deploy").
2. Đối chiếu output: nếu xuất hiện mẫu lặp lại nhiều lần rõ ràng có thể parse được (ví dụ
   một cách viết số tuổi mới chưa có trong bộ luật), quay lại xem xét bổ sung luật cho
   `age_group_backfill.py` trước khi migrate, thay vì chấp nhận rơi vào `not_disclosed`.
3. Nếu không có mẫu nào đáng kể (toàn giá trị hiếm, mỗi giá trị 1-2 dòng), coi như đã kiểm
   chứng xong, ghi lại kết quả (số dòng, vài mẫu tiêu biểu) vào ticket/báo cáo triển khai
   rồi mới chạy migration.
4. Sau migration, đối chiếu phân bố thực tế với bảng ước tính ở mục "Trước khi deploy prod"
   (kỳ vọng `not_disclosed` khoảng 385; nếu vượt xa 400, dừng lại và soi lại parser).

Không có bước nào ở trên đã được thực hiện tại thời điểm viết mục này — người chạy deploy
prod là người chịu trách nhiệm thực hiện và ghi nhận kết quả.
