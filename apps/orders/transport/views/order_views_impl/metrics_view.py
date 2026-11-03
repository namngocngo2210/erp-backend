"""Prometheus /metrics endpoint.

No-op friendly: if `prometheus_client` is not installed yet, returns 503.
When `PROMETHEUS_MULTIPROC_DIR` is set, aggregates across gunicorn
workers using the multiprocess collector.
"""
import os
import secrets

from django.conf import settings
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny


# Endpoint Prometheus text exposition (không phải JSON-contract) → loại khỏi schema.
@extend_schema(exclude=True)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def prometheus_metrics(request):
    # AllowAny là bắt buộc (scraper Prometheus không mang JWT), nên chặn nằm ở nginx.
    # Nhưng repo có hai file nginx và chỉ một file từng chặn — nếu môi trường dùng
    # file kia thì metrics phơi ra internet (audit 2026-07-26 C3). Token này là lớp
    # hai để bảo mật không phụ thuộc vào việc chọn đúng file nginx. Trống = tắt, giữ
    # nguyên hành vi cũ cho các scraper nội bộ đang chạy.
    expected = getattr(settings, "METRICS_ALLOW_TOKEN", "")
    if expected:
        provided = (request.headers.get("X-Metrics-Token") or "").strip()
        if not provided or not secrets.compare_digest(provided, expected):
            return HttpResponse("forbidden", status=403, content_type="text/plain")

    try:
        from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
        from prometheus_client import multiprocess
    except Exception:
        return HttpResponse(
            "prometheus_client not installed",
            status=503,
            content_type="text/plain",
        )

    multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR", "").strip()
    if multiproc_dir:
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        output = generate_latest(registry)
    else:
        output = generate_latest()

    return HttpResponse(output, content_type=CONTENT_TYPE_LATEST)
