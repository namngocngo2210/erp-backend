"""Hằng số và helper dùng chung cho các view Facebook Ads.

Tách riêng vì `_get_app` được cả 4 nhóm view (cấu hình app, đồng bộ, insights,
báo cáo chi phí, tài khoản) dùng lại; `PRESET_TO_RANGE` đặt cùng chỗ để khớp
bố cục của app anh em `google_ads`.
"""

from datetime import date as date_cls, timedelta

from django.utils import timezone

from apps.facebook_ads.models import FacebookApp


def _get_app(app_id: int) -> FacebookApp | None:
    return FacebookApp.objects.filter(id=app_id, deleted_at__isnull=True).first()


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
