# Danh sách đơn: tổng & sắp xếp theo "Tổng sau giảm giá" (2026-09-25)

Nhánh: `feat/order-list-total-sum`

## Yêu cầu

1. Các trang danh sách đơn hiển thị **tổng cột "Tổng sau giảm giá"** ở dưới cùng bảng.
2. Thêm sắp xếp **Tổng sau giảm giá: Cao → Thấp** và **Thấp → Cao**.

Phạm vi: `/api/orders` (Đơn hàng), `/api/orders/koc` (KOC), `/api/orders/landing`
(Landing: Tổng hợp / Sheet 1 / Sheet 2), `/api/orders/shipments` (chỉ backend — trang
Vận đơn không có cột tiền). Sheet 3 (Tư vấn) giữ thứ tự riêng của nó, không đổi.

## Thiết kế

### Tổng: toàn tập khớp bộ lọc, không chỉ trang đang xem

Mỗi trang tối đa 100 dòng, nên cộng riêng trang hiện tại dễ gây hiểu nhầm. Backend
trả `total_after_discount_sum` = SUM trên **toàn tập khớp bộ lọc** (mọi trang);
chân bảng hiện số này, kèm "Trang này: …" khi danh sách có nhiều trang.

- `OrderRepository._totals_with_cache` gộp `COUNT(id)` + `SUM(total_after_discount)`
  trong **một** aggregate, thay cho `base.count()` — cùng một lượt quét. Đo trên prod
  (~61k đơn hiệu lực): COUNT 213ms → COUNT+SUM 259ms (không thêm round-trip).
- Cache cùng khuôn + TTL với count cũ (`ORDERS_LIST_COUNT_CACHE_TTL`), khóa riêng
  `orders:list_totals:` (giá trị khác kiểu), lưu tuple để không buộc vào path module.
- Định dạng: chuỗi 2 số lẻ (`"123456.00"`) như các field tiền khác của
  `OrderListSerializer`; rỗng → `"0.00"`.
- `list_orders` / `list_orders_with_status_counts` trả `OrderListTotals` (frozen
  dataclass) thay cho `total_count` int; view dựng payload bằng `orders_totals_payload`.

### Sắp xếp

- `ORDERS_SORT_VALUES` thêm `amount_desc`, `amount_asc`; mệnh đề nằm trong
  `_ORDERS_SORT_CLAUSES` nên cả 4 endpoint dùng `apply_orders_sort` đều có ngay.
- Tie-break theo `id` cùng chiều → phân trang ổn định.
- Index `ord_total_after_disc_idx (total_after_discount, id) WHERE deleted_at IS NULL`
  (migration 0147, `AddIndexConcurrently`). Cột NOT NULL → **một** index quét ngược
  là đủ cho DESC (khác `sales_closed_at` cần 2 index vì NULLS LAST). Trước index:
  top-N heapsort trên seq scan ~350ms.

### Frontend

- `OrdersSortValue` + menu sắp xếp: nhóm "Tổng sau giảm giá" (có vạch ngăn giữa
  các nhóm), dùng chung cho desktop và menu "⋯" điện thoại.
- State `ordersAmountSum` (string | null) đi cùng đường với `ordersTotalCount`.
- `OrdersTableTotalsRow`: `<tfoot>` dính đáy, ô dưới cột "Tổng sau giảm giá";
  các cột ghim giữ đúng offset sticky. Chỉ hiện khi cột đó đang hiển thị.
- Điện thoại: `OrdersMobileTotalsBar` dưới danh sách thẻ.
- `null` (endpoint không trả tổng) → fallback cộng các dòng trang hiện tại (tính theo xu).

## Tiến độ

| Hạng mục | Trạng thái |
|---|---|
| BE: sort `amount_desc/asc` + index 0147 | ✅ |
| BE: `total_after_discount_sum` 4 endpoint + OpenAPI | ✅ |
| BE test `test_order_list_amount_totals.py` | ✅ |
| FE: menu sort, dòng tổng desktop + mobile, i18n vi/en | ✅ |
| FE test `orders-table-totals.test.tsx` | ✅ |

### Chưa làm / lưu ý

- Tổng toàn tập có thể trễ tối đa TTL giây (giống nhãn "tổng N đơn"); sửa inline
  một đơn không cập nhật tổng toàn tập cho tới lần load kế tiếp (tổng trang thì có).
- Migration 0147 chạy `CREATE INDEX CONCURRENTLY` — deploy giờ thấp điểm.
- Nhánh `fix/be-db-audit-2026-09-25` cũng có `0147/0148` cùng cha `0146`; nhánh merge
  sau cần `makemigrations --merge` (hoặc đổi dependency).
- Export Excel chưa có dòng tổng (ngoài phạm vi yêu cầu).

## Điều chỉnh 2026-09-25 (nhánh `fix/order-list-page-total`)

Feedback người dùng: chỉ cần **tổng theo trang** (các dòng đang hiển thị, theo page
size đang chọn), chỉ hiện con số — không cần tổng toàn tập.

- Gỡ `total_after_discount_sum` khỏi 4 endpoint list, gỡ `_totals_with_cache` /
  `OrderListTotals` — list quay lại `COUNT` như cũ (bớt SUM ~40ms mỗi lần load).
- FE: `OrdersTable` nhận `showTotals`; ô chân bảng chỉ hiện số = tổng các dòng
  trang hiện tại (`sumPageAmountAfterDiscount`), cập nhật ngay khi sửa inline.
- Giữ nguyên sort `amount_desc/asc` + index 0147.
- Mục "Tổng toàn tập…" ở phần Thiết kế phía trên KHÔNG còn hiệu lực.
