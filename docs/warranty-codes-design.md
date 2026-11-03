# Thiết kế: Trang "Mã bảo hành" (phát hành serial + mã QR iCheck)

**Ngày:** 2026-08-05
**Trạng thái:** Đã triển khai xong (S1-S4), chờ review cuối + merge
**Nhánh:** `feat/warranty-codes`

## 1. Bối cảnh & mục tiêu

Công ty dùng tem QR iCheck dán lên sản phẩm để khách xác thực hàng thật/giả.
File tem iCheck phát hành gồm 3 cột: `SERIAL` (vd `DY-015152`), `QRI`
(`http://qcheck.vn/<mã>`), `SMS` (mã nhắn tin). Thay vì nhận file từ iCheck,
ta chủ động **phát hành dải mã của riêng mình** rồi xuất Excel gửi iCheck in tem.

Trang quản trị mới **"Mã bảo hành"** cho phép:

1. Sinh lô mã (serial `DX-xxxxxx` + mã 10 ký tự) — nối tiếp toàn hệ thống.
2. Gắn lô (hoặc phần tách của lô) vào một sản phẩm.
3. Tra cứu từng mã (tab IMEI) theo sản phẩm / ngày tạo / tìm kiếm.
4. Xuất Excel theo lô: `SERIAL | URL | SKU` (URL = base qcheck + mã 10 ký tự;
   SKU sản phẩm gắn với lô, lô reserved chưa gắn sản phẩm thì để trống; cột
   IMEI đã bỏ theo yêu cầu 06/08 — `code` 10 ký tự vẫn lưu trong DB, chỉ
   không xuất ra file nữa).

Đây là nền tảng dữ liệu cho giai đoạn sau: portal public tra cứu hành trình
đơn hàng + bảo hành theo serial (ngoài phạm vi tài liệu này).

### Quyết định đã chốt với chủ dự án

| Câu hỏi | Quyết định |
| --- | --- |
| Trải nghiệm quét của khách | 1 tem iCheck duy nhất; landing qcheck.vn gắn link về portal công ty (làm việc với iCheck, ngoài phạm vi sprint này) |
| Đánh số serial | Nối tiếp toàn hệ thống, không reset theo lô; pad 6 chữ số (`DX-000001`) |
| Gắn sản phẩm | Tùy chọn lúc tạo lô; gắn sau được (Reserved → Assigned) |
| File Excel | Gửi iCheck in tem; 3 cột SERIAL/URL/SKU — SKU thêm theo yêu cầu 05/08, URL (base qcheck + mã) bổ sung lại theo yêu cầu 06/08, cột IMEI bỏ theo yêu cầu 06/08 (chủ dự án phản hồi lại, không cần in) |
| Bảng danh sách hiển thị sản phẩm | Chỉ hiện SKU (`product_sku`), không hiện tên đầy đủ — tên dài làm vỡ layout bảng "Lô hàng" / tab "IMEI"; yêu cầu 06/08 |

## 2. Kiến trúc

- **Backend**: app Django mới `backend/apps/warranty_codes/` theo chuẩn clean
  architecture của repo (`docs/architecture-standard.md`), mẫu tham chiếu
  `apps/cnv_loyalty`:

```
apps/warranty_codes/
├── apps.py, __init__.py
├── models.py                      # WarrantyCodeBatch, WarrantyCode, WarrantyCodeCounter
├── migrations/
├── contracts/dtos.py              # DTO tạo lô / gán / tách / filter
├── domain/serial.py               # format serial, sinh code 10 ký tự (secrets)
├── application/services/
│   ├── batch_service.py           # tạo lô, gán SP, tách lô
│   └── export_service.py          # xuất Excel
└── transport/
    ├── serializers.py
    ├── views/batch_views.py
    ├── views/code_views.py
    └── urls.py
```

- **Không** nhét vào `apps/warranty` (claims): app cũ dạng phẳng, nghiệp vụ
  khác nhau (xử lý khiếu nại vs phát hành mã).
- Sinh mã chạy **đồng bộ** (không Celery): bulk insert theo chunk, trần
  100.000 mã/lô (~vài giây trên Postgres).

## 3. Data model

### 3.1 `warranty_code_batches`

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `id` | PK | |
| `batch_code` | varchar, unique | Tự sinh `LOT-YYYYMMDD-NNN` (NNN reset theo ngày) |
| `start_seq` / `end_seq` | bigint | Dải số toàn cục, `end_seq >= start_seq` |
| `quantity` | int | `end_seq - start_seq + 1` (denormalize để list nhanh) |
| `generated_seq` | bigint | Seq lớn nhất đã có dòng `warranty_codes`; `< end_seq` = đang sinh nền (§4.1b). Migration 0007 backfill = `end_seq` |
| `status` | varchar | `reserved` \| `assigned` |
| `product_id` | FK `orders.Product`, null | Null khi `reserved` |
| `parent_id` | FK self, null | Lô cha nếu sinh ra từ tách |
| `created_by` / `assigned_by` | FK user | |
| `assigned_at` / `exported_at` | datetime, null | `exported_at` = lần xuất gần nhất |
| `created_at` | datetime | |

Index: `status`, `product_id`, `created_at`, unique `batch_code`.

### 3.2 `warranty_codes`

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `id` | PK | |
| `seq` | bigint, unique | Số thứ tự toàn cục |
| `serial` | varchar, unique | `DX-000001` (prefix + pad 6, cấu hình settings) |
| `code` | char(10), unique | A–Z + 0–9, sinh bằng `secrets` — chính là "IMEI" |
| `batch_id` | FK batch | |
| `product_id` | FK, null | Denormalize từ lô để lọc nhanh |
| `created_at` | datetime | |

Index: unique(`seq`), unique(`serial`), unique(`code`), (`batch_id`),
(`product_id`, `created_at`). Trạng thái từng mã **suy ra từ lô** (không lưu
cột status riêng — tránh lệch dữ liệu).

### 3.3 `warranty_code_counter`

Bảng 1 dòng giữ `last_seq`. Cấp phát dải liên tiếp bằng
`select_for_update` trong transaction → hai request Sinh Mã song song không
bao giờ trùng dải. Migration seed dòng counter = 0.

### 3.4 Settings

```python
WARRANTY_CODE_SERIAL_PREFIX = env("WARRANTY_CODE_SERIAL_PREFIX", default="DX-")
WARRANTY_CODE_SERIAL_PAD = 6
WARRANTY_QR_BASE_URL = env("WARRANTY_QR_BASE_URL", default="http://qcheck.vn/")
# Từ 17/09/2026: KHÔNG còn trần số lượng/lô. Tới ngưỡng này sinh mã ngay trong
# request; lớn hơn đẩy Celery (queue `slow`) sinh nền — xem §4.1b.
WARRANTY_CODE_SYNC_GENERATE_MAX = 20_000
```

## 4. Nghiệp vụ

### 4.1 Tạo lô

Input: `quantity` (≥ 1, không trần), `start_seq` (tùy chọn, ≥ 1), `product_id`
(tùy chọn).

1. Transaction: lock counter. `start_seq` bỏ trống → `start = last_seq + 1`
   (nối tiếp như trước); có nhập → `start = start_seq`. `end = start + quantity - 1`.
   Chặn đè dải: nếu tồn tại lô có `start_seq <= end AND end_seq >= start` →
   400 kèm tên lô đang chiếm (mọi mã đều thuộc một lô có dải bao trùm, tách lô
   giữ bất biến này, nên chỉ cần so với bảng lô; `unique(seq)` là chốt cuối).
   Cho phép nhảy cóc (bỏ trống khoảng giữa). `last_seq = max(last_seq, end)` —
   counter chỉ tiến, lô lấp khoảng trống phía dưới không làm lô "nối tiếp" sau
   đó quay lại đè dải đã cấp.
2. Tạo batch (`reserved`, hoặc `assigned` nếu có `product_id`), `generated_seq
   = start - 1`. Transaction kết thúc ở đây — KHÔNG giữ lock counter trong lúc
   insert mã (lô 20k ≈ 6s).
3. Sinh mã (§4.1b): `quantity <= WARRANTY_CODE_SYNC_GENERATE_MAX` → gọi
   `generate_codes` ngay trong request; lớn hơn → `transaction.on_commit` đẩy
   task `generate_batch_codes` (queue `slow`).

### 4.1b Sinh mã theo chunk, resumable

`WarrantyCodeBatchService.generate_codes(batch_id)`: lặp tới khi
`generated_seq == end_seq`; mỗi vòng là 1 transaction: lock dòng lô
(`select_for_update(of=("self",))`), đọc lại `generated_seq`, insert chunk
5.000 mã `[generated_seq + 1, ...]`, rồi `update(generated_seq=chunk_end)`.
Serial từ seq (`DX-` + pad 6, quá 999.999 serial tự dài ra `DX-1000000`); code
10 ký tự `secrets.choice("A-Z0-9")`. `bulk_create`; nếu `IntegrityError` do
trùng `code` (xác suất ~36^-10) → sinh lại chunk đó, tối đa 3 lần.

- Worker chết giữa chừng → chạy lại nối tiếp từ `generated_seq + 1`, không
  trùng, không thiếu. Hai lượt chạy trùng lô (autoretry đè lượt cũ) xếp hàng
  chờ lock dòng lô rồi thấy nhau đã tiến tới đâu.
- Task `apps.warranty_codes.tasks.generate_batch_codes`: time_limit 1h
  (1 triệu mã ≈ 5 phút, ~3.500 mã/s đo trên prod), `autoretry_for=Exception`,
  backoff tối đa 5 phút, 10 lần. Hết retry mà vẫn dở → lô kẹt ở "Đang sinh
  x/y"; chạy tay `WarrantyCodeBatchService.generate_codes(batch_id=...)`
  trong `manage.py shell` để sinh nốt.
- `is_generating = generated_seq < end_seq`: trong lúc đó backend từ chối gán
  SP / tách / xuất Excel (400 "Lô đang sinh mã…"); UI ẩn 3 nút này, hiện badge
  "Đang sinh x/y" và poll list 3s/lần tới khi xong.
4. `batch_code`: `LOT-<YYYYMMDD>-<NNN>` — NNN đếm theo ngày. Mọi thao tác
   sinh `batch_code` (tạo lô **và** tách lô) đều nằm trong transaction có
   lock counter chung để không trùng NNN; `batch_code` vẫn có unique
   constraint làm chốt chặn cuối.

### 4.2 Gán sản phẩm

Chỉ khi `status = reserved`. Đặt `product_id`, `status = assigned`,
`assigned_by/at`; update `product_id` toàn bộ mã trong lô (chunked update
theo dải seq). Audit tự động qua `AuditLogMiddleware` (log mọi request
`/api/warranty-codes/*`) — không có audit call thủ công trong service.

### 4.3 Tách lô

Chỉ khi `status = reserved` (đã gắn SP thì không tách — khớp mockup đã
duyệt; nhu cầu đổi SP của lô đã gán nằm ngoài phạm vi). Input `cut_seq` với
`start_seq <= cut_seq < end_seq`:

1. Lô cha thu về `[start_seq, cut_seq]`, cập nhật `quantity`.
2. Tạo lô con `[cut_seq + 1, end_seq]`, `parent_id` = cha, `batch_code` mới.
3. Update `batch_id` các mã thuộc dải con (theo `seq` range — 1 câu UPDATE).

### 4.4 Xuất Excel

**Từ 17/09/2026 — xuất nhiều lô vào một file.** Bảng lô không còn nút Excel
từng hàng; có cột checkbox (ô đầu bảng = chọn/bỏ cả trang; lô đang sinh mã
bị vô hiệu; chọn giữ qua trang, reset khi đổi bộ lọc) và một nút **Xuất
Excel** trên thanh công cụ:
- có tick → job `warranty_code_batch` với `{batch_ids: [...]}` (nhãn "Xuất
  Excel (N)");
- không tick → `{filters: {q, product_id, status, date_from, date_to}}` =
  toàn bộ lô khớp bộ lọc hiện tại, mọi trang. Backend dùng chung
  `repository.filter_batches` với list endpoint nên tập xuất khớp bảng.

`validate()` giải tập lô ngay lúc tạo job thành `batch_ids` sort ổn định
(dedupe theo params_hash; lô tạo sau khi bấm không lọt vào). Rỗng → 400
"Không có lô nào để xuất". Có lô `is_generating` → 400 nêu tên lô (quyết định
chủ: từ chối, không âm thầm bỏ qua). File vẫn 3 cột `SERIAL | URL | SKU`,
sắp theo serial, SKU tra theo lô của từng dòng; 1 lô giữ tên `<mã phiếu>.xlsx`,
nhiều lô → `ma-bao-hanh-<N>-lo-<YYYYMMDD>.xlsx`. Xong ghi `exported_at` cho
mọi lô trong file. `batch_id` đơn vẫn nhận (tương thích).

*(Phần dưới là thiết kế gốc luồng đồng bộ, đã thay bằng job export nền ở Task 18.)*

`GET /batches/{id}/export/` → openpyxl `write_only` + `FileResponse`
(stream), tên file `<batch_code>.xlsx`. 3 cột SERIAL/URL/SKU (SKU thêm theo
yêu cầu 05/08 — lấy 1 lần từ `batch.product`, không join per-row, lô
reserved chưa gắn sản phẩm để trống; URL = `WARRANTY_QR_BASE_URL` ghép với
mã 10 ký tự — cột IMEI bỏ theo yêu cầu 06/08, `code` vẫn lưu trong DB, chỉ
không xuất ra file nữa):

| SERIAL | URL | SKU |
| --- | --- | --- |
| DX-000001 | http://qcheck.vn/A8K2M4P9QX | SKU-CFCT01 |

Query iterator theo chunk (tránh load 100k row vào RAM). Ghi `exported_at`.
Xuất được nhiều lần (in lại tem).

Cột SKU (cột cuối, thứ 3) là dữ liệu người dùng nhập (`product.sku`, khác
SERIAL/URL do hệ thống tự sinh) nên phải chống formula-injection: dùng
`clean_excel_cell` (`apps/common/excel_utils.py`) lọc ký tự control, rồi ép
cell suy thành formula (`data_type == "f"`, vd SKU bắt đầu bằng `=`) về lại
text (`data_type = "s"`, `quotePrefix = True`) — fix sự cố phát hiện 05/08
sau khi đổi cột URL sang SKU. Sheet `write_only` không giữ cell sau khi
`ws.append()` nên không mirror trực tiếp `append_clean_row` được, phải tự
dựng `WriteOnlyCell` với `data_type` đã sửa trước khi append (xem
`export_service._sanitized_sku_cell`). Cell này vẫn dựng **mới cho mỗi
dòng** — fix giữ nguyên dù SKU đã là cột cuối từ khi bỏ IMEI 06/08 (bug gốc
05/08→06/08: writer `write_only` của openpyxl mutate cell theo vị trí lúc
serialize, share 1 instance qua nhiều dòng khi có cột theo sau đổi giá trị
sẽ làm SKU bị lệch sang giá trị dòng trước).

`WARRANTY_QR_BASE_URL` (setting, `config/settings_parts/base.py`, default
`http://qcheck.vn/`) dùng lại trong export từ 06/08 để build cột URL; đọc 1
lần ngoài vòng lặp.

## 5. API

Prefix `/api/warranty-codes/`, auth bắt buộc, RBAC section mới
`warranty-codes` (mirror vào `SECTION_PERMISSION` backend + frontend).

| Method | Path | Chức năng |
| --- | --- | --- |
| POST | `/batches/` | Tạo lô `{quantity, start_seq?, product_id?}` — response thêm `is_generating`, `generated_count` |
| GET | `/batches/` | List + filter: `batch_code`, `product`, `status`, `date_from/to`; phân trang |
| POST | `/batches/{id}/assign/` | `{product_id}` |
| POST | `/batches/{id}/split/` | `{cut_seq}` |
| GET | `/batches/{id}/export/` | Tải xlsx |
| GET | `/codes/` | List mã: `search` (serial/code — match exact hoặc prefix, dùng btree `varchar_pattern_ops`; không dùng `icontains` vì không có index phục vụ), `product`, `date_from/to`; phân trang |

Lỗi nghiệp vụ trả 400 kèm message tiếng Việt (theo pattern serializers hiện có).

## 6. Frontend

### 6.1 Sidebar

- Module mới **"Bảo hành"** trong `frontend/src/app/modules.ts`: icon
  `ShieldCheck`, accent riêng, `basePath: "/aftersales"`.
  (Không dùng prefix `/warranty*` — `main.tsx` đã dành `path.startsWith("/warranty")`
  cho trang public.)
- Chuyển section `warranty` (claims) từ module Sales sang module mới; thêm
  section `warranty-codes` ("Mã bảo hành") với quyền RBAC riêng.
- Cập nhật `nav-layout.ts`, `routes.tsx`, `routed-pages.tsx`, i18n labels và
  test `modules.test.ts` / `nav-layout.test.ts` tương ứng. Route claims cũ
  redirect sang path mới.

### 6.2 Trang "Mã bảo hành" (`pages/WarrantyCodesPage.tsx` + `features/warranty-codes/`)

Hai tab (theo mockup đã duyệt):

**Tab Lô hàng**
- Bảng: Mã phiếu, Khoảng số, Số lượng, Trạng thái (Reserved/Assigned),
  Sản phẩm (chỉ hiện SKU — `product_sku`, không hiện tên đầy đủ; đổi theo
  yêu cầu 06/08 vì tên sản phẩm dài làm vỡ cột), Người tạo, Người gán,
  Ngày tạo, Thao tác.
- Bộ lọc: mã phiếu, sản phẩm, trạng thái, từ/đến ngày.
- Nút **Sinh Mã**: dialog nhập số lượng (+ chọn sản phẩm tùy chọn), hiện chú
  thích prefix và trạng thái.
- Thao tác từng dòng: Xuất Excel, Xem (nhảy sang tab IMEI lọc theo lô),
  Gán SP (chỉ reserved), Tách (chỉ reserved — dialog nhập điểm cắt, validate dải).

**Tab IMEI**
- Bảng: Serial, Mã (10 ký tự), Lô, Sản phẩm (chỉ hiện SKU, như tab Lô hàng —
  yêu cầu 06/08), Trạng thái, Ngày tạo.
- Bộ lọc: tìm kiếm serial/mã, sản phẩm, từ/đến ngày; phân trang server-side.

Không làm (YAGNI): tab Sản phẩm/Danh mục/Nhật ký, pool mã Free, đổi trạng
thái từng mã, gán mã theo đơn hàng.

## 7. Kiểm thử

- **Service**: cấp dải nối tiếp qua nhiều lô; 2 thread tạo lô song song không
  trùng dải; tách tại biên (`cut_seq = start/end` → 400); retry khi trùng
  code; gán cập nhật đủ `product_id`; nội dung + tên file Excel.
- **API**: quyền (user không có section `warranty-codes` → 403), filter,
  phân trang, validate quantity trần.
- **FE**: test vitest theo pattern hiện có cho bảng + dialog sinh/gán/tách.
- Chạy test backend bằng Docker theo quy trình nội bộ (dựng lại container test
  nếu chưa có).

## 8. Sprint & tiến độ

| Sprint | Nội dung | Trạng thái |
| --- | --- | --- |
| S1 | Backend: app `warranty_codes`, models + migration, service tạo lô/gán/tách, unit test | Đã xong — models + migrations 0001/0002 (seed counter), `domain/serial.py`, `application/services/batch_service.py` (tạo lô có lock counter, gán, tách), test service (`tests/test_batch_service.py`, `tests/test_serial.py`) cover cấp dải song song, tách biên, retry trùng code |
| S2 | Backend: API DRF + RBAC + export Excel + test API | Đã xong — `transport/batch_views.py`, `transport/code_views.py`, RBAC section `warranty-codes` (`require_section`/`require_permission`), `application/services/export_service.py` xuất xlsx streaming, test API (`tests/test_batch_api.py`, `tests/test_code_api.py`, `tests/test_export.py`) |
| S3 | Frontend: module sidebar "Bảo hành", trang 2 tab, dialogs, i18n, FE test | Đã xong — module `aftersales` trong `modules.ts` (basePath `/aftersales`), `WarrantyCodesPage` 2 tab (Lô hàng + IMEI), dialogs Sinh Mã/Gán/Tách, api layer `features/warranty-codes/`, i18n, test vitest cho bảng + dialog theo pattern `DataTableShell` hiện có |
| S4 | Rà soát: index/EXPLAIN với 100k+ mã, review, MR | Đã xong — full suite backend (`warranty_codes`, `users`, `warranty`) 250 passed/0 failed, ruff sạch; FE `test:ci`/`tsc`/`lint:strict` sạch (không fail mới ngoài baseline đỏ sẵn trên master); review từng task (1-9) đều "review clean" (xem `progress.md`); index đã khai trong migration 0001 theo §3, verify qua test chạy trên Postgres thật (`--create-db`) |

Sau mỗi sprint cập nhật cột trạng thái + ghi chú những gì đã/chưa làm tại đây.

### Deviation khi triển khai

So với thiết kế ban đầu ở tài liệu này, quá trình triển khai có vài điều chỉnh nhỏ (đều đã qua review từng task, không đổi hành vi nghiệp vụ):

- **(a) DTO**: dùng `repository.py` thay vì `contracts/dtos.py` như phác thảo ở
  §2 — bám theo pattern `claim_repository.py` sẵn có của `apps/warranty`
  (app tham chiếu gần nhất trong repo cho nghiệp vụ tương tự), gộp truy vấn +
  chuyển đổi dữ liệu vào một lớp repository thay vì tách riêng DTO.
- **(b) Xuất Excel**: dùng openpyxl `Workbook(write_only=True)` để stream
  từng dòng thay vì `append_clean_row` (`apps/common/excel_utils.py`) nguyên
  bản — hàm đó đọc lại `ws[ws.max_row]` sau khi `append()`, không dùng được
  với sheet `write_only` (không giữ cell trong bộ nhớ sau khi ghi). Ban đầu
  (cột 3 = URL ghép từ mã hệ thống sinh) coi cả 3 cột là an toàn nên bỏ qua
  bước lọc formula-injection hoàn toàn. **Cập nhật 05/08 sau khi đổi cột 3
  thành SKU** (`product.sku` — dữ liệu người dùng nhập, có thể bắt đầu bằng
  `=` và bị openpyxl suy thành formula cell): thêm `_sanitized_sku_cell` tự
  dựng `WriteOnlyCell` với `clean_excel_cell` + ép `data_type`/`quotePrefix`
  giống `append_clean_row`, chỉ áp cho cột SKU (SERIAL/IMEI vẫn máy sinh nên
  không cần) — xem §4.4. **Cập nhật 06/08 — thêm lại cột URL làm cột 4**
  (giữ nguyên SKU ở cột 3, không thay thế): phát hiện bug khi thêm cột —
  `_sanitized_sku_cell` trước đó dựng 1 `WriteOnlyCell` rồi tái dùng cho mọi
  dòng (an toàn khi SKU là cột cuối); khi có cột URL theo sau với giá trị
  đổi theo dòng, writer `write_only` của openpyxl mutate cell theo vị trí
  lúc serialize nên SKU của mỗi dòng bị lệch thành URL của dòng trước. Fix:
  dựng `WriteOnlyCell` mới cho từng dòng thay vì share 1 instance — xem
  §4.4. **Cập nhật 06/08 — bỏ cột IMEI** (chủ dự án phản hồi không cần in
  IMEI ra file): 3 cột còn lại là SERIAL/URL/SKU, thứ tự giữ nguyên
  URL trước SKU; giữ nguyên fix "dựng `WriteOnlyCell` mới mỗi dòng" dù SKU
  giờ lại là cột cuối.
- **(f) Hiển thị sản phẩm ở bảng danh sách**: `WarrantyCodeBatchSerializer`
  và `WarrantyCodeSerializer` thêm field `product_sku` (source
  `product.sku`, read_only, default `None`) — giữ nguyên `product_name` để
  không phá contract khác (vd nơi khác đã dùng), chỉ FE đổi cột "SẢN PHẨM"
  ở `BatchesTab.tsx`/`CodesTab.tsx` (bảng desktop + card mobile) sang hiện
  `product_sku ?? "—"` thay vì `product_name` — yêu cầu 06/08.
- **(c) Quyền tạo lô**: bản đầu tiên POST tạo lô chỉ dùng
  `require_section("warranty-codes")` (khớp pattern `claim_views.py`), không
  kiểm tra riêng quyền `warranty-codes:create`. **Cập nhật fix-wave 05/08**
  (final review): thêm check tường minh `user_has_any_permission(request.user,
  "warranty-codes:create")` ở đầu nhánh POST, trả 403 kèm message nếu thiếu
  quyền — khớp lại với thiết kế gốc §5 (RBAC theo action cho từng endpoint,
  giống assign/split/export đã dùng `require_permission`).
- **(d) Route**: đặt module dưới `/aftersales` (không dùng prefix `/warranty*`
  như cân nhắc ban đầu) vì `main.tsx` đã dành `path.startsWith("/warranty")`
  cho trang public tra cứu bảo hành; đây là điều đã chốt trong thiết kế §6.1,
  ghi lại ở đây để rõ lý do khi đọc lại sau này.
- **(e) Xuất Excel — response**: `export_batch` build workbook `write_only`
  vào buffer `io.BytesIO()` trong bộ nhớ rồi trả `HttpResponse(buffer.getvalue())`
  thay vì `FileResponse` stream trực tiếp từ đĩa như phác thảo ở §4.4 — lô
  100k dòng chỉ nặng vài MB nên buffer in-memory đơn giản hơn mà không đánh
  đổi bộ nhớ đáng kể; ghi tuần tự theo chunk (không giữ 100k cell) vẫn theo
  đúng nguyên tắc streaming ở §4.4.
