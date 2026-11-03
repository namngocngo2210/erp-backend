"""Facade module to preserve public imports while delegating to layered implementation."""

from .transport.views.callcenter_views_impl import (
    order_call_records,
    webphone_token,
)
from .transport.views.hermes_views_impl import hermes_calls
from .transport.views.quality_views_impl import (
    quality_agents,
    quality_call_detail,
    quality_calls,
    quality_filter_options,
    quality_review,
    quality_rubric,
    quality_summary,
    quality_trend,
)

__all__ = [
    "hermes_calls",
    "order_call_records",
    "quality_agents",
    "webphone_token",
    "quality_call_detail",
    "quality_calls",
    "quality_filter_options",
    "quality_review",
    "quality_rubric",
    "quality_summary",
    "quality_trend",
]
