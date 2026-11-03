# Spec — Inventory: Trạng thái tồn kho + Quản lý trả hàng (Phase 1.5)

> Mở rộng module `inventory` (sau Phase 1 MVP) với **chiều Trạng thái** (bán được / bảo hành / lỗi)
> và **Phiếu trả hàng** (reverse logistics). Phủ Sprint A→C.
>
> - Ngày tạo: 2026-06-11
> - Nền: `docs/erp-inventory.md` (Phase 1 đã merge). Chuẩn: `docs/architecture-standard.md`.
> - Lý do làm ngay: inventory **chưa chạy prod** (migration chưa apply) → thêm chiều `state` rẻ nhất bây giờ.

> ⚠️ **Cập nhật 2026-07-11 — §3 (Phiếu trả hàng) đã đổi mô hình.** `ReturnNote` mô tả ở §3.1 (1 phiếu =
> 1 đơn hoàn, `code` = `TH{yyyymmdd}-{seq}`, có `return_type`/`source_order`/`status draft→posted`) nay là
> **`ReturnEntry`**. `ReturnNote` trở thành **phiếu NGÀY** (`PTH-YYYYMMDD-K{wh}`, 1 phiếu / ngày / kho,
> status `open`/`closed`). `ReturnNoteLine.return_type` **bắt buộc** ở từng dòng. `StockMovement` tham
> chiếu `("return_entry", entry_id)` thay cho `("return", note_id)`. API `/inventory/returns*` thao tác
> trên **entry**; thêm `/inventory/return-notes/{id}` + `/close` + `/cancel`.
> → Mô hình hiện hành + runbook deploy: **`docs/erp-return-daily-note.md`**.

---

## 1. Quyết định

| # | Quyết định |
|---|---|
| R1 | Tồn kho có **2 chiều vuông góc**: **Kho** (vị trí vật lý) × **Trạng thái** (`sellable`/`warranty`/`defect`). Không double-encode (không tạo "kho bảo hành" riêng). |
| R2 | Tồn = `(warehouse × product × state) → quantity`. Sổ kho ghi rõ `state`. |
| R3 | **Bán hàng chỉ trừ `sellable`**; warranty/defect không tính là bán được, không bị trừ. |
| R4 | **Phiếu trả hàng** ghi tồn vào đúng state theo loại trả về, và **gắn nguồn gốc** (đơn gốc + khách). |
| R5 | Xử lý tiếp (bảo hành/đổi lỗi) = **chuyển trạng thái** (reclassify) hoặc xuất (gửi lại/huỷ). |

**Trạng thái (`StockState`):** `sellable` (bán được), `warranty` (đang bảo hành), `defect` (lỗi).

---

## 2. Sprint A — Chiều Trạng thái vào core inventory

### 2.1 Model
- `StockState(TextChoices)`: sellable / warranty / defect.
- **`StockItem`**: thêm `state` (default `sellable`); đổi unique `(warehouse, product)` → **`(warehouse, product, state)`**.
- **`StockMovement`**: thêm `state` (default `sellable`).
- **`MovementType`**: bổ sung `return_in`, `reclass_in`, `reclass_out`, `disposal` (dùng ở B/C).
- Migration `inventory` mới (chưa prod nên an toàn).

### 2.2 Service
- `StockCommandService.record_movement(..., state="sellable")`: get_or_create + `select_for_update` theo `(warehouse, product, state)`; ghi movement có `state`.
- `StockQueryService.list_stock(..., state=None)`: trả rows `(warehouse, product, state, quantity)`; cho lọc theo state.
- **`issue_for_order` chỉ trừ `state=sellable`** (thêm `state=StockState.SELLABLE` khi gọi record_movement).
- `StockDocumentService` (receipt/issue/adjustment/transfer): mặc định `state=sellable`. Adjustment đọc số dư hiện tại theo `(warehouse, product, sellable)`.
- `rebuild_stock_items`: group theo `(warehouse_id, product_id, state)`.

### 2.3 API + FE
- API `/inventory/stock` trả thêm `state`; nhận query `?state=`.
- **Trang Tồn kho**: pivot mỗi `(kho × SP)` thành các cột **Bán được / Bảo hành / Lỗi / Tổng** (FE gộp các row state). Tồn âm/bất thường tô màu.

---

## 3. Sprint B — Phiếu trả hàng (Return Note)

### 3.1 Model `ReturnNote` + `ReturnNoteLine`
**ReturnNote**
| Field | Ghi chú |
|---|---|
| code | tự sinh `TH{yyyymmdd}-{seq}` |
| return_type | `delivery_returned` (hàng hoàn), `external_return` (trả ngoài), `warranty` (bảo hành), `defect` (đổi lỗi) |
| source_order | FK orders.Order, **null** (trả ngoài có thể không có đơn) |
| customer_name / customer_phone / source_note | nguồn gốc ("trả về từ đâu") |
| warehouse | FK Warehouse (kho vật lý nhận hàng) |
| status | draft → posted → cancelled |
| note, created_by, created_at, posted_at | |

**ReturnNoteLine**: product, quantity (>0), condition_note (tuỳ chọn). unique(return_note, product).

### 3.2 Ánh xạ loại → state khi POST
| return_type | state ghi tồn |
|---|---|
| delivery_returned | **sellable** (+ bán được) |
| external_return | **sellable** |
| warranty | **warranty** |
| defect | **defect** |

Post → mỗi line `record_movement(warehouse, product, +qty, movement_type=RETURN_IN, state=<map>, reference_type="return", reference_id=note.id)`. Cancel → ghi movement đảo (như StockDocument cancel).

### 3.3 Quy tắc chống cộng trùng
- Tồn chỉ trừ khi **confirm** đơn và chỉ + lại khi **CANCELLED** (Phase 1). Trạng thái đơn "đã chuyển hoàn" KHÔNG tự cộng tồn → **Phiếu trả hàng là chứng từ cộng lại** (có nguồn gốc). Không gọi `release_for_order` cho đơn đã có phiếu trả hàng.

### 3.4 API + FE
- API: `GET/POST /inventory/returns`, `GET /inventory/returns/<id>`, `POST .../post`, `POST .../cancel`. Permission section `returns`.
- FE: section **"Trả hàng"** trong module Kho (`/inventory/returns`): danh sách + dialog tạo (chọn loại trả về, tìm đơn gốc/nhập khách, kho nhận, dòng SP+SL), nút Ghi sổ/Huỷ.
- **Báo cáo** (mức cơ bản): đếm theo return_type, theo nguồn/đơn.

---

## 4. Sprint C — Xử lý tiếp (resolution = chuyển trạng thái)

### 4.1 Hành động disposition trên tồn tạm
Một service `StockReclassService` + endpoint `POST /inventory/stock/reclassify` nhận
`(warehouse_id, product_id, quantity, from_state, action)`:
- **action=`to_sellable`** (sửa xong → bán được): `reclass_out`(−from_state) + `reclass_in`(+sellable), cùng kho. Dùng cho warranty→sellable, defect→sellable.
- **action=`send_back`** (gửi lại khách — bảo hành): `disposal`(−warranty) (xuất khỏi tồn, ghi lý do gửi khách).
- **action=`scrap`** (huỷ — lỗi không sửa được): `disposal`(−defect).

Tất cả atomic, ghi sổ immutable, không cho quá tồn của state đó (cảnh báo nếu âm — nhất quán Phase 1).

### 4.2 FE
- Trên trang Tồn kho (hoặc trang Trả hàng): với dòng có tồn `warranty`/`defect` → nút **"Xử lý"** mở dialog chọn hành động (chuyển bán được / gửi lại khách / huỷ) + số lượng.

---

## 5. Bản đồ Movement type (đầy đủ sau Phase 1.5)
`receipt, issue, adjustment, transfer_in, transfer_out, sale, sale_reversal` (Phase 1)
`+ return_in` (nhập trả hàng), `reclass_in/reclass_out` (chuyển trạng thái), `disposal` (xuất huỷ/gửi lại).

---

## 6. Sprint & Tiến độ
| Sprint | Phạm vi | Trạng thái |
|---|---|---|
| **A** | Chiều `state` vào StockItem/StockMovement + record_movement + queries + sale chỉ trừ sellable + FE tồn theo 3 trạng thái. Migration. | ✅ Xong (2026-06-11) — migration `0003`; trang Tồn kho pivot Bán được/Bảo hành/Lỗi/Tổng. |
| **B** | `ReturnNote`/`Line` + post/cancel (map state) + API + FE section "Trả hàng" + báo cáo cơ bản. | ✅ Xong (2026-06-11) — migration `0004`; 4 loại trả về gắn đơn gốc; section "Trả hàng". |
| **C** | `StockReclassService` (to_sellable/send_back/scrap) + API + FE nút "Xử lý" trên tồn tạm. | ✅ Xong (2026-06-11) — nút "Xử lý" trên tồn bảo hành/lỗi. |

> **Đã hoàn thành (2026-06-11).** Branch `feat/inventory-returns`. BE 68 test inventory + FE tsc/shell test xanh. Migration `0003`,`0004` (chưa chạy prod).

---

## 7. Rủi ro
- **R-A1 — Đổi unique key StockItem** (thêm state): module chưa prod → migration sạch; vẫn phải sửa `rebuild_stock_items` + mọi nơi đọc StockItem theo (kho,SP) sang (kho,SP,state).
- **R-A2 — Sale phải trừ đúng sellable**: nếu quên `state=sellable` ở `issue_for_order` → trừ nhầm. Có test bắt buộc.
- **R-B1 — Cộng trùng tồn khi hàng hoàn**: tách rõ "đã chuyển hoàn" (không auto +) vs phiếu trả hàng (+ có chứng từ). Test.
- **R-C1 — Chuyển/huỷ quá tồn state**: cho âm + cảnh báo (nhất quán Phase 1) hoặc chặn — theo quy tắc Phase 1 (cho âm + cảnh báo).
