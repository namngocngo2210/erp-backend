-- Ảnh chụp tức thời lúc hệ thống đang chậm: ai đang chạy gì, chờ gì.
-- Dùng KHI đang có sự cố, không dùng để xếp hạng.
--
-- Chạy: docker exec -i desi-postgres psql -U desi -d desi < scripts/perf/pg-live-activity.sql
\pset pager off

\echo '── Phân bố connection theo state + application_name ──'
SELECT
    application_name,
    state,
    count(*) AS conns,
    max(now() - state_change)::interval(0) AS longest_in_state
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY application_name, state
ORDER BY conns DESC;

\echo ''
\echo '── Query đang chạy > 1s ──'
SELECT
    pid,
    application_name,
    (now() - query_start)::interval(0) AS runtime,
    wait_event_type,
    wait_event,
    left(regexp_replace(query, '\s+', ' ', 'g'), 120) AS query
FROM pg_stat_activity
WHERE state = 'active'
  AND now() - query_start > interval '1 second'
  AND pid <> pg_backend_pid()
ORDER BY query_start;
