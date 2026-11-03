# Gỡ khớp mã vận đơn khớp nhầm (Lịch sử nhập xuất)

Ngày: 2026-08-05 · Nhánh: `fix/return-tab-matched-cancel`

## Bài toán

Kho quét mã / khớp tay nhầm đơn thì trước đây không có đường lùi tại chỗ:

- **Tab Nhập hoàn**: dòng mã quét đã khớp chỉ có nút "Điều chỉnh"; muốn huỷ phải
  tự tìm dòng lịch sử (nền trắng) tương ứng phía dưới.
- **Tab Xuất**: không có nút nào — phải mở màn đơn hàng CRM, bỏ trạng thái
  "Đã xuất kho" (un-dispatch), rồi quét lại mã.

## Giải pháp

### Nhập hoàn — nút "Huỷ" ngay trên dòng đã khớp

Dòng mã quét đã khớp (nền xanh) có thêm nút **Huỷ** cạnh "Điều chỉnh", dùng đúng
`POST /api/inventory/returns/{entry_id}/cancel` sẵn có (đảo bút toán cộng tồn).
Cần quyền `returns:approve` như dòng lịch sử.

### Xuất — endpoint mới `POST /api/inventory/flow-pending/{scan_id}/unmatch`

`PendingScanService.unmatch_dispatch` (1 transaction, thuần DB — không hỏi KiotViet):

- Lần khớp **đã ghi SALE** (error không chứa `already_dispatched`): un-dispatch
  đơn qua đúng đường `OrderService.update_order_inline` → hoàn tồn outstanding
  (idempotent, đã trừ credit hoàn) + trạng thái giao vận về "Kho đang xử lý"
  (`warehouse_processing`) + sự kiện cập nhật FE. Chỉ chạy khi đơn **vẫn đang**
  "Đã xuất kho" — đơn đã sang "Đã nhận"… thì trả lỗi, xử lý trên màn đơn hàng.
- Lần khớp **không ghi SALE** (`already_dispatched` — quét trùng / hàng gửi bù):
  chỉ tháo scan, tuyệt đối không đụng tồn/đơn.
- Mã đã sinh **phiếu xuất bù còn hiệu lực** (chờ duyệt / đã ghi sổ): chặn — phải
  huỷ/từ chối phiếu trước. Phiếu đã huỷ hoặc bị từ chối (về nháp) không chặn.
- Scan về **FAILED** kèm ghi chú "Đã gỡ khớp khỏi đơn …" (cron chỉ quét PENDING
  nên không tự khớp lại mã vừa gỡ); giữ `matched_order` làm vết. Sau đó khớp tay
  lại đơn đúng như một mã lỗi bình thường.

Quyền: đọc qua section `stock`, hành động soi section `shipments` (như khớp tay).

FE: `list_unmatched` trả thêm cờ `can_unmatch` (chỉ kind=dispatch, matched, gắn
đơn, không còn phiếu bù hiệu lực — 1 query gộp, không N+1). `PendingActions`
(flow-shared, dùng ở IssueTab) hiện nút **Gỡ khớp** khi cờ bật, confirm rồi gọi
`useFlowUnmatch` (invalidate issue-pending / issue-orders / stock / ledger).

## Quy trình sửa khớp nhầm (người dùng)

1. Tab Xuất → dòng mã đã khớp nhầm → **Gỡ khớp** (tồn hoàn lại, đơn về "Kho đang
   xử lý"). Tab Nhập hoàn → **Huỷ** ngay trên dòng.
2. Dòng chuyển về trạng thái lỗi → bấm **Khớp** chọn đúng mã hóa đơn.

## Test

- BE: `apps/inventory/tests/test_pending_scan_unmatch.py` (9 test: hoàn tồn, khớp
  lại, already_dispatched, chặn phiếu bù, chặn đơn đã sang trạng thái sau,
  quyền 403, cờ `can_unmatch`).
- FE: `pending-replacement-panel.test.tsx` (nút Gỡ khớp), `return-tab-actions.test.tsx`
  (nút Huỷ dòng đã khớp).
