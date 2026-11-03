"""Hook cho schema FE↔BE chính (SPECTACULAR_SETTINGS.PREPROCESSING_HOOKS)."""

DAILY_REPORT_PREFIX = "/api/daily-report"


def drop_daily_report_paths(endpoints):
    """API Daily Report có file OpenAPI riêng (docs/openapi/daily-report.yaml);
    giữ schema chính đúng là hợp đồng FE↔BE (D7)."""
    return [ep for ep in endpoints if not ep[0].startswith(DAILY_REPORT_PREFIX)]
