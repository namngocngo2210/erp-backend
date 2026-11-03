-- Top query theo TỔNG thời gian thực thi (không phải theo mean).
-- Xếp hạng đúng thứ cần tối ưu: một câu 5ms chạy 100k lần tốn nhiều thời gian
-- hơn một câu 2s chạy 10 lần.
--
-- Chạy: docker exec -i desi-postgres psql -U desi -d desi < scripts/perf/pg-top-queries.sql
\pset pager off
\x auto

SELECT
    round(total_exec_time)::bigint            AS total_ms,
    calls,
    round(mean_exec_time::numeric, 1)         AS mean_ms,
    round(stddev_exec_time::numeric, 1)       AS stddev_ms,
    rows,
    round(100 * total_exec_time / NULLIF(sum(total_exec_time) OVER (), 0))::int AS pct,
    left(regexp_replace(query, '\s+', ' ', 'g'), 160) AS query
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY total_exec_time DESC
LIMIT 20;
