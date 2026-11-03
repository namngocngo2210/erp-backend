"""Đồng bộ danh mục quảng cáo và page từ Graph API.

Tách khỏi ``services.py`` (đang lo chi tiêu grain account) để hai luồng có nhịp
cron và giới hạn thời gian riêng — spec §5.
"""

import json
import logging
from datetime import timedelta

from celery.exceptions import SoftTimeLimitExceeded
from django.db import transaction
from django.utils import timezone

from apps.facebook_ads.ad_catalog import parse_ad_payload
from apps.facebook_ads.infrastructure.campaign_upsert import resolve_campaigns_bulk
from apps.facebook_ads.models import FacebookAd, FacebookAdAccount, FacebookApp, FacebookPage
from apps.facebook_ads.services import (
    GRAPH_API_BASE,
    FacebookTokenError,
    _client,
    _error_from_response,
)

logger = logging.getLogger(__name__)

AD_FIELDS = (
    "id,name,"
    "campaign{id,name},"
    "adset{id,name,promoted_object},"
    "creative{id,effective_object_story_id,object_story_spec{page_id}}"
)
AD_PAGE_LIMIT = 200
# TKQC nhiều ad + field lồng (creative/adset) làm Graph API từ chối trang 200 với
# "Please reduce the amount of data you're asking for" (code 1) — sự cố
# 2026-09-23: TKQC 73/85/92/... fail mỗi giờ, cả task chạm soft limit 600s. Gặp
# lỗi này thì chia đôi limit và gọi lại ĐÚNG trang đó, tới sàn này mới bỏ cuộc.
AD_PAGE_LIMIT_MIN = 25
# Tên page hiếm khi đổi — refresh mỗi 24h thay vì mỗi lượt cron 1h.
PAGE_NAME_REFRESH_HOURS = 24


def _is_too_much_data(response) -> bool:
    try:
        err = (response.json() or {}).get("error") or {}
    except ValueError:
        return False
    return err.get("code") == 1 and "reduce the amount of data" in (err.get("message") or "")


def _fetch_ads(ad_account: FacebookAdAccount) -> list[dict]:
    """Kéo toàn bộ ad của một TKQC, đi hết phân trang.

    Phân trang bằng cursor ``after`` thay vì URL ``paging.next``: URL next đã chốt
    cứng ``limit`` nên không hạ được limit giữa chừng khi Graph API chê trang nặng.
    """
    url = f"{GRAPH_API_BASE}/{ad_account.fb_id}/ads"
    limit = AD_PAGE_LIMIT
    after: str | None = None
    results: list[dict] = []
    while True:
        params = {
            "fields": AD_FIELDS,
            "access_token": ad_account.app.access_token,
            "limit": limit,
        }
        if after:
            params["after"] = after
        response = _client.get(url, params=params, timeout=60)
        if response.status_code != 200:
            if limit > AD_PAGE_LIMIT_MIN and _is_too_much_data(response):
                limit = max(AD_PAGE_LIMIT_MIN, limit // 2)
                logger.info(
                    "facebook_ads ads page too large account_id=%s → limit=%s",
                    ad_account.id,
                    limit,
                )
                continue
            raise _error_from_response(response, "Graph API error (ads)")
        payload = response.json()
        results.extend(payload.get("data") or [])
        paging = payload.get("paging") or {}
        after = (paging.get("cursors") or {}).get("after")
        if not paging.get("next") or not after:
            break
    return results


def _fetch_page_names(app: FacebookApp, page_ids: list[str]) -> dict[str, str]:
    """Lấy tên nhiều page trong một lời gọi Batch. Page lỗi bị bỏ qua, không raise.

    Một page bị hạn chế quyền không được phép làm hỏng cả lượt sync — ad vẫn map
    được vào page, chỉ là hiển thị bằng id cho tới lượt sau.
    """
    if not page_ids:
        return {}
    names: dict[str, str] = {}
    for i in range(0, len(page_ids), 50):
        chunk = page_ids[i : i + 50]
        batch = [
            {"method": "GET", "name": f"page_{pid}", "relative_url": f"{pid}?fields=name"}
            for pid in chunk
        ]
        response = _client.post(
            f"{GRAPH_API_BASE}/",
            data={
                "access_token": app.access_token,
                "batch": json.dumps(batch),
                "include_headers": "false",
            },
            timeout=60,
        )
        if response.status_code != 200:
            raise _error_from_response(response, "Batch error (pages)")
        for pid, sub in zip(chunk, response.json() or []):
            if not sub or sub.get("code") != 200:
                continue
            try:
                body = json.loads(sub.get("body") or "{}")
            except (TypeError, ValueError):
                continue
            if body.get("name"):
                names[pid] = str(body["name"])
    return names


def _pages_needing_name_refresh(page_ids: set[str]) -> list[str]:
    """Page chưa có trong DB, hoặc đã quá hạn refresh tên."""
    stale_before = timezone.now() - timedelta(hours=PAGE_NAME_REFRESH_HOURS)
    fresh = set(
        FacebookPage.objects.filter(
            fb_page_id__in=page_ids, last_synced_at__gte=stale_before,
        ).values_list("fb_page_id", flat=True)
    )
    return sorted(page_ids - fresh)


@transaction.atomic
def _upsert_catalog(
    ad_account: FacebookAdAccount, parsed: list[dict], page_names: dict[str, str],
) -> dict:
    now = timezone.now()

    page_by_fb_id: dict[str, FacebookPage] = {}
    for fb_page_id in {p["fb_page_id"] for p in parsed if p["fb_page_id"]}:
        # Chỉ stamp last_synced_at khi tên page THỰC SỰ được lấy lại trong lượt này.
        # Nếu stamp cho mọi page xuất hiện trong /ads (kể cả page không được refetch
        # tên vì đang còn "tươi"), _pages_needing_name_refresh sẽ luôn thấy page này
        # "mới sync" và không bao giờ coi là stale nữa — tên chỉ được lấy đúng 1 lần
        # duy nhất, đổi tên trên FB sẽ không bao giờ được phản ánh. Cùng lý do, page
        # mới phát hiện nhưng lấy tên thất bại phải giữ last_synced_at = NULL để lượt
        # sau retry (filter last_synced_at__gte=... không khớp NULL).
        defaults = {}
        if fb_page_id in page_names:
            defaults["name"] = page_names[fb_page_id]
            defaults["last_synced_at"] = now
        page, _ = FacebookPage.objects.update_or_create(
            fb_page_id=fb_page_id, defaults=defaults,
        )
        page_by_fb_id[fb_page_id] = page

    # Upsert chiến dịch trước, MỘT lô cho mọi campaign_id trong lượt này — không
    # gọi trong vòng lặp ad (một chiến dịch có hàng chục ad → thừa hàng chục query).
    # Tên lấy từ ad đầu tiên mang campaign_id đó (cùng thứ tự với bản lặp cũ).
    campaign_names: dict[str, str] = {}
    for item in parsed:
        key = item["campaign_id"]
        if key and key not in campaign_names:
            campaign_names[key] = item["campaign_name"]
    campaign_by_id = resolve_campaigns_bulk(campaign_names, ad_account=ad_account, now=now)

    # Ad: 1 SELECT `fb_ad_id__in` rồi chia ba lô — tạo mới (bulk_create), đổi field
    # (bulk_update), và CHỈ đóng dấu last_seen_at (một `update()` theo id__in). Trước
    # đây `update_or_create` từng ad = SELECT + UPDATE mỗi ad mỗi giờ dù không đổi gì
    # ngoài last_seen_at (audit 2026-09-23 T1 — ghi từng dòng qua PgBouncer).
    unresolved = 0
    wanted: dict[str, dict] = {}
    for item in parsed:
        if not item["fb_ad_id"]:
            continue
        # Trùng fb_ad_id trong cùng lượt: bản SAU thắng (cùng luật với update_or_create).
        wanted[item["fb_ad_id"]] = item
    existing = {a.fb_ad_id: a for a in FacebookAd.objects.filter(fb_ad_id__in=list(wanted))}
    to_create: list[FacebookAd] = []
    to_update: list[FacebookAd] = []
    seen_only_ids: list[int] = []
    for fb_ad_id, item in wanted.items():
        page = page_by_fb_id.get(item["fb_page_id"])
        if page is None:
            unresolved += 1
        campaign = campaign_by_id.get(item["campaign_id"])
        values = {
            "ad_account_id": ad_account.id,
            "page_id": page.id if page is not None else None,
            "name": item["name"],
            "campaign_id": campaign.id if campaign is not None else None,
            "adset_id": item["adset_id"],
            "adset_name": item["adset_name"],
        }
        ad = existing.get(fb_ad_id)
        if ad is None:
            to_create.append(FacebookAd(fb_ad_id=fb_ad_id, last_seen_at=now, **values))
            continue
        if all(getattr(ad, field) == value for field, value in values.items()):
            seen_only_ids.append(ad.id)
            continue
        for field, value in values.items():
            setattr(ad, field, value)
        ad.last_seen_at = now
        to_update.append(ad)
    if to_create:
        FacebookAd.objects.bulk_create(to_create)
    if to_update:
        FacebookAd.objects.bulk_update(
            to_update,
            ["ad_account", "page", "name", "campaign", "adset_id", "adset_name", "last_seen_at"],
        )
    if seen_only_ids:
        FacebookAd.objects.filter(id__in=seen_only_ids).update(last_seen_at=now)

    return {
        "ads_upserted": len(wanted),
        "pages_upserted": len(page_by_fb_id),
        "campaigns_upserted": len(campaign_by_id),
        "unresolved_pages": unresolved,
    }


def sync_ad_catalog_for_app(app: FacebookApp, synced_fb_ids: set[str] | None = None) -> dict:
    """Đồng bộ catalog ad + page cho mọi TKQC active của một app.

    ``synced_fb_ids``: fb_id đã kéo thành công trong lượt này qua app khác — bỏ qua,
    và fb_id kéo thành công ở đây được thêm vào. Prod 2026-09-23: cùng 71 TKQC gắn
    vào cả 4 app nên mỗi lượt kéo 284 lần, không bao giờ xong trong 600s và ad cứ
    nhảy chủ giữa các dòng TKQC của từng app. Chỉ ghi nhận khi THÀNH CÔNG: TKQC
    lỗi ở app này vẫn được app sau thử bằng token khác.
    """
    if synced_fb_ids is None:
        synced_fb_ids = set()
    accounts = [
        account
        for account in FacebookAdAccount.objects.filter(
            app=app, status=FacebookAdAccount.STATUS_ACTIVE,
        )
        if account.fb_id not in synced_fb_ids
    ]
    total = {"ads_upserted": 0, "pages_upserted": 0, "unresolved_pages": 0}
    failed = 0
    for account in accounts:
        try:
            parsed = [parse_ad_payload(ad) for ad in _fetch_ads(account)]
            wanted = {p["fb_page_id"] for p in parsed if p["fb_page_id"]}
            # Không gọi Batch API khi không còn page nào cần refresh — tránh round-trip
            # thừa (test_skips_page_name_fetch_when_recently_synced canh đúng điều này).
            stale_page_ids = _pages_needing_name_refresh(wanted)
            page_names = _fetch_page_names(app, stale_page_ids) if stale_page_ids else {}
            result = _upsert_catalog(account, parsed, page_names)
        except FacebookTokenError:
            # Token hỏng là sự cố cấp APP, không phải của riêng account này: nuốt ở
            # đây thì mọi account đều "lỗi lẻ", ngắt mạch token không bao giờ bật và
            # không ai được gọi đi cấp token mới. Phải leo thang lên task.
            raise
        except SoftTimeLimitExceeded:
            # Tín hiệu dừng mềm của Celery cũng kế thừa Exception — nuốt nó là vô
            # hiệu hoá soft limit, task sẽ bị hard-kill ở time_limit và giữ nguyên
            # khoá single_flight tới hết TTL.
            raise
        except Exception:  # noqa: BLE001 - 1 TKQC lỗi không được chặn các TKQC còn lại
            # Cùng guard với sibling `ad_spend_sync.sync_ad_spend_for_app`. Không có
            # nó, một non-200 của `_fetch_ads` ở TKQC thứ 3/50 làm hỏng catalog của
            # 47 TKQC còn lại, MỖI GIỜ. Hệ quả là tiền: ad thiếu trong catalog sẽ
            # được lazy-fetch với page = NULL nên chi phí rơi vào "Không rõ page".
            logger.exception(
                "facebook_ads ad catalog sync failed account_id=%s", account.id,
            )
            failed += 1
            continue
        synced_fb_ids.add(account.fb_id)
        for key in ("ads_upserted", "pages_upserted", "unresolved_pages"):
            total[key] += result[key]
    total["accounts"] = len(accounts)
    total["failed_accounts"] = failed
    return total
