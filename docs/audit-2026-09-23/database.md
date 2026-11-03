# Audit Database prod `desi` — 2026-09-23 (chỉ đọc/đo)

## 0. Bối cảnh quan trọng
- Container prod thật là `desi-postgres` (postgres:16-alpine, PG 16.15), không phải `deploy-postgres-1` (chỉ chứa DB `crawler` FDW). DB `desi` 7,1GB.
- Role `claude_ro` của audit trước KHÔNG còn; phải dùng superuser với `default_transaction_read_only=on`.
- **Cluster prod vừa dựng lại 22/09/2026** (initdb 06:21 UTC, restore từ dump, host uptime từ 12:58+07). Hệ quả: mọi counter pg_stat_* chỉ tích luỹ ~20h; bloat "tự tốt lên" do restore; mất role/GRANT; image đổi `postgres:16` → `16-alpine` (musl) → collation `datcollversion` NULL, sort chuỗi khác; "Data page checksums are disabled". Không có runbook nào trong docs/.

## 1. Đối chiếu audit 08-19
| Mục | Trạng thái |
|---|---|
| GRANT claude_ro | Vô hiệu — role không còn |
| Drop `ord_delivered_cust_cr_idx`, `order_old_id_idx`, `customer_code_phone_idx`, `customer_phone_raw_idx` | ĐÓNG (`customers/migrations/0035_search_trgm_codify_drop_dead.py`) |
| C6 trgm customers vô dụng | ĐÓNG theo cách khác: `customers/repositories.py:19-24` dùng `__ilike_contains` (`common/db_lookups.py:20`); search 2,3s → 36ms. Nhưng còn sót 3 nơi (P1-2) |
| 6 index trùng tuyệt đối | ĐÓNG |
| autovacuum override `order_assessments` | CHƯA |
| Repack customers | ĐÓNG do restore |
| 3 cặp trùng số migration orders, 90 file pre-squash | CHƯA |

## 2. Phát hiện
### P0-1. Prod DB dựng lại 22/09 không runbook, mất role read-only, checksum tắt, backup không offsite
`/opt/desi-backup` chỉ 2 dump 22/09 (cùng host); metric `desi_backup_offsite_configured 0`. Sửa: runbook `docs/runbooks/2026-09-22-postgres-rebuild.md`; bật offsite trong `scripts/desi-backup.sh`; tạo lại role đọc (`GRANT pg_read_all_stats, pg_read_all_data`); lần recreate kế `POSTGRES_INITDB_ARGS="--data-checksums"`.

### P1-1. Image alpine/musl đổi ngữ nghĩa sort chuỗi
initdb "no usable system locales"; `datlocprovider=c`. Sửa: quyết định giữ alpine (ghi nhận) hoặc về `postgres:16` debian lần dump/restore kế (`docker-compose.prod.yml:334`, `docs/deploy-docker.md:118`).

### P1-2. Cùng lớp lỗi C6 còn sót: `icontains` vô hiệu GIN trgm → Parallel Seq Scan orders/customers
EXPLAIN prod: `UPPER(phone) LIKE UPPER('%..%')` → Parallel Seq Scan orders; `phone ILIKE` → Bitmap `order_phone_trgm_idx`.
- `warranty/claim_repository.py:32-33` (comment :29-30 "không có trgm" là SAI — `order_phone_trgm_idx` 22MB, `order_phone_norm_trgm_idx` 20MB có).
- `orders/application/services/contact_list_service.py:465-467` (full_name/phone_normalized/phone_raw icontains).
- `customers/customer_resale_service.py:142`.
Sửa: đổi 5 lookup sang `__ilike_contains`.

### P1-3. ETL ai_mart `khach_hang` 30 phút/lần aggregate toàn bảng `ai.don_hang` (560k) — mean-time cao nhất DB
pgss: 23 calls × 3,7s, temp 1,45GB; `pg_stat_database.temp_files` 843 / 7,7GB trong 20h. `ai_mart/infrastructure/sql/khach_hang.py:26-55,88`: lọc id chỉ ở ngoài cùng dù tập id đã có ở :11-22. Sửa: đẩy `WHERE khach_id IN (...)` vào CTE; `SET LOCAL work_mem='64MB'`.

### P1-4. Rebuild đêm `rebuild_all_marts` ~15 phút DB time, statement 230s, seq-scan bảng nóng, ép checkpoint
`ai.hoi_thoai` 230s, `ai.don_hang` 148s; orders seq_scan 309/75M tuples, order_items 96/27M, audit_logs 25/24M; checkpoints_req 12%, buffers_backend 3,39M > buffers_checkpoint 1,1M, 18GB WAL/20h. Sửa: UNLOGGED cho bảng ai.* derived; work_mem cục bộ; max_wal_size 4GB.

### P1-5. 5 model unique tuyệt đối + soft-delete → IntegrityError khi tạo lại cùng mã
`catalog/models.py:12,27` Product.sku; `:44,49` ProductGroup.code; `:67,74` ProductCategory.code; `hr/models.py:53,112` Employee.employee_code; `warehouses/models.py:5-8` Warehouse.code/name. Mẫu đúng: `customers/models.py:66-69`. Sửa: partial UniqueConstraint `deleted_at__isnull=True`.

### P2
- P2-1. autovacuum override `order_assessments` chưa áp (RunSQL ALTER TABLE SET scale_factor 0.05).
- P2-2. Index 0-scan (chờ ≥30 ngày trước khi drop): ứng viên `order_customer_created_idx` 17MB (bị 3 index cùng prefix che), `order_order_code_idx` 20MB, `order_shiptype_created_idx` 17MB, `order_item_order_cr_idx` 22MB (`orders/models.py:996`), `snap_lastbuy_spent_idx` 23MB; audit_logs 5 index 382MB 0 scan → giảm ~230MB (partial 90 ngày cho source_/action_). Giữ: trgm product/ext_order_id/phone, sales_closed, resale.
- P2-3. 37 cặp index trùng tuyệt đối (~26MB), chủ yếu FK-auto ⟷ Meta.indexes cùng cột: `orders/models.py:997 order_item_product_idx`, `wc_code_like_idx`/`wc_serial_like_idx` (unique đã tự sinh `_like`), `fb_ad_campaign_idx`/`fb_ad_page_idx`, order_need_*, returnentry_*, hr_*, team_*, rbac_*.
- P2-4. Settings PG: host 11GB RAM, container limit 2,44GB; `shared_buffers=1GB`; **`max_wal_size=1GB`**, **`random_page_cost=4`**, `effective_io_concurrency=1`, `track_io_timing=off`, `log_min_duration_statement=-1`, `log_lock_waits=off`. Sửa 1 lần recreate: `-c max_wal_size=4GB -c random_page_cost=1.1 -c effective_io_concurrency=200 -c track_io_timing=on -c log_min_duration_statement=2000 -c log_lock_waits=on`.
- P2-5. orders 49 index, idx/heap 1,70 (697MB/411MB); 4 GIN trgm mã cùng 1 ô search (`repositories.py:1354-1360`) có thể gộp.
- P2-6. Ghi từng dòng qua PgBouncer: `pending_shipment_scans` 106k UPDATE+SELECT/20h (`pending_scan_service.py:539-548`); `facebook_ad_account_daily_spends` 111k (`facebook_ads/services.py:397-420` update_or_create trong loop); `facebook_ads` 105k; `facebook_ad_campaigns.last_seen_at` 57k. Sửa: bulk_create(update_conflicts=True) như `ad_spend_sync.py:180-199`; update-if-changed.
- P2-7. `customers.code` trùng 7.069 mã/14.143 dòng active (unique bỏ ở migration 0011); lookup theo code ở `gateway_kship_customer_webhook.py:384-398` rủi ro trả nhiều khách. `orders.order_code` trùng là chủ ý (tách hoá đơn KiotViet) — không thêm unique. `orders (channel_id, external_order_id)` 2.655 cặp trùng active — cần xác nhận nghiệp vụ (`orders/models.py:239`).
- P2-8. Decimal tiền 4 chuẩn (12,2 / 14,2 / 18,2 / 20,2) → hằng số MONEY; phone chưa normalize ở `warranty/models.py:182,341`; `customers.phone_normalized/phone_raw` TextField có btree (`customers/models.py:29,51`) → CharField.
- P2-9. Thiếu CheckConstraint ≥0 tiền (`orders/models.py:249-260,982-989`), CHECK status. on_delete OK (PROTECT đúng chỗ).
- P2-10. Migration: `inventory/0022_return_entry.py:199` RunPython không reverse; `orders/0119:46` AddIndex atomic trên orders (đã apply); prod 405 [X], 0 chưa apply, makemigrations sạch; 3 cặp trùng số 0001/0112/0121 + 90 file pre-squash chưa dọn. Thiếu composite `(staff, status, -created_at)` cho "đơn của tôi" — chỉ thêm sau EXPLAIN.
- P2-11. Connections tốt (14/100 backend, pgbouncer transaction mode pool 25, maxwait 0, 0 deadlock). Thiếu `lock_timeout` client → thêm `-c lock_timeout=5000` cho web.

## 3. Số đo
Top bảng: audit_logs 2790MB (heap 2286/idx 478, 2,23M dòng); orders 1108MB (411/697, 562k); customers 302MB; ai.tin_nhan 296MB; ai.don_hang 284MB; order_items 260MB; ai.khach_hang 242MB; order_assessments 141MB; cnv_sync_records 126MB. public 177 bảng 5,1GB; ai 11 bảng 1,16GB; dwh 3 MV ~780MB. DB time 0,44h/20h (nhàn), cache hit 95,6%.

## Top 5
1. P0-1: runbook rebuild + offsite backup + tạo lại role đọc.
2. Đổi 5 `icontains` → `__ilike_contains` (P1-2).
3. ai_mart: lọc id trong CTE khach_hang, work_mem cục bộ, UNLOGGED ai.* (P1-3/P1-4).
4. Partial UniqueConstraint 5 model (P1-5) + quyết định customers.code/external_order_id + CHECK tiền.
5. Recreate PG 1 lần với 6 tham số (P2-4) + quyết định image + autovacuum order_assessments; index đợt 3 sau 30 ngày.
