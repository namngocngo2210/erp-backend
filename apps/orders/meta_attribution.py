"""Bóc campaign id / ad id Facebook từ URL landing lưu ở ``Order.url_page``.

Meta điền tham số động vào URL đích của quảng cáo (kiểm trên prod 21/09/2026):
``utm_id={{campaign.id}}``, ``utm_content={{ad.id}}`` — ổn định ở cả hai kiểu
cấu hình đang dùng (utm_campaign = tên chiến dịch, hoặc = campaign.id).
Chỉ nhận chuỗi toàn số (id Meta); landing tự đặt utm bằng chữ → coi như không có.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

CAMPAIGN_PARAM = "utm_id"
AD_PARAM = "utm_content"


def _numeric_param(params: dict[str, list[str]], name: str) -> str:
    values = params.get(name) or []
    first = (values[0] if values else "").strip()
    return first if first.isdigit() else ""


def parse_meta_attribution(url: str | None) -> tuple[str, str]:
    """→ ``(fb_campaign_id, fb_ad_id)``; rỗng khi URL không mang id Meta."""
    if not url:
        return "", ""
    try:
        params = parse_qs(urlsplit(str(url)).query, keep_blank_values=False)
    except ValueError:
        return "", ""
    return _numeric_param(params, CAMPAIGN_PARAM), _numeric_param(params, AD_PARAM)


def meta_attribution_fields(url: str | None) -> dict[str, str]:
    """Kwargs ``Order`` (``fb_campaign_id``, ``fb_ad_id``) từ URL landing."""
    campaign_id, ad_id = parse_meta_attribution(url)
    return {"fb_campaign_id": campaign_id, "fb_ad_id": ad_id}
