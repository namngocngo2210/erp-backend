"""Characterization tests for apps.dispatch — the Celery task registry/wrapper
every ``.delay()``/``.apply_async()`` call site in the codebase routes through.

These tests assert the CURRENT behavior of ``registry.py``/``api.py`` as written,
including surprising edge cases (see docstrings below), not a "corrected" version.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from apps.dispatch import api, registry


class FakeCeleryTask:
    """Stand-in for a Celery task object exposing apply_async/delay."""

    def __init__(self):
        self.apply_async = MagicMock(return_value="async-result")
        self.delay = MagicMock(return_value="delay-result")


class GetCeleryTaskTests(SimpleTestCase):
    """registry.get_celery_task() resolution + failure behavior."""

    def test_resolves_registered_handler_to_real_task_object(self):
        # "audit.create" is a real registry entry; resolving it lazy-imports
        # apps.audit.tasks.create_audit_log_task and returns the Celery task.
        task = registry.get_celery_task("audit.create")
        self.assertIsNotNone(task)
        self.assertTrue(hasattr(task, "delay"))
        self.assertTrue(hasattr(task, "apply_async"))

    def test_unregistered_key_raises_keyerror_with_descriptive_message(self):
        # Current behavior: raise KeyError (not log-and-return-None, not silent
        # no-op). Message includes the offending name for debuggability.
        with self.assertRaises(KeyError) as ctx:
            registry.get_celery_task("nope.does_not_exist")
        self.assertIn("Unknown dispatch task name: nope.does_not_exist", str(ctx.exception))

    def test_all_task_names_returns_sorted_registered_keys(self):
        names = registry.all_task_names()
        self.assertEqual(names, sorted(names))
        self.assertIn("audit.create", names)
        self.assertIn("audit.create_batch", names)
        self.assertIn("kv.process_job", names)
        self.assertIn("orders.landing_ingest", names)

    def test_duplicate_registration_last_write_wins(self):
        # _REGISTRY is a plain module-level dict — there is no dedicated
        # "register()" API guarding against duplicate keys. Assigning the same
        # key twice simply overwrites the earlier resolver silently (no error,
        # no warning). This characterizes that surprising-but-real behavior.
        original = dict(registry._REGISTRY)
        try:
            registry._REGISTRY["dup.key"] = lambda: "first"
            registry._REGISTRY["dup.key"] = lambda: "second"
            self.assertEqual(registry.get_celery_task("dup.key"), "second")
        finally:
            registry._REGISTRY.clear()
            registry._REGISTRY.update(original)


class DispatchApiTests(SimpleTestCase):
    """api.dispatch() / api.write_audit() wrapper behavior."""

    def test_dispatch_calls_apply_async_with_payload_and_queue(self):
        fake_task = FakeCeleryTask()
        with patch("apps.dispatch.api.get_celery_task", return_value=fake_task) as mock_get:
            result = api.dispatch("kv.process_job", queue="kiotviet", job_id=42)

        mock_get.assert_called_once_with("kv.process_job")
        fake_task.apply_async.assert_called_once_with(kwargs={"job_id": 42}, queue="kiotviet")
        self.assertEqual(result, "async-result")

    def test_dispatch_with_countdown_passes_countdown_kwarg(self):
        fake_task = FakeCeleryTask()
        with patch("apps.dispatch.api.get_celery_task", return_value=fake_task):
            api.dispatch("kv.process_job", queue="default", countdown=30, job_id=1)

        fake_task.apply_async.assert_called_once_with(
            kwargs={"job_id": 1}, queue="default", countdown=30
        )

    def test_dispatch_without_countdown_omits_countdown_kwarg(self):
        # countdown=0 (falsy default) takes the plain apply_async branch —
        # no countdown= kwarg is passed at all in that case.
        fake_task = FakeCeleryTask()
        with patch("apps.dispatch.api.get_celery_task", return_value=fake_task):
            api.dispatch("kv.process_job", job_id=2)

        fake_task.apply_async.assert_called_once_with(kwargs={"job_id": 2}, queue="default")

    def test_dispatch_unregistered_task_name_raises_keyerror(self):
        with self.assertRaises(KeyError):
            api.dispatch("nope.unknown", foo="bar")

    def test_write_audit_swallows_error_when_both_celery_and_db_fail(self):
        # Neither failure path raises — both are caught and logged, so a broker
        # outage combined with a DB error is silently swallowed by write_audit().
        with override_settings(AUDIT_BATCH_SIZE=1):
            with patch("apps.dispatch.api.get_celery_task", side_effect=Exception("broker down")):
                with patch(
                    "apps.audit.models.AuditLog.objects.bulk_create",
                    side_effect=Exception("db down"),
                ):
                    result = api.write_audit(actor_id=1, action="test")  # should not raise

        self.assertIsNone(result)


class WriteAuditBatchingTests(SimpleTestCase):
    """write_audit() gom entry thành batch — sự cố 2026-08-03/04.

    Mỗi request một task ``audit.create`` = ~260 task churn trên queue audit trong
    một phút burst. Batching: buffer trong process web, đầy AUDIT_BATCH_SIZE thì
    enqueue MỘT task ``audit.create_batch``; batch lửng được timer flush sau
    AUDIT_BATCH_MAX_DELAY_SECONDS để audit không nằm chờ vô hạn lúc vắng traffic.
    """

    def setUp(self):
        api._reset_audit_buffer_for_tests()
        self.addCleanup(api._reset_audit_buffer_for_tests)

    def test_buffers_until_batch_size_then_enqueues_one_batch_task(self):
        fake_task = FakeCeleryTask()
        with override_settings(AUDIT_BATCH_SIZE=3, AUDIT_BATCH_MAX_DELAY_SECONDS=60):
            with patch("apps.dispatch.api.get_celery_task", return_value=fake_task) as mock_get:
                api.write_audit(actor_id=1, action="a")
                api.write_audit(actor_id=2, action="b")
                fake_task.delay.assert_not_called()
                api.write_audit(actor_id=3, action="c")

        mock_get.assert_called_once_with("audit.create_batch")
        fake_task.delay.assert_called_once_with(
            entries=[
                {"actor_id": 1, "action": "a"},
                {"actor_id": 2, "action": "b"},
                {"actor_id": 3, "action": "c"},
            ]
        )

    def test_partial_batch_is_flushed_by_timer_after_max_delay(self):
        fake_task = FakeCeleryTask()
        with override_settings(AUDIT_BATCH_SIZE=100, AUDIT_BATCH_MAX_DELAY_SECONDS=0.05):
            with patch("apps.dispatch.api.get_celery_task", return_value=fake_task):
                api.write_audit(actor_id=1, action="solo")
                fake_task.delay.assert_not_called()
                # Chờ timer flush (poll thay vì sleep cứng — tránh flaky).
                for _ in range(200):
                    if fake_task.delay.called:
                        break
                    time.sleep(0.01)

        fake_task.delay.assert_called_once_with(entries=[{"actor_id": 1, "action": "solo"}])

    def test_batch_size_one_enqueues_immediately(self):
        fake_task = FakeCeleryTask()
        with override_settings(AUDIT_BATCH_SIZE=1):
            with patch("apps.dispatch.api.get_celery_task", return_value=fake_task):
                api.write_audit(actor_id=1, action="now")

        fake_task.delay.assert_called_once_with(entries=[{"actor_id": 1, "action": "now"}])

    def test_enqueue_failure_falls_back_to_sync_bulk_db_write(self):
        # Payload phải là field thật của AuditLog: fallback dựng model instance
        # TRƯỚC khi gọi bulk_create — field giả sẽ nổ TypeError ở constructor.
        with override_settings(AUDIT_BATCH_SIZE=1):
            with patch("apps.dispatch.api.get_celery_task", side_effect=Exception("broker down")):
                with patch("apps.audit.models.AuditLog.objects.bulk_create") as mock_bulk:
                    api.write_audit(action_type="CREATE", entity_type="ORDER")

        mock_bulk.assert_called_once()
        (objs,), _kwargs = mock_bulk.call_args
        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0].action_type, "CREATE")
