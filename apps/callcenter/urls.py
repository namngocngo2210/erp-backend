from django.urls import path

from .views import (
    hermes_calls,
    order_call_records,
    quality_agents,
    quality_call_detail,
    quality_calls,
    quality_filter_options,
    quality_review,
    quality_rubric,
    quality_summary,
    quality_trend,
    webphone_token,
)

urlpatterns = [
    path("token", webphone_token, name="callcenter-webphone-token"),
    path(
        "orders/<int:order_id>/call-records",
        order_call_records,
        name="callcenter-order-call-records",
    ),
    # Kênh máy-tới-máy cho agent phân tích ngoài — token tĩnh, không dính JWT CRM.
    path("hermes/calls", hermes_calls, name="callcenter-hermes-calls"),
    path("quality/summary", quality_summary, name="call-quality-summary"),
    path("quality/trend", quality_trend, name="call-quality-trend"),
    path("quality/agents", quality_agents, name="call-quality-agents"),
    path("quality/rubric", quality_rubric, name="call-quality-rubric"),
    path("quality/filters", quality_filter_options, name="call-quality-filters"),
    path("quality/calls", quality_calls, name="call-quality-calls"),
    path("quality/calls/<int:score_id>", quality_call_detail, name="call-quality-call-detail"),
    path("quality/calls/<int:score_id>/review", quality_review, name="call-quality-review"),
]
