# Tối ưu hiệu năng trang "Theo dõi resale"

> Trạng thái: Sprint 1 + 2 + 3 **DONE**
> Cập nhật: 2026-07-09
> Phạm vi: `apps/customers/customer_resale_service.py`, `apps/orders/models.py`, endpoint `GET /api/customers/resale`

## 1. Bối cảnh & triệu chứng

Trang theo dõi resale tải chậm. Mỗi lần load (fast path) bắn **4 query**: `facets` + `count()` + `listing` + `history`. Đo bằng `EXPLAIN (ANALYZE, BUFFERS)` trực tiếp trên prod (`desi-postgres`).

Quy mô dữ liệu thực:

| Bảng | Số dòng thực | `pg_stat.n_live_tup` |
|---|---|---|
| `orders` | 504.849 | 504.490 |
| `orders` (status=3 & active) | **455.161 (≈90%)** | — |
| `customers` | **735.432** | 5.500 ❌ (lệch) |

## 2. Chẩn đoán (bằng số đo)

| Query | Trước | Nguyên nhân trong plan |
|---|---|---|
| **Listing** (LIMIT 20) | **270–294 ms** | `Parallel Seq Scan` toàn bộ `orders` → `Sort` ~455k dòng theo `updated_at` chỉ để lấy 20 |
| **count()** | **526–565 ms** | Seq Scan 455k đơn + `Hash Join` toàn bộ 735k `customers` (chỉ để loại 4 khách deleted) |
| **facets** (DISTINCT staff) | **~108 ms** | Quét toàn scope rút ra ~14 sale (gần như cố định) |

### Nguyên nhân gốc

1. **Không có index phục vụ `ORDER BY updated_at DESC`.** Vì `status=3 AND deleted_at IS NULL` chiếm ~90% số đơn, filter này không lọc được gì → Postgres buộc seq scan + sort toàn bảng mỗi trang.
2. **Thống kê sai nghiêm trọng.** `customers` thực 735k nhưng `n_live_tup=5500`, `last_autoanalyze = NULL` → planner "mù". Đã chạy `ANALYZE orders; ANALYZE customers;` (an toàn). *Khuyến nghị vận hành: bật/kiểm tra autovacuum cho 2 bảng này.*
3. **JOIN `customers` trong count vô ích** (chỉ 4 khách deleted / 735k).
4. **Full-scan path** (lọc `segment`/`min_days`): `list(listing)` nạp toàn bộ ~455k `Order` vào Python rồi tính trong RAM → nguy cơ chậm vài giây→phút. Xem Sprint 3.

## 3. Sprint 1 — Partial index cho listing ✅ DONE

```sql
CREATE INDEX CONCURRENTLY ord_resale_delivered_upd_idx
ON orders (updated_at DESC, id DESC)
WHERE deleted_at IS NULL AND status = 3;   -- ~14 MB
```

- Khớp đúng filter + thứ tự sort → Postgres đi thẳng theo index, lấy 20 dòng, join customers bằng PK, **không seq scan, không sort**.
- **Kết quả: 270 ms → ~10 ms (thực thi thật ~0.6 ms), nhanh ~27 lần.**
- Code: thêm vào `Order.Meta.indexes` (`apps/orders/models.py`).
- Migration: `apps/orders/migrations/0077_order_resale_delivered_upd_idx.py` — dùng `SeparateDatabaseAndState` + `RunSQL ... CREATE INDEX CONCURRENTLY IF NOT EXISTS` (idempotent, `atomic = False`), vì index đã được tạo out-of-band trên prod để giảm chậm ngay.

Plan sau index:
```
Limit (actual time=0.158..0.618 rows=20)
  -> Index Scan using ord_resale_delivered_upd_idx on orders
Execution Time: 10.083 ms
```

## 4. Sprint 2 — Cache Redis cho count + facets ✅ DONE

`count()` (455k dòng) và `facets` (DISTINCT toàn scope) nặng nhưng đổi chậm → cache Redis TTL ngắn (theo đúng pattern `CustomerLoyaltyService`).

- `RESALE_CACHE_PREFIX = "resale_tracking:v1"`
- `count`: key theo `(q, date_from, date_to, sale, contact_status)`, TTL **60s**.
- `facets`: key theo `(q, date_from, date_to)`, TTL **300s**.
- Key dài (q tự do) được hash SHA1 để giới hạn độ dài.
- Test: thêm `cache.clear()` vào `setUp` của `test_resale_tracking_filters.py` (cô lập). **15/15 test pass.**
- Đánh đổi: tổng/facets có thể trễ tối đa TTL — chấp nhận được cho nghiệp vụ resale. Bump version prefix khi đổi cấu trúc filter.

## 5. Sprint 3 — Refactor full-scan path (segment / min_days) ✅ DONE (2026-07-09)

**Đã triển khai Phương án A**, tận dụng bảng rollup **đã có sẵn** `customer_segment_snapshot`
(`SegmentSnapshotService`, cron `rebuild_segment_snapshot` chạy mỗi 2h — dựng cho báo cáo/
reactivation, nay nối luôn vào trang resale). Không cần tạo bảng/migration mới.

Thay đổi (`customer_resale_service.py`):
- Bỏ hẳn nhánh `needs_full_scan`/`list(listing)`. `segment`/`min_days` giờ là **filter SQL** qua
  JOIN `Order → customer → segment_snapshot`:
  - `segment`: `customer__segment_snapshot__segment_code=<code>`.
  - `min_days`: `customer__segment_snapshot__last_purchase_at <= now() - min_days ngày`.
- Phân trang bằng SQL `LIMIT/OFFSET` như fast path; `count()` cache theo **toàn bộ** bộ filter
  (thêm `segment`, `min_days` vào key).
- **Nguồn sự thật duy nhất cho phân khúc = snapshot**: `_resale_row` lấy `segment_code` +
  `days_since_last_purchase` từ snapshot (bỏ `classify_segment` realtime per-row). Filter và
  hiển thị luôn khớp nhau.

Đánh đổi (chấp nhận được):
- Phân khúc trễ tối đa 1 chu kỳ rebuild (≤2h). Khách vừa có đơn đầu tiên mà **chưa** có snapshot
  sẽ hiện phân khúc trống và không lọt filter `segment` cho tới lần rebuild kế tiếp.
- `has_problem` của snapshot rộng hơn logic realtime cũ (gộp thêm tín hiệu từ `assessment`) → một
  số khách có thể đổi phân khúc so với trước; đây là hành vi **đúng hơn** về nghiệp vụ.

Kết quả: bỏ full-scan ~455k `Order` trong Python → còn 1 truy vấn phân trang có LIMIT + JOIN
snapshot (probe theo PK). Test: `apps/customers/tests/test_resale_tracking_filters.py` (khôi phục
+ mở rộng: filter phụ thuộc snapshot, hiển thị theo snapshot) — **9/9 pass**.

<details><summary>Bối cảnh thiết kế ban đầu (giữ lại)</summary>

### Vấn đề
`segment_code` và `days_since_last_purchase` (`customer_resale_service.py:271-315`) được tính trong Python từ **toàn bộ lịch sử mua** của khách, nên khi user lọc theo phân khúc/số ngày, code phải:
- `list(listing)` → nạp ~455k `Order` (kèm `select_related` 6 bảng) vào RAM;
- gọi `_resale_order_history_by_customer` → query lại lịch sử của tất cả khách;
- lọc + phân trang trong Python.

→ Chậm vài giây→phút, tốn RAM, không scale.

### Hướng thiết kế (đề xuất, cần chốt)

**Phương án A — Bảng tổng hợp `customer_resale_stats` (materialized rollup).** Khuyến nghị.
- Cột: `customer_id (PK)`, `delivered_count`, `total_spent`, `last_purchase_at`, `has_problem`, `segment_code`, `updated_at`.
- Cập nhật theo event: khi đơn chuyển trạng thái giao thành công / hủy / hoàn (signal hoặc Celery task), recompute 1 khách. Có management command backfill + tác vụ định kỳ đối soát.
- Trang resale JOIN sang bảng này → `segment_code`/`days_since_last_purchase` lọc & phân trang **ở SQL**, bỏ hẳn full-scan. `days_since_last_purchase` = `now() - last_purchase_at` (tính trên SQL).
- Index: `(segment_code, last_purchase_at)`.

**Phương án B — Materialized View + `REFRESH CONCURRENTLY` định kỳ.** Đơn giản hơn nhưng dữ liệu trễ theo chu kỳ refresh, và refresh toàn bộ vẫn nặng. Phù hợp nếu chấp nhận trễ 5–15 phút.

**Phương án C — Annotate bằng subquery/window trong ORM.** Không khả thi sạch vì `classify_segment` là logic Python nhiều nhánh (`segmentation.py`); sẽ phải dịch toàn bộ sang SQL `CASE`, khó bảo trì.

### Việc cần làm (khi triển khai Sprint 3)
- [x] Chốt phương án (A — tái dùng `customer_segment_snapshot` sẵn có, không tạo bảng mới).
- [x] Sửa `list_resale_tracking` để lọc `segment`/`min_days` xuống SQL, bỏ nhánh full-scan.
- [x] Dùng snapshot làm nguồn hiển thị `segment_code`/`days_since_last_purchase`.
- [x] Unit test: filter phụ thuộc snapshot, hiển thị theo snapshot, min_days.

</details>

## 6. Ghi chú vận hành
- Index `ord_resale_delivered_upd_idx` đã sống trên prod (tạo `CONCURRENTLY`); migration 0077 idempotent nên deploy an toàn (no-op nếu đã tồn tại).
- Theo dõi tỉ lệ cache hit của `resale_tracking:*` trên Redis sau khi deploy Sprint 2.
- Cân nhắc chỉnh `autovacuum`/`ANALYZE` định kỳ cho `orders`, `customers` để planner không lệch.
- **Sprint 3 phụ thuộc cron `rebuild_segment_snapshot` (mỗi 2h).** Nếu beat schedule bị tắt,
  phân khúc trang resale sẽ đứng yên — đảm bảo task này luôn chạy trên prod.
- Cân nhắc index `customer_segment_snapshot (segment_code, last_purchase_at)` nếu lọc phân khúc
  hiếm (vd S6) vẫn chậm ở quy mô lớn (hiện dựa vào partial index `orders` dẫn dắt + probe PK).
