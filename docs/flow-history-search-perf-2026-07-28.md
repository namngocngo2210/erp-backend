# Tìm theo mã ở Lịch sử nhập xuất: bỏ seq scan và bỏ 4 lần dò thừa

Ngày: 2026-07-28
Nhánh: `perf/flow-history-order-search`

## 1. Đo trước khi sửa

`EXPLAIN (ANALYZE, BUFFERS)` trên prod cho câu ghép (tìm đơn + gộp sổ):

```
Execution Time: 66 ms   ·   Planning Time: 21 ms
  Bitmap Heap Scan on orders  60 ms   ← BitmapOr 3 index trigram, trả về 2 dòng
  Append (stock_movements)     3.8 ms
```

Nhưng câu EXPLAIN đó **viết tay bằng `ILIKE`**, còn Django sinh ra:

```sql
UPPER("orders"."order_code"::text) LIKE UPPER('%…%')
```

Ba index `gin_trgm_ops` nằm trên **cột thô** (`order_code`, `invoice_code`,
`shipping_code`), mà `UPPER(col)` là biểu thức khác nên planner **không dùng
được index nào** → seq scan cả bảng `orders`. Nghĩa là chi phí thật còn cao hơn
60ms đo được ở trên.

## 2. Hai nguyên nhân, sửa cả hai

**(a) Sai dạng câu — index có mà không dùng được.** Thêm lookup
`__ilike_contains` (`apps/common/db_lookups.py`) sinh thẳng `col ILIKE %s`.
pg_trgm hỗ trợ cả LIKE lẫn ILIKE nên **không cần thêm index mới**, chỉ là dùng
đúng thứ đã trả tiền. Lookup tự escape `%`, `_`, `\` — mã TikTok dạng
`HDTTS_5852…` có dấu gạch dưới, không escape thì nó thành ký tự đại diện.

Đặt tên lookup tránh mọi tiền tố `i` mà backend Postgres coi là case-insensitive
(`icontains`, `iexact`, …), nếu không `lookup_cast` lại bọc `UPPER()` quanh vế
trái và ta quay về đúng chỗ cũ.

**(b) Dò lặp 5 lần cho mỗi lượt dựng bảng.** `base_queryset` được gọi lại ở:
danh sách nhóm, đếm tổng, dòng SKU, nhãn kho/người thực hiện, tổng SL. Mỗi lần
chạy lại đúng câu tìm đơn đó.

- `resolve_order_ids()` + `with_resolved_order()`: tầng page giải **một lần**,
  truyền `order_ids` xuống.
- `enrich_orders` truyền thẳng id của trang hiện tại (chặt hơn ô tìm) nên không
  phải dò lại lần nào.

Kết quả: **1 lần dò trigram cho mỗi lượt dựng bảng** thay vì 5, và lần đó dùng
được index.

## 3. Kiểm thử

`test_order_search_resolves_once_per_page` / `test_sku_search_resolves_once_per_page`
đếm thẳng số câu SQL chứa `ILIKE` trong một lần gọi `orders_page`/`skus_page` và
chốt ở 1. Đây là loại hồi quy im lặng — thêm một chỗ gọi `base_queryset` là số
lần dò tăng lại mà không ai thấy.

## 4. Chưa làm

- Cùng lỗi dạng câu (`icontains` trên cột có index trigram) còn ở các màn khác:
  danh sách Đơn hàng, `return_history_service`, `pending_scan_service`. Sửa
  riêng, cùng cách, sau khi đo lại từng chỗ.
- `Planning Time` 21ms mỗi câu trên bảng partition vẫn còn — chỉ giảm được bằng
  cách giảm số câu, không phải bằng index.
