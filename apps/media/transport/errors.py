"""Đổi MediaError → Response {detail, code} (spec §4.6). Mọi view Media bọc decorator này."""

from __future__ import annotations

import functools
import logging

from rest_framework.response import Response

from apps.media.exceptions import MediaError
from apps.media.metrics import media_proxy_requests_total

logger = logging.getLogger(__name__)


def media_errors(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            response = view(request, *args, **kwargs)
        except MediaError as exc:
            if exc.status >= 500:
                logger.warning("media proxy %s %s → %s %s", request.method, request.path, exc.status, exc.code)
            response = Response({"detail": exc.detail, "code": exc.code}, status=exc.status)
        route = (request.resolver_match.url_name if request.resolver_match else None) or "-"
        mode = getattr(request, "_media_mode", "-")
        media_proxy_requests_total.labels(route=route, mode=mode, status=str(response.status_code)).inc()
        return response

    return wrapper
