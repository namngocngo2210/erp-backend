# Giảm 200k cho dòng SL 2 & Foxia `message` = `product_raw` (2026-09-29)

## 1. Dòng hàng SL 2 luôn giảm 200k

**Luật:** một dòng hàng có `quantity == 2` được giảm 200.000đ cho cả dòng.
`OrderItem.discount` là tiền giảm **mỗi sản phẩm** (`line_total = (unit_price − discount) × quantity`)
nên giá trị lưu là **100.000**. Kẹp ≤ đơn giá để thành tiền không âm.

| Luồng | Hành vi |
|---|---|
| Landing ingest (`create_order_from_landing` — landing, API, Pancake) | Dòng khớp catalog có SL 2 → `discount = 100k`, `line_total`, `total_amount`, `total_after_discount`, `landing_confirmed_amount`, snapshot CREATED đều là số sau giảm. |
| Ingest có `total_amount` / `total_after_discount` tường minh | **Không** tự giảm — payload là nguồn sự thật (giữ quy ước spec 2026-08-28). |
| Form tạo/sửa đơn (`OrderFormItemsSection`) & bảng hàng hoá chi tiết đơn (`BlockProducts`) | Đổi SL sang 2 → tự điền giảm 100k (ghi đè cả chế độ %). Rời SL 2 mà giảm giá vẫn đúng 100k → gỡ. Sale sửa tay sau đó được giữ. |

Hằng số trùng nhau ở `backend/apps/orders/domain/line_discount.py` và
`frontend/src/features/orders/order-form/order-form-helpers.ts` (`applyTwoUnitLineDiscount`).

## 2. Foxia: `message` = `product_raw`

Payload đẩy cơ hội sang Foxia trước đây gửi `message` = SKU các dòng hàng. Nay gửi
`order.product_raw` (mô tả sản phẩm khách chọn trên landing) — hiện ở ô "Khách hàng ghi chú"
bên Foxia. Rỗng/chỉ khoảng trắng → bỏ hẳn key `message`. Log `worker-foxia` không còn in `sku=`.

## Tiến độ

- [x] BE: luật giảm giá dòng SL 2 ở ingest + test
- [x] BE: Foxia `message` = `product_raw` + cập nhật test
- [x] FE: tự điền giảm giá khi đổi SL (form đơn + BlockProducts) + test
- [ ] Không áp: hoá đơn đồng bộ từ KiotViet và import Lark/Excel (không đi qua `create_order_from_landing`, giá do nguồn chốt).
- [ ] Chưa làm: backfill đơn cũ — không backfill, chỉ áp cho đơn mới.
