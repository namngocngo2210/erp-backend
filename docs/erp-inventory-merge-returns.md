# Spec — Inventory: Gộp Trả hàng vào trang Phiếu kho (sổ phiếu hợp nhất)

> Hợp nhất danh sách **Phiếu kho** (StockDocument: nhập/xuất/điều chỉnh/chuyển) và **Phiếu trả hàng**
> (ReturnNote) thành MỘT bảng trên trang Phiếu kho, với cột "Loại phiếu" + bộ lọc. Trang Trả hàng đứng
> riêng bị gỡ khỏi menu và redirect về trang gộp.
>
> - Ngày tạo: 2026-06-20
> - Branch: `feat/merge-returns-into-stock-documents` (tách từ master sau khi feature quét vận đơn đã merge).
> - Nền: `docs/erp-inventory.md`, `docs/erp-inventory-returns.md`, `docs/erp-inventory-scan-shipment.md`, `docs/architecture-standard.md`.
> - Module: `inventory` (StockDocument + ReturnNote), FE `features/inventory`.

> ⚠️ **Cập nhật 2026-07-11.** Trang gộp vẫn đúng, nhưng dòng `kind=return` nay là **phiếu NGÀY**
> (`PTH-YYYYMMDD-K{wh}`, 1 phiếu / ngày / kho, gồm nhiều `ReturnEntry` = nhiều đơn) chứ **không** còn là
> 1 phiếu / 1 đơn hoàn. Ledger trả thêm `doc_date`, `entry_count`, `qty_total`, `cancelled_count`; trang có
> bộ lọc khoảng ngày + phân trang. Phiếu trả nay **có dialog chi tiết** (M7 cũ nói "không có" — đã lỗi thời).
> → **`docs/erp-return-daily-note.md`**.

---

## 1. Quyết định

| # | Quyết định |
|---|---|
| M1 | **Một bảng gộp** trên trang `/inventory/stock-documents`, cột **"Loại phiếu"** + bộ lọc loại & trạng thái. (Không dùng tab, không 2 bảng rời.) |
| M2 | Dữ liệu từ **endpoint union ở backend** (chuẩn hoá 2 model, lọc/sắp xếp/phân trang + đếm tổng chính xác ở DB). |
| M3 | **RBAC theo nguồn:** chỉ kèm dòng `stock` nếu user có section `stock-documents`; chỉ kèm dòng `return` nếu có `returns`. Trang vào được nếu có **một trong hai** section. Bộ lọc "Loại phiếu" ẩn lựa chọn user không có quyền. |
| M4 | Nút **"Tạo"** = dropdown: *Phiếu kho* (mở `CreateDocumentDialog` hiện có) / *Phiếu trả hàng* (mở `CreateReturnDialog` hiện có). Tái dùng nguyên 2 dialog. |
| M5 | **Thao tác theo loại dòng:** dòng `stock` giữ nguyên (Xem + dialog chi tiết / Sửa / Gửi duyệt / Duyệt / Từ chối / Huỷ); dòng `return` giữ Ghi sổ (draft) / Huỷ (posted) như trang Returns. |
| M6 | **Gỡ "Trả hàng" khỏi sidebar**; route `/inventory/returns` **redirect** → `/inventory/stock-documents`. Chuyển nút **"Quét hàng hoàn"** (ScanReturnDialog, vốn ở trang Returns) sang trang gộp. |
| M7 | ReturnNote **không** có "Chứng từ" (đính kèm) và không có dialog chi tiết → cột Chứng từ để "—", không thêm detail mới (YAGNI). |

---

## 2. Hiện trạng (tham chiếu)

- **StockDocument** (`apps/inventory/models.py`): `code`, `doc_type` (receipt/issue/adjustment/transfer), `status` (draft/pending_approval/posted/cancelled), `source_warehouse`/`dest_warehouse`, `created_at`, attachments. List API `GET /api/inventory/documents`. FE: `StockDocumentsPage.tsx` + `StockDocumentDetailDialog` + `documents-queries.ts`.
- **ReturnNote**: `code`, `return_type` (delivery_returned/external_return/warranty/defect), `status` (draft/posted/cancelled), `warehouse`, `source_order`/`customer_*`, `created_at`. List API `GET /api/inventory/returns`; `post`/`cancel`. FE: `ReturnsPage.tsx` + `CreateReturnDialog.tsx` + `returns-queries.ts`. Thao tác: Ghi sổ (draft) / Huỷ (posted). Không có detail dialog.
- Routes: `routes.tsx` — `inventory/stock-documents` (Guard `stock-documents`), `inventory/returns` (Guard `returns`).
- Quét: `features/inventory/scan/ScanReturnDialog.tsx` đang được mount trên `ReturnsPage`.

---

## 3. Sprint S1 — Backend: endpoint union

### 3.1 `GET /api/inventory/ledger-documents`
File: view mới `apps/inventory/transport/views/ledger_views.py`, route trong `apps/inventory/urls.py`.

**Query params:** `type` (receipt|issue|adjustment|transfer|return — nhiều giá trị), `status` (nhiều), `q` (khớp `code`), `date_from`/`date_to`, `limit`, `offset`.

**Chuẩn hoá (mỗi dòng):**
```json
{
  "kind": "stock" | "return",
  "id": 123,
  "code": "NK20260620-0001",
  "type": "receipt" | "return",          // doc_type hoặc "return"
  "return_type": "defect" | null,         // chỉ dòng return
  "type_label": "Nhập kho" | "Trả hàng: Lỗi",
  "status": "posted",
  "status_label": "Đã ghi sổ",
  "warehouse_label": "Kho A → Kho B" | "Kho A",
  "has_attachments": true,
  "source_label": "DH123 · Nguyễn A" | "",  // chỉ return có nguồn
  "created_at": "2026-06-20T..."
}
```

**Thực hiện (DB-level, đếm chính xác):**
- Hai queryset đã lọc (theo type/status/q/ngày) trên StockDocument và ReturnNote, **chuẩn hoá cùng tập cột** qua `annotate` + `.values()` (kind literal, type, status, created_at, id, warehouse_id chuẩn hoá `Coalesce(source,dest)` cho stock / `warehouse_id` cho return, `has_attachments` qua `Exists` cho stock / `Value(False)` cho return).
- `union(all=True)` → `order_by("-created_at","-id")` → slice `[offset:offset+limit]`. `count` = tổng 2 count đã lọc.
- Sau khi lấy trang: resolve nhãn (warehouse name, type_label, status_label, source_label) — batch tránh N+1 (nạp sẵn map warehouse, order code cho các return id trong trang).

**RBAC (M3):** đọc quyền: nếu thiếu `stock-documents:view` → bỏ nhánh stock; nếu thiếu `returns:view` → bỏ nhánh return. Nếu thiếu cả hai → 403. Dùng pattern `require_section` hợp 2 section cho view, và lọc nguồn theo `resolve_effective_permissions` (xem memory rbac-scope-by-section-gotcha).

### 3.2 Test (`apps/inventory/tests/`, pytest qua docker ephemeral)
- Union trả cả 2 loại, sắp theo created_at desc, phân trang + count đúng.
- Filter `type=return` chỉ trả return; `type=issue` chỉ stock issue; filter status; `q` theo code.
- RBAC: user chỉ `stock-documents` không thấy dòng return (và ngược lại); thiếu cả hai → 403.
- type_label/status_label/warehouse_label/source_label/has_attachments đúng cho mỗi loại.

---

## 4. Sprint S2 — Frontend: bảng gộp

### 4.1 Trang `StockDocumentsPage.tsx`
- Thay nguồn list: hook mới `useLedgerDocuments(filters)` gọi `/api/inventory/ledger-documents` (thay cho `useStockDocuments` ở phần bảng). Giữ phân trang/filter.
- **Cột:** Mã · Loại phiếu (`type_label`) · Trạng thái · Kho (`warehouse_label`) · Chứng từ (chỉ `kind==="stock"`, dùng `DocAttachmentsCell`; return → "—") · Ngày tạo · Thao tác.
- **Bộ lọc:** "Loại phiếu" (Nhập/Xuất/Điều chỉnh/Chuyển/Trả hàng) + "Trạng thái" (hợp của 2: draft/pending_approval/posted/cancelled). Ẩn lựa chọn theo quyền.
- **Nút "Tạo"** = dropdown (shadcn `DropdownMenu`): *Phiếu kho* → `CreateDocumentDialog`; *Phiếu trả hàng* → `CreateReturnDialog`. Giữ nút Import Excel hiện có.
- **Nút "Quét hàng hoàn"** → `ScanReturnDialog` (chuyển từ ReturnsPage sang).
- **Thao tác/row (M5):**
  - `kind==="stock"`: như hiện tại (Xem → `StockDocumentDetailDialog`; Sửa nếu draft; Gửi duyệt/Duyệt/Từ chối/Huỷ theo trạng thái + quyền).
  - `kind==="return"`: Ghi sổ (draft) qua `usePostReturn`; Huỷ (posted) qua `useCancelReturn` (tái dùng `returns-queries.ts`).
- Sau mutate (tạo/duyệt/post/cancel) → invalidate query key của bảng gộp (`["inventory","ledger"]`).

### 4.2 Routing & menu
- `routes.tsx`: `inventory/returns` → `<Navigate to="/inventory/stock-documents" replace />` (giữ Guard hợp lý hoặc bỏ guard cho redirect). Trang gộp: Guard cho phép nếu có `stock-documents` **hoặc** `returns`.
- Sidebar: gỡ mục "Trả hàng". (Đổi nhãn mục "Phiếu kho" nếu cần, ví dụ giữ "Phiếu kho".)

### 4.3 Dọn dẹp
- `ReturnsPage.tsx`: không còn dùng làm route (redirect). Giữ file nếu còn dùng phần nào, hoặc xoá nếu không (kiểm tra import). `CreateReturnDialog`, `returns-queries`, `ScanReturnDialog` vẫn dùng (từ trang gộp) → giữ.

---

## 5. Sprint S3 — Hoàn thiện
- Đảm bảo phân trang/đếm đúng khi lọc theo loại đơn lẻ.
- Kiểm thử quyền end-to-end (user chỉ 1 section).
- Cập nhật tài liệu menu/route nếu có.

---

## 6. Tiến độ

| Sprint | Trạng thái | Ghi chú |
|---|---|---|
| S1 — Backend union | ⬜ Chưa làm | endpoint ledger-documents + RBAC + test |
| S2 — Frontend bảng gộp | ⬜ Chưa làm | cột Loại phiếu + filter + Tạo dropdown + thao tác theo loại + chuyển nút Quét hàng hoàn + redirect/menu |
| S3 — Hoàn thiện | ⬜ Chưa làm | phân trang/quyền/tài liệu |

> Cập nhật cột Trạng thái + ghi chú sau mỗi sprint.
