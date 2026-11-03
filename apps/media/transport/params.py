"""Allowlist query param từng route ads-crm (tài liệu §3). Param lạ bị lược im lặng."""

from __future__ import annotations

CONTENTS_LIST = frozenset({
    "source_id", "content_type_id", "overall_rating", "product_id", "category_id", "channel_id",
    "ad_rating", "uploaded_from", "uploaded_to", "sort", "order", "page", "page_size",
})
PROPOSALS_LIST = frozenset({"status", "product_id", "q", "page", "page_size"})
REPORTS = frozenset({"uploaded_from", "uploaded_to"})
TAXONOMY_GROUPS = ("content-types", "products", "product-categories", "ad-channels", "content-sources")


def pick(query_params, allowed: frozenset[str]) -> list[tuple[str, str]]:
    """Giữ thứ tự và giá trị lặp (`?a=1&a=2`) — ads-crm nhận cả hai kiểu."""
    out: list[tuple[str, str]] = []
    for key in query_params:
        if key in allowed:
            out.extend((key, v) for v in query_params.getlist(key) if v != "")
    return out
