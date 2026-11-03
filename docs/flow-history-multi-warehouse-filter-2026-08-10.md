# Lịch sử nhập xuất — lọc NHIỀU kho (2026-08-10)

## Bối cảnh

Ô "Kho" ở trang Lịch sử nhập xuất mới chuyển sang select có ô tìm kiếm
(MR !1190) nhưng vẫn chỉ chọn được MỘT kho. Nghiệp vụ thực tế cần đối chiếu vài
kho cùng lúc (ví dụ 2 kho Hà Nội) mà không phải xuất 2 file rồi cộng tay.

Yêu cầu kèm theo: **file Excel phải bám đúng tập kho đang chọn trên màn hình**.
Đây là điểm dễ vỡ nhất — job export chạy lại bộ lọc ở worker qua một nhánh code
riêng (`exports_definitions.py`), lệch tham số là file rộng hơn màn hình mà
không có cảnh báo nào.

## Quyết định thiết kế

| Vấn đề | Quyết định | Lý do |
|---|---|---|
| Kiểu tham số API | `warehouse_ids=1,5` (CSV) | Đồng bộ với `product_ids` đã có trên chính màn này; không phải chế thêm quy ước thứ hai |
| Tham số cũ `warehouse_id` | Vẫn đọc, gộp vào danh sách | Link đã lưu / tích hợp ngoài và các job export đang nằm trong hàng đợi không được gãy |
| Danh sách rỗng | = TẤT CẢ kho | Giữ đúng ngữ nghĩa `warehouse_id=None` cũ, không cần giá trị "all" giả |
| Chỗ parse | `apps/inventory/flow_filters.py` | Trước đây luật parse bị chép 2 bản (view + export) — nguồn gốc của lớp lỗi "màn hình lọc đúng, file Excel thì không" |
| Component FE | `MultiSelect` có sẵn | Cùng loại ô với bộ lọc Sản phẩm ngay cạnh (có sẵn ô tìm + "chọn tất cả"), không thêm thư viện |

Ở tầng truy vấn, `warehouse_id=<id>` đổi thành `warehouse_id__in=<ids>`. Không
có index mới: các index hiện có trên `warehouse_id` phục vụ `IN` như nhau.

## Phạm vi thay đổi

Backend — filter key `warehouse_id` → `warehouse_ids` trong toàn chuỗi đọc của
màn này:

- `transport/views/flow_history_views.py` (`_common_filters`), `awaiting_return_views.py`
- `application/services/`: `dispatch_history_service`, `issue_document_history`,
  `transfer_document_history`, `return_history_service`, `pending_scan_service`
  (`list_unmatched`, `scan_notes_by_code`), `awaiting_return_service`
- `exports_definitions.py` — cả 2 kind `dispatch_history` / `return_history`,
  cả nhánh theo đơn lẫn nhánh theo SKU, kể cả khối "mã chờ khớp" và ghi chú mã quét
- `flow_filters.py` (mới) — `int_csv`, `int_or_none`, `parse_warehouse_ids`

KHÔNG đụng: export tồn kho (`kind="stock"`) vẫn bắt buộc đúng MỘT kho — đó là
báo cáo tồn của một kho, không phải bộ lọc lịch sử.

Frontend:

- `FlowFilters.warehouseId: number | null` → `warehouseIds: string[]`
- `buildFlowParams` / `buildFlowExportParams` → gửi `warehouse_ids` CSV
- `DispatchHistoryPage` dùng `MultiSelect`; `AwaitingReturnTab` +
  `useAwaitingReturn` nhận danh sách kho (tab này dùng chung ô lọc kho)

## Kiểm chứng

- Backend: `apps/inventory` + `apps/exports` — thêm test lọc nhiều kho cho danh
  sách, tile tổng hợp, khối chờ khớp, tab Chờ hàng hoàn về, tham số cũ
  `warehouse_id`, và 4 test cho chính file export (theo đơn + theo SKU).
- Frontend: `buildFlowParams` / `buildFlowExportParams` với 0, 1, nhiều kho.
- `ruff check .` sạch; `tsc --noEmit`, eslint, prettier sạch trên phần đã sửa.

## Việc chưa làm

- `schema.yaml` chưa regen (đang lệch sẵn ~444 dòng trên master; regen ở đây sẽ
  kéo nhiễu của feature khác vào MR — trả bằng MR chore riêng).
- Bộ lọc kho ở các trang khác (Tồn kho, Phiếu kho, Quét xuất) vẫn chọn một kho;
  nếu muốn thống nhất nên gom thành component `WarehouseSelect` dùng chung thay
  vì sửa lẻ từng trang.
