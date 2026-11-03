# Gỡ UI + logic thao tác tay của Phiếu trả hàng

**Ngày:** 2026-07-14 · **Branch:** `refactor/remove-return-note-ui`

## Bối cảnh & quyết định

Kho chỉ dùng **Quét trả hàng**; phiếu trả hàng (PTH-) trên trang Phiếu kho không ai
thao tác tay, việc theo dõi hoàn đã có tab **Nhập hoàn** của Lịch sử nhập xuất.
Quyết định (user chốt 2026-07-14): gỡ toàn bộ UI phiếu trả + logic backend đi kèm;
thao tác sửa sai chuyển sang Lịch sử nhập xuất.

**Không đụng phần nền** — quét trả hàng vẫn chạy trên ReturnNote/ReturnEntry làm sổ
ghi tồn ngầm:

- Models `ReturnNote` / `ReturnEntry` / `ReturnNoteLine` (không migration).
- `ReturnEntryService` create / post / cancel / update_note, `resolve_open_note`.
- Luồng pending-scan (bảng chờ + cron khớp + manual match), lệnh
  `merge_return_notes_by_day`, `post_matched_return_drafts`.
- Endpoint `POST /api/inventory/returns` (ScanReturnDialog dùng, `auto_post=true`),
  `POST /api/inventory/returns/{id}/cancel`, toàn bộ `return-history/*`.
- RBAC section `returns` (vẫn gate tạo qua quét + huỷ).

## Backend gỡ

| Thứ | Chi tiết |
|---|---|
| `ledger_views.py` | Bỏ nhánh union ReturnNote + `_resolve_return_extra` + 5 field return-only; endpoint chỉ còn phiếu kho, permission `require_section("stock-documents")`. |
| `return_note_views.py` | Xoá cả file + 3 route `/return-notes/{id}` (detail/close/cancel). |
| `return_views.py` | Bỏ nhánh GET list (view thành `create_return_entry`, POST-only); xoá `return_detail` (GET+PATCH khớp đơn thủ công cũ) + `post_return_view` (ghi sổ nháp tay) + route. Giữ `cancel_return_view`. |
| `ReturnNoteService` | Xoá `close_note` / `cancel_note` (không còn caller ngoài test). Phiếu ngày không bao giờ đóng nữa — `resolve_open_note`/`_next_code` giữ nguyên. |
| `return_entry_edit_service` | Xoá `update_draft` (khớp tay giờ đi qua `PendingScanService.manual_match`); giữ `update_note`. Xoá wrapper `ReturnEntryService.update_draft`. |
| Serializers | Xoá `UpdateReturnEntrySerializer`, `ReturnNoteDetailSerializer` (+ helper chỉ chúng dùng). |
| Tests | Xoá `test_return_entry_close_race.py`; trim close/cancel-note + update_draft trong `test_return_note_api.py`, `test_return_note_daily.py`, `test_return_entry_service.py`, `test_return_unmatched.py`, `test_return_line_type.py`; `test_ledger_documents.py` viết lại chỉ còn phiếu kho. |

## Frontend gỡ

- `StockDocumentsPage`: bỏ dialog phiếu trả (tạo tay + chi tiết), menu "Tạo → Phiếu
  trả hàng", option lọc "Phiếu trả hàng", 2 mutation chốt/huỷ phiếu. Filter trạng
  thái luôn bật (chỉ còn 1 tập trạng thái phiếu kho). Giữ "Quét trả hàng (hàng
  loạt)" + "Chờ khớp đơn".
- `LedgerTable`: bỏ toàn bộ nhánh `kind === "return"`.
- Xoá file: `CreateReturnDialog.tsx`, `ReturnNoteDetailDialog.tsx`,
  `ReturnEntryMatchPanel.tsx`, `create-return-payload.ts` + tests tương ứng.
- `returns-queries.ts`: chỉ giữ `useCreateReturn` (quét) + `useCancelReturnEntry`.
- `ledger-types.ts` / `returns-types.ts` / `return-line-type.ts`: trim type/const
  không còn ai dùng (`LedgerKind`, field return-only, `NOTE_STATUS_*`,
  `ReturnNoteDetail`, `UpdateReturnEntryPayload`, `buildLineTypePayload`, ...).

## Frontend thêm — tab Nhập hoàn (Lịch sử nhập xuất)

- Cột **Thao tác**: nút **Huỷ** trên mỗi dòng lịch sử (dòng lịch sử = đơn hoàn ĐÃ
  ghi sổ), gate quyền `returns:approve`, confirm rồi gọi
  `POST /returns/{id}/cancel` (đảo tồn); huỷ xong dòng rời danh sách (list chỉ hiện
  posted) + invalidate tồn kho.
- "Gắn đơn" cho mã chưa khớp **đã có sẵn** ở khối mã chờ khớp (PendingMatchPanel) —
  không cần thêm.

## Không còn đường tạo đơn hoàn NHÁP

Sau thay đổi: quét khớp mã → ghi sổ ngay; mã chưa khớp → bảng chờ (cron/manual
match, khi khớp cũng ghi sổ ngay). Nháp chỉ còn là dữ liệu legacy — đã có lệnh
`post_matched_return_drafts` xử lý, không cần UI.
