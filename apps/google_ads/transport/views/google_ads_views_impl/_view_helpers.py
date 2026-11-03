"""Hằng số và helper dùng chung cho các view Google Ads.

Tách riêng vì `SECTION`/`PRESET_TO_RANGE`/`_get_config` được cả 4 nhóm view
(cấu hình, đồng bộ, báo cáo chi phí, tài khoản) dùng lại.
"""

from datetime import date as date_cls, timedelta

from django.utils import timezone

from apps.google_ads.models import GoogleAdsConfig

SECTION = "google-ads"

# Mirror các preset của Facebook Ads để FE dùng chung danh sách lựa chọn.
PRESET_TO_RANGE = {
    "today": lambda t: (t, t),
    "yesterday": lambda t: (t - timedelta(days=1), t - timedelta(days=1)),
    "last_7d": lambda t: (t - timedelta(days=6), t),
    "last_30d": lambda t: (t - timedelta(days=29), t),
    "this_month": lambda t: (t.replace(day=1), t),
    "last_month": lambda t: (
        (t.replace(day=1) - timedelta(days=1)).replace(day=1),
        t.replace(day=1) - timedelta(days=1),
    ),
    "this_year": lambda t: (t.replace(month=1, day=1), t),
    "maximum": lambda _t: (date_cls(2000, 1, 1), timezone.localdate()),
}


def _get_config(config_id: int) -> GoogleAdsConfig | None:
    return GoogleAdsConfig.objects.filter(id=config_id, deleted_at__isnull=True).first()
