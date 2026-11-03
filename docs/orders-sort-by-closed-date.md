# Sắp xếp trang đơn theo Ngày chốt

Ngày chốt thiết kế: 2026-08-26. Nhánh: `feat/orders-sort-closed-date`.

## 1. Bài toán

Toolbar trang đơn hiện chỉ có một nút đảo chiều `created_at` (Mới → Cũ / Cũ → Mới).
Sale cần lấy ra **các đơn vừa mới chốt xong** để xử lý tiếp (lên đơn, đóng gói).
Ngày tạo không phục vụ được việc này: khoảng cách trung bình từ lúc đơn đổ về đến
lúc sale chốt là **4,1 giờ** (đo prod 30 ngày, xem §2), nên thứ tự theo ngày tạo
khác hẳn thứ tự theo ngày chốt.

Bốn tuỳ chọn cần có:

| Giá trị `sort` | Nhãn |
|---|---|
| `oldest` | Ngày tạo: Cũ → Mới |
| `newest` (mặc định) | Ngày tạo: Mới → Cũ |
| `closed_oldest` | Ngày chốt: Cũ → Mới |
| `closed_newest` | Ngày chốt: Mới → Cũ |

Phạm vi: trang **Đơn hàng**, **Đơn KOC**, **Đơn Landing**. Trang **Vận đơn**
CỐ Ý không đưa tuỳ chọn mới ra FE (backend vẫn hiểu giá trị nếu ai đó gửi tay,
vì cả 4 endpoint dùng chung helper `apply_orders_sort`).

## 2. Vì sao phải thêm cột mới

Hệ thống KHÔNG có mốc "ngày chốt" ở bất kỳ đâu:

- `orders` chỉ có `created_at`, `kiotviet_purchase_date`, `return_date`.
- Trạng thái chốt nằm ở `order_assessments.sales_status_code` — chỉ là **trạng
  thái hiện tại**, không mang mốc thời gian.
- Thời điểm đổi trạng thái được ghi rời rạc ở `order_journey_events`
  (`event_type='sales_status_changed'`), có từ 2026-04-01.

Đo trên prod (30 ngày tính tới 2026-08-26, 8.494 đơn):

| Chỉ số | Giá trị |
|---|---|
| Đơn có log chuyển sang nhóm chốt | 3.245 (38%) |
| Trung bình chốt sau khi đơn đổ về | 4,1 giờ (`closed`) |
| Số đơn chốt trong vòng 5 phút kể từ lúc tạo | 397 / 3.245 |

62% đơn còn lại (đơn sàn, đơn sync KiotViet, đơn import) không đi qua luồng CRM
nên **không có ngày chốt** → `sales_closed_at` NULL. Xem §5 về cách xếp NULL.

## 3. Đặt cột ở đâu — `orders`, không phải `order_assessments`

Ứng viên tự nhiên là `order_assessments` (nơi giữ `sales_status_code`). Nhưng
trang Đơn hàng là **endpoint nóng nhất hệ thống** và `ORDER BY` trên bảng join
không dùng được index. Đo bằng EXPLAIN trên prod với hai cột tương đương:

| Truy vấn (LIMIT 50, `deleted_at IS NULL`) | Kế hoạch | Cost |
|---|---|---|
| `ORDER BY order_assessments.next_followup_at DESC` | Seq Scan ×2 + Hash Join + Sort | **117.179** |
| `ORDER BY orders.kiotviet_purchase_date DESC` | Index Scan + Incremental Sort | **62** |

```sql
EXPLAIN SELECT o.id FROM orders o
LEFT JOIN order_assessments a ON a.order_id = o.id
WHERE o.deleted_at IS NULL
ORDER BY a.next_followup_at DESC, o.id DESC LIMIT 50;
```

→ Cột nằm trên `orders`: `Order.sales_closed_at`. Đây là **mốc vòng đời của
đơn**, cùng họ với `kiotviet_purchase_date` / `vat_invoice_notified_at`, nên chỗ
này hợp lý về mặt mô hình chứ không chỉ vì hiệu năng. Đánh đổi: giá trị được suy
ra từ `sales_status_code` bên `order_assessments` nên phải ghi đồng bộ — chỉ có
hai điểm ghi, liệt kê ở §4.

## 4. Luật ghi `sales_closed_at`

Nhóm chốt = `CLOSED_SALES_STATUS_CODES` = `closed`, `closed_express`,
`closed_scheduled_date`, `created_order` (dùng lại đúng hằng số của Báo cáo Sale;
hằng số được dời về `crm_catalog.py` và re-export ở `reporting/shared.py` để tầng
service import được mà không phải gọi ngược vào tầng báo cáo).

Ghi mốc khi **chuyển từ NGOÀI nhóm chốt vào TRONG nhóm chốt**:

- `no_answer_2` → `closed`: ghi mốc = thời điểm đổi. ✅
- `closed` → `created_order`: **KHÔNG** đổi mốc (đi trong nội bộ nhóm). ✅
- `closed` → `not_buying` → `closed`: ghi lại mốc MỚI. Đúng nghĩa "vừa chốt
  xong"; đơn huỷ rồi chốt lại phải nổi lên đầu danh sách.
- Ra khỏi nhóm chốt: **giữ nguyên** mốc cũ, không xoá — cột là dấu vết lịch sử,
  và xoá đi sẽ làm đơn biến mất khỏi kết quả sắp xếp một cách khó hiểu.

Hai điểm ghi:

1. `OrderServiceHelpers._upsert_order_assessment` — luồng CRM chính (sale đổi
   trạng thái). Đặt cạnh chỗ ghi `order_journey_events` để mốc và log luôn khớp.
2. `OrderServiceHelpers.duplicate_order` với `default_sales_status` — luồng
   "Khôi phục đơn" preset thẳng `closed`, không đi qua `_upsert_order_assessment`.

## 5. Sắp xếp và NULL

62% đơn có `sales_closed_at` NULL. `NULLS LAST` ở **cả hai chiều** — đơn chưa chốt
luôn xuống cuối, vì người dùng chọn sắp xếp theo ngày chốt là đang tìm đơn ĐÃ chốt.
(Mặc định của Postgres là `NULLS FIRST` cho `DESC`, sẽ đẩy 62% đơn NULL lên đầu.)

Vì `NULLS LAST` không đối xứng qua việc quét ngược index, cần **hai** index:

```
ord_sales_closed_desc_idx : (sales_closed_at DESC NULLS LAST, id DESC) WHERE deleted_at IS NULL
ord_sales_closed_asc_idx  : (sales_closed_at ASC  NULLS LAST, id ASC)  WHERE deleted_at IS NULL
```

Cả hai đều partial theo `deleted_at IS NULL` để khớp `_base_orders_queryset`.

## 6. Backfill

Management command `backfill_order_sales_closed_at`, dựng lại đúng luật §4 từ
`order_journey_events`: lấy event **mới nhất** có `to_code ∈` nhóm chốt và
(`from_code` NULL hoặc `from_code ∉` nhóm chốt).

- Chạy theo lô (`--batch-size`, mặc định 5.000) bằng `bulk_update`, có `--dry-run`.
- Chỉ ghi đè khi ô đang NULL trừ khi truyền `--force`.
- Dữ liệu chỉ có từ **2026-04-01** — đơn cũ hơn sẽ mãi NULL. Đây là giới hạn của
  dữ liệu, không phải lỗi: đơn trước mốc đó không có log chuyển trạng thái nào.

## 7. Thứ tự deploy

1. `migrate` — `0138` thêm cột (`ADD COLUMN ... NULL`, tức thì trên PG11+),
   `0139` tạo 2 index `CONCURRENTLY` (`atomic=False`). **Chạy giờ thấp điểm.**
   Đứt giữa chừng để lại index INVALID → `DROP INDEX CONCURRENTLY` rồi migrate lại.
2. Roll code backend + frontend.
3. Chạy `python manage.py backfill_order_sales_closed_at` (một lần).

Migrate TRƯỚC roll code: code mới đọc/ghi cột, cột phải có sẵn. Ngược lại thì
không sao — cột NULL trên code cũ là vô hại.

## 8. Việc còn nợ

- [ ] Đo `EXPLAIN ANALYZE` thật trên prod sau khi có cột + index, với bộ lọc điển
      hình của trang Đơn hàng (không chỉ `deleted_at IS NULL` trần).
- [ ] Sau backfill: đối chiếu số đơn có `sales_closed_at` với số đơn có event
      chốt trong `order_journey_events` — hai số phải bằng nhau.
- [ ] Cột "Ngày chốt" mới thêm vào bộ chọn cột KHÔNG bật mặc định. Nếu sale phàn
      nàn không thấy, hướng dẫn bật qua hộp thoại chọn cột.

## 9. Tiến độ

| Hạng mục | Trạng thái |
|---|---|
| Thiết kế + đo prod | ✅ |
| Model + migration + index | ✅ |
| Luật ghi ở 2 điểm | ✅ |
| Command backfill | ✅ |
| `_parse_sort` + `apply_orders_sort` cho 4 endpoint | ✅ |
| Serializer + cột "Ngày chốt" | ✅ |
| Dropdown 4 tuỳ chọn ở FE (3 trang) | ✅ |
| Test backend + frontend | ✅ |
| Deploy prod + backfill | ⬜ chờ merge |
