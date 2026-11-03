import logging
import re as _re
from collections.abc import Iterable
from urllib.parse import quote as _quote, urlparse as _urlparse

from django.db import transaction
from django.utils import timezone

from decimal import Decimal

from apps.common.http_client import HttpClient
from .models import FacebookAdAccount, FacebookAdAccountDailySpend, FacebookApp

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
PAGE_LIMIT = 100

# Nhánh recurring refresh cửa sổ trailing này (số ngày lùi từ hôm nay), luôn gồm hôm nay.
RECENT_DAYS = 7

# Shared pooled client (retry on 429/5xx + per-call timeout override).
_client = HttpClient()


class FacebookGraphError(Exception):
    pass


class FacebookTokenError(FacebookGraphError):
    """Graph API từ chối vì access token — không phải lỗi tạm thời.

    Tách riêng vì cách xử lý khác hẳn: lỗi tạm thời (rate limit, 5xx) thì thử lại
    lượt sau, còn token hỏng thì thử lại bao nhiêu lần cũng vô nghĩa — phải ngắt
    mạch và gọi người cấp token mới.
    """


# Phân loại theo MÃ LỖI, không theo ``type``: Facebook trả ``type="OAuthException"``
# cho cả rate limit (code 4/17/613) lẫn lỗi tạm thời (code 1/2), nên bắt theo type
# sẽ khoá nhầm app đang khoẻ.
FB_AUTH_ERROR_CODES = frozenset({102, 190})
# Subcode của code 190: 458 app gỡ, 459/464 user bị checkpoint, 460 đổi mật khẩu,
# 463 hết hạn, 467 token không hợp lệ, 492 user mất quyền trên object.
FB_AUTH_ERROR_SUBCODES = frozenset({458, 459, 460, 463, 464, 467, 492})


def _is_auth_error(err: dict) -> bool:
    return (
        err.get("code") in FB_AUTH_ERROR_CODES
        or err.get("error_subcode") in FB_AUTH_ERROR_SUBCODES
    )


def _graph_error(err: dict, message: str, prefix: str) -> FacebookGraphError:
    text = f"{prefix}: {message}"
    return FacebookTokenError(text) if _is_auth_error(err) else FacebookGraphError(text)


def _error_from_response(response, prefix: str) -> FacebookGraphError:
    """Dựng exception đúng lớp từ một response non-200 của Graph API."""
    try:
        err = ((response.json() or {}).get("error")) or {}
    except ValueError:
        err = {}
    message = err.get("message") or response.text
    return _graph_error(err, message, prefix)


def _fetch_all_ad_accounts(access_token: str) -> list[dict]:
    url = f"{GRAPH_API_BASE}/me/adaccounts"
    base_params = {
        "fields": "name,account_id,spend_cap,currency",
        "access_token": access_token,
        "limit": PAGE_LIMIT,
    }
    results: list[dict] = []
    next_url: str | None = None
    after_cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        if next_url:
            response = _client.get(next_url, timeout=30)
        else:
            params = dict(base_params)
            if after_cursor:
                params["after"] = after_cursor
            response = _client.get(url, params=params, timeout=30)
        if response.status_code != 200:
            raise _error_from_response(response, "Graph API error")
        payload = response.json()
        data = payload.get("data") or []
        results.extend(data)
        paging = payload.get("paging") or {}
        next_url = paging.get("next")
        cursors = paging.get("cursors") or {}
        new_after = cursors.get("after")
        if next_url:
            continue
        if not data or not new_after or new_after in seen_cursors or len(data) < PAGE_LIMIT:
            break
        seen_cursors.add(new_after)
        after_cursor = new_after
    return results


@transaction.atomic
def sync_ad_accounts_for_app(app: FacebookApp) -> dict:
    items = _fetch_all_ad_accounts(app.access_token)
    now = timezone.now()
    seen_ids: set[str] = set()
    created = 0
    updated = 0
    for item in items:
        account_id = str(item.get("account_id") or "").strip()
        if not account_id:
            continue
        seen_ids.add(account_id)
        defaults = {
            "name": item.get("name") or "",
            "fb_id": item.get("id") or f"act_{account_id}",
            "spend_cap": str(item.get("spend_cap") or "0"),
            "currency": item.get("currency") or "",
            "status": FacebookAdAccount.STATUS_ACTIVE,
            "last_synced_at": now,
        }
        _, was_created = FacebookAdAccount.objects.update_or_create(
            app=app, account_id=account_id, defaults=defaults,
        )
        if was_created:
            created += 1
        else:
            updated += 1

    removed = FacebookAdAccount.objects.filter(app=app).exclude(account_id__in=seen_ids).update(
        status=FacebookAdAccount.STATUS_REMOVED, last_synced_at=now,
    )

    return {
        "fetched": len(items),
        "created": created,
        "updated": updated,
        "removed": removed,
        "synced_at": now.isoformat(),
    }


VALID_DATE_PRESETS = {
    "today",
    "yesterday",
    "last_3d",
    "last_7d",
    "last_14d",
    "last_28d",
    "last_30d",
    "last_90d",
    "this_week_mon_today",
    "this_week_sun_today",
    "this_month",
    "last_month",
    "this_quarter",
    "last_quarter",
    "this_year",
    "last_year",
    "maximum",
}

VALID_INSIGHT_LEVELS = {"account", "campaign", "adset", "ad"}

# Chặn vòng lặp `paging.next` bất thường: thà log ồn ào rồi cắt còn hơn treo mãi.
MAX_INSIGHTS_PAGES = 50


def fetch_ad_account_insights(
    ad_account: FacebookAdAccount,
    *,
    level: str = "campaign",
    date_preset: str | None = None,
    since: str | None = None,
    until: str | None = None,
    time_increment: str | int = 1,
) -> list[dict]:
    if level not in VALID_INSIGHT_LEVELS:
        raise FacebookGraphError(f"Invalid level: {level}")
    fields_by_level = {
        "account": "spend,impressions,clicks,ctr,cpm,reach",
        "campaign": "campaign_id,campaign_name,spend,impressions,clicks,ctr,cpm,reach",
        "adset": "campaign_name,adset_id,adset_name,spend,impressions,clicks,ctr,cpm,reach",
        "ad": "campaign_name,adset_name,ad_id,ad_name,spend,impressions,clicks,ctr,cpm,reach",
    }
    params: dict = {
        "fields": fields_by_level[level],
        "level": level,
        "time_increment": str(time_increment),
        "access_token": ad_account.app.access_token,
        "limit": 500,
    }
    if since and until:
        import json as _json

        params["time_range"] = _json.dumps({"since": since, "until": until})
    else:
        params["date_preset"] = date_preset or "last_30d"

    url = f"{GRAPH_API_BASE}/{ad_account.fb_id}/insights"
    results: list[dict] = []
    next_url: str | None = None
    while True:
        if next_url:
            response = _client.get(next_url, timeout=60)
        else:
            response = _client.get(url, params=params, timeout=60)
        if response.status_code != 200:
            raise _error_from_response(response, "Graph API error")
        payload = response.json()
        results.extend(payload.get("data") or [])
        next_url = (payload.get("paging") or {}).get("next")
        if not next_url:
            break
    return results


def _build_insights_query(
    *,
    level: str,
    fields: str,
    date_preset: str | None,
    since: str | None,
    until: str | None,
    time_increment: str | int,
) -> str:
    import json as _json
    from urllib.parse import urlencode

    params: dict[str, str] = {
        "fields": fields,
        "level": level,
        "time_increment": str(time_increment),
        "limit": "500",
    }
    if since and until:
        params["time_range"] = _json.dumps({"since": since, "until": until})
    else:
        params["date_preset"] = date_preset or "last_30d"
    return urlencode(params)


def _ensure_access_token(url: str, access_token: str) -> str:
    """Bảo đảm URL phân trang có `access_token`. Đã có sẵn thì trả nguyên URL.

    Vì sao cần: `paging.next` ở đây lấy từ BODY của một sub-request trong Batch API,
    mà sub-request không mang token riêng (token cấp một lần ở envelope). Nếu URL FB
    sinh ra không kèm token thì GET tiếp theo trả error 190 → `_error_from_response`
    xếp vào FacebookTokenError → task gọi `mark_token_invalid(app)` → ngắt mạch chặn
    luôn cả grain account và báo cáo ads sang Lark. Một token KHOẺ bị khai tử toàn
    công ty chỉ vì một URL phân trang.

    KHÔNG nối đè khi token đã có (URL FB sinh thường có sẵn): hai tham số
    `access_token` trong cùng query là request hỏng. Cũng KHÔNG parse rồi encode lại
    query — cursor của FB là base64 có ký tự `+`, roundtrip qua `parse_qsl/urlencode`
    sẽ đổi `+` thành khoảng trắng và làm hỏng cursor.
    """
    if not access_token:
        return url
    query = _urlparse(url).query
    if _re.search(r"(^|&)access_token=", query):
        return url
    separator = "&" if query else "?"
    return f"{url}{separator}access_token={_quote(access_token, safe='')}"


def _follow_insights_pages(
    first_data: list[dict], next_url: str | None, access_token: str,
) -> list[dict]:
    """Theo `paging.next` của 1 sub-request trong Batch API cho tới hết trang.

    `limit=500` trong query chưa từng tràn ở level=account (7 ngày = 7 dòng/account),
    nên bug im lặng cắt bớt data tồn tại lâu mà không ai thấy. Ở level=ad với
    time_increment=1, số dòng = (số ad) × (số ngày) — 1 account >16 ad chạy backfill
    1 tháng là vượt 500 dòng, FB không báo lỗi, chỉ trả ít dòng hơn thực tế. Đây là
    mất tiền âm thầm — đúng thứ cả feature này sinh ra để ngăn.
    """
    data = list(first_data)
    pages = 1
    while next_url:
        if pages >= MAX_INSIGHTS_PAGES:
            logger.warning(
                "facebook_ads insights pagination chạm giới hạn %d trang — có thể "
                "vẫn còn dữ liệu chưa lấy, next=%s",
                MAX_INSIGHTS_PAGES, next_url,
            )
            break
        response = _client.get(_ensure_access_token(next_url, access_token), timeout=60)
        if response.status_code != 200:
            # Trang lỗi (vd token hỏng giữa chừng) phải leo thang như batch chính,
            # không được âm thầm bỏ qua — im lặng ở đây tái tạo đúng bug đang sửa.
            raise _error_from_response(response, "Graph API error (pagination)")
        payload = response.json()
        data.extend(payload.get("data") or [])
        next_url = (payload.get("paging") or {}).get("next")
        pages += 1
    return data


def fetch_insights_batch(
    app: FacebookApp,
    ad_accounts: list[FacebookAdAccount],
    *,
    level: str = "account",
    date_preset: str | None = None,
    since: str | None = None,
    until: str | None = None,
    time_increment: str | int = "all_days",
    chunk_size: int = 50,
) -> dict[int, list[dict]]:
    if level not in VALID_INSIGHT_LEVELS:
        raise FacebookGraphError(f"Invalid level: {level}")
    fields_by_level = {
        "account": "spend,impressions,clicks,ctr,cpm,reach",
        "campaign": "campaign_id,campaign_name,spend,impressions,clicks,ctr,cpm,reach",
        "adset": "campaign_name,adset_id,adset_name,spend,impressions,clicks,ctr,cpm,reach",
        "ad": "campaign_name,adset_name,ad_id,ad_name,spend,impressions,clicks,ctr,cpm,reach",
    }
    query = _build_insights_query(
        level=level,
        fields=fields_by_level[level],
        date_preset=date_preset,
        since=since,
        until=until,
        time_increment=time_increment,
    )

    results: dict[int, list[dict]] = {a.id: [] for a in ad_accounts}
    if not ad_accounts:
        return results

    import json as _json

    for i in range(0, len(ad_accounts), chunk_size):
        chunk = ad_accounts[i : i + chunk_size]
        batch = [
            {
                "method": "GET",
                "name": f"acc_{a.id}",
                "relative_url": f"{a.fb_id}/insights?{query}",
            }
            for a in chunk
        ]
        response = _client.post(
            f"{GRAPH_API_BASE}/",
            data={
                "access_token": app.access_token,
                "batch": _json.dumps(batch),
                "include_headers": "false",
            },
            timeout=120,
        )
        if response.status_code != 200:
            raise _error_from_response(response, "Batch error")
        sub_results = response.json() or []
        for acc, sub in zip(chunk, sub_results):
            if not sub:
                continue
            sub_code = sub.get("code")
            sub_body_raw = sub.get("body") or "{}"
            try:
                sub_body = _json.loads(sub_body_raw)
            except (TypeError, ValueError):
                sub_body = {}
            if sub_code != 200:
                sub_err = sub_body.get("error") or {}
                err_msg = sub_err.get("message", "Unknown error")
                # Sub-request lỗi xác thực = token cấp app hỏng, không phải sự cố
                # riêng của account này. Ghi nhận "1 account lỗi" rồi đi tiếp sẽ
                # biến token chết thành chi tiêu 0đ trên báo cáo — phải leo thang.
                if _is_auth_error(sub_err):
                    raise FacebookTokenError(f"Batch sub-request error: {err_msg}")
                logger.warning(
                    "facebook_ads insights batch failed account_id=%s sub_code=%s error=%s",
                    acc.id, sub_code, err_msg,
                )
                results[acc.id] = []
                # Sentinel key "_errors" (str, không phải account id int) — mọi nơi
                # đọc `results` theo id account (int) phải dùng .get(acc.id) nên
                # không va chạm. Trước đây .setdefault(...) đứng riêng một dòng nên
                # không gán được gì vào dict trả về — lỗi sub-account bị nuốt im
                # lặng (token hết hạn → tưởng chi tiêu = 0).
                results.setdefault("_errors", {})[acc.id] = err_msg  # type: ignore[index]
                continue
            next_url = (sub_body.get("paging") or {}).get("next")
            results[acc.id] = _follow_insights_pages(
                sub_body.get("data") or [], next_url, app.access_token,
            )
    return results


def _log_batch_errors(results: dict, accounts: list[FacebookAdAccount]) -> int:
    """Đọc sentinel `_errors` từ `fetch_insights_batch`, log tổng số account lỗi.

    Trước fix task 2.7, `_errors` luôn rỗng (bug) nên account bị Graph API từ chối
    (vd token hết hạn) lặng lẽ ghi chi tiêu = 0 — không ai thấy trong log/Sentry.
    Trả về số account lỗi để caller cộng dồn vào summary.
    """
    errors: dict = results.get("_errors") or {}
    if errors:
        logger.warning(
            "facebook_ads sync_daily_spend: %d/%d account(s) failed: %s",
            len(errors), len(accounts), errors,
        )
    return len(errors)


def _upsert_daily_rows(acc: FacebookAdAccount, rows: list[dict]) -> int:
    """Ghi các dòng insights theo ngày của một TKQC bằng MỘT upsert.

    Trước: `update_or_create` từng dòng (SELECT + UPDATE/INSERT) × 365 ngày × N TKQC
    mỗi phút — 111k UPDATE/20h trên `facebook_ad_account_daily_spends` qua PgBouncer
    (audit 2026-09-23 T1). Cùng khuôn `ad_spend_sync._upsert_ad_rows`:
    `bulk_create(update_conflicts=True)` theo unique (ad_account, date). Khử trùng
    `date_start` trước — Postgres cấm ON CONFLICT DO UPDATE chạm một dòng hai lần;
    giữ dòng đứng SAU (FB gửi lại = bản mới hơn, cùng luật với `update_or_create`).
    """
    deduped: dict[str, dict] = {}
    for r in rows:
        date_str = r.get("date_start")
        if not date_str:
            continue
        deduped[date_str] = r
    objs = [
        FacebookAdAccountDailySpend(
            ad_account=acc,
            date=date_str,
            spend=Decimal(str(r.get("spend") or "0")),
            impressions=int(r.get("impressions") or 0),
            clicks=int(r.get("clicks") or 0),
            reach=int(r.get("reach") or 0),
        )
        for date_str, r in deduped.items()
    ]
    if objs:
        FacebookAdAccountDailySpend.objects.bulk_create(
            objs,
            update_conflicts=True,
            update_fields=["spend", "impressions", "clicks", "reach", "last_synced_at"],
            unique_fields=["ad_account", "date"],
        )
    return len(objs)


def sync_daily_spend_for_app(
    app: FacebookApp,
    *,
    date_preset: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Sync per-day insights into DailySpend table.

    Uses Batch API + time_increment=1 to get daily rows.
    Accounts that have no data yet → backfill the full 365 days.
    Accounts with data → refresh a trailing window ending today (catch late updates + today).
    Custom range via since/until or date_preset overrides the per-account logic.

    NB: dùng time_range (since/until) cho nhánh recurring thay vì date_preset="last_7d".
    Preset last_7d của FB KHÔNG bao gồm ngày hôm nay (7 ngày kết thúc ở hôm qua), nên cron
    mỗi phút sẽ không bao giờ cập nhật chi tiêu hôm nay — buộc phải bấm đồng bộ tay.
    """
    accounts = list(FacebookAdAccount.objects.filter(app=app, status=FacebookAdAccount.STATUS_ACTIVE))
    if not accounts:
        return {"accounts": 0, "rows_upserted": 0}

    if since and until:
        results = fetch_insights_batch(
            app, accounts, level="account", since=since, until=until, time_increment="1",
        )
        failed = _log_batch_errors(results, accounts)
        upserted = sum(_upsert_daily_rows(a, results.get(a.id) or []) for a in accounts)
        return {"accounts": len(accounts), "rows_upserted": upserted, "failed_accounts": failed}

    if date_preset:
        results = fetch_insights_batch(
            app, accounts, level="account", date_preset=date_preset, time_increment="1",
        )
        failed = _log_batch_errors(results, accounts)
        upserted = sum(_upsert_daily_rows(a, results.get(a.id) or []) for a in accounts)
        return {"accounts": len(accounts), "rows_upserted": upserted, "failed_accounts": failed}

    from datetime import timedelta as _td

    today = timezone.localdate()
    yr_since = (today - _td(days=365)).isoformat()
    yr_until = today.isoformat()
    recent_since = (today - _td(days=RECENT_DAYS)).isoformat()

    has_data_ids = set(
        FacebookAdAccountDailySpend.objects.filter(ad_account__in=accounts)
        .values_list("ad_account_id", flat=True)
        .distinct()
    )
    fresh = [a for a in accounts if a.id not in has_data_ids]
    recurring = [a for a in accounts if a.id in has_data_ids]

    upserted = 0
    failed = 0
    if fresh:
        results = fetch_insights_batch(
            app, fresh, level="account", since=yr_since, until=yr_until, time_increment="1",
        )
        failed += _log_batch_errors(results, fresh)
        upserted += sum(_upsert_daily_rows(a, results.get(a.id) or []) for a in fresh)
    if recurring:
        results = fetch_insights_batch(
            app, recurring, level="account",
            since=recent_since, until=yr_until, time_increment="1",
        )
        failed += _log_batch_errors(results, recurring)
        upserted += sum(_upsert_daily_rows(a, results.get(a.id) or []) for a in recurring)

    return {
        "accounts": len(accounts),
        "backfilled_accounts": len(fresh),
        "rows_upserted": upserted,
        "failed_accounts": failed,
    }


def list_ad_accounts(app: FacebookApp, *, include_removed: bool = False) -> Iterable[FacebookAdAccount]:
    qs = FacebookAdAccount.objects.filter(app=app)
    if not include_removed:
        qs = qs.filter(status=FacebookAdAccount.STATUS_ACTIVE)
    return qs.order_by("name")
