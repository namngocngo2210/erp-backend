# Ghi chú cho mọi dòng ở Lịch sử nhập xuất — thiết kế & tiến độ

**Ngày:** 2026-08-22 · **Branch:** `feat/flow-history-notes`

## 1. Vấn đề

Cột **Ghi chú** của trang Lịch sử nhập xuất đang chắp vá: chỉ 3 trong 6 loại
dòng nhập được, số còn lại hiện `—` cứng hoặc không có cột.

| Tab / kiểu xem | Dòng | Hiện trạng |
| --- | --- | --- |
| Xuất hàng ra (theo đơn) | mã quét chờ khớp | sửa được (`PendingShipmentScan.note`) |
| Xuất hàng ra (theo đơn) | đơn xuất (`kind=order`) | **`—` hard-code** |
| Xuất hàng ra (theo đơn) | phiếu xuất kho / chuyển kho | **`—` hard-code** (dù `StockDocument.note` có sẵn) |
| Xuất hàng ra (theo SKU) | mọi dòng | không có cột |
| Nhập hàng hoàn về | phiếu trả | sửa được (`ReturnEntry.note`) |
| Nhập hàng hoàn về | dòng chuyển kho | **chỉ đọc** |
| Chờ hàng hoàn về | mọi dòng | **không có cột** (`closed_note` chỉ nhập lúc đóng) |

Hệ quả nghiệp vụ: kho muốn ghi lại "đóng thiếu 1 cái", "khách hẹn trả 20/8",
"phiếu này bù cho đơn X" thì không có chỗ ghi, phải nhắn ngoài Lark — thông tin
rơi khỏi hệ thống.

## 2. Quyết định đã chốt

1. **Mọi dòng nhập tay được**, trừ kiểu xem theo SKU.
2. **Kiểu xem theo SKU không có ô ghi chú.** Dòng SKU là số tổng hợp theo
   khoảng ngày đang lọc, không có thực thể ổn định để gắn note — gắn vào đó thì
   ý nghĩa của note đổi mỗi lần đổi bộ lọc.
3. **Mỗi tab một ghi chú riêng cho dòng theo đơn.** Một đơn huỷ sau khi xuất
   xuất hiện ở cả tab *Xuất hàng ra* và tab *Chờ hàng hoàn về*; hai tình huống
   khác nhau (chuyện lúc xuất vs chuyện đòi hàng) nên hai ghi chú tách biệt.
4. **Ngoại lệ có chủ ý — phiếu chuyển kho dùng chung một ghi chú** ở cả hai tab.
   Nó là **cùng một chứng từ** (xuất ở kho nguồn = nhập ở kho đích), tách note
   ra sẽ đẻ ra hai sự thật cho một tờ phiếu.
5. **Sửa note phiếu kho ở màn lịch sử ghi thẳng vào `StockDocument.note`** —
   cùng ô mà người tạo phiếu nhập trong dialog tạo phiếu. Một phiếu, một ghi chú.

## 3. Thiết kế

### 3.1 Chỗ lưu

Ba loại dòng **đã có** chỗ lưu, giữ nguyên (không migrate — đụng vào là ảnh
hưởng luồng quét kho và Excel đang chạy):

| Dòng | Chỗ lưu sẵn có |
| --- | --- |
| mã quét chờ khớp | `PendingShipmentScan.note` |
| phiếu trả (nhóm đơn hoàn) | `ReturnEntry.note` |
| phiếu xuất kho / chuyển kho | `StockDocument.note` |

Hai loại **chưa có** chỗ lưu → bảng mới `stock_flow_notes`:

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `id` | bigserial | PK |
| `target_type` | varchar(32) | `order_dispatch` · `awaiting_return` |
| `target_id` | bigint | id của thực thể theo `target_type` |
| `note` | text | rỗng = không có ghi chú (không xoá dòng) |
| `updated_by` | FK user, `SET_NULL` | ai sửa lần cuối |
| `updated_at` | timestamptz | `auto_now` |
| `created_at` | timestamptz | |

- **Unique `(target_type, target_id)`** — mỗi dòng đúng một ghi chú.
- `target_id` của `order_dispatch` là **`order_id`** (dòng lịch sử xuất gom
  movement theo `reference_id = order_id`, đây là khoá ổn định).
- `target_id` của `awaiting_return` cũng là **`order_id`** —
  `CancelledDispatchWatch.order` là `OneToOneField`, một đơn đúng một dòng theo
  dõi. Hai loại nằm ở hai `target_type` khác nhau nên **không đè nhau**
  (quyết định §2.3).
- **CỐ Ý không dùng FK** tới `orders`: bảng này là sổ ghi chú của module kho,
  gắn FK sẽ kéo phụ thuộc ngược tầng và chặn xoá đơn. Dòng mồ côi vô hại.

Cân nhắc đã loại:

- *Mỗi thực thể một field* (thêm `note` vào `CancelledDispatchWatch` + bảng
  `order_dispatch_notes` riêng): đúng về mô hình hoá nhưng thành 3 endpoint, 3
  nhánh FE, và loại dòng thứ 7 lại đẻ bảng thứ 3.
- *Ghi vào `StockMovement.note`*: một dòng UI = N movement → ghi chú nhân bản,
  sai ngữ nghĩa (`StockMovement.note` là ghi chú kỹ thuật của bút toán).

### 3.2 API

Một endpoint duy nhất cho mọi loại dòng, kể cả loại đã có chỗ lưu — FE chỉ cần
một component:

```
PATCH /api/inventory/flow-notes
body: {"target_type": "...", "target_id": 123, "note": "..."}
→ 200 {"target_type", "target_id", "note"}
```

`target_type` hợp lệ: `order_dispatch` · `awaiting_return` · `stock_document` ·
`return_entry` · `pending_scan`. Service điều hướng:

| `target_type` | Ghi vào |
| --- | --- |
| `order_dispatch`, `awaiting_return` | `stock_flow_notes` (upsert) |
| `stock_document` | `StockDocument.note` |
| `return_entry` | `ReturnEntry.note` (gọi lại `ReturnEntryService.update_note`) |
| `pending_scan` | `PendingShipmentScan.note` (gọi lại `PendingScanService.update_note`) |

Hai endpoint cũ (`return-history/<id>/note`, `flow-pending/<id>/note`) **giữ
nguyên** — đang được dùng và có test; endpoint mới chỉ uỷ quyền lại cho cùng
service, không nhân đôi logic ghi.

**Quyền:** `stock:edit` — đúng mức hai endpoint note hiện tại đang gác.

### 3.3 Đọc & join

Note được nhồi vào row **bên trong** `MergedIssueHistory.orders_page` /
`MergedReturnHistory.list_page` / `AwaitingReturnService.list_page` — mỗi trang
**một query** (`FlowNoteService.notes_for`), không N+1.

> **Bẫy đã biết:** màn hình và job export Excel là **hai nhánh code riêng**
> (`transport/views/flow_*_views.py` vs `exports_definitions.py`, nhánh sau chạy
> ở worker và tự parse params). Bỏ sót một nhánh = file Excel lệch màn hình mà
> không có cảnh báo nào.
>
> Chọn nhồi note **trong hàm `*_page`** chính là để triệt bẫy này: cả view lẫn
> job export đều gọi đúng hai hàm đó, nên không thể lệch nguồn. Phần còn phải
> làm tay ở export chỉ là **ghép chuỗi** vào cột Ghi chú (§3.5).

### 3.4 Giao diện

Dùng lại `InlineNoteCell` đang có (tự lưu: debounce 700ms khi gõ + lưu ngay khi
blur, lạc quan + revert khi lỗi). Bọc thành `FlowNoteCell` nhận
`targetType`/`targetId`.

| Chỗ sửa | Việc |
| --- | --- |
| `IssueTab.tsx` | thay `—` ở dòng đơn / phiếu bằng `FlowNoteCell` |
| `ReturnTab.tsx` | dòng chuyển kho: chỉ đọc → sửa được |
| `AwaitingReturnTab.tsx` | **thêm cột Ghi chú** (bảng) + `CardField` (mobile) |
| `flow-mobile.tsx` | thẻ dòng đơn / phiếu: đổ `FlowNoteCell` vào `note` |

- **Không có quyền `stock:edit` → hiện chữ thường, không hiện ô nhập.** Hiện tại
  FE không gác gì, người thiếu quyền gõ xong mới ăn 403 và mất chữ.
- Kiểu xem theo SKU: không đụng.
- **KHÔNG hiện "ai sửa lúc nào"** trên ô: chỉ 2 trong 5 loại dòng có
  `updated_by` (hai loại ở bảng mới); ba loại ghi vào field sẵn có không lưu
  người sửa. Hiện tooltip chỗ có chỗ không sẽ khó hiểu hơn là không hiện. Bảng
  vẫn lưu `updated_by`/`updated_at` để tra khi cần.

### 3.5 Excel

Cột *Ghi chú* của sheet Xuất/Nhập **đã tồn tại**, hiện chỉ chứa note của mã quét
(join theo mã vận đơn). Bổ sung note mới, nối bằng `"; "` — đúng cách sheet Nhập
hoàn đang gộp scan note vào note phiếu trả (audit 2026-07-15b).

Tab *Chờ hàng hoàn về* **không có export** (registry chỉ có `stock`,
`dispatch_history`, `return_history`) → không đụng tới, ghi chú chỉ trên màn hình.

## 4. Migration

`apps/inventory/migrations/0041_stock_flow_note.py` — tạo bảng + unique index
`(target_type, target_id)`. **Không backfill**: chưa có dữ liệu nguồn nào để
chuyển. Bảng rỗng lúc deploy nên **an toàn chạy trước khi roll code**.

## 5. Kiểm thử

**Backend** (`apps/inventory/tests/test_flow_notes.py`):

- upsert lần đầu tạo dòng, lần hai ghi đè, không đẻ dòng thứ hai (unique).
- 5 `target_type` ghi đúng chỗ (2 loại ở bảng mới + 3 field cũ).
- `target_type` lạ → 400; thiếu `stock:edit` → 403.
- Note của cùng `order_id` ở `order_dispatch` và `awaiting_return` **không đè nhau**.
- List view trả đúng note cho từng loại dòng; export Excel ra **cùng chuỗi** với
  màn hình (chống lệch hai nhánh).

**Frontend** (`features/inventory/__tests__/`):

- Gõ vào ô note dòng đơn → gọi PATCH đúng `target_type` / `target_id`.
- Thiếu quyền `stock:edit` → không render `<input>`.
- Cột Ghi chú xuất hiện ở tab Chờ hàng hoàn về (cả bảng và thẻ mobile).

> Bẫy test đã biết: `ResponsiveList` + `matchMedia` — jsdom mặc định rơi vào
> nhánh thẻ mobile; chốt viewport bằng `src/test/viewport.ts`. Test export có
> **chỉ số cột cứng** — thêm cột là phải sửa test tương ứng.

## 6. Sprint

| Sprint | Nội dung | Trạng thái |
| --- | --- | --- |
| 1 | Model + migration 0041 + `FlowNoteService` + endpoint `PATCH /flow-notes` | ✅ xong |
| 2 | Nhồi note vào 3 danh sách (xuất / hoàn / chờ hoàn) | ✅ xong |
| 3 | FE: `FlowNoteCell`, 3 tab, bản mobile, gác quyền | ✅ xong |
| 4 | Excel: nối note vào cột Ghi chú của sheet Xuất | ✅ xong |

### Đã làm (2026-08-23)

- `stock_flow_notes` + migration `0041_stock_flow_note` (bảng rỗng lúc deploy).
- `FlowNoteService.update` điều hướng 5 `target_type`; `notes_for` đọc 1 query/trang.
- `PATCH /api/inventory/flow-notes` gác `stock:edit`.
- `note`/`doc_id` trả về từ `MergedIssueHistory.orders_page`,
  `MergedReturnHistory.list_page`, `AwaitingReturnService.list_page`.
- FE: `FlowNoteCell` (gác quyền, thiếu `stock:edit` thì hiện chữ thường), gắn vào
  tab Xuất, tab Nhập hoàn (dòng chuyển kho), tab Chờ hàng hoàn về (cột mới) và
  cả 2 thẻ mobile.
- Excel sheet Xuất: nối ghi chú dòng + ghi chú mã quét bằng `"; "`. Sheet Nhập
  hoàn tự có vì đã đọc `row["note"]` từ trước.
- Test: backend `apps/inventory/tests/test_flow_notes.py` (20) + 2 test export;
  FE `flow-note-cell.test.tsx` (4).

### Còn nợ

- **Chưa gán quyền mới nào** — dùng `stock:edit` sẵn có, ai đang sửa được phiếu
  kho là sửa được ghi chú.
- Tab *Chờ hàng hoàn về* gác `require_section("returns")` còn ghi chú gác
  `stock:edit`: người chỉ có section `returns` xem được dòng nhưng không sửa
  được ghi chú. Chấp nhận vì trang này nằm trong section `stock`.
- Chưa hiển thị "ai sửa lúc nào" ở đâu cả (bảng mới có lưu `updated_by`/
  `updated_at` của lần sửa cuối — xem §3.4 vì sao chưa hiện).

## 7. Rủi ro

- **Lệch màn hình ↔ Excel** nếu quên nhánh export — đã nêu ở §3.3, có test đối chiếu.
- **Ghi đè note phiếu kho**: người tạo phiếu và người sửa ở màn lịch sử dùng
  chung một ô. Đã chốt là hành vi mong muốn (§2.5).
- **Không có khoá chống ghi đè đồng thời**: hai người sửa cùng một ô thì người
  lưu sau thắng. Chấp nhận — đúng như hành vi hai ô note đang chạy.
