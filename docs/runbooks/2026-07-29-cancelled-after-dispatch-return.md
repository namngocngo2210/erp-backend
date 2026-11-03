# Runbook: đơn huỷ sau khi đã xuất kho

Ngày: 2026-07-29 · Spec: `docs/superpowers/specs/2026-07-29-cancelled-after-dispatch-return-design.md`

## 1. Hành vi thay đổi

| | Trước | Từ nay |
|---|---|---|
| Huỷ đơn ĐÃ quét xuất | ghi `sale_reversal`, cộng tồn ngay lúc huỷ | **không ghi gì**; đơn vào tab "Chờ hàng hoàn về" |
| Quét nhập hoàn đơn đã huỷ | bị chặn ("tồn đã được tự động hoàn lại") | chạy bình thường, cộng tồn đúng một lần |
| Huỷ đơn CHƯA quét xuất | no-op | không đổi (quét xuất vẫn bị chặn `order_cancelled`) |
| Huỷ đơn có bản xuất lại `.NN` | chuyển sổ sang đơn mới | không đổi |

**Ảnh hưởng số liệu:** tồn sellable từ nay thấp hơn cách tính cũ đúng bằng lượng hàng của
các đơn huỷ đang trên đường về. Đây là số đúng — hàng đó chưa bán được cho ai. Không có cú
sốc tại thời điểm deploy: đơn huỷ **trước** ngày deploy giữ nguyên sổ cũ.

## 2. Vận hành hằng ngày

Tab **Lịch sử nhập xuất → Chờ hàng hoàn về** liệt kê đơn huỷ mà hàng còn ngoài kho.

- Hàng về, kho quét nhập hoàn → dòng tự chuyển "Hàng đã về". Không ai phải bấm gì.
- Hàng chắc chắn không về (khách giữ, thất lạc, huỷ tại chỗ) → bấm **Đóng**, chọn lý do.
  Cần quyền `returns:approve`. Đóng **không** cộng tồn: hàng đã rời kho vĩnh viễn nên tồn
  hiện tại đã đúng.
- Đóng nhầm mà hàng về sau: cứ quét nhập hoàn như bình thường, dòng tự quay lại "Hàng đã về".

Cron `inventory-alert-awaiting-return` (08:30 giờ VN) bắn Lark danh sách đơn chờ quá 14 ngày.
Nó chỉ nhắc, không tự đóng đơn nào.

## 3. Câu SQL đối chiếu

Đơn đang chờ hàng về, kèm số ngày:

```sql
SELECT w.order_id, o.shipping_code, o.invoice_code, w.status,
       w.cancelled_at, now()::date - w.cancelled_at::date AS days_waiting
FROM cancelled_dispatch_watches w
JOIN orders o ON o.id = w.order_id
WHERE w.status = 'waiting'
ORDER BY w.cancelled_at;
```

Sổ kho của một đơn (thay mã ở CTE):

```sql
WITH o AS (
    SELECT id FROM orders
    WHERE '<mã vận đơn / hoá đơn>' IN (invoice_code, shipping_code, order_code, old_invoice_code)
)
SELECT m.occurred_at, m.movement_type, m.qty_delta, m.balance_after, p.sku, m.note
FROM stock_movements m
JOIN products p ON p.id = m.product_id
WHERE (m.reference_type = 'order' AND m.reference_id IN (SELECT id FROM o))
   OR (m.reference_type = 'return_entry'
       AND m.reference_id IN (SELECT id FROM stock_return_entries
                              WHERE source_order_id IN (SELECT id FROM o)))
ORDER BY m.occurred_at, m.id;
```

## 4. Khi một đơn kẹt không quét nhập hoàn được

Đọc hai con số của đơn trước khi động vào sổ:

- `outstanding_by_key(order_id)` — phần đã trừ tồn chưa hoàn.
- `pending_cancel_release_by_key(order_id)` — phần đã auto-hoàn lúc huỷ mà chưa được bù
  (chỉ có ở dữ liệu trước 2026-07-29).

Hạn mức mà phiếu hoàn được phép cộng = tổng hai con số đó, trừ đi phần các phiếu hoàn trước
đã ghi sổ. Thông báo lỗi của guard nói rõ còn bao nhiêu đơn vị.

Ba lý do thường gặp:

1. **Đơn huỷ chưa từng xuất kho** — "hàng chưa bao giờ rời kho nên không có gì để nhập hoàn".
   Đúng: hàng không có ở ngoài, cộng vào là tồn ảo.
2. **Đã có phiếu hoàn khác ghi sổ cho đơn đó** — kiểm tra `stock_return_entries` theo
   `source_order_id`.
3. **Sổ của đơn đã được chuyển sang bản `.NN`** (`Chuyển sổ xuất sang đơn xuất lại`) — quét
   nhập hoàn phải gắn vào đơn `.NN`, không phải đơn gốc.

Sổ là append-only: đừng UPDATE/DELETE `stock_movements` để "chữa". Sai sót sửa bằng bút toán
đảo qua phiếu kho.

## 5. Bút toán mới cần biết khi đọc sổ

| Note | Ý nghĩa |
|---|---|
| `Thu hồi tồn đã hoàn tự động khi huỷ` | Đơn huỷ CŨ đã auto-hoàn tồn, nay hàng mới về: `return_in +n` đi kèm `SALE −n` này để tồn không cộng hai lần. Không phải một lần hàng rời kho — nó nằm trong `ADMIN_SALE_NOTES` nên không tính vào sản lượng xuất. |
