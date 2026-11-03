# The Go task-queue models (Job, GoCronRun, AuditBatchMark) were removed when the
# Go services were decommissioned and async work returned to Celery. The backing
# tables (jobs, go_cron_runs, go_audit_batch_marks) are dropped by migration 0003.
