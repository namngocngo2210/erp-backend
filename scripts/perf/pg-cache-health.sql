-- Sức khoẻ bộ nhớ đệm của Postgres + bảng phình + độ trễ vacuum.
-- shared_buffers=1GB: nếu heap hit ratio < 0.99 trên bảng nóng thì DB đang đọc đĩa.
--
-- Chạy: docker exec -i desi-postgres psql -U desi -d desi < scripts/perf/pg-cache-health.sql
\pset pager off

\echo '── Tỉ lệ trúng buffer toàn DB (mong muốn > 0.99) ──'
SELECT
    round(sum(blks_hit)::numeric / NULLIF(sum(blks_hit + blks_read), 0), 4) AS buffer_hit_ratio,
    sum(blks_read)  AS disk_reads,
    sum(xact_commit + xact_rollback) AS transactions
FROM pg_stat_database
WHERE datname = current_database();

\echo ''
\echo '── Bảng nóng nhất: hit ratio + kích thước ──'
SELECT
    relname AS table_name,
    round(heap_blks_hit::numeric / NULLIF(heap_blks_hit + heap_blks_read, 0), 4) AS heap_hit_ratio,
    pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_statio_user_tables
ORDER BY heap_blks_hit + heap_blks_read DESC
LIMIT 15;

\echo ''
\echo '── Dead tuple (autovacuum có theo kịp không) ──'
SELECT
    relname AS table_name,
    n_live_tup AS live_rows,
    n_dead_tup AS dead_rows,
    round(100 * n_dead_tup::numeric / NULLIF(n_live_tup, 0), 1) AS dead_pct,
    last_autovacuum
FROM pg_stat_user_tables
WHERE n_dead_tup > 1000
ORDER BY n_dead_tup DESC
LIMIT 15;
