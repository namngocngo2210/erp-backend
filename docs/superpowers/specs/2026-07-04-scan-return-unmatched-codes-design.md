# Thiết kế: Quét trả hàng — ghi nhận mã vận đơn chưa khớp đơn

**Ngày:** 2026-07-04
**Nhánh:** `feat/scan-return-unmatched-codes`
**Trạng thái:** Đã duyệt thiết kế, chờ implementation plan

## Bối cảnh & vấn đề

Flow quét mã tạo phiếu trả hàng hiện tại
(`frontend/src/features/inventory/pages/CreateReturnDialog.tsx`, hàm
`handleScanCode`) gọi `GET /api/orders/lookup-by-shipping-code`. Nếu không tìm
thấy đơn, hệ thống chỉ beep lỗi + toast rồi **bỏ mất mã quét** — kho phải để
gói hàng sang một bên và nhớ xử lý sau bằng tay.

Yêu cầu: khi quét mã không tìm thấy đơn, vẫn ghi nhận mã đó (để trống đơn
hàng), sau đó kho **khớp đơn thủ công** vào mã đó sau.

## Quyết định đã chốt với user

1. **Khớp đơn: chỉ thủ công** — không có job tự động tra lại.
2. **Hình thức ghi nhận:** mỗi mã chưa khớp → 1 **phiếu trả nháp** (lưu mã vận
   đơn, đơn gốc trống, 0 dòng hàng). Không tạo model/màn hình "mã chờ khớp"
   riêng.
3. **UX quét: dạng hàng loạt (batch list)** — quét nhiều mã liên tục, mỗi mã 1
   dòng trong danh sách (khớp: hiện đơn; không khớp: bôi đỏ), bấm **Xác nhận**
   1 lần → tạo mỗi mã 1 phiếu nháp.

## Thiết kế

### 1. Model & DB

- Thêm trường `shipping_code` vào `ReturnNote`
  (`backend/apps/inventory/models.py`): `CharField(max_length=64, blank=True,
  default="")`, có index. 1 migration mới trong app `inventory`.
- **Chặn trùng:** khi tạo phiếu chưa khớp (không có `source_order`), nếu đã
  tồn tại phiếu **nháp** khác cùng `shipping_code` cũng chưa gắn đơn → lỗi
  "Mã này đã được ghi nhận ở phiếu <code>" (tránh 2 phiếu cho cùng 1 gói).
  Phiếu đã khớp đơn / đã ghi sổ / đã huỷ không tính vào guard này.

### 2. Backend API

- `CreateReturnNoteSerializer`
  (`backend/apps/inventory/transport/serializers/return_serializers.py`):
  - Thêm `shipping_code` (tuỳ chọn, blank OK).
  - Nới validate: **`lines` được rỗng CHỈ KHI có `shipping_code` và không có
    `source_order_id`** (phiếu chờ khớp). Mọi trường hợp khác giữ nguyên bắt
    buộc ≥1 dòng.
- `ReturnNoteService.create_return`
  (`backend/apps/inventory/application/services/return_note_service.py`):
  nhận `shipping_code`, thực hiện guard chặn trùng mã ở trên.
- `ReturnNoteService.post_return`: **chặn ghi sổ phiếu 0 dòng** — lỗi rõ ràng
  "Phiếu chưa có dòng hàng — hãy khớp đơn trước khi ghi sổ." (hiện tại phiếu
  0 dòng sẽ ghi sổ "rỗng" âm thầm, không tạo movement nào).
- **Endpoint mới** `PATCH /api/inventory/returns/<id>` (chỉ phiếu DRAFT):
  - Cập nhật được: `source_order_id`, `customer_name`, `customer_phone`,
    `lines` (thay toàn bộ — replace-all), `note`, `source_note`.
  - Service method mới `ReturnNoteService.update_draft`.
  - Phiếu không ở trạng thái DRAFT → 400.
  - RBAC: `require_section("returns")` (giống create). Ghi sổ/huỷ giữ nguyên
    quyền hiện có.
- `ReturnNoteSerializer` thêm `shipping_code` vào output. Endpoint
  ledger-documents union (trang Phiếu kho) bổ sung trường cần thiết để FE
  hiển thị badge "Chưa khớp đơn" (shipping_code + source_order null).
- **Tạo N phiếu khi xác nhận:** frontend lặp POST `/api/inventory/returns`
  từng mã (endpoint hiện có), kết quả ✓/✗ hiển thị per-row (giống pattern
  `ScanDispatchDialog`). Không cần batch endpoint — mỗi phiếu độc lập, lỗi
  từng phiếu không chặn phiếu khác.

### 3. Frontend — dialog quét trả hàng loạt (mới)

- Component mới `ScanReturnDialog` trong `frontend/src/features/inventory/scan/`,
  mirror `ScanDispatchDialog.tsx`:
  - Chọn **Loại trả về** (`return_type`, mặc định "Hàng hoàn") + **Kho nhận**.
  - Ô quét `ScanShipmentInput` + súng quét `useScannerGun` +
    `PhoneScanButton purpose="return"`.
  - Mỗi mã quét thành 1 dòng trong bảng:
    - **Khớp đơn:** hiện mã vận đơn, mã đơn, tên khách (như bảng dispatch).
    - **Không khớp:** dòng **bôi đỏ** (`bg-red-50` + text đỏ), chỉ có mã +
      nhãn "Chưa khớp đơn".
  - Quét trùng mã đã có trong list → cảnh báo + beep lỗi, bỏ qua.
  - Bấm **"Xác nhận (N)"** → tạo tuần tự mỗi dòng 1 phiếu nháp:
    - Dòng khớp: phiếu có `source_order_id` + khách + dòng hàng nạp từ items
      của đơn (`orderItemsAsLines`) + `shipping_code`.
    - Dòng đỏ: phiếu trống đơn, 0 dòng hàng, chỉ lưu `shipping_code`.
    - Kết quả ✓ "đã tạo <code phiếu>" / ✗ lỗi hiển thị per-row.
- `StockDocumentsPage.tsx`: thêm nút "Quét trả hàng" mở `ScanReturnDialog`
  (bên cạnh nút tạo phiếu trả hiện có). `CreateReturnDialog` giữ nguyên cho
  flow tạo thủ công.
- Mutations dùng React Query (`useMutation` + invalidate
  `["inventory", "returns"]`) theo convention; API types từ `schema.d.ts`
  (regen openapi-typescript sau khi thêm endpoint PATCH).

### 4. Khớp đơn thủ công (trang Phiếu kho)

- Danh sách + chi tiết phiếu trả: phiếu có `shipping_code` mà `source_order`
  trống → **badge đỏ "Chưa khớp đơn"**.
- Trong chi tiết phiếu **nháp** chưa khớp, khối "Khớp đơn":
  - Nút **"Tra lại mã"**: gọi lại `lookup-by-shipping-code` với mã đã lưu
    (đơn có thể đã được nhập mã vận đơn/sync về sau).
  - Ô **nhập ID đơn** thủ công (fallback khi tra không ra).
  - Chọn được đơn → PATCH gắn `source_order_id` + tên/SĐT khách + nạp
    `lines` từ items của đơn → phiếu thành phiếu thường, ghi sổ như cũ.
- Nút "Ghi sổ" disable khi phiếu 0 dòng (đồng bộ với guard BE).

### 5. Tests

Backend (`backend/apps/inventory/tests/`):
- Tạo phiếu chờ khớp (0 dòng + `shipping_code`, không đơn) → 201.
- 0 dòng mà không có `shipping_code` → 400 (giữ hành vi cũ).
- 0 dòng + có `source_order_id` → 400.
- Ghi sổ phiếu 0 dòng → 400.
- Trùng `shipping_code` giữa 2 phiếu nháp chưa khớp → 400.
- PATCH gắn đơn + lines vào phiếu nháp → 200, sau đó ghi sổ OK (movement
  đúng số lượng).
- PATCH phiếu đã ghi sổ → 400.
- RBAC: user không có section `returns` → 403.

Frontend (vitest):
- Logic list quét: thêm dòng khớp/không khớp, dedupe mã trùng.
- Xác nhận tạo N phiếu: payload đúng cho dòng khớp (có order+lines) và dòng
  đỏ (chỉ shipping_code, lines rỗng).
- Flow khớp đơn: tra lại mã / nhập ID → PATCH payload đúng.

### Ngoài phạm vi (YAGNI)

- Job tự động tra lại & khớp đơn định kỳ.
- Model/màn hình riêng cho "mã chờ khớp".
- Batch endpoint tạo nhiều phiếu 1 request.
- Khớp 1 mã với nhiều đơn (lookup trả multiple → lấy đơn đầu, kho tự sửa,
  giống hành vi dispatch hiện tại).
