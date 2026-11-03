# Gộp Phiếu Xuất kho vào báo cáo "Xuất" (Lịch sử nhập xuất)

- Ngày: 2026-07-16
- Branch: `feat/issue-report-include-stock-issue-docs`
- Loại: thay đổi **báo cáo (đọc)** — KHÔNG đụng cách ghi sổ, tồn kho, hay guard xuất kho.

## 1. Bối cảnh & vấn đề

Trang **Lịch sử nhập xuất → tab Xuất** hiện chỉ đếm hàng **xuất theo đơn bán**:
`StockMovement` có `movement_type ∈ {sale, sale_reversal}` và `reference_type = "order"`
(xem `DispatchHistoryService.base_queryset`).

Có nghiệp vụ **"đổi hàng lỗi 2 chiều"**: một đơn đã xuất kho (đã trừ tồn) nay gửi **lại**
một sản phẩm thay thế bằng mã vận đơn mới. Vì đơn đã có tồn treo (`outstanding > 0`),
guard `already_dispatched` chặn trừ tồn lần 2 trên cùng đơn (đúng — tránh trừ 2 lần).
Cái thay thế phải được trừ tồn bằng **Phiếu Xuất kho** (`StockDocument` doc_type=`issue`).

Hệ quả: phiếu xuất kho đó **giảm tồn đúng** nhưng **không hiện** trong báo cáo "Xuất"
(báo cáo scope theo đơn), nên tổng SL xuất bị thiếu — ví dụ thực tế xuất 8 cái DV20
nhưng báo cáo chỉ hiện 7.

Ca thực tế đã đối chiếu: scan #362 (mã VĐ `84859680911619`) khớp tay vào đơn 530459
(HĐ `HDTTS_584892891057194198`, mã VĐ gốc `VNGH80536847410`) đã xuất kho từ 07/07
(`sale`, DV20, −1) → `already_dispatched` → không trừ lại → không vào thống kê 15/07.

## 2. Mục tiêu

Cho báo cáo "Xuất" (cả 2 view **Theo đơn** và **Theo SKU**) tính **thêm** các
**Phiếu Xuất kho** (`issue`), để một màn hình ra đúng tổng lượng hàng rời kho, đồng
thời **tách bạch** rõ "xuất theo đơn" vs "xuất phiếu kho" (không làm hỏng số liệu bán
hàng của người khác).

Phi mục tiêu (YAGNI): KHÔNG tính `transfer_out`, `adjustment` (điều chỉnh âm),
`receipt`. Chỉ đúng phiếu Xuất kho. KHÔNG đổi luồng ghi sổ / guard / tồn.

## 3. Nguyên tắc dữ liệu

- Phiếu Xuất kho ghi sổ tạo `StockMovement`: `movement_type = "issue"`,
  `reference_type = "document"`, `reference_id = doc.id`, `qty_delta = -SL` (âm).
- **Huỷ phiếu** (`cancel_document`) tạo bút toán đảo **cùng** `movement_type = "issue"`,
  `qty_delta = +SL`. Vì vậy:
  - **SL đã xuất qua phiếu = `-SUM(qty_delta)`** trên `movement_type='issue'` →
    **tự động về 0 khi phiếu bị huỷ**, không cần lọc trạng thái phiếu.
- `movement_type = "issue"` **chỉ** phát sinh từ `StockDocument` doc_type=ISSUE
  (transfer dùng `transfer_out/in`, điều chỉnh dùng `adjustment`) → lọc theo
  `movement_type='issue'` là đủ định danh "phiếu xuất kho", không lẫn loại khác.
- Ngày lọc: dùng `StockMovement.created_at` (= lúc ghi sổ) — đồng nhất với dòng đơn.

## 4. Thiết kế Backend

### 4.1 Cấu trúc file

`dispatch_history_service.py` đang ~246 dòng; thêm nguồn phiếu kho sẽ vượt chuẩn
~300 dòng. Tách phần phiếu kho ra module riêng:

- **Mới:** `apps/inventory/application/services/issue_document_history.py`
  → `IssueDocumentHistoryService` với các hàm thuần đọc:
  - `base_queryset(**filters)` — `StockMovement` lọc `movement_type='issue'`,
    `reference_type='document'` + date/warehouse/product; ô tìm `order` khớp
    **mã phiếu `StockDocument.code`** hoặc **`note`** (icontains) thay cho mã đơn.
  - `rows(**filters)` — gộp theo `reference_id` (document): trả list dòng phiếu
    (doc_id, code, warehouse_name, performed_by, note, posted_at, lines[], sku_count,
    quantity). `quantity = -SUM(qty_delta)`; `performed_by = created_by` của movement
    (người ghi sổ phiếu, đồng nhất cách `orders_page` lấy người thực hiện);
    `note`, `posted_at`, `warehouse_name` lấy từ `StockDocument` tương ứng.
  - `skus(**filters)` — gộp theo `product_id`: `{product_id, sku, name, doc_quantity, doc_count}`.
  - `sku_documents(product_id, **filters)` — drill-down phiếu chứa 1 SKU.
- `DispatchHistoryService` giữ nguyên trách nhiệm "xuất theo đơn"; **tầng orchestration**
  (view) gọi cả 2 service rồi trộn. Nếu logic trộn đủ nặng, đặt tại
  `flow_history_views.py` (transport) — không nhét chéo vào service đơn.

### 4.2 View "Theo đơn" — `IssueOrdersHistory`

Trộn 2 nguồn thành 1 danh sách phân trang chung, **sắp theo thời gian giảm dần**:

- Dòng đơn (`kind="order"`): như hiện tại.
- Dòng phiếu (`kind="document"`): `order_code=null`, `invoice_code=null`,
  `shipping_code=null`, thêm `doc_code` (vd `PXK-000123`), `warehouse_name`,
  `performed_by`, `note`, `last_at=posted_at`, `lines`, `sku_count`, `net=quantity`.

Phân trang trộn: `total = order_total + doc_total`; lấy đủ đầu 2 nguồn theo
`created_at desc`, merge-sort, cắt trang. Footer trả breakdown:
`sale_quantity`, `doc_quantity`, `total_quantity = sale + doc`.

### 4.3 View "Theo SKU" — `IssueSkusHistory`

Gộp theo SKU từ cả 2 nguồn:

- Mỗi SKU: `dispatched`/`reversed`/`net` (theo đơn, như cũ) + `doc_quantity`
  (phiếu kho) + `doc_count`. `SL xuất` hiển thị = `net + doc_quantity`.
- Footer breakdown: `sale_quantity`, `doc_quantity`, `total_quantity`.
- Drill-down 1 SKU: nối danh sách đơn (`sku_orders`, như cũ) + danh sách phiếu
  (`sku_documents`), mỗi dòng có `kind` để FE gắn nhãn.

### 4.4 Serializer / OpenAPI

- `IssueOrdersHistoryItem`: thêm `kind: "order" | "document"`, `doc_code: str | null`.
  Các field mã đơn cho phép null với dòng document.
- `IssueOrdersHistory` / `IssueSkusHistory` (envelope): thêm `sale_quantity`,
  `doc_quantity` (giữ `total_quantity` = tổng cộng).
- `IssueSkusHistoryItem`: thêm `doc_quantity`, `doc_count`.
- Drill-down item: thêm `kind` (+ `doc_code` cho dòng phiếu).
- Regen `frontend/src/api/schema.d.ts` bằng `npm run gen:api` **rồi chạy prettier**
  (tránh diff giả 4-space — xem lưu ý gen:api).

## 5. Thiết kế Frontend (`IssueTab.tsx`)

- **Theo đơn:** render `data.items` theo `kind`:
  - `order`: như hiện tại.
  - `document`: cột Mã hóa đơn hiện badge xám **"Phiếu xuất kho {doc_code}"** +
    `SkuLines`; Mã vận đơn = "—"; Kho/Người/Ghi chú/Số SKU/SL xuất từ dòng phiếu.
  - Footer đổi thành breakdown: **"Tổng SL xuất (N): theo đơn A + phiếu kho B = C"**.
- **Theo SKU:** cột SL xuất = `net + doc_quantity`; chú thích nhỏ *"(gồm N phiếu kho)"*
  khi `doc_count > 0`. Footer breakdown như trên. Drill-down phân biệt dòng phiếu
  (nhãn `PXK-…`) với dòng đơn.
- **Export Excel:** bổ sung dòng phiếu kho + dòng tổng breakdown, đồng bộ với bảng.

## 6. Bộ lọc (nhất quán)

| Bộ lọc | Dòng đơn | Dòng phiếu kho |
|---|---|---|
| Ngày | `movement.created_at` | `movement.created_at` |
| Kho | `warehouse_id` | `warehouse_id` (source_warehouse) |
| Sản phẩm (SKU/tên) | `product__sku/name icontains` | như trái |
| Ô tìm mã đơn/vận đơn | order/invoice/shipping icontains | **doc.code / doc.note icontains** (không có mã đơn) |

Gõ một mã đơn không liên quan → dòng phiếu tự ẩn (không match code/note).

## 7. Kiểm thử

**Backend (pytest):**
- `issue` document post → xuất hiện trong `rows`/`skus` với đúng SL; **huỷ phiếu → net 0**.
- Lọc theo ngày/kho/sản phẩm áp đúng cho phiếu.
- Ô tìm khớp `doc.code`/`note`, không khớp mã đơn không liên quan.
- Phân trang trộn 2 nguồn: đúng thứ tự thời gian, đúng `total`, breakdown footer đúng.
- `transfer_out`/`adjustment`/`receipt` **không** bị tính.
- View "theo đơn" hiện tại (chỉ đơn) không đổi khi không có phiếu nào.

**Frontend (vitest):**
- Render dòng `kind="document"` (badge phiếu, mã vận "—").
- Footer breakdown A + B = C.
- "Theo SKU": SL xuất cộng doc_quantity + chú thích "(gồm N phiếu kho)".

## 8. Không đụng tới

- Luồng xuất theo đơn, guard `already_dispatched`, `issue_for_order`, tồn kho.
- Cách ghi/huỷ Phiếu Xuất kho.
- Tab "Nhập hoàn về" (chỉ đổi tab Xuất).

## 9. Sprint

- **Sprint 1 — Backend + schema:** `IssueDocumentHistoryService`, trộn ở view (Theo đơn/
  Theo SKU + drill-down), serializer + regen schema, pytest. DoD: test xanh, tổng breakdown đúng.
- **Sprint 2 — Frontend + export:** render `kind`, footer breakdown, Theo SKU chú thích,
  export Excel, vitest. DoD: UI ra đúng "7 + 1 = 8", export khớp bảng.
- **Sprint 3 — Kiểm thử & MR:** rà lint (ruff, eslint), pull master resolve conflict,
  smoke tay trên dữ liệu thật, gửi MR.

## 9b. Tiến độ thực hiện (2026-07-16)

- **Sprint 1 (Backend + schema) — XONG.**
  - `DispatchHistoryService`: tách `order_groups` / `enrich_orders` / `sku_groups` /
    `_sku_item` / `total_issued`, giữ nguyên output `orders_page`/`skus_page`/`sku_orders`.
  - Mới `issue_document_history.py`: `IssueDocumentHistoryService` (base_queryset,
    doc_groups, enrich_docs, skus_map, sku_documents) + `MergedIssueHistory`
    (orders_page/skus_page/sku_drill có breakdown, phân trang trộn 2 nguồn).
  - Views `issue_orders`/`issue_skus`/`issue_sku_orders`/`issue_export` chuyển sang
    `MergedIssueHistory`; serializer thêm `kind`/`doc_code`/`sale_quantity`/`doc_quantity`/
    `doc_count`. Regen `schema.yaml` + `schema.d.ts` (prettier 2-space).
  - Excel export: dòng phiếu (nhãn "Phiếu xuất kho …") + breakdown 3 dòng tổng.
  - pytest: `test_issue_document_history.py` (11 ca) + cập nhật export/view test.
    **69 passed** (inventory flow suite).
- **Sprint 2 (Frontend) — XONG.**
  - `IssueTab.tsx`: Theo đơn render dòng `kind="document"` (badge "Phiếu xuất kho"),
    Theo SKU cộng `doc_quantity` + chú thích "gồm N phiếu kho", footer breakdown cả 2 view,
    drill-down phân biệt dòng phiếu. typecheck + eslint sạch; vitest **12 passed**.
- **Sprint 3 (Kiểm thử & MR) — ĐANG:** còn smoke tay trên dữ liệu thật + tạo MR.

## 10. Rủi ro / lưu ý

- **Phân trang trộn 2 nguồn** là chỗ dễ sai nhất (off-by-one, tổng lệch) → test kỹ.
- Hiệu năng: cả 2 nguồn đều group + index sẵn theo `reference_type`/`created_at`;
  tránh N+1 khi lấy `lines`/tên kho/người thực hiện (1 query gộp như `orders_page`).
- Giữ **breakdown** để báo cáo doanh số theo-đơn vẫn đọc được, không bị phiếu kho làm nhiễu.
