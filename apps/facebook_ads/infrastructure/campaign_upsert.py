"""Upsert danh mục chiến dịch Facebook.

Dùng chung cho hai đường ghi ad: sync catalog (``/ads``) và lazy fetch trong sync
chi tiêu. Để hai chỗ tự viết lấy là sớm muộn cũng lệch nhau ở chỗ xử lý tên rỗng.
"""

from django.utils import timezone

from apps.facebook_ads.models import FacebookAdCampaign


def resolve_campaign(
    fb_campaign_id: str,
    name: str,
    ad_account=None,
    now=None,
) -> FacebookAdCampaign | None:
    """Lấy (hoặc tạo) chiến dịch theo id Facebook. ``""`` → None.

    Tên rỗng KHÔNG ghi đè tên đang có: lazy fetch của ad đã xoá thường không lấy
    được tên, mà ghi "" lên là xoá mất tên thật của một chiến dịch còn sống.
    """
    fb_campaign_id = (fb_campaign_id or "").strip()
    if not fb_campaign_id:
        return None

    now = now or timezone.now()
    campaign, created = FacebookAdCampaign.objects.get_or_create(
        fb_campaign_id=fb_campaign_id,
        defaults={
            "name": name or "",
            "ad_account": ad_account,
            "first_seen_at": now,
            "last_seen_at": now,
        },
    )
    if created:
        return campaign

    updates = {"last_seen_at": now}
    if name and name != campaign.name:
        updates["name"] = name
    # TKQC chỉ điền khi đang trống — không đảo qua lại giữa các bản sao TKQC mỗi
    # lượt sync (xem ghi chú unique ở FacebookAdCampaign).
    if ad_account is not None and campaign.ad_account_id is None:
        updates["ad_account"] = ad_account

    for field, value in updates.items():
        setattr(campaign, field, value)
    campaign.save(update_fields=list(updates))
    return campaign


def resolve_campaigns_bulk(
    items: dict[str, str],
    ad_account=None,
    now=None,
) -> dict[str, FacebookAdCampaign]:
    """Bản theo lô của ``resolve_campaign`` cho một lượt sync catalog: ``{id: name}``
    → ``{id: campaign}``, cùng luật (tên rỗng không ghi đè, TKQC chỉ điền khi trống).

    Số query hằng theo số chiến dịch (audit 2026-09-23 T1): 1 SELECT ``fb_campaign_id__in``,
    1 ``bulk_create`` phần thiếu, 1 ``bulk_update`` phần đổi tên/TKQC, và MỘT
    ``update(last_seen_at=now)`` theo ``id__in`` cho phần chỉ cần đóng dấu "còn thấy"
    — thay vì SELECT + UPDATE mỗi chiến dịch dù không có gì đổi ngoài dấu thời gian.
    """
    wanted = {str(k or "").strip(): (v or "") for k, v in items.items() if str(k or "").strip()}
    if not wanted:
        return {}
    now = now or timezone.now()
    by_id = {
        c.fb_campaign_id: c
        for c in FacebookAdCampaign.objects.filter(fb_campaign_id__in=list(wanted))
    }

    to_create = [
        FacebookAdCampaign(
            fb_campaign_id=fb_id, name=name, ad_account=ad_account,
            first_seen_at=now, last_seen_at=now,
        )
        for fb_id, name in wanted.items()
        if fb_id not in by_id
    ]
    if to_create:
        # ignore_conflicts: lazy fetch ở worker khác có thể vừa tạo cùng id — đọc lại
        # phần thiếu thay vì IntegrityError.
        FacebookAdCampaign.objects.bulk_create(to_create, ignore_conflicts=True)
        for c in FacebookAdCampaign.objects.filter(
            fb_campaign_id__in=[c.fb_campaign_id for c in to_create]
        ):
            by_id[c.fb_campaign_id] = c

    changed: list[FacebookAdCampaign] = []
    seen_only_ids: list[int] = []
    for fb_id, name in wanted.items():
        campaign = by_id.get(fb_id)
        if campaign is None or campaign.first_seen_at == now:
            continue  # vừa tạo ở trên — đã có last_seen_at=now
        fields_changed = False
        if name and name != campaign.name:
            campaign.name = name
            fields_changed = True
        if ad_account is not None and campaign.ad_account_id is None:
            campaign.ad_account = ad_account
            fields_changed = True
        campaign.last_seen_at = now
        if fields_changed:
            changed.append(campaign)
        else:
            seen_only_ids.append(campaign.id)
    if changed:
        FacebookAdCampaign.objects.bulk_update(changed, ["name", "ad_account", "last_seen_at"])
    if seen_only_ids:
        FacebookAdCampaign.objects.filter(id__in=seen_only_ids).update(last_seen_at=now)
    return by_id
