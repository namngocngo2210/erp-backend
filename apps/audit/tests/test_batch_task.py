"""Test task ``audit.create_batch`` — gom nhiều dòng audit vào 1 bulk INSERT.

Batching phía web (apps.dispatch.api.write_audit) cắt task churn trên queue
audit lúc burst (~260 task/phút → vài task); phía worker mỗi task ghi cả batch
bằng bulk_create. Một dòng hỏng không được kéo chết cả batch — fallback ghi
từng dòng và bỏ dòng hỏng.
"""

from django.test import TestCase

from apps.audit.models import AuditLog
from apps.audit.tasks import create_audit_log_batch_task


def _entry(**overrides):
    entry = {
        "user_id": None,
        "action_type": "CREATE",
        "entity_type": "ORDER",
        "entity_id": "548607",
        "method": "POST",
        "ip_address": "172.19.0.1",
        "response_data": {"status": 201},
        "duration_ms": 700,
        "source": "api_request",
        "curl_command": "curl -X POST 'http://x/api/orders'",
        "metadata": {"path": "/api/orders"},
    }
    entry.update(overrides)
    return entry


class CreateAuditLogBatchTaskTests(TestCase):
    def test_bulk_creates_all_entries(self):
        create_audit_log_batch_task(
            entries=[_entry(entity_id="1"), _entry(entity_id="2"), _entry(entity_id="3")]
        )

        self.assertEqual(AuditLog.objects.count(), 3)
        self.assertEqual(
            set(AuditLog.objects.values_list("entity_id", flat=True)), {"1", "2", "3"}
        )

    def test_bad_row_does_not_kill_batch(self):
        # ip_address sai định dạng → bulk_create nổ cả batch; fallback per-row
        # phải giữ lại 2 dòng lành.
        create_audit_log_batch_task(
            entries=[
                _entry(entity_id="ok-1"),
                _entry(entity_id="bad", ip_address="not-an-ip"),
                _entry(entity_id="ok-2"),
            ]
        )

        self.assertEqual(
            set(AuditLog.objects.values_list("entity_id", flat=True)), {"ok-1", "ok-2"}
        )
