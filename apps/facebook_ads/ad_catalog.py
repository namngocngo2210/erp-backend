"""Đọc payload /ads của Graph API thành dữ liệu catalog.

Thuần hàm, không I/O — test chạy không cần mock HTTP.
"""


def _as_dict(value) -> dict:
    """Chỉ nhận dict, mọi thứ khác coi như rỗng.

    Graph API là schema-less: một field khai là object vẫn có thể về dưới dạng
    chuỗi, list hoặc null tuỳ loại ad / phiên bản API. `x.get(...)` trên chuỗi ném
    AttributeError, và ở đây một payload rác như vậy sẽ giết cả lượt refresh catalog
    của app — kéo theo ad thiếu catalog bị lazy-fetch với page = NULL, chi phí rơi
    vào "Không rõ page". Sai một ad còn chấp nhận được; sai cả app thì không.
    """
    return value if isinstance(value, dict) else {}


def resolve_page_id(ad: dict) -> str:
    """Suy page_id của một ad. Trả "" nếu không nguồn nào cho ra id hợp lệ.

    Ba nguồn, thử theo thứ tự:
      1. ``creative.effective_object_story_id`` — dạng ``{page_id}_{post_id}``.
      2. ``creative.object_story_spec.page_id``.
      3. ``adset.promoted_object.page_id`` — campaign tin nhắn/lead không có creative
         gắn bài viết nên hai nguồn trên rỗng.

    Chỉ nhận chuỗi toàn chữ số: Graph API thỉnh thoảng trả placeholder dạng text,
    lấy bừa sẽ tạo ra page rác trong danh mục.
    """
    ad = _as_dict(ad)
    creative = _as_dict(ad.get("creative"))

    story_id = str(creative.get("effective_object_story_id") or "")
    if "_" in story_id:
        candidate = story_id.split("_", 1)[0]
        if candidate.isdigit():
            return candidate

    spec_page = str(_as_dict(creative.get("object_story_spec")).get("page_id") or "")
    if spec_page.isdigit():
        return spec_page

    promoted = _as_dict(_as_dict(ad.get("adset")).get("promoted_object"))
    promo_page = str(promoted.get("page_id") or "")
    if promo_page.isdigit():
        return promo_page

    return ""


def parse_ad_payload(ad: dict) -> dict:
    """Payload một ad → dict phẳng khớp field của model ``FacebookAd``.

    Payload dị dạng suy biến thành "ad không rõ page" (fb_ad_id rỗng nếu cả id cũng
    hỏng, caller sẽ bỏ qua) chứ không được ném exception — xem ``_as_dict``.
    """
    ad = _as_dict(ad)
    campaign = _as_dict(ad.get("campaign"))
    adset = _as_dict(ad.get("adset"))
    return {
        "fb_ad_id": str(ad.get("id") or ""),
        "name": str(ad.get("name") or ""),
        "campaign_id": str(campaign.get("id") or ""),
        "campaign_name": str(campaign.get("name") or ""),
        "adset_id": str(adset.get("id") or ""),
        "adset_name": str(adset.get("name") or ""),
        "fb_page_id": resolve_page_id(ad),
    }
