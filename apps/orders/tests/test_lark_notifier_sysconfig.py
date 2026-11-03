from apps.orders.infrastructure import lark_notifier
from apps.sysconfig import override_sysconfig


def test_order_bot_reads_sysconfig_at_call_time():
    with override_sysconfig(
        LARK_ORDER_BOT_WEBHOOK_URL="https://lark.example/hook",
        LARK_ORDER_BOT_SECRET="s3",
        LARK_ORDER_BOT_TIMEOUT_SECONDS="7",
    ):
        cfg = lark_notifier.order_bot_config()
    assert cfg.webhook_url == "https://lark.example/hook"
    assert cfg.secret == "s3"
    assert cfg.timeout == 7.0


def test_ops_alert_uses_runtime_config():
    from apps.common import ops_alerts

    with override_sysconfig(LARK_OPS_ALERT_WEBHOOK_URL="https://lark.example/ops"):
        assert ops_alerts._webhook_url() == "https://lark.example/ops"


def test_post_lark_body_clamps_zero_timeout(monkeypatch):
    """Timeout 0 từ DB tới requests → ValueError (không phải RequestException). Kẹp sàn 0.5s."""
    seen = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"code": 0}

    def fake_post(url, *, json, timeout):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(lark_notifier._client, "post", fake_post)
    assert lark_notifier._post_lark_body(webhook_url="https://lark.example/h", secret="", body={}, timeout=0) is True
    assert seen["timeout"] == 0.5
    lark_notifier._post_lark_body(webhook_url="https://lark.example/h", secret="", body={}, timeout=7)
    assert seen["timeout"] == 7.0
