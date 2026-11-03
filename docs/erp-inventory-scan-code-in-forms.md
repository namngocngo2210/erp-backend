# Spec — Inventory: "Quét mã" trong form tạo phiếu (kho + hoàn)

> Chuyển chức năng quét mã vận đơn **vào trong 2 form tạo phiếu** (Phiếu kho và Phiếu trả hàng),
> đặt tên **"Quét mã"**. Quét mã vận đơn → tra đơn → bung sản phẩm của đơn vào dòng phiếu.
> Gỡ nút "Quét hàng hoàn" rời. Thuần frontend — không đổi backend.
>
> - Ngày tạo: 2026-06-22
> - Branch: `feat/scan-code-in-create-forms` (tách từ master, sau khi merge-returns đã merge).
> - Nền: `docs/erp-inventory-scan-shipment.md`, `docs/erp-inventory-merge-returns.md`.
> - Tái dùng: `features/inventory/scan/ScanShipmentInput.tsx`, `scan-queries.ts` (`lookupByShippingCode`, `isMultiple`), `scan-sound.ts` (`beepSuccess`/`beepError`). Endpoint `GET /api/orders/lookup-by-shipping-code` đã có.

---

## 1. Quyết định

| # | Quyết định |
|---|---|
| C1 | "Quét mã" = quét **shipping_code** → `lookupByShippingCode` → bung `OrderItem` của đơn vào dòng phiếu. Nhúng trong cả 2 form tạo phiếu. Không đổi backend. |
| C2 | **Form Phiếu kho** (`CreateDocumentDialog`, mọi doc_type): quét → **gộp sản phẩm vào `lines` theo product_id** (cộng dồn số lượng); quét nhiều đơn → tích luỹ. Chống quét trùng đơn (nhớ order_id đã quét) → cảnh báo + beep đỏ, không cộng đôi. |
| C3 | **Form Phiếu hoàn** (`CreateReturnDialog`, ReturnNote = 1 đơn/phiếu): quét → **tự điền** `sourceOrderId`, `customerName`, `customerPhone` + đặt `lines` = sản phẩm của đơn (thay thế). Quét đơn khác → thay thế, toast báo. |
| C4 | Phản hồi quét: `beepSuccess` khi thêm/điền OK; `beepError` + toast khi không thấy mã / trùng (phiếu kho). Nhiều kết quả (`isMultiple`) → lấy đơn đầu, toast liệt kê (như chuẩn hiện có). |
| C5 | **Gỡ nút "Quét hàng hoàn"** (ScanReturnDialog) khỏi trang Phiếu kho. **Giữ "Quét xuất kho"** trên trang Vận đơn (luồng dispatch, ngoài phạm vi). |
| C6 | Dọn dẹp (tuỳ chọn, không bắt buộc trong sprint này): `ScanReturnDialog`, hook `useCreateReturnsBatch`, endpoint `returns/batch` trở thành không dùng → có thể xoá ở bước dọn riêng. |

---

## 2. Hiện trạng (tham chiếu)

- `CreateDocumentDialog` (trong `frontend/src/features/inventory/pages/StockDocumentsPage.tsx`): state `lines: LineForm[]` (`{product_id, sku, name, quantity}`), `addProductLine(p)`, `setLines`, search sản phẩm theo SKU/tên. Lưu: POST `/api/inventory/documents` với `lines`.
- `CreateReturnDialog` (`frontend/src/features/inventory/pages/CreateReturnDialog.tsx`): state `returnType`, `warehouseId`, `sourceOrderId`, `customerName`, `customerPhone`, `lines: LineForm[]`, `setLines`, `addProductLine`. Lưu: POST `/api/inventory/returns` (payload nhận `source_order_id`, `customer_name`, `customer_phone`, `lines`).
- `lookupByShippingCode(code)` trả `LookupOrder { order_id, order_code, customer_name, customer_phone, items:[{product_id, sku, name, quantity}], ... }` hoặc `{multiple, orders}`.
- Trang Phiếu kho (`StockDocumentsPage.tsx`) hiện có nút "Quét hàng hoàn" mở `ScanReturnDialog` (sẽ gỡ).

---

## 3. Sprint S1 — Helper gộp dòng (dùng chung) + test

File: `frontend/src/features/inventory/scan/merge-order-lines.ts` (+ test `__tests__/merge-order-lines.test.ts`).

- `type LineForm = { product_id: string; sku: string; name: string; quantity: string }` (khớp dạng đang dùng trong 2 dialog — import lại hoặc khai báo cục bộ).
- `mergeOrderItemsIntoLines(existing: LineForm[], items: LookupItem[]): LineForm[]` — với mỗi item: nếu `product_id` đã có trong `existing` → cộng `quantity`; nếu chưa → thêm dòng mới `{product_id, sku, name, quantity}`. Trả mảng mới (immutable).
- `orderItemsAsLines(items: LookupItem[]): LineForm[]` — chuyển items thành LineForm[] (cho phiếu hoàn: thay thế).
- Test: gộp cộng dồn đúng; thêm mới đúng; thay thế đúng; không mutate input.

---

## 4. Sprint S2 — "Quét mã" trong CreateDocumentDialog (phiếu kho)

File: `StockDocumentsPage.tsx` (phần `CreateDocumentDialog`).

- Thêm khu **"Quét mã"**: `ScanShipmentInput` (auto-focus, Enter → `handleScanCode`).
- State `scannedOrderIds: Set<number>` (hoặc number[]) để chống trùng.
- `handleScanCode(code)`:
  - `lookupByShippingCode(code)`; lỗi → `beepError` + toast "Không tìm thấy đơn cho mã …".
  - `isMultiple` → lấy `orders[0]`, toast liệt kê mã đơn.
  - Nếu `order_id` đã trong `scannedOrderIds` → `beepError` + toast "Đơn … đã quét"; return.
  - `setLines(mergeOrderItemsIntoLines(lines, order.items))`; thêm order_id vào set; `beepSuccess`.
- `resetForm` xoá `scannedOrderIds`. Hoạt động với mọi doc_type. Không đổi luồng lưu.

---

## 5. Sprint S3 — "Quét mã" trong CreateReturnDialog (phiếu hoàn)

File: `CreateReturnDialog.tsx`.

- Thêm khu **"Quét mã"**: `ScanShipmentInput` (Enter → `handleScanCode`).
- `handleScanCode(code)`:
  - `lookupByShippingCode(code)`; lỗi → `beepError` + toast.
  - `isMultiple` → `orders[0]` + toast.
  - Đặt `setSourceOrderId(String(order.order_id))`, `setCustomerName(order.customer_name)`, `setCustomerPhone(order.customer_phone)`, `setLines(orderItemsAsLines(order.items))`; `beepSuccess` + toast "Đã nạp đơn …".
  - Nếu đã có đơn trước đó → thay thế (toast "Thay bằng đơn …").
- Không đổi payload/luồng lưu (đã nhận source_order_id/customer/lines).

---

## 6. Sprint S4 — Gỡ nút "Quét hàng hoàn"

File: `StockDocumentsPage.tsx`.

- Gỡ nút "Quét hàng hoàn" + mount `ScanReturnDialog` + state liên quan (`scanReturnOpen`). Giữ nút "Tạo ▼" và Import Excel.
- Kiểm tra import không còn dùng (`ScanReturnDialog`) → gỡ để eslint sạch.
- (Không đụng trang Vận đơn / "Quét xuất kho".)

---

## 7. Kiểm thử & xác minh
- Unit test helper `merge-order-lines` (vitest).
- `npx tsc --noEmit` = 0; `npx eslint <files> --max-warnings=9999` không lỗi mới; `npx vitest run src/features/inventory` pass.

## 8. Tiến độ

| Sprint | Trạng thái | Ghi chú |
|---|---|---|
| S1 — helper + test | ⬜ | merge-order-lines |
| S2 — quét trong phiếu kho | ⬜ | accumulate + dedup |
| S3 — quét trong phiếu hoàn | ⬜ | fill source_order/customer/lines |
| S4 — gỡ nút Quét hàng hoàn | ⬜ | dọn UI |

> Cập nhật sau mỗi sprint.
