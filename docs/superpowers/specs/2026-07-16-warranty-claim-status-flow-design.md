# Bộ trạng thái mới cho phiếu bảo hành (WarrantyClaim)

**Ngày:** 2026-07-16
**Trạng thái:** Đã duyệt thiết kế, chờ triển khai

## Bối cảnh

Trạng thái phiếu bảo hành (`WarrantyClaim.status`) hiện chỉ có 3 giá trị (Đang xử lý /
Hoàn thành / Hủy) — quá thô so với quy trình CSKH thực tế. Nghiệp vụ cần theo dõi phiếu
qua từng chặng: từ lúc chờ khách xác nhận đến khi gửi lại hàng và xin feedback.

**Phạm vi:** chỉ tầng trạng thái PHIẾU. Trạng thái hành trình (`WarrantyJourney.status`
và `return_status`) giữ nguyên, tiếp tục theo dõi vận đơn từng chặng.

## Quyết định đã chốt

| Câu hỏi | Quyết định |
|---|---|
| Áp dụng vào tầng nào? | Trạng thái phiếu (`WarrantyClaim.status`) |
| Cơ chế cập nhật | Nhân viên chọn tay (dropdown), không auto-suggest |
| Trạng thái Hủy | Giữ, thêm vào cuối bộ mã |
| Migrate phiếu "Đang xử lý" cũ | Về "Đợi khách XN" (đầu flow), nhân viên tự kéo về đúng chặng |
| Đánh mã số | Đánh lại 1→11 theo thứ tự flow, remap data cũ |

## Bộ trạng thái mới

| Mã | Code | Nhãn |
|---|---|---|
| 1 | `WAITING_CUSTOMER_CONFIRM` | Đợi khách XN |
| 2 | `WAITING_ORDER_CREATE` | Chờ tạo đơn |
| 3 | `WAITING_SHIPMENT` | Chờ vận chuyển |
| 4 | `REPAIRING` | Đang sửa chữa |
| 5 | `WAITING_RESOLUTION` | Chờ chốt phương án |
| 6 | `WAITING_RESEND_ORDER` | Chờ tạo đơn gửi lại |
| 7 | `RESENDING` | Đang gửi lại khách |
| 8 | `WAITING_FEEDBACK` | Xin feedback |
| 9 | `COMPLETED` | Hoàn thành |
| 10 | `LOST` | Thất lạc |
| 11 | `CANCELLED` | Hủy |

Default khi tạo phiếu = 1 (Đợi khách XN). Không ràng buộc thứ tự chuyển — nhân viên
chọn tự do bất kỳ trạng thái nào.

## Backend

### Model & migration (`apps/warranty/models.py`)

- `WarrantyClaimStatus` (IntegerChoices) thay bằng 11 giá trị trên.
- Migration gồm 2 bước trong 1 file:
  1. Data migration remap phiếu cũ — chạy `3→11` (Hủy) trước, rồi `2→9` (Hoàn thành);
     `1→1` giữ nguyên số, chỉ đổi nghĩa nhãn. Viết reverse tương ứng (`11→3`, `9→2`).
  2. `AlterField` cho choices mới (không đổi kiểu cột, chỉ metadata).

### Service (`apps/warranty/claim_service.py`)

- `update_claim` nhận `status` như hiện tại (đã nằm trong `_CLAIM_EDITABLE_FIELDS`).
- Bổ sung: khi `update_claim` đổi status **sang 9 (Hoàn thành)** và `completed_at`
  đang trống → stamp `completed_at = now()`. Rời khỏi Hoàn thành **không** xóa
  `completed_at` (lưu vết lần hoàn thành gần nhất).
- Endpoint/`complete()` giữ nguyên hành vi, trỏ về mã 9.

### API schema

- Enum trong serializer/OpenAPI tự cập nhật theo choices → regen `backend/schema.yaml`
  và FE `gen:api`. **Gotcha:** chạy `prettier --write` trên `schema.d.ts` sau khi gen
  (tránh diff giả 4-space).
- Export Excel (`claim_excel.py`) dùng `status_label` nên tự đúng, không sửa.

## Frontend

- `warranty-claim-types.ts`: `WARRANTY_STATUS_OPTIONS` mở rộng 11 mục theo bảng trên.
  Type `WarrantyClaimStatus` derive từ schema nên tự thành `1|...|11` sau gen:api.
- `STATUS_BADGE_CLASS` (`warranty-claim-columns.ts`): từ `Record<1|2|3>` thành đủ 11 mã.
  Tông màu: 1–8 (đang chạy) xanh da trời / amber nhạt phân theo chặng thu hồi vs gửi lại,
  9 Hoàn thành xanh lá, 10 Thất lạc đỏ, 11 Hủy xám.
- Gỡ các cast cứng `as 1 | 2 | 3` ở `WarrantyClaimsPage.tsx`, `WarrantyClaimDetailPage.tsx`,
  `WarrantyClaimsTable.tsx`.
- Filter dropdown trang danh sách + select trong `WarrantyClaimFormDialog` tự ăn theo
  `WARRANTY_STATUS_OPTIONS`, không cần logic mới.

## Test

- **BE:** choices mới đúng mã/nhãn; migration remap (2→9, 3→11, 1 giữ nguyên);
  `update_claim` stamp `completed_at` khi sang 9 (và không stamp lại/không xóa khi đổi tiếp).
- **FE:** cập nhật test hiện có của columns/table theo options mới; badge class đủ 11 mã.

## Không làm (YAGNI)

- Không ràng buộc/validate thứ tự chuyển trạng thái.
- Không auto-suggest trạng thái phiếu từ dữ liệu hành trình.
- Không log lịch sử đổi trạng thái.

## Triển khai

- Branch: `feat/warranty-claim-status-flow` (worktree `.claude/worktrees/warranty-claim-statuses`).
- Migration prod chạy khi deploy như thường lệ (bảng `warranty_claims` nhỏ, UPDATE nhanh).
