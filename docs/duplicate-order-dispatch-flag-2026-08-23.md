# Đơn nhân bản đeo sẵn cờ "đã xuất kho" (2026-08-23)

## 1. Tóm tắt

Kho báo: nhân bản một đơn cũ → đẩy KiotViet → tạo vận đơn KShip → **quét mã ở kho
thì báo "đã xuất kho" dù mã vận đơn đó chưa xuất bao giờ**.

Nguyên nhân không nằm ở KShip hay KiotViet: nút **Nhân bản** copy nguyên trạng
thái giao hàng của đơn gốc sang bản sao, và guard chống xuất trùng của luồng quét
kho đọc thẳng field đó.

Phạm vi đo trên prod ngày 23/08: **7 đơn** mang cờ sai (3 đơn KShip ngày 21–22/08
là ca người dùng báo, 1 đơn 03/08, 3 đơn huỷ 29/07). Tồn kho **không sai** — đây
là cảnh báo sai, không phải sai sổ.

## 2. Cơ chế lỗi

`OrderServiceHelpers.duplicate_order` với `reset_progress=False` (đúng nút Nhân
bản) clone toàn bộ `OrderAssessment` của đơn gốc theo `ASSESSMENT_FIELDS`, trong
đó có `delivery_business_status_code`. Đơn gốc đã giao nên field này là
`dispatched` → bản sao ra đời đã đeo cờ, `order_assessments.created_at` lệch
`orders.created_at` chỉ 0,2 giây.

Khi kho quét mã vận đơn mới của bản sao:

| Đường đi | Nơi đọc cờ | Hệ quả |
|---|---|---|
| Quét trực tiếp (lookup) | `_is_dispatched` — `shipping_lookup_view.py` | Màn quét hiện "đã xuất kho trước đó" |
| Cron khớp mã chờ | `_has_dispatched_delivery_state` — `order_service_commands.py` | Ghi warning `already_dispatched` vào mã quét |

Cả hai chỗ đọc cờ **có chủ đích**: đơn không khớp được sản phẩm nào xuất kho với
`lines=[]` nên không sinh bút toán, sổ tồn một mình không đủ để biết đơn đã xuất.
Vì vậy fix phải nằm ở nguồn phát sinh cờ sai, không phải ở guard.

Cùng lớp lỗi với `is_export_vat` / `vat_link` — hai marker đã được loại khỏi bản
sao từ trước, chỉ sót nhóm assessment.

## 3. Bằng chứng

Ba cặp gốc/clone cùng khách, cùng số tiền:

| Đơn gốc | Bút toán gốc | Bản sao | Bút toán bản sao | Cờ bản sao |
|---|---|---|---|---|
| DH014994 (14/08) | sale −1 | DH015704 (22/08) | 0 | `dispatched` từ lúc tạo |
| DH012603 (20/07) | sale −1 | DH015706 (22/08) | 0 | `dispatched` từ lúc tạo |
| DH015402 (18/08) | sale −1 | DH015669 (21/08) | 0 | `dispatched` từ lúc tạo |

Cả 7 đơn đều có dòng hàng, **0 mã quét khớp, 0 bút toán tồn kho**.

## 4. Fix

- `NON_CLONED_ASSESSMENT_FIELDS` (`apps/orders/assessment_fields.py`) — danh sách
  field không được nhân bản; hiện có đúng `delivery_business_status_code`.
  `duplicate_order` bỏ qua các field này khi clone assessment.
- Phạm vi cố ý HẸP: chân dung khách, trạng thái chốt sale, lý do bom, đánh giá
  sau mua… vẫn copy y như trước. Chỉ trạng thái giao hàng bị loại vì chỉ nó chặn
  quét kho.
- Luồng **Khôi phục đơn huỷ** (`reset_progress=True`) không clone assessment nên
  không bị ảnh hưởng.

## 5. Backfill — migration `orders/0135`

`clear_cloned_dispatch_flags` đưa cờ về `NULL` cho đơn thoả **cả ba** điều kiện:

1. `delivery_business_status_code = 'dispatched'` (chỉ mã này chặn quét kho);
2. assessment sinh trong vòng 5 giây kể từ lúc tạo đơn — dấu vân tay của bản sao;
3. đơn chưa từng có bút toán tồn kho **và** chưa từng có mã quét nào khớp vào.

Điều kiện 3 phải đủ hai vế: "không có bút toán" một mình không chứng minh được
đơn chưa xuất (đơn `lines=[]`). Migration log ra danh sách `order_id` đã sửa —
chiều ngược là noop nên đó là dấu vết duy nhất.

**Cố ý KHÔNG đụng tới** cụm 103 đơn mang cờ `received` (18/04–15/05) cũng có
assessment sinh cùng lúc với đơn: mã `received` không chặn quét kho, thời gian và
nguồn phát sinh khác hẳn — cần điều tra riêng trước khi dọn.

## 6. Sau deploy

- Ba đơn KShip 21–22/08 quét lại được ngay sau khi migration chạy; không cần thao
  tác tay ở kho.
- Báo cáo chất lượng giao hàng: 7 đơn rời khỏi rổ "đã xuất kho" → con số này
  **giảm nhẹ**, đúng hướng (trước đây đếm khống).
- Nếu kho còn gặp "đã xuất kho trước đó" trên đơn mới nhân bản sau deploy: kiểm
  `order_assessments.delivery_business_status_code` của chính đơn đó trước, rồi
  mới nghi tới sổ tồn.
