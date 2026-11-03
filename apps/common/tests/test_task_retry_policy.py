"""Chính sách retry Celery (audit 2026-09-23 T1): 16 file task trước đây không khai
`autoretry_for`/`max_retries`.

- Task IDEMPOTENT (purge, sync/upsert, sweep có single_flight, ETL): tự retry khi
  lỗi hạ tầng tạm thời (`OperationalError`/`ConnectionError`/`TimeoutError`) với
  backoff + jitter, tối đa 3 lần.
- Task KHÔNG idempotent (gửi thông báo Lark/DM, đẩy dữ liệu ra ngoài, khôi phục
  đơn): `max_retries=0` — chạy lại là gửi/đẩy trùng; tick beat sau tự bù.

Thêm task mới vào một trong hai danh sách khi tạo — test đỏ nếu quên khai.
"""
import importlib

from django.db import OperationalError
from django.test import SimpleTestCase

IDEMPOTENT_TASKS = [
    "apps.best_express.tasks.purge_old_best_webhook_events",
    "apps.best_express.tasks.retry_unprocessed_best_webhook_events",
    "apps.customers.tasks.rebuild_segment_snapshot",
    "apps.callcenter.tasks.sync_zenify_call_histories",
    "apps.callcenter.tasks.transcribe_pending_call_records",
    "apps.callcenter.tasks.extract_pending_call_insights",
    "apps.callcenter.tasks.score_pending_call_quality",
    "apps.inventory.tasks.ensure_stock_movements_partition",
    "apps.inventory.tasks.match_pending_shipment_scans",
    "apps.inventory.tasks.sweep_reissue_ledger",
    "apps.inventory.tasks.audit_revived_orders_stock",
    "apps.inventory.tasks.purge_matched_pending_scans",
    "apps.hr.tasks.purge_old_attendance_photos",
    "apps.google_ads.tasks.sync_all_daily_spend_google",
    "apps.google_ads.tasks.sync_all_accounts_google",
    "apps.google_ads.tasks.sync_all_campaign_spend_google",
    "apps.google_ads.tasks.assign_campaign_products_google",
    "apps.analytics.tasks.refresh_dwh_marts",
    "apps.ai_mart.tasks.sync_hot_marts",
    "apps.ai_mart.tasks.rebuild_all_marts",
    "apps.ai_mart.tasks.extract_chat_labels",
    "apps.facebook_ads.tasks.sync_all_daily_spend",
    "apps.facebook_ads.tasks.check_facebook_tokens",
    "apps.facebook_ads.tasks.sync_all_ad_catalog",
    "apps.facebook_ads.tasks.sync_all_ad_spend",
    "apps.facebook_ads.tasks.assign_campaign_products",
    "apps.warranty.tasks.sync_best_tracking_statuses",
    "apps.orders.tasks.purge_old_ingest_traces",
    "apps.orders.tasks.purge_old_journey_events",
]

NON_IDEMPOTENT_TASKS = [
    "apps.inventory.tasks.alert_awaiting_return_overdue",
    "apps.hr.tasks.send_attendance_reminder",
    "apps.facebook_ads.tasks.sync_ads_report_lark",
    "apps.common.tasks.ops_heartbeat",
    "apps.orders.tasks.process_sale_reminders",
    "apps.orders.tasks.process_no_answer_reminders",
    "apps.orders.tasks.notify_lark_order_created",
    "apps.orders.tasks.notify_lark_vat_invoice_required",
    "apps.orders.tasks.notify_lark_cancelled_after_dispatch",
    "apps.orders.tasks.process_restore_orders",
    "apps.exports.infrastructure.tasks.run_export",
]

# module import path → module thật (name= của task có thể khác module chứa hàm)
_MODULES = [
    "apps.best_express.tasks", "apps.customers.tasks", "apps.callcenter.tasks",
    "apps.inventory.tasks", "apps.hr.tasks", "apps.google_ads.tasks",
    "apps.analytics.infrastructure.tasks", "apps.ai_mart.infrastructure.tasks",
    "apps.facebook_ads.tasks", "apps.common.tasks", "apps.warranty.tasks",
    "apps.orders.tasks", "apps.exports.infrastructure.tasks",
]


def _registry():
    from celery import current_app

    for module in _MODULES:
        importlib.import_module(module)
    return current_app.tasks


class TaskRetryPolicyTests(SimpleTestCase):
    def test_idempotent_tasks_autoretry_on_transient_errors(self):
        tasks = _registry()
        for name in IDEMPOTENT_TASKS:
            with self.subTest(task=name):
                task = tasks.get(name)
                self.assertIsNotNone(task, f"task chưa đăng ký: {name}")
                autoretry = tuple(getattr(task, "autoretry_for", ()) or ())
                self.assertIn(OperationalError, autoretry)
                self.assertIn(ConnectionError, autoretry)
                self.assertIn(TimeoutError, autoretry)
                self.assertTrue(getattr(task, "retry_backoff", False))
                self.assertTrue(getattr(task, "retry_jitter", False))
                self.assertEqual(task.max_retries, 3)

    def test_non_idempotent_tasks_never_retry(self):
        tasks = _registry()
        for name in NON_IDEMPOTENT_TASKS:
            with self.subTest(task=name):
                task = tasks.get(name)
                self.assertIsNotNone(task, f"task chưa đăng ký: {name}")
                self.assertEqual(task.max_retries, 0)
                self.assertFalse(tuple(getattr(task, "autoretry_for", ()) or ()))
