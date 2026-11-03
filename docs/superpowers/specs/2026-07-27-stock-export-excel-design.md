# Xuất Excel tồn kho theo kho — thiết kế

Ngày: 2026-07-27
Trạng thái: đã duyệt, đang triển khai

## Bối cảnh

Trang tồn kho (`frontend/src/features/inventory/pages/StockPage.tsx`) hiển thị bảng tồn
theo kho đang chọn: mỗi dòng là một SKU với các cột Bán được / Bảo hành / Lỗi / Tổng /
RR3 / RR7. Dữ liệu lấy từ `GET /api/inventory/stock` (trả từng dòng `StockItem` theo
`state`), phần pivot sang cột trạng thái làm ở client (`buildPivot`).

Người dùng cần tải danh sách tồn hiện tại của MỘT kho ra file Excel để đối chiếu ngoài
hệ thống.

## Phạm vi

- Xuất đúng kho đang chọn trên màn hình, kèm từ khoá tìm kiếm đang áp dụng.
- Một file `.xlsx`, một sheet.
- Xuất ĐẦY ĐỦ số dòng của kho, không bị giới hạn 200 dòng như bảng web.

Ngoài phạm vi:

- Xuất nhiều kho / mỗi kho một sheet.
- Sửa giới hạn 200 dòng không phân trang của bảng web (xem "Ghi nhận nợ kỹ thuật").

## Quyết định kiến trúc

Backend sinh file `.xlsx`, không dùng thư viện JS phía client:

- Đồng bộ với hai export đã có (`dispatch-history/export`, `return-history/export`) dùng
  `openpyxl` + `apps/common/excel_utils.append_clean_row`.
- Server mới xuất được toàn bộ dữ liệu; client chỉ có tối đa những gì đã tải.
- Chỉ server mới gate được quyền `stock:export` thật sự.

## Thiết kế backend

### 1. `StockQueryService.pivot_stock(warehouse_id, search)`

Trả về list dict đã gộp theo `(warehouse_id, product_id)`:

```
{warehouse_name, product_sku, product_name, sellable, warranty, defect, total, rr3, rr7}
```

Tái dùng `list_stock()` và `sales_run_rates()` sẵn có nên số liệu file luôn khớp với
bảng web — cùng một nguồn truy vấn, chỉ khác chỗ pivot (server thay vì client).

Thứ tự dòng giữ theo `product__sku` như `list_stock()`.

### 2. `apps/inventory/infrastructure/stock_excel.py`

`export_stock_pivot(rows, warehouse_name)` dựng workbook và trả `HttpResponse`.

Cột: `Kho | SKU | Tên sản phẩm | Bán được | Bảo hành | Lỗi | Tổng | RR3 | RR7`
(đúng thứ tự bảng web, thêm cột Tên sản phẩm vì file đọc ngoài hệ thống cần nó).

Mọi dòng ghi qua `append_clean_row` — đã xử lý ký tự control và chống formula injection.

Tên file: `ton-kho-<mã-kho>-<yyyymmdd>.xlsx`.

### 3. View `stock_export`

`GET /api/inventory/stock/export?warehouse_id=&search=`

- Gate `require_permission("stock:export")`, KHÔNG dùng `require_section("stock")`:
  section chỉ đòi `stock:view`, tức ai xem được là tải được cả file (đã chốt ở audit
  2026-07-14 vòng 2 cho hai export kia).
- `warehouse_id` BẮT BUỘC → thiếu/không hợp lệ trả 400. Trang luôn chọn sẵn một kho;
  bắt buộc tham số để không vô tình dump tồn toàn hệ thống qua URL trần.
- Trần an toàn `_EXPORT_MAX_ROWS = 20000` dòng `StockItem` để một kho khổng lồ không kéo
  sập worker; vượt trần trả 400 kèm hướng dẫn thu hẹp bằng ô tìm kiếm.
- `@extend_schema(exclude=True)` vì trả nhị phân, không phải JSON.

### 4. Test backend

`backend/apps/inventory/tests/test_stock_export.py`

- Pivot gộp đúng ba state thành ba cột và cột Tổng.
- Lọc đúng theo `warehouse_id` (không lẫn kho khác) và theo `search`.
- Thiếu `warehouse_id` → 400.
- Không có `stock:export` → 403 (dù đã có `stock:view`).
- Response là `.xlsx` với header đúng thứ tự cột.

## Thiết kế frontend

### 5. Tách `downloadXlsxExport` dùng chung

Hàm đang nằm trong `features/inventory/issue-history-queries.ts` và bị
`return-history-queries.ts` import chéo. Chuyển sang `src/lib/api/xlsx.ts`, hai file cũ
re-export/import từ đó. Không đổi hành vi.

### 6. `downloadStockExport(warehouseId, search)`

Thêm vào `features/inventory/queries.ts`, dựng query string giống `useStockList` để file
khớp đúng bộ lọc đang xem.

### 7. Nút "Xuất Excel" trên `StockPage`

- Đặt trong `PageHeader actions`, cạnh ô chọn kho và ô tìm kiếm.
- Ẩn khi không có quyền `stock:export` (dùng `usePermission`, theo mẫu
  `AttendanceHistoryPage`).
- Disable khi chưa chọn kho hoặc đang tải; hiện trạng thái busy trong lúc tải.

### 8. Test frontend

`features/inventory/__tests__/stock-export.test.tsx`

- Bấm nút gọi đúng URL kèm `warehouse_id` và `search` hiện hành.
- Ẩn nút khi thiếu quyền `stock:export`.

## Ghi nhận nợ kỹ thuật (không xử lý ở sprint này)

`useStockList` gọi `limit=200, offset=0` và trang KHÔNG có phân trang: kho nhiều hơn ~67
SKU (mỗi SKU tối đa 3 dòng state) sẽ bị cắt dữ liệu trên màn hình mà người dùng không hề
biết. File Excel xuất đầy đủ nên số dòng trong file có thể NHIỀU HƠN trên màn hình. Đây
là hành vi đúng của tính năng xuất, còn phần bảng web bị cắt cần một task riêng (phân
trang hoặc pivot phía server).

## Sprint

| # | Việc | Trạng thái |
|---|------|-----------|
| 1 | Backend: `pivot_stock`, `stock_excel`, view + route | ✅ xong |
| 2 | Backend: test pytest (`test_stock_export.py`, 10 test) | ✅ xong |
| 3 | Frontend: tách `downloadXlsxExport`, `downloadStockExport`, nút trên `StockPage` | ✅ xong |
| 4 | Frontend: test vitest (`stock-export.test.tsx`, 4 test) | ✅ xong |
| 5 | Chạy lint + test, mở MR | ✅ xong |

### Ghi chú sau sprint

- Đã làm: đúng phạm vi thiết kế, không phát sinh ngoài.
- Kết quả kiểm chứng: `apps/inventory` + `apps/users` 570 test pass; frontend
  `src/features/inventory` 190 test pass; `tsc --noEmit`, `eslint src`, `ruff check .` sạch.
- `src/app/__tests__/RequireAuth.test.tsx` fail 2 test do lỗi AbortSignal/undici có sẵn trên
  master — đã đối chiếu nhánh chưa có thay đổi này, fail y hệt.
- Phụ thu ngoài kế hoạch (nhỏ): khi chuyển `downloadXlsxExport` sang `src/lib/api/xlsx.ts`,
  chỉ thị `eslint-disable no-restricted-globals` không còn cần — rule chỉ chặn `fetch` trần
  ở `pages/features/components`, còn `lib/api/**` chính là nơi được phép. Đã bỏ chỉ thị.
- Chưa làm (cố ý): phân trang cho bảng tồn kho trên web (xem phần nợ kỹ thuật ở trên).
