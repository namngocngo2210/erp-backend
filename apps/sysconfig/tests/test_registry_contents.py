from apps.audit.middleware_impl import AuditLogMiddleware
from apps.sysconfig.registry import BY_KEY, GROUPS, SETTINGS

EXPECTED_SECRET = {
    "LARK_ORDER_BOT_SECRET", "LARK_SALE_REMINDER_SECRET", "LARK_VAT_BOT_SECRET",
    "LARK_CANCEL_DISPATCH_BOT_SECRET", "LARK_OPS_ALERT_SECRET", "LARK_INVENTORY_BOT_SECRET",
    "LARK_APP_SECRET", "LARK_CHECKIN_REMINDER_APP_SECRET", "LARK_BITABLE_APP_TOKEN",
    "GEMINI_API_KEY", "CHAT_CRAWLER_DB_PASSWORD", "ZENIFY_API_KEY", "ZENIFY_API_SECRET",
    "HERMES_API_TOKEN", "DAILY_REPORT_API_TOKEN", "ORDERS_INGEST_API_TOKEN",
    "KIOTVIET_WEBHOOK_SECRET", "WARRANTY_PORTAL_ZALO_TOKEN", "PANCAKE_POS_API_KEY",
    "PANCAKE_POS_WEBHOOK_SECRET",
}


def test_registry_has_expected_size_and_groups():
    # 18 lark_bots + 12 lark_open + 8 ai + 5 call_qa + 4 zenify + 35 integrations
    # + 7 inbound_tokens + 13 orders_portal
    assert len(SETTINGS) == 102
    assert {s.group for s in SETTINGS} == {k for k, _ in GROUPS}


def test_secret_flags_match_spec():
    assert {s.key for s in SETTINGS if s.secret} == EXPECTED_SECRET


def test_every_secret_key_is_redacted_by_audit_middleware():
    """Body PATCH đi qua audit middleware; tên khóa phải bị nhận diện là nhạy cảm
    để không lưu plaintext vào audit_logs.curl_command."""
    mw = AuditLogMiddleware(lambda r: None)
    for key in EXPECTED_SECRET:
        assert mw._is_sensitive_key(key), key


def test_staging_forbidden_covers_outbound_credentials():
    forbidden = {s.key for s in SETTINGS if s.staging_forbidden}
    for key in ("GEMINI_API_KEY", "ZENIFY_API_KEY", "LARK_APP_SECRET", "LARK_ORDER_BOT_WEBHOOK_URL"):
        assert key in forbidden
    # Cờ bật tích hợp gọi ra đối tác/Gemini: staging phải đọc False (default) dù DB
    # restore từ prod đang bật (review 2026-09-21 F2).
    for key in (
        "FOXIA_PUSH_ENABLED", "FOXIA_RESALE_PUSH_ENABLED", "PANCAKE_POLL_ENABLED", "CNV_SYNC_ENABLED",
        "ZENIFY_CALL_SYNC_ENABLED", "QA_SCORING_ENABLED", "GEMINI_EXTRACT_ENABLED",
        "AI_CHAT_EXTRACT_ENABLED", "PANCAKE_POS_ENABLED", "PANCAKE_POS_PUSH_ORDERS_ENABLED",
    ):
        assert key in forbidden, key
        assert BY_KEY[key].default is False, key
    # Token vào không cấm (staging cần token riêng), nhưng neutralize vẫn xoá — Task 7.
    assert "HERMES_API_TOKEN" not in forbidden


def test_defaults_match_previous_env_defaults():
    assert BY_KEY["QA_PASS_SCORE"].default == 80
    assert BY_KEY["GEMINI_MAX_RPM"].default == 8
    assert BY_KEY["WARRANTY_PORTAL_MONTHS"].default == 24
    assert BY_KEY["LARK_OPEN_BASE_URL"].default == "https://open.larksuite.com/open-apis"
    assert BY_KEY["CNV_SSO_SERVER_URI"].default == "https://id.cnv.vn"
    assert BY_KEY["FOXIA_PUSH_BASE_URL"].default == "https://desiglobal.foxia.vn/duongdankhachhang"
    assert BY_KEY["WARRANTY_QR_BASE_URL"].default == "https://delax.com.vn/sp/"
