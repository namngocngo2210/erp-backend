"""GET feed conversion kênh Google Ads Web — xem docs/google-ads-web-conversion-feed.md."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from django.db import DatabaseError
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response

from apps.analytics.sql.tz import BUSINESS_TZ
from apps.conversion_feed.application import feed_service
from apps.conversion_feed.domain import params as p
from apps.conversion_feed.transport.auth import conversion_feed_view
from apps.sysconfig import sysconfig

logger = logging.getLogger(__name__)
TZ = ZoneInfo(BUSINESS_TZ)


# Kênh máy-tới-máy (token tĩnh) → loại khỏi schema CRM nội bộ.
@extend_schema(exclude=True)
@conversion_feed_view
def google_ads_web(request):
    try:
        start, end = p.parse_range(request.query_params, now=timezone.now(), tz=TZ)
        after = p.parse_cursor(request.query_params)
        limit = p.parse_limit(request.query_params)
    except p.ParamError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    try:
        payload = feed_service.feed_payload(
            channel_id=sysconfig.get("CONVERSION_FEED_GOOGLE_ADS_WEB_CHANNEL_ID"),
            start=start,
            end=end,
            after=after,
            limit=limit,
            tz=TZ,
        )
    except DatabaseError:
        logger.exception("conversion_feed: truy vấn thất bại")
        return Response(
            {"detail": "Truy vấn quá lâu hoặc DB lỗi — thử lại sau."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response(payload)
