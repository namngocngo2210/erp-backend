# Runbook — Bật `pg_stat_statements` trên prod

> Audit 2026-07-23 mục C1. Thời lượng: ~5 phút, có **1 lần restart Postgres**.
> Rủi ro: thấp. Rollback: gỡ 4 dòng `-c pg_stat_statements.*` rồi restart lại.

## Vì sao

Đang có Prometheus + Grafana + `postgres-exporter`, tức thấy được connection, lock,
cache-hit ratio — nhưng **không biết query nào tốn thời gian nhất**. Trong khi toàn bộ
lịch sử sự cố hiệu năng đều là query: `report-precompute-timeout-2026-07-22`,
`resale-tracking-performance`, `REPORT_QUERY_TIMEOUT_SECONDS`, `statement_timeout=30s`
toàn cục. Hiện đang chữa triệu chứng bằng timeout mà không có công cụ tìm nguyên nhân.

## Điều kiện tiên quyết

- Đã merge nhánh chứa thay đổi `docker-compose.prod.yml` (thêm `shared_preload_libraries`).
- Chọn giờ thấp điểm — restart Postgres cắt mọi connection đang mở.
  `shared_preload_libraries` **KHÔNG reload được**, bắt buộc restart tiến trình.

## Các bước

1. Kéo code mới trên VPS:
   ```bash
   cd /path/to/desi-order && git pull
   ```

2. Xác nhận diff chỉ đụng service postgres:
   ```bash
   git diff HEAD~1 -- docker-compose.prod.yml | grep -A5 pg_stat_statements
   ```

3. Restart **chỉ** postgres (xem `docs/prod-compose-recreate-gotchas` — `up -d` nhiều
   service cùng lúc từng gây trùng tên container):
   ```bash
   docker compose -f docker-compose.prod.yml up -d --no-deps postgres
   ```

4. Chờ healthy rồi xác nhận library đã nạp:
   ```bash
   docker exec desi-postgres psql -U desi -d desi -c "SHOW shared_preload_libraries;"
   # kỳ vọng: pg_stat_statements
   ```

5. Tạo extension (chỉ cần 1 lần, cần quyền superuser):
   ```bash
   docker exec desi-postgres psql -U desi -d desi -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
   ```

6. Kiểm tra có số liệu:
   ```bash
   docker exec desi-postgres psql -U desi -d desi -c \
     "SELECT calls, round(total_exec_time::numeric,1) AS total_ms, \
             round(mean_exec_time::numeric,1) AS mean_ms, left(query,80) AS q \
      FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10;"
   ```

7. Bật collector ở exporter (đã thêm `--collector.stat_statements` vào compose):
   ```bash
   docker compose -f docker-compose.prod.yml --profile monitoring up -d --no-deps postgres-exporter
   ```
   Xác nhận metric xuất hiện:
   ```bash
   curl -s localhost:9187/metrics | grep -c pg_stat_statements
   ```

## Sau khi bật — dùng để làm gì

Câu truy vấn đáng chạy hàng tuần (query tốn tổng thời gian nhiều nhất, tức thứ
đáng tối ưu nhất — KHÔNG phải query chậm nhất một lần):

```sql
SELECT calls,
       round(total_exec_time::numeric / 1000, 1) AS total_sec,
       round(mean_exec_time::numeric, 1)         AS mean_ms,
       rows,
       left(query, 120)                          AS query
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 20;
```

Reset số liệu sau khi tối ưu để đo lại từ mốc sạch:
```sql
SELECT pg_stat_statements_reset();
```

## Rollback

Gỡ 4 dòng `-c pg_stat_statements.*` + `-c shared_preload_libraries=...` khỏi
`docker-compose.prod.yml`, rồi `up -d --no-deps postgres`. Extension có thể để lại
(vô hại khi library không nạp) hoặc `DROP EXTENSION pg_stat_statements;`.
