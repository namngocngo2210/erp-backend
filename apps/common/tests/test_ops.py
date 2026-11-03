"""Tests cho tầng ops của apps.common: heartbeat + cảnh báo vận hành.

Gộp từ: test_ops_heartbeat.py, test_ops_alerts.py (restructure 2026-08-05).
"""

from __future__ import annotations

from unittest import mock
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.common import ops_alerts
from apps.common.tasks import ops_heartbeat

# ---------------------------------------------------------------------------
# Task ops_heartbeat (docs/ops-hardening-2026-07.md sprint 2).
# ---------------------------------------------------------------------------


@override_settings(OPS_HEARTBEAT_URL="")
def test_heartbeat_noop_when_url_empty():
    with mock.patch("apps.common.tasks.HttpClient") as client_cls:
        assert ops_heartbeat() == "skipped"
    client_cls.assert_not_called()


@override_settings(OPS_HEARTBEAT_URL="https://hc-ping.com/abc")
def test_heartbeat_pings_configured_url():
    with mock.patch("apps.common.tasks.HttpClient") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.return_value.raise_for_status.return_value = None
        assert ops_heartbeat() == "ok"
    client.get.assert_called_once_with("https://hc-ping.com/abc")


def test_heartbeat_registered_in_beat_schedule():
    # Beat entry phải trỏ đúng tên task đã đăng ký (autodiscover apps.common).
    from django.conf import settings

    entry = settings.CELERY_BEAT_SCHEDULE["ops-heartbeat"]
    assert entry["task"] == "apps.common.tasks.ops_heartbeat"
    assert entry["task"] == ops_heartbeat.name


def test_ops_heartbeat_url_forbidden_on_staging():
    # Staging ping cùng URL prod sẽ che tín hiệu prod chết → guard phải chặn.
    from config.settings_parts.base import STAGING_FORBIDDEN_OUTBOUND_ENVS

    assert "OPS_HEARTBEAT_URL" in STAGING_FORBIDDEN_OUTBOUND_ENVS


# ---------------------------------------------------------------------------
# Cảnh báo vận hành: env-gated, dedupe, luôn để lại dấu vết trong log.
#
# Sự cố 25–27/07/2026 kéo dài 2 ngày vì cảnh báo duy nhất là `logger.warning` chìm
# trong log worker. Module ops_alerts là kênh gọi người.
# ---------------------------------------------------------------------------

# LocMemCache cùng LOCATION chia sẻ một dict toàn cục — giữ LOCATION unique cho
# nhóm test này và giữ @override_settings ở mức class.
_LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "ops-alerts-tests",
    }
}


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {"code": 0}
        self.text = "{}"

    def json(self):
        return self._payload


@override_settings(CACHES=_LOCMEM, LARK_OPS_ALERT_WEBHOOK_URL="", LARK_OPS_ALERT_SECRET="")
class OpsAlertWithoutWebhookTests(SimpleTestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.delete(ops_alerts._dedupe_key("fb:token:1"))

    def test_logs_error_even_when_webhook_unconfigured(self):
        with patch.object(ops_alerts._client, "post") as mock_post:
            with self.assertLogs("apps.common.ops_alerts", level="ERROR") as logs:
                sent = ops_alerts.send_ops_alert(
                    key="fb:token:1", title="Token Facebook hỏng", lines=["app 2"],
                )

        self.assertFalse(sent)
        mock_post.assert_not_called()
        self.assertTrue(any("Token Facebook hỏng" in line for line in logs.output))


@override_settings(
    CACHES=_LOCMEM,
    LARK_OPS_ALERT_WEBHOOK_URL="https://open.larksuite.com/open-apis/bot/v2/hook/xxx",
    LARK_OPS_ALERT_SECRET="",
)
class OpsAlertWebhookTests(SimpleTestCase):
    def setUp(self):
        from django.core.cache import cache

        for key in ("fb:token:1", "fb:token:2"):
            cache.delete(ops_alerts._dedupe_key(key))

    def test_posts_text_payload_to_webhook(self):
        with patch.object(ops_alerts._client, "post", return_value=_FakeResponse()) as mock_post:
            with self.assertLogs("apps.common.ops_alerts", level="ERROR"):
                sent = ops_alerts.send_ops_alert(
                    key="fb:token:1", title="Token Facebook hỏng", lines=["app 2", "app 3"],
                )

        self.assertTrue(sent)
        args, kwargs = mock_post.call_args
        payload = kwargs["json"]
        self.assertEqual(payload["msg_type"], "text")
        self.assertIn("Token Facebook hỏng", payload["content"]["text"])
        self.assertIn("app 3", payload["content"]["text"])

    def test_repeated_alert_within_window_is_deduped(self):
        with patch.object(ops_alerts._client, "post", return_value=_FakeResponse()) as mock_post:
            with self.assertLogs("apps.common.ops_alerts", level="ERROR"):
                ops_alerts.send_ops_alert(key="fb:token:2", title="T", lines=["x"])
            second = ops_alerts.send_ops_alert(key="fb:token:2", title="T", lines=["x"])

        self.assertFalse(second)
        self.assertEqual(mock_post.call_count, 1)

    def test_webhook_failure_does_not_raise(self):
        # Cảnh báo hỏng không được kéo theo task nghiệp vụ chết.
        with patch.object(ops_alerts._client, "post", side_effect=RuntimeError("boom")):
            with self.assertLogs("apps.common.ops_alerts", level="ERROR"):
                sent = ops_alerts.send_ops_alert(key="fb:token:1", title="T", lines=["x"])

        self.assertFalse(sent)


@override_settings(
    CACHES=_LOCMEM,
    LARK_OPS_ALERT_WEBHOOK_URL="https://open.larksuite.com/open-apis/bot/v2/hook/xxx",
    LARK_OPS_ALERT_SECRET="s3cret",
)
class OpsAlertSignatureTests(SimpleTestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.delete(ops_alerts._dedupe_key("fb:token:signed"))

    def test_signed_bot_payload_carries_timestamp_and_sign(self):
        with patch.object(ops_alerts._client, "post", return_value=_FakeResponse()) as mock_post:
            with self.assertLogs("apps.common.ops_alerts", level="ERROR"):
                ops_alerts.send_ops_alert(key="fb:token:signed", title="T", lines=["x"])

        payload = mock_post.call_args.kwargs["json"]
        self.assertIn("timestamp", payload)
        self.assertIn("sign", payload)
