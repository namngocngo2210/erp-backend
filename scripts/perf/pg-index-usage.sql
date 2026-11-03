-- Index chết (chưa từng được dùng) + bảng bị seq scan nhiều.
-- Index chết vẫn tốn chi phí mỗi lần INSERT/UPDATE và tốn dung lượng.
--
-- Chạy: docker exec -i desi-postgres psql -U desi -d desi < scripts/perf/pg-index-usage.sql
\pset pager off

\echo '── Index chưa từng dùng (bỏ qua index của PK/unique constraint) ──'
SELECT
    s.relname                                   AS table_name,
    s.indexrelname                              AS index_name,
    pg_size_pretty(pg_relation_size(s.indexrelid)) AS size
FROM pg_stat_user_indexes s
JOIN pg_index i ON i.indexrelid = s.indexrelid
WHERE s.idx_scan = 0
  AND NOT i.indisunique
  AND NOT i.indisprimary
ORDER BY pg_relation_size(s.indexrelid) DESC
LIMIT 25;

\echo ''
\echo '── Bảng có seq scan nhiều (ứng viên thiếu index) ──'
SELECT
    relname                     AS table_name,
    seq_scan,
    seq_tup_read,
    idx_scan,
    n_live_tup                  AS live_rows,
    CASE WHEN seq_scan > 0 THEN (seq_tup_read / seq_scan)::bigint END AS avg_rows_per_seq_scan
FROM pg_stat_user_tables
WHERE seq_scan > 0
ORDER BY seq_tup_read DESC
LIMIT 20;
