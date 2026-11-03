"""Tests cho retention audit_logs (task purge) + middleware bỏ response_data GET 2xx.

Audit hardening 2026-07 mục 3.1.
"""
import json
from datetime import timedelta
from unittest import mock

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.audit.middleware_impl import AuditLogMiddleware
from apps.audit.models import AuditLog


def _make_log(days_old: int) -> AuditLog:
    log = AuditLog.objects.create(
        action_type="API_REQUEST",
        entity_type="OTHER",
        method="GET",
        source="api_request",
    )
    # timestamp là auto_now_add → chỉnh lùi qua queryset update.
    AuditLog.objects.filter(pk=log.pk).update(
        timestamp=timezone.now() - timedelta(days=days_old)
    )
    return log


class PurgeOldAuditLogsTests(TestCase):
    @override_settings(AUDIT_LOG_RETENTION_DAYS=90)
    def test_purges_only_older_than_retention(self):
        from apps.audit.tasks import purge_old_audit_logs

        old = _make_log(days_old=120)
        recent = _make_log(days_old=10)

        deleted = purge_old_audit_logs()

        self.assertGreaterEqual(deleted, 1)
        self.assertFalse(AuditLog.objects.filter(pk=old.pk).exists())
        self.assertTrue(AuditLog.objects.filter(pk=recent.pk).exists())

    @override_settings(AUDIT_LOG_RETENTION_DAYS=90)
    def test_purges_in_batches(self):
        from apps.audit.tasks import purge_old_audit_logs

        for _ in range(3):
            _make_log(days_old=200)

        deleted = purge_old_audit_logs(batch_size=1)

        self.assertEqual(deleted, 3)
        self.assertEqual(
            AuditLog.objects.filter(
                timestamp__lt=timezone.now() - timedelta(days=90)
            ).count(),
            0,
        )

    @override_settings(AUDIT_LOG_RETENTION_DAYS=0)
    def test_retention_zero_disables_purge(self):
        from apps.audit.tasks import purge_old_audit_logs

        old = _make_log(days_old=400)

        deleted = purge_old_audit_logs()

        self.assertEqual(deleted, 0)
        self.assertTrue(AuditLog.objects.filter(pk=old.pk).exists())

    @override_settings(AUDIT_LOG_RETENTION_DAYS=90)
    def test_skips_when_lock_held(self):
        from apps.audit import tasks as audit_tasks

        old = _make_log(days_old=120)

        with mock.patch("apps.audit.tasks.single_flight") as sf:
            sf.return_value.__enter__.return_value = False
            result = audit_tasks.purge_old_audit_logs()

        self.assertEqual(result, -1)
        self.assertTrue(AuditLog.objects.filter(pk=old.pk).exists())


@override_settings(AUDIT_LOG_SKIP_READS=False)  # xem test_skip_reads.py
class AuditMiddlewareGetResponseTests(SimpleTestCase):
    def _run(self, method: str, status: int = 200):
        middleware = AuditLogMiddleware(lambda request: None)
        factory = RequestFactory()
        request = getattr(factory, method.lower())("/api/orders/")
        request.user = mock.Mock(is_authenticated=False)
        middleware.process_request(request)

        response = HttpResponse(
            json.dumps({"results": [1, 2], "count": 2}),
            content_type="application/json",
            status=status,
        )
        captured = {}

        with mock.patch.object(
            AuditLogMiddleware, "_write_audit_log",
            side_effect=lambda **kw: captured.update(kw),
        ):
            middleware.process_response(request, response)
        return captured

    def test_get_2xx_does_not_store_response_body_by_default(self):
        # Default: AUDIT_LOG_GET_RESPONSE tắt → chỉ giữ status.
        captured = self._run("GET", 200)
        self.assertEqual(captured["response_data"], {"status": 200})

    @override_settings(AUDIT_LOG_GET_RESPONSE=True)
    def test_get_2xx_stores_summary_when_enabled(self):
        captured = self._run("GET", 200)
        self.assertIn("keys", captured["response_data"])
        self.assertEqual(captured["response_data"]["count"], 2)

    def test_get_error_still_stores_summary(self):
        captured = self._run("GET", 500)
        self.assertEqual(captured["response_data"]["status"], 500)
        self.assertIn("keys", captured["response_data"])

    def test_post_still_stores_summary(self):
        captured = self._run("POST", 200)
        self.assertIn("keys", captured["response_data"])


@override_settings(AUDIT_LOG_SKIP_READS=False)  # xem test_skip_reads.py
class AuditMiddlewareSkipPathTests(SimpleTestCase):
    """Bổ sung WP8.2: SKIP_PATHS + OPTIONS không được ghi audit log."""

    def _run_path(self, path: str, method: str = "get"):
        middleware = AuditLogMiddleware(lambda request: None)
        factory = RequestFactory()
        request = getattr(factory, method)(path)
        request.user = mock.Mock(is_authenticated=False)
        middleware.process_request(request)

        response = HttpResponse("{}", content_type="application/json", status=200)
        with mock.patch.object(AuditLogMiddleware, "_write_audit_log") as mock_write:
            middleware.process_response(request, response)
        return mock_write

    def test_skip_paths_are_not_logged(self):
        for path in ["/health/", "/static/app.js", "/media/x.png", "/favicon.ico"]:
            with self.subTest(path=path):
                mock_write = self._run_path(path)
                mock_write.assert_not_called()

    def test_options_requests_are_not_logged(self):
        mock_write = self._run_path("/api/orders/", method="options")
        mock_write.assert_not_called()

    def test_api_path_is_logged(self):
        mock_write = self._run_path("/api/orders/")
        mock_write.assert_called_once()
        self.assertEqual(mock_write.call_args.kwargs["entity_type"], "ORDER")
