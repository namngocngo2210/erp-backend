# Ghi nhận hàng gửi bù (đổi lỗi 2 chiều) khi khớp tay mã chờ khớp

- Ngày: 2026-07-24
- Branch: `feat/replacement-issue-on-manual-match`
- Loại: thay đổi **ghi sổ** (sinh thêm Phiếu Xuất kho) + FE luồng khớp tay. KHÔNG đụng
  guard `already_dispatched`, `issue_for_order`, hay invariant tồn treo theo đơn.

## 1. Bối cảnh & vấn đề

Nghiệp vụ **đổi hàng lỗi 2 chiều**: đơn đã xuất kho nay gửi **thêm** một sản phẩm thay
thế bằng mã vận đơn mới, đồng thời thu cái lỗi về. Luật đã chốt từ
[erp-issue-history-include-stock-documents.md](../../erp-issue-history-include-stock-documents.md):
đơn đã có tồn treo nên guard `already_dispatched` chặn trừ tồn lần 2 trên cùng đơn
(đúng — tránh trừ 2 lần); **cái thay thế phải trừ tồn bằng Phiếu Xuất kho**
(`StockDocument` doc_type=`issue`), và báo cáo tab Xuất đã gộp phiếu XK vào từ 16/07.

Luật đúng nhưng **phụ thuộc trí nhớ nhân viên**: phiếu phải tạo tay ở một màn hình khác
(`StockDocumentsPage`), không có gì nhắc tại nơi phát sinh.

Ca thực tế 2026-07-24 — đơn `DH012628` / HĐ `HD013124` (SKU `MSC06`):

| Thời điểm | Việc | Ghi sổ thực tế | Đúng ra phải |
|---|---|---|---|
| 21/7 16:35 | Xuất đơn gốc, VĐ `84859697925080` | `SALE` −1 | `SALE` −1 |
| 24/7 08:42 | Gửi hàng đổi, VĐ `84859707184483`, khớp tay vào `HD013124` | **không gì** | `issue` −1 |
| sau đó | Thu cái lỗi về | `ReturnEntry` +1 | `ReturnEntry` +1 |

Kết quả: tile `PHIẾU XUẤT KHO = 0`, tổng SL xuất hiện **1** trong khi **2 cái đã rời kho**.
Sau khi hàng lỗi về (+1), tồn sổ sẽ **dôi +1** so với thực tế.

Luật tồn kho đúng (đã chốt với CTO): gửi lần 1 trừ tồn, **gửi lần 2 cũng trừ tồn**, nhận
cái lỗi về thì cộng tồn → **net = trừ 1**.

## 2. Mục tiêu

Khi **khớp tay** một mã chờ khớp (kind=`dispatch`) vào một đơn **đã xuất kho trước đó**,
cho người dùng ghi nhận ngay đây là **hàng gửi bù** và sinh **Phiếu Xuất kho** tương ứng —
không phải nhớ sang màn hình khác.

Phi mục tiêu (YAGNI):

- KHÔNG áp ở dialog Quét xuất kho và KHÔNG áp ở cron tự khớp (xem §7).
- KHÔNG thêm loại chứng từ mới, KHÔNG cho một đơn ghi 2 lần `SALE`.
- KHÔNG đụng tab Nhập hoàn — chiều thu hàng lỗi về đã chạy đúng qua `_complete_return`.

## 3. Quyết định thiết kế & lý do

| Quyết định | Lý do |
|---|---|
| Dùng **Phiếu Xuất kho** (`issue`), không nới guard | Nới guard phá invariant `outstanding` theo đơn mà un-dispatch / release / reissue / đối soát KiotViet đang dựa vào, mất luôn lá chắn quét trùng, và thổi doanh số theo đơn. |
| Phiếu sinh ở trạng thái **CHỜ DUYỆT** (`PENDING_APPROVAL`) | Giữ nguyên vòng kiểm soát xuất kho: tồn chỉ trừ khi có người duyệt. Vào thẳng hàng chờ duyệt nên không thành phiếu nháp mồ côi. |
| **Một** request, BE quyết định | FE hỏi trước dựa trên cờ ở lookup, nhưng BE mới là nơi biết chắc lần khớp đó có ghi `SALE` hay không. Đơn vừa được người khác xuất → BE tự bỏ qua việc tạo phiếu (fail-safe), không cần lưu trạng thái trung gian giữa 2 call. |
| Bút toán mang **ngày quét**, không phải ngày duyệt | Báo cáo lọc/sắp theo `occurred_at`. Cùng luật với `_complete_dispatch` / `_complete_return` (mã quét ngày D khớp ở D+n vẫn thuộc ngày D). Duyệt muộn 2 hôm không được làm sản lượng rơi sai ngày. |

## 4. Luồng nghiệp vụ đích

1. Đơn gốc xuất kho → `SALE` −1. *(đã có)*
2. Kho gửi hàng đổi bằng VĐ mới → quét xuất, không khớp đơn nào → vào bảng chờ khớp. *(đã có)*
3. Người dùng khớp tay vào đơn gốc → guard `already_dispatched` chặn `SALE` lần 2. *(giữ nguyên)*
4. **MỚI** — panel khớp tay hỏi *"đơn đã xuất trước đó, đây là hàng gửi bù/đổi 2 chiều?"* →
   xác nhận SKU × SL → sinh phiếu `XK…` ở trạng thái **chờ duyệt**, mốc nghiệp vụ = ngày quét.
5. Người có quyền duyệt phiếu → `issue` −1.
6. Hàng lỗi thu về → quét Nhập hoàn, khớp tay vào đơn gốc → `ReturnEntry` +1. *(đã có)*

Tồn net = **−1**. Báo cáo tab Xuất = **2** (theo đơn 1 + phiếu kho 1), Nhập hoàn = 1.

## 5. Thiết kế Backend

### 5.1 `StockDocument.occurred_at` (migration)

Thêm cột **nullable** `occurred_at: datetime | None` — mốc nghiệp vụ của phiếu (khi hàng
thực rời/về kho), tách khỏi `created_at` (lúc lập phiếu) và `posted_at` (lúc duyệt).

- `StockDocumentService.create_document` nhận thêm `occurred_at=None` và lưu.
- `StockDocumentService._apply_line` truyền `occurred_at=doc.occurred_at` xuống
  `StockCommandService.record_movement`.
- `None` → `record_movement` mặc định `now()` ⇒ **mọi phiếu tay hiện có giữ nguyên hành vi**.

Ràng buộc sẵn có phải tôn trọng: `record_movement` ném lỗi nếu `occurred_at` ở tương lai
(bất biến `occurred_at <= created_at`, là cơ sở cho partition pruning ở
`filter_occurred_between`).

### 5.2 `PendingScanService.manual_match(..., replacement=None)`

`replacement: dict | None` với shape `{"lines": [{"product_id": int, "quantity": int}], "note": str}`.

- `None` (mặc định) → hành vi hiện tại **không đổi một dòng nào**.
- Có giá trị → sau khi `_dispatch_and_finalize` trả về, chạy `_create_replacement_issue`.

`_create_replacement_issue` — thứ tự guard, dừng ở guard đầu tiên không thoả:

1. `result["ok"]` là `False` → không tạo. Khớp thất bại thì không có gì để bù.
2. `"already_dispatched"` **không** nằm trong warnings → **không tạo**, trả
   `replacement_skipped="order_was_not_dispatched"`. Đây là chốt chặn chống trừ tồn 2 lần:
   khi đó `issue_for_order` vừa ghi `SALE` cho chính lần khớp này.
3. `scan.result_ref` khác rỗng → **không tạo**, trả `replacement_skipped="already_created"`.
   Chống double-click / retry sinh phiếu thứ 2.
4. `lines` rỗng hoặc có `quantity <= 0` → `ValueError` → 400.

Tạo phiếu trong **một transaction ngắn riêng, chạy sau** `_dispatch_and_finalize` (hàm đó
đã đóng transaction của nó trước khi trả về, và tuyệt đối không được giữ transaction mở
qua HTTP tới KiotViet). Transaction này khoá lại scan bằng `select_for_update`, **re-check
guard 3** rồi mới tạo — cùng khuôn mẫu "nhả lock khi chờ mạng, khoá lại khi ghi" mà
`_dispatch_and_finalize` đang dùng:

```python
doc = StockDocumentService.create_document(
    doc_type=DocType.ISSUE,
    source_warehouse_id=scan.warehouse_id,
    lines=replacement["lines"],
    note=note,                      # xem §5.3
    user_id=actor.id if actor else scan.scanned_by_id,
    occurred_at=scan.created_at,    # ngày kho QUÉT, không phải ngày duyệt
)
StockDocumentService.submit_for_approval(document_id=doc.id, user_id=...)
scan.result_ref = f"stock-doc:{doc.id}"
```

`result_ref` ở nhánh dispatch hiện luôn rỗng (`_complete_dispatch` trả `""`), và quy ước
tiền tố đã có sẵn (`warranty-journey:{id}`) nên dùng lại được, không cần cột mới.

### 5.3 Ghi chú phiếu (truy vết ngược)

Luôn ghi, kể cả khi người dùng để trống ô ghi chú:

```
Hàng gửi bù đổi 2 chiều · đơn {invoice_code hoặc order_code} · VĐ {scan.code}[ · {ghi chú người dùng}]
```

Ô tìm của báo cáo khớp `doc.code` **hoặc `doc.note`** (§6 của spec phiếu kho) → gõ mã đơn
gốc hay mã VĐ đều ra được dòng phiếu bù.

### 5.4 Endpoint `flow_manual_match`

- Body nhận thêm `replacement` (optional). Quyền giữ nguyên: `stock` + `shipments`.
- Response thêm `stock_doc_code: str` (rỗng nếu không tạo) và
  `replacement_skipped: str` (rỗng nếu không bỏ qua).

### 5.5 Endpoint `flow_order_lookup`

Mỗi đơn trả thêm:

- `already_dispatched: bool` — dùng lại đúng luật 2-tín-hiệu của `_is_dispatched`
  ([shipping_lookup_view.py](../../../backend/apps/orders/transport/views/shipping_lookup_view.py)):
  tồn treo **hoặc** trạng thái giao hàng = `dispatched`. Tính gộp cho cả trang kết quả,
  không N+1.
- `items: [{product_id, sku, quantity}]` — gợi ý SKU × SL cho hộp xác nhận.

Cờ này **chỉ để FE biết có nên hỏi hay không**; quyết định thật vẫn ở §5.2 guard 2.

## 6. Thiết kế Frontend — `PendingMatchPanel`

Chọn một đơn **chưa xuất** → giữ nguyên hành vi hiện tại (khớp luôn, đóng panel).

Chọn một đơn **đã xuất** → panel chuyển sang bước xác nhận thay vì khớp ngay:

- Câu hỏi: *"Đơn {mã} đã xuất kho trước đó. Đây có phải **hàng gửi bù** (đổi lỗi 2 chiều) không?"*
- Dòng cảnh báo: *"Chỉ chọn 'Có' khi kho đã gửi **thêm** một kiện nữa. Nếu chỉ là quét trùng
  cùng một kiện, chọn 'Không'."*
- Bảng SKU × SL: mặc định lấy `items` của đơn, **sửa SL / xoá dòng được** (hàng gửi bù có
  thể "gửi ngoài, không vào điện" nên lệch với đơn).
- Ô ghi chú (tuỳ chọn) — BE tự ghép tiền tố truy vết ở §5.3.
- Hai nút: **[Không — chỉ khớp]** (mặc định, gọi `match` không kèm `replacement`) /
  **[Có — tạo phiếu xuất kho]**.

Sau khi khớp: toast `Đã tạo phiếu xuất kho {code} — chờ duyệt.` Nếu BE trả
`replacement_skipped` thì toast cảnh báo nêu đúng lý do, không im lặng.

`useFlowManualMatch` invalidate thêm `["inventory", "documents"]`.

## 7. Không đụng tới

- Guard `already_dispatched`, `issue_for_order`, invariant tồn treo theo đơn.
- **Cron tự khớp** — tuyệt đối không tự sinh phiếu; phải có người xác nhận đây là kiện thứ 2.
- Dialog Quét xuất kho (`ScanDispatchDialog`) — giữ nguyên toast cảnh báo hiện có.
- Tab Nhập hoàn, `ReturnEntryService`.
- Quy trình duyệt phiếu kho (`submit_for_approval` / `approve_document`) — dùng lại nguyên trạng.

## 8. Rủi ro

| Rủi ro | Chống |
|---|---|
| **Trừ tồn 2 lần** khi đơn thật ra chưa xuất | Guard 2 ở §5.2 — BE bỏ qua khi warnings không có `already_dispatched`. |
| Sinh phiếu trùng do double-click / retry | Guard 3 — `scan.result_ref` idempotent. |
| Nhân viên dùng nhầm cho ca **quét trùng cùng một kiện** | Mặc định là "Không", chữ cảnh báo nói rõ; phiếu ở trạng thái **chờ duyệt** nên còn một người nữa soi; note mang mã đơn + mã VĐ để đối soát ngược. |
| Phiếu chờ duyệt bị bỏ quên → báo cáo vẫn thiếu | Phiếu nằm trong hàng chờ duyệt sẵn có, không phải nháp mồ côi. Không thêm cơ chế nhắc trong phạm vi này. |
| Migration đụng bảng phiếu | Cột nullable, không backfill, không index mới. |

## 9. Kiểm thử

**Backend (pytest):**

- Khớp tay vào đơn **đã xuất** + `replacement` → đúng **1** phiếu `issue` trạng thái
  `pending_approval`, `scan.result_ref == "stock-doc:{id}"`, **tồn chưa đổi**; duyệt phiếu → tồn −1.
- Bút toán sau duyệt có `occurred_at == scan.created_at` (không phải lúc duyệt).
- Gọi lại lần 2 trên cùng scan → không sinh phiếu thứ 2, trả `replacement_skipped="already_created"`.
- Khớp tay vào đơn **chưa xuất** + `replacement` → ghi `SALE` bình thường, **không** có phiếu,
  trả `replacement_skipped="order_was_not_dispatched"`.
- `replacement=None` → hành vi cũ không đổi (regression).
- `lines` rỗng / `quantity <= 0` → 400, không tạo phiếu.
- Phiếu sau duyệt hiện trong `MergedIssueHistory` đúng **ngày quét**; huỷ phiếu → net về 0.
- Ô tìm theo mã đơn gốc và theo mã VĐ đều ra dòng phiếu (khớp `doc.note`).
- Phiếu tay hiện có (`occurred_at=None`) sau duyệt vẫn có `occurred_at ≈ now()` — không hồi quy.
- Quyền: user chỉ có section `stock` → 403.

**Frontend (vitest):**

- Đơn `already_dispatched=false` → bấm chọn là khớp ngay, không hiện hộp xác nhận.
- Đơn `already_dispatched=true` → hiện hộp xác nhận, chưa gọi `match`.
- Bấm "Không" → gọi `match` **không** kèm `replacement`.
- Sửa SL rồi bấm "Có" → payload `replacement.lines` đúng số đã sửa.
- BE trả `replacement_skipped` → hiện toast cảnh báo.

## 10. Sprint

- **Sprint 1 — Backend:** migration `StockDocument.occurred_at`, `create_document` +
  `_apply_line` truyền `occurred_at`, `manual_match(replacement=...)` + 4 guard,
  `flow_manual_match` / `flow_order_lookup` mở rộng, serializer + regen `schema.yaml` &
  `schema.d.ts` (`npm run gen:api` rồi prettier). pytest. DoD: test xanh, không hồi quy phiếu tay.
- **Sprint 2 — Frontend:** `PendingMatchPanel` hộp xác nhận + bảng SKU sửa được, toast,
  invalidate `documents`. vitest + typecheck + eslint. DoD: 5 ca vitest xanh.
- **Sprint 3 — Kiểm thử & MR:** ruff + eslint sạch, pull master resolve conflict,
  smoke tay trên dữ liệu thật với đúng ca `HD013124`, gửi MR.

## 11. Dữ liệu cũ

Backfill **tự động là không an toàn**: trong sổ, "hàng gửi bù thật" và "quét trùng cùng một
kiện" để lại **cùng một dấu vết** (mã `MATCHED` vào đơn đã xuất, không sinh bút toán). Tạo
phiếu hàng loạt sẽ trừ tồn oan cho mọi ca quét trùng.

Thay vào đó (bổ sung sau khi chốt với CTO — §12 Task 6): **đánh dấu để người xử lý tay**.
Các lần khớp bị guard chặn đều đã lưu chuỗi `already_dispatched` vào
`PendingShipmentScan.error`, nên tập nghi ngờ dò được chính xác:

```
kind=dispatch · status=matched · error chứa "already_dispatched" · result_ref=""
```

→ backend trả cờ `needs_replacement_issue`, dòng hiện nút **"Chưa có phiếu bù"**, bấm vào mở
đúng form xác nhận rồi gọi `POST /api/inventory/flow-pending/{id}/replacement-issue`. Không
ai bấm thì không có gì đụng tới tồn.

Ca `HD013124` / VĐ `84859707184483` trong ảnh sẽ nằm trong danh sách này sau khi lên.

## 12. Tiến độ thực hiện (2026-07-24)

- **Task 1 — XONG.** `StockDocument.occurred_at` (migration `0029`) + `create_document` /
  `_apply_line` truyền xuống bút toán. Phiếu tay (`occurred_at=None`) không hồi quy.
- **Task 2 — XONG.** `_complete_dispatch` trả warning thô, `_dispatch_and_finalize` mang ra
  ngoài, `manual_match(replacement=…)` + `_create_replacement_issue` với đủ guard.
- **Task 3 — XONG.** `flow_manual_match` validate `replacement` (400 sạch ở view),
  `flow_order_lookup` trả `already_dispatched` + `items`. Regen schema.
- **Task 4 — XONG.** `PendingMatchPanel` hộp xác nhận hàng gửi bù.
- **Task 5 — XONG.** Hồi quy `apps/inventory apps/orders`: **1033 passed**.
- **Task 6 — XONG.** Cờ `needs_replacement_issue` + `create_replacement_issue_for_scan` +
  endpoint `replacement-issue`, form dùng chung `ReplacementIssueForm`, nút "Chưa có phiếu bù".
- **Còn lại:** smoke tay trên dữ liệu thật.

### Lệch so với thiết kế ban đầu

- Guard `lines` rỗng / `quantity <= 0` chuyển từ service lên **view** (`_parse_replacement`)
  để trả 400 khi chưa đụng gì tới sổ; service giữ tầng phòng thủ, trả
  `replacement_skipped="invalid_lines"` thay vì ném lỗi sau khi mã đã khớp xong.
- Regen `schema.yaml` nhặt thêm `StockMovement.occurred_at` — **drift có sẵn** từ migration
  `0024`, không phải do thay đổi này.

### Đã biết, không thuộc phạm vi

- `src/app/__tests__/RequireAuth.test.tsx` fail 2 ca (react-router) — pre-existing, đã đối
  chứng bằng cách stash toàn bộ thay đổi của nhánh này.
- `npm run lint:strict` còn 6 warning ở `routes.tsx`, `flow-shared.tsx`, `warranty-claims/*`
  — pre-existing; các file mới/sửa của nhánh này sạch.
