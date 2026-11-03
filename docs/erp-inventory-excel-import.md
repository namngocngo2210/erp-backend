# Spec — Import dòng hàng từ Excel (Phiếu kho + Trả hàng)

> Thêm upload Excel để nhập nhanh dòng hàng cho **Phiếu kho** (`StockDocument`) và **Phiếu trả hàng** (`ReturnNote`). Dùng chung một endpoint parse.

- Ngày: 2026-06-11. Nền: module `inventory` (đã merge). Pattern Excel tham chiếu: `apps/orders/import_excel.py` (openpyxl).

## Luồng
Upload → backend parse (openpyxl) + resolve SKU→sản phẩm → trả `{lines, errors}` (xem trước) → FE đổ vào danh sách dòng của dialog (gộp, chống trùng) → user chọn loại/kho và bấm Tạo phiếu như thường. **Không** tạo phiếu trực tiếp từ file (an toàn, tái dùng luồng tạo sẵn).

## Excel mẫu
3 cột header (hàng 1): **SKU** | **Số lượng** | **Ghi chú** (Ghi chú tuỳ chọn). Mỗi hàng = 1 sản phẩm. Loại phiếu/kho/nguồn KHÔNG nằm trong Excel — chọn trên dialog.

## Backend (`apps/inventory`)
- `infrastructure/excel_lines.py`:
  - `parse_lines_from_workbook(file_obj) -> {"lines": [...], "errors": [...]}`. Đọc `load_workbook(read_only, data_only)`; map cột theo header (không phân biệt hoa thường; chấp nhận alias "SKU"/"Mã SP", "Số lượng"/"SL"/"Quantity", "Ghi chú"/"Note"). Mỗi hàng dữ liệu: SKU (str), qty (int>0), note. Resolve `Product.objects.filter(sku=..., deleted_at__isnull=True)`. **Gộp trùng SKU** (cộng dồn qty). Hàng rỗng → bỏ qua. Lỗi → `errors:[{row, sku, message}]` (SKU trống / không tồn tại / qty không hợp lệ).
    - line shape: `{product_id, sku, name, quantity, condition_note}`.
  - `build_lines_import_template() -> bytes`: xlsx header + 1 hàng ví dụ.
- `transport/views/import_views.py`:
  - `POST /api/inventory/import/parse-lines` (multipart `file`) → `{lines, errors}`. 400 nếu thiếu file / không đọc được.
  - `GET /api/inventory/import/template` → tải xlsx.
  - Permission: `require_section("stock-documents", "returns")` (ai có 1 trong 2 đều dùng được).
- urls: thêm 2 route.
- Test `tests/test_excel_import.py`: dựng workbook in-memory (2 SKU hợp lệ + 1 SKU lạ + 1 trùng SKU), parse → lines đúng (trùng gộp), errors có SKU lạ; template trả 200.

## Frontend
- `services/http.ts`: trong `fetchJson`, **KHÔNG** set `Content-Type: application/json` khi `options.body instanceof FormData` (2 chỗ: lần đầu + retry). (Hạ tầng dùng chung cho mọi upload.)
- `features/inventory/import-lines.ts`: `parseImportLines(file) -> {lines, errors}` (POST FormData) + `downloadLinesTemplate()` (GET → tải file).
- **CreateDocumentDialog** (StockDocumentsPage) + **CreateReturnDialog**: cạnh ô tìm sản phẩm, thêm:
  - nút **"Tải Excel"** (input file ẩn, accept `.xlsx`) → `parseImportLines` → gộp `lines` vào state (dedupe theo product_id, cộng qty) → `toast.success("Đã nhập N dòng")`; nếu có errors → `toast.error`/list ngắn.
  - nút **"Tải mẫu"** → `downloadLinesTemplate()`.
  - Dòng từ Excel hiển thị như dòng thường (SKU + tên + qty + ghi chú), sửa/xoá được trước khi tạo.

## Kiểm thử
- BE: test parse (gộp trùng, lỗi SKU lạ) + template 200.
- FE: tsc sạch; (test upload mock nếu khả thi).
