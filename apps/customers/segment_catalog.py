"""Customer-segment catalog lookup.

Standalone so both the loyalty/reporting service and the resale-tracking service
can share it without importing each other (avoids an import cycle).
"""

from __future__ import annotations

from .models import CustomerSegmentCategory


def segment_catalog() -> dict[str, dict]:
    """Danh mục phân khúc {code: {name, care_priority}} từ DB."""
    return {
        category.code: {"name": category.name, "care_priority": category.care_priority}
        for category in CustomerSegmentCategory.objects.all()
    }
