# Audit chi tiết — Database production `desi` (2026-08-19)

> Báo cáo gốc của luồng audit DB. Bản tổng hợp: `../audit-2026-08-19-full-stack.md`.
> Nguồn số liệu: MCP prod-db (user `claude_ro`), PostgreSQL 16.14, chỉ SELECT/health.
> Mọi đề xuất đã đối chiếu code Django trước khi kết luận (bài học audit trước:
> idx_scan=0 không đủ để drop).

## 1. Sức khoẻ tổng quan

| Hạng mục | Kết quả |
|---|---|
| Connections | 26–28 / max_connections=**100** (không còn 50 như sự cố 30/07) — PgBouncer gánh tốt |
| Vacuum wraparound / invalid index / constraint | Sạch |
| Cache hit index | 99.8% — tốt |
| Cache hit **table** | **87.0% — dưới ngưỡng 95%**. Thủ phạm: `orders` 79.2% (543M blocks disk), `facebook_ad_account_daily_spends` 83.1%, `audit_logs` 52.4%. shared_buffers = 1GB / DB 7GB, VPS chung ads-crm |
| Sequence | Tool bị chặn quyền; đã bù bằng kiểm tra kiểu cột: **toàn bigint → rủi ro tràn = 0** |
| Bloat | Không index bloat. `audit_logs` heap 3.38GB là payload thật (avg row ~1336B, bloat ~15%), retention 90 ngày đang co (~11/2026) — đúng quyết định MR !1236, **không repack** |

```sql
SELECT current_user, version(), (SELECT count(*) FROM pg_stat_activity), current_setting('max_connections');
SELECT stats_reset FROM pg_stat_database WHERE datname = current_database();  -- NULL = chưa từng reset
SELECT count(*), round(avg(pg_column_size(audit_logs))) FROM audit_logs TABLESAMPLE SYSTEM (1);
SELECT relname, heap_blks_read, heap_blks_hit, round(100.0*heap_blks_hit/GREATEST(heap_blks_hit+heap_blks_read,1),1)
FROM pg_statio_user_tables WHERE heap_blks_read + heap_blks_hit > 100000 ORDER BY heap_blks_read DESC LIMIT 10;
```

## 2. Top queries — BỊ CHẶN QUYỀN (hạn chế quan trọng)

`pg_stat_statements` trả `<insufficient privilege>` cho toàn bộ query text (thiếu
`pg_read_all_stats`). Chỉ thấy số ẩn danh, tích luỹ từ đầu: query nặng nhất 150k calls ×
205ms mean = 8.6h tổng; một query 934 calls × **2650ms mean** trả 6M dòng — dáng
export/report, soi khi có quyền.

**Đề xuất 1 phút (chạy tay prod):** `GRANT pg_read_all_stats TO claude_ro;` — chỉ mở
stats, không mở dữ liệu. Không có nó, mục top-query của mọi audit sau đều mù.

## 3. Bảng lớn & index (ĐÃ đối chiếu code)

Top size: `audit_logs` 4.1GB · `orders` 1.9GB (heap 714MB / **idx 1231MB**) · `customers`
561MB (heap 128MB / **idx 432MB**) · `order_items` 250MB · `cnv_sync_records` 142MB ·
`order_assessments` 131MB · `customer_segment_snapshot` 111MB (đã co từ 523MB — repack
sau !1236 có vẻ ĐÃ chạy).

### 3a. Index "idx_scan=0" nhưng CODE CÒN SỐNG — KHÔNG drop

| Index (0 scans) | Call site sống |
|---|---|
| `order_product_upper_trgm_idx` 100MB | `repositories.py:636` product__icontains (0109 tạo để phục vụ nó) |
| `order_phone_trgm_idx` 47MB | `repositories.py:1276` phone__contains + comment models.py:602 |
| `order_ext_order_id_trgm_idx` 37MB | `repositories.py:1265` external_order_id__contains |
| `ord_item_name/sku_trgm_idx` 63MB | `repositories.py:632` |
| `cust_phone_raw_trgm_idx` 37MB | `customer_resale_service.py:150` |
| 5 index audit_logs ~550MB | `audit_views_impl.py:24` filterset — công cụ điều tra |

Nhánh search hiếm chạy nên scan ≈ 0, nhưng drop là gãy chức năng.

### 3b. Index CHẾT THẬT — đã xác minh không còn call site (drop được, ~147MB)

1. **`ord_delivered_cust_cr_idx` 29MB** — bị `ord_gtc_customer_idx` (249M scans) thay
   100%: cùng cột (customer_id, created_at), cùng predicate `deleted_at IS NULL AND
   status=3` (chỉ khác DESC — btree quét 2 chiều). Chỉ tồn tại qua RunSQL (0059+squash),
   không trong Meta.indexes → drop bằng RunSQL, không đụng state. An toàn nhất.
   *(→ ĐÃ LÀM: MR chore/drop-dead-index-ord-delivered, migration 0131.)*
2. **`order_old_id_idx` 22MB** — old_id chỉ ghi/đọc thuộc tính, không có filter nào.
   Khai ở models.py:553 → RemoveIndex + DROP CONCURRENTLY mẫu 0109.
3. **`customer_code_phone_idx` 63MB** — không query cặp; lookup filter(code=) đã được
   `customer_code_phone_raw_idx` (6.3k scans) phục vụ cùng prefix.
4. **`customer_phone_raw_idx` 33MB** — không exact-match phone_raw; mọi lookup dùng
   phone_normalized.

### 3c. Phát hiện đáng giá hơn cả drop: search khách hàng seq-scan 756k dòng

`customers/repositories.py:13-15` dùng `full_name__icontains | code__icontains` → Django
dịch thành `UPPER(col) LIKE` — **không dùng được** GIN trgm bare-column, nên
`cust_fullname_trgm_idx` (18MB) và `cust_code_trgm_idx` (37MB) 0 scans dù "code có dùng
cột". Repo đã giải bài này cho orders bằng functional index (`order_name_upper_trgm_idx`
— 115k scans). **Đề xuất:** thay 2 index bare bằng
`GinIndex(OpClass(Upper("full_name"/"code"), gin_trgm_ops))` — vừa hết 55MB index chết,
vừa hết seq-scan màn search khách.

### 3d. Index trùng lặp

112 cặp "covered-by" là auto-FK-index bị composite che — planner vẫn chọn bản FK nhỏ hơn
(orders_order_customer_id 753M scans) → **bỏ qua, không drop**. Trùng **tuyệt đối** (cùng
cột cùng thứ tự, Meta.indexes lặp unique constraint) đáng dọn trong MR chore:
`fb_dailyspend_acc_date_idx` (bảng ghi nóng), `gads_dailyspend_acc_date_idx`,
`gads_camp_spend_cd_idx`, `hr_att_evt_emp_date_idx`, `stockitem_wh_idx`/`stockitem_prod_idx`.

### 3e. FK thiếu index

Chỉ `product_id` trên các partition stock_movements (index cha là (warehouse_id,
product_id, …)). Bảng vài MB, luôn lọc kèm warehouse → không cần làm.

## 4. Bloat & autovacuum

| Bảng | dead % | Ghi chú |
|---|---|---|
| orders | 1.6% | override 0.05 đã áp (!1236) — ổn |
| customers | 1.5% | fillfactor=85 có tác dụng trên page mới: HOT 638/1321 ≈ 48% (baseline 1.9%). Nhưng idx 432MB/heap 128MB nguyên → **nợ repack customers của !1236 CHƯA trả** |
| customer_segment_snapshot | 0.4% | heap 523→62MB — nợ repack #2 có vẻ đã trả + skip_unchanged hiệu quả; xác nhận với người chạy |
| audit_logs | 6.4% | insert-only, retention chạy đúng — để yên |
| **order_assessments** | **8.1%, chưa từng autovacuum** | 39k dead / ngưỡng ~97k (scale 0.2) → thêm override 0.05 như 3 bảng kia |
| cnv_sync_records | 6.9% | autovacuum chạy đều (55 lần) — ổn |

Settings toàn cục mặc định hợp lý; chiến lược per-table override đang đúng hướng.

## 5. Migration hygiene (phía code)

- Số migration: orders **133** · users 38 · inventory 37 · customers 34 · warranty 22 ·
  channels_core 21 · kiotviet 16.
- orders có squash `0001_squashed_0089` nhưng **90 file gốc 0001–0089 vẫn nằm cạnh** —
  dọn khi mọi env đã qua squash. ⚠️ KHÔNG đề xuất squash lần 3 (runbook 2026-07-26 đã
  ghi lý do).
- **3 cặp trùng số trong orders: 0001, 0112, 0121** — mầm lỗi khi conflict merge, đánh
  số lại lần chạm tới.
- 71 migration có RunPython; backfill nặng cần nhớ khi restore/dựng env:
  orders/0110_backfill_age_group, orders/0128_copy_recall_entries (**không idempotent**
  — MR !1229), orders/0079, inventory/0025, customers/0027, warranty/0006.
- Rác local: `apps/orders/migrations/backend/.pytest_cache/` (untracked) — xoá tay.

## 6. Kế hoạch (đã đối chiếu code, phân loại)

**Làm ngay:** (1) GRANT pg_read_all_stats (chạy tay prod); (2) MR drop
ord_delivered_cust_cr_idx ✅ đã tạo; (3) xoá rác pytest_cache.
**Chờ trả nốt nợ !1236 rồi làm:** (4) repack/VACUUM FULL customers (pg_repack KHÔNG có
trên prod → VACUUM FULL + lock_timeout=60s, giờ thấp điểm); (5) đo tiếp fillfactor sau đó.
**MR index đợt 2** (mẫu 0109, CONCURRENTLY + SeparateDatabaseAndState): (6) drop
order_old_id_idx + customer_code_phone_idx + customer_phone_raw_idx; (7) đổi 2 trgm
customers sang functional Upper() — item giá trị nhất về hiệu năng người dùng; (8) dọn 6
index trùng tuyệt đối.
**Dài hạn:** (9) autovacuum override order_assessments; (10) đo lại cache hit sau khi
cắt index, cân nhắc shared_buffers 1→2GB (xét RAM chung ads-crm); (11) squash orders đợt
2 + dọn 90 file pre-squash + sửa 3 cặp trùng số.

**Trùng !1236 — KHÔNG làm lại:** drop audit user_id idx; audit_entity_id_ts_idx;
fillfactor customers; autovacuum overrides 3 bảng; ANALYZE (17/08); quyết định
không-repack-audit_logs (số liệu hôm nay xác nhận đúng).

**Hạn chế:** không đọc được query text pg_stat_statements (quyền); counter tích luỹ từ
đầu đời DB — index mới (0109, 26/07) idx_scan thấp tự nhiên.
