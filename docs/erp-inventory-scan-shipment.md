# Spec — Inventory: Quét mã vận đơn → xuất giao / nhập hoàn

> Thêm chế độ **Quét vận đơn**. Nhân viên kho dùng máy quét bắn mã vận đơn trên kiện hàng →
> hệ thống map sang đơn hàng có `shipping_code` đó → **xuất giao**: đẩy đơn sang "Đã xuất kho"
> (tự trừ tồn); **nhập hoàn**: tạo phiếu trả hàng (cộng tồn).
>
> - Ngày tạo: 2026-06-19
> - Nền: `docs/erp-inventory.md` (Phase 1), `docs/erp-inventory-returns.md` (state + ReturnNote), `docs/architecture-standard.md`.
> - Module liên quan: `inventory` (ReturnNote + StockSalesService), `orders` (Order + OrderItem + shipping_code + OrderAssessment), `warehouses`.
> - Thiết bị: máy quét Corisu **Bluetooth chế độ HID (keyboard wedge)** → "gõ mã + Enter" vào ô input
>   đang focus. FE xử lý giống nhau cho cả USB/Bluetooth, desktop/mobile. **Không cần PWA / Web Bluetooth API.**

> ⚠️ **Cập nhật 2026-07-11.** Mọi chỗ doc này viết "mỗi đơn quét → 1 `ReturnNote`" nay đọc là **1
> `ReturnEntry`**: entry rơi vào **phiếu ngày** đang mở của kho (`PTH-YYYYMMDD-K{wh}`, 1 phiếu / ngày / kho).
> Ngữ nghĩa kho không đổi (cộng tồn theo `return_type` từng dòng, guard chống cộng đôi, RETURNED không
> auto-release). → **`docs/erp-return-daily-note.md`**.

---

## 1. Quyết định kiến trúc

| # | Quyết định |
|---|---|
| Q1 | **Xuất giao KHÔNG tạo `StockDocument` mới.** Tái dùng cơ chế sẵn có: set đơn sang **`delivery_business_status_code = "dispatched"`** ("Đã xuất kho") → hệ thống tự gọi `StockSalesService.issue_for_order()` trừ tồn. |
| Q2 | **Nhập hoàn dùng `ReturnNote`** (model chuyên dụng, giữ `source_order`). Mỗi đơn quét → 1 ReturnNote; quét hàng loạt = batch nhiều ReturnNote. |
| Q3 | Quét map theo **`Order.shipping_code`** (exact match). Dùng **chung 1 endpoint lookup** + **chung 1 component quét** ở FE. |
| Q4 | Hàng hoàn: mỗi đơn chọn **loại trả** = `return_type`, **mặc định `defect`** (lỗi/chờ kiểm). `return_type` đã tự map sang state. |
| Q5 | **Kiểm soát chặt**: chặn quét trùng trong phiên; cảnh báo (không chặn cứng) khi đơn sai trạng thái kỳ vọng; mã không thấy → beep đỏ. |
| Q6 | **Không cộng tồn 2 lần**: **bỏ auto-release khi `RETURNED(5)`** — ReturnNote (quét vật lý) là nguồn sự thật duy nhất cho nhập lại tồn hàng hoàn (§3.4). Giữ idempotent của `issue_for_order` cho xuất giao. |
| Q7 | **Ngoài phạm vi**: đồng bộ ngược tồn sang KiotViet; quét bằng camera điện thoại. |

---

## 2. Cơ chế tồn kho sẵn có (nền tảng — bám sát, không phát minh lại)

### 2.1 Xuất kho khi "dispatched"
- `OrderAssessment.delivery_business_status_code = "dispatched"` ⇒ label **"Đã xuất kho"** (`crm_catalog.py`).
- Trigger: `order_service_commands.py:409-434` — chỉ khi field **đổi** sang `"dispatched"`:
  - Bắt buộc `order.fulfillment_warehouse_id` (thiếu → `ValidationError`, rollback).
  - Gọi `StockSalesService.issue_for_order(order_id, warehouse_id=fulfillment_warehouse, lines=OrderItems)`.
- `issue_for_order` (`stock_sales_service.py:30-55`): **idempotent** (guard theo `StockMovement(reference_type="order", reference_id=order_id, movement_type=SALE)` đã tồn tại → return sớm); trừ `state=SELLABLE`; ghi `StockMovement` có `reference=order:<id>` (truy ngược được).
- API có sẵn: `PATCH /api/orders/<id>/` payload `{"crm": {"delivery_business_status_code": "dispatched"}}` (cần `fulfillment_warehouse_id`). Phát event `OrderEventContract`.

### 2.2 Auto hoàn tồn khi RETURNED/CANCELLED (đã GỠ HẲN — 2026-07-29)
- Lịch sử: `inventory/subscribers.py` từng auto-release khi `new_status ∈ {5 RETURNED, 6 CANCELLED}`. RETURNED bị bỏ trước (Q6, §3.4) vì cộng đôi với ReturnNote.
- **Từ 2026-07-29 `CANCELLED(6)` cũng KHÔNG auto-release nữa.** Huỷ đơn đã xuất kho không ghi bút toán nào: hàng đang trên đường về, tồn chỉ được cộng khi kho quét nhập hoàn. Đơn nằm trong hàng đợi `cancelled_dispatch_watches` (tab "Chờ hàng hoàn về") cho tới khi hàng về hoặc có người đóng sổ.
- Đây chính là "residual edge" mà bản doc cũ ghi nhận và hoãn lại: đơn CANCELLED sau khi đã xuất vừa auto +sellable vừa bị chặn quét nhập hoàn. Nay đã xử lý dứt điểm.
- Chi tiết: `docs/superpowers/specs/2026-07-29-cancelled-after-dispatch-return-design.md`, vận hành: `docs/runbooks/2026-07-29-cancelled-after-dispatch-return.md`.

### 2.3 ReturnNote (nhập hoàn)
- `ReturnNote`: `code`, `return_type`, `source_order` (FK), `customer_*`, `warehouse`, `status (draft→posted→cancelled)`. `ReturnNoteLine`: product, quantity, condition_note.
- Map `return_type → state`: `delivery_returned→sellable`, `external_return→sellable`, `warranty→warranty`, `defect→defect`. UI: **bán được=`delivery_returned`**, **bảo hành=`warranty`**, **lỗi/chờ kiểm (mặc định)=`defect`**.

---

## 3. Sprint S1 — Backend

### 3.1 Endpoint lookup (dùng chung) — `GET /api/orders/lookup-by-shipping-code`
File: view mới trong `backend/apps/orders/transport/views/...`, route `apps/orders/urls.py`.
- Query `?code=<mã>` — **exact match** trên `shipping_code` (loại chuỗi rỗng).
- Trả 1 order:
  ```json
  {
    "order_id": 123, "order_code": "DH...", "shipping_code": "...",
    "status": 11, "status_label": "Chờ giao",
    "delivery_business_status_code": "warehouse_processing",
    "fulfillment_warehouse_id": null,
    "customer_name": "...", "customer_phone": "...",
    "items": [{"product_id": 1, "sku": "...", "name": "...", "quantity": 2}]
  }
  ```
- `0` → `404 {"detail":"not_found"}`. `>1` (shipping_code không unique) → `200 {"multiple": true, "orders": [...]}` để FE cho chọn.
- Cờ `already_dispatched` (FE dùng để chặn quét trùng) tính theo **HAI** tín hiệu, đúng một
  cái là đủ (`_is_dispatched`):
  1. Sổ tồn còn NET đã trừ chưa hoàn (`SALE - SALE_REVERSAL > 0`);
  2. `assessment.delivery_business_status_code == "dispatched"`.

  Chỉ tín hiệu (1) là **thiếu**: đơn không khớp được sản phẩm nào (`create_order_manual`
  chỉ tạo `OrderItem` khi có `matched_items`) xuất kho với `lines=[]` → không sinh bút
  toán nào → cờ báo "chưa xuất" vĩnh viễn. Cùng lỗ hổng với đơn xuất lại bị bỏ rơi sổ
  (`docs/reissue-ledger-leak-2026-07-21.md`). Chỉ luồng xuất kho mới ghi mã `"dispatched"`
  nên (2) không chặn oan; un-dispatch đưa mã về `""`/`"warehouse_processing"` → lại quét được.

### 3.2 Xuất giao — endpoint batch dispatch: `POST /api/orders/dispatch-batch`
File: view mới `apps/orders/transport/views/...`, service mở rộng `OrderServiceCommands`.
- Payload:
  ```json
  { "fulfillment_warehouse_id": 1, "order_ids": [123, 456, 789] }
  ```
- Với mỗi order (atomic per-order, không để 1 đơn lỗi chặn cả lô): gán `fulfillment_warehouse` + set `delivery_business_status_code="dispatched"` qua đúng đường `update_order_inline` → tự trừ tồn (idempotent) + phát event.
- **Cảnh báo (không chặn)**: đơn ngoài `{AWAITING_SHIPMENT(11), IN_DELIVERY(2)}` → `warnings:[{order_id, code:"unexpected_status"}]`. Đơn đã "dispatched" → `code:"already_dispatched"` (vẫn an toàn vì idempotent).
  Cảnh báo `already_dispatched` bám cùng hai tín hiệu như §3.1. Nhưng việc **bỏ round-trip
  xác minh KiotViet** thì CHỈ dựa vào sổ tồn: nhánh đó an toàn vì tồn đã trừ thật
  (`issue_for_order` no-op), còn đơn "dispatched mà sổ trống" vẫn phải fail-closed như thường.
  FE toast riêng danh sách đơn dính cảnh báo này — badge nhỏ trên dòng dễ trôi khi xác nhận cả lô.
- Trả kết quả từng đơn: `{order_id, ok, warnings, stock_warnings}` (stock_warnings = tồn âm từ `issue_for_order`).
- **Không tạo bảng/model mới.** Truy vết nằm ở `StockMovement(reference=order:<id>)` sẵn có.

### 3.3 Nhập hoàn — batch tạo `ReturnNote`: `POST /api/inventory/return-notes/batch`
File: `return_note_service.py` (mở rộng) + serializer/view `inventory/transport`.
- Payload:
  ```json
  {
    "warehouse_id": 2,
    "items": [
      {"order_id": 123, "shipping_code": "...", "return_type": "defect"},
      {"order_id": 456, "shipping_code": "...", "return_type": "delivery_returned"}
    ]
  }
  ```
- Service (atomic per-order): mỗi phần tử → **1 `ReturnNote`** với `source_order`, `customer_*` từ order, `return_type` theo chọn, `ReturnNoteLine` bung từ `OrderItem`; post theo luồng có sẵn (cộng tồn theo state).
- **Cảnh báo (không chặn)**: order ngoài `{IN_RETURN(4), RETURNED(5), AWAITING_RETURN_CONFIRMATION(13)}` → `unexpected_status`.
- **Dedup**: đã có `ReturnNote(source_order, status=posted)` → cảnh báo `already_returned` (người dùng quyết).

### 3.4 Bỏ auto-release khi RETURNED (đã chốt — xác minh xong)
Đã xác minh: `release_for_order` (`stock_sales_service.py`) idempotent, +`sellable`; subscriber `inventory/subscribers.py` trigger trên `{5,6}`; `ReturnNote.post_return` (`return_note_service.py`) ghi `RETURN_IN` +state, **không** guard với auto-release → cộng đôi. `Order.status→RETURNED` do KiotViet sync set (`kiotviet/.../service_actions.py`).
- **Thay đổi**: trong `subscribers.py`, đổi `_RELEASE_STATUSES` từ `{5, 6}` → `{6}` (bỏ RETURNED). ReturnNote (quét) thành nguồn sự thật duy nhất cho nhập lại tồn hàng hoàn.
- **Cập nhật test** `test_release_on_status_change.py`: RETURNED không còn auto-release; CANCELLED giữ nguyên.
- Nhờ vậy luồng nhập hoàn (§3.3) **không cần** logic reclass/bù trừ — ReturnNote cứ +state bình thường, không sợ cộng đôi.

### 3.5 Unit test (`backend/apps/inventory/tests/` + `apps/orders/tests/`, chạy bằng pytest — memory running-backend-tests)
- Lookup: thấy / không thấy / nhiều kết quả.
- Dispatch-batch: set dispatched + trừ `sellable`; gọi lại không trừ lần 2 (idempotent); cảnh báo `unexpected_status`/`already_dispatched`; thiếu kho → lỗi rõ ràng.
- Return-batch: mỗi đơn → 1 ReturnNote đúng source_order/customer/lines; `return_type→state` đúng; dedup `already_returned`.
- Auto-release: RETURNED **không** còn auto +sellable; CANCELLED vẫn release. Kịch bản RETURNED rồi quét ReturnNote → tồn chỉ +1 lần (theo §3.4).

---

## 4. Sprint S2 — Frontend

Component quét dùng chung: **`ScanShipmentInput`** (ô auto-focus capture keyboard-wedge, nghe `Enter`, chống double-fire, beep xanh/đỏ + dòng cảnh báo) + hook `useLookupByShippingCode`.

### 4.1 Xuất giao — trang Vận đơn / Stock Documents
- Nút **"Quét xuất kho"** → dialog: chọn **kho xuất** (bắt buộc) + `ScanShipmentInput`.
- Mỗi lần quét: lookup → thêm đơn vào danh sách phiên (mã vận đơn, mã đơn, khách, trạng thái + badge cảnh báo); trùng → bỏ qua + beep đỏ; không thấy → beep đỏ.
- Bấm **"Xác nhận xuất kho"** → `POST /api/orders/dispatch-batch` → hiện **bảng kết quả** từng đơn (OK / cảnh báo / tồn âm). Đây chính là "tạo phiếu" theo cách bạn mô tả; trạng thái mọi đơn → "Đã xuất kho".

### 4.2 Nhập hoàn — trang Returns (`/inventory/returns`)
- Nút **"Quét hàng hoàn"** → dialog batch: chọn **kho nhận** + `ScanShipmentInput`.
- Mỗi đơn quét: 1 dòng với dropdown **loại trả** (`return_type`, mặc định `defect`); sản phẩm/số lượng hiện từ đơn; nút xoá. Số lượng chỉnh bằng nút **+/-** (né bàn phím ảo bị ẩn khi dùng máy quét Bluetooth).
- "Lưu" → `POST /api/inventory/return-notes/batch` → bảng kết quả phiếu tạo + cảnh báo.

---

## 5. Sprint S3 — Hoàn thiện & vận hành

- Audit: xuất giao truy vết qua `StockMovement(reference=order)`; nhập hoàn qua `ReturnNote.source_order`. Bổ sung view/đối soát "đơn đã xuất theo phiên/ngày" nếu cần.
- Hiển thị cảnh báo dedup rõ ràng (already_dispatched / already_returned / cross-check release).
- Tài liệu hướng dẫn cấu hình **Corisu HID mode** (quét mã cấu hình "Keyboard/HID" + pair Bluetooth) cho nhân viên kho.
- (Tuỳ chọn) RBAC: gate quyền dùng chế độ quét theo section Kho.

---

## 6. Tiến độ

| Sprint | Trạng thái | Ghi chú |
|---|---|---|
| S1 — Backend | ⬜ Chưa làm | lookup + dispatch-batch + return-batch + hoà hợp release + test |
| S2 — Frontend | ⬜ Chưa làm | ScanShipmentInput dùng chung; xuất giao (dispatch) + nhập hoàn (Returns) |
| S3 — Hoàn thiện | ⬜ Chưa làm | audit + đối soát + hướng dẫn Corisu |

> Cập nhật cột Trạng thái + ghi chú "đã làm / chưa làm" sau mỗi sprint.
