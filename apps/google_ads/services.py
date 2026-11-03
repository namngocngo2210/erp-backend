"""Tầng tích hợp Google Ads API.

Mirror của apps/facebook_ads/services.py nhưng dùng thư viện chính chủ `google-ads`
(gRPC) + truy vấn GAQL thay cho REST. Thư viện được import lazy bên trong hàm để app
vẫn load được khi chưa cài lib (hữu ích cho unit test mock).

Lưu ý quan trọng: Google trả chi phí ở đơn vị `cost_micros` (micro) — phải chia
1_000_000 mới ra số tiền thật.
"""

import logging
from datetime import date as date_cls, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import GoogleAdsAccount, GoogleAdsAccountDailySpend, GoogleAdsConfig

logger = logging.getLogger(__name__)

# Không ép API version — để thư viện google-ads tự dùng version mặc định (mới nhất nó
# hỗ trợ), tránh lệch khi nâng/hạ phiên bản lib. Lưu ý: phải giữ google-ads đủ mới vì
# Google chặn các API version cũ (vd v20 bị deprecate → cần google-ads>=31 dùng v24).
MICROS_PER_UNIT = Decimal(1_000_000)
BACKFILL_DAYS = 365
RECENT_DAYS = 7

# Google trả trạng thái customer dạng enum proto-plus.
# Chỉ hạ trạng thái khi đọc ra ĐÚNG một giá trị Google định nghĩa là đã đóng.
# Giá trị lạ (lib đổi kiểu trả về, status mới của Google) giữ nguyên active: đánh
# nhầm thành removed làm sync bỏ qua tài khoản và chi phí âm thầm về 0 — hỏng nặng
# hơn nhiều so với việc tốn ít quota gọi API cho một tài khoản đã đóng, nhất là khi
# lỗi per-account đã được cách ly.
INACTIVE_CUSTOMER_STATUSES = frozenset({"CANCELED", "SUSPENDED", "CLOSED"})


class GoogleAdsApiError(Exception):
    pass


def _enum_name(value) -> str:
    """Tên enum dạng chuỗi.

    Thư viện google-ads (use_proto_plus) trả enum có `.name`; test và một số phiên
    bản trả thẳng chuỗi. Đọc cả hai để tầng gọi không phải biết đang chạy với dạng nào.
    """
    name = getattr(value, "name", None)
    return name if isinstance(name, str) else str(value)


def _safe_iso_date(value: str) -> str:
    """Chuẩn hóa & xác thực ngày về dạng canonical YYYY-MM-DD.

    Chặn GAQL injection: chuỗi date được nội suy vào query nên phải parse chặt
    bằng date.fromisoformat (raise nếu không đúng YYYY-MM-DD) rồi re-emit isoformat.
    """
    try:
        return date_cls.fromisoformat(str(value)).isoformat()
    except (ValueError, TypeError) as exc:
        raise GoogleAdsApiError(f"Ngày không hợp lệ (cần YYYY-MM-DD): {value!r}") from exc


def _build_client(config: GoogleAdsConfig):
    """Khởi tạo GoogleAdsClient từ credential lưu trong DB (không dùng file YAML)."""
    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError as exc:  # pragma: no cover - chỉ xảy ra khi chưa cài lib
        raise GoogleAdsApiError(
            "Thư viện google-ads chưa được cài đặt (pip install google-ads)."
        ) from exc

    cfg = {
        "developer_token": config.developer_token,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": config.refresh_token,
        "login_customer_id": config.login_customer_id,
        "use_proto_plus": True,
    }
    try:
        return GoogleAdsClient.load_from_dict(cfg)
    except Exception as exc:
        raise GoogleAdsApiError(f"Không khởi tạo được Google Ads client: {exc}") from exc


def _search(client, customer_id: str, query: str):
    """Chạy GAQL, trả iterator các row. Bọc lỗi GoogleAdsException thành GoogleAdsApiError."""
    try:
        from google.ads.googleads.errors import GoogleAdsException
    except ImportError as exc:  # pragma: no cover
        raise GoogleAdsApiError("Thư viện google-ads chưa được cài đặt.") from exc

    service = client.get_service("GoogleAdsService")
    try:
        return service.search(customer_id=customer_id, query=query)
    except GoogleAdsException as exc:
        messages = "; ".join(
            error.message for error in exc.failure.errors
        ) or str(exc)
        raise GoogleAdsApiError(f"Google Ads API error: {messages}") from exc


@transaction.atomic
def sync_accounts_for_config(config: GoogleAdsConfig) -> dict:
    """Liệt kê MỌI customer dưới cây MCC và upsert vào GoogleAdsAccount.

    KHÔNG lọc `customer_client.level`: cây tài khoản Google thường là
    MCC → MCC con → TKQC, giới hạn độ sâu 1 làm mọi tài khoản dưới MCC con vô hình.
    `login_customer_id` đã trỏ MCC gốc nên gọi API cho tài khoản lồng sâu vẫn hợp lệ.
    """
    client = _build_client(config)
    query = """
        SELECT
            customer_client.id,
            customer_client.descriptive_name,
            customer_client.currency_code,
            customer_client.status,
            customer_client.manager,
            customer_client.level
        FROM customer_client
    """
    rows = _search(client, config.login_customer_id, query)

    now = timezone.now()
    seen_ids: set[str] = set()
    created = 0
    updated = 0
    for row in rows:
        cc = row.customer_client
        # Bỏ qua tài khoản quản lý (MCC gốc và MCC con) — chúng không tự chạy ads.
        if cc.manager:
            continue
        customer_id = str(cc.id).strip()
        if not customer_id:
            continue
        # Cùng một customer có thể xuất hiện dưới nhiều MCC con; update_or_create
        # theo (config, customer_id) tự khử trùng.
        seen_ids.add(customer_id)
        status = (
            GoogleAdsAccount.STATUS_REMOVED
            if _enum_name(cc.status) in INACTIVE_CUSTOMER_STATUSES
            else GoogleAdsAccount.STATUS_ACTIVE
        )
        defaults = {
            "name": cc.descriptive_name or customer_id,
            "currency": cc.currency_code or "",
            "status": status,
            "last_synced_at": now,
        }
        _, was_created = GoogleAdsAccount.objects.update_or_create(
            config=config, customer_id=customer_id, defaults=defaults,
        )
        if was_created:
            created += 1
        else:
            updated += 1

    removed = (
        GoogleAdsAccount.objects.filter(config=config)
        .exclude(customer_id__in=seen_ids)
        .update(status=GoogleAdsAccount.STATUS_REMOVED, last_synced_at=now)
    )

    return {
        "fetched": len(seen_ids),
        "created": created,
        "updated": updated,
        "removed": removed,
        "synced_at": now.isoformat(),
    }


def _fetch_daily_rows(client, customer_id: str, since: str, until: str) -> list[dict]:
    """Lấy chi phí theo ngày của 1 customer trong khoảng [since, until]."""
    # Phòng GAQL injection: chỉ cho phép YYYY-MM-DD đã chuẩn hóa.
    since = _safe_iso_date(since)
    until = _safe_iso_date(until)
    query = f"""
        SELECT
            segments.date,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks
        FROM customer
        WHERE segments.date BETWEEN '{since}' AND '{until}'
    """
    rows = _search(client, customer_id, query)
    out: list[dict] = []
    for row in rows:
        out.append(
            {
                "date": row.segments.date,
                "spend": (Decimal(row.metrics.cost_micros) / MICROS_PER_UNIT).quantize(Decimal("0.01")),
                "impressions": int(row.metrics.impressions or 0),
                "clicks": int(row.metrics.clicks or 0),
            }
        )
    return out


def _upsert_daily_rows(account: GoogleAdsAccount, rows: list[dict]) -> int:
    upserted = 0
    for row in rows:
        GoogleAdsAccountDailySpend.objects.update_or_create(
            ad_account=account,
            date=row["date"],
            defaults={
                "spend": row["spend"],
                "impressions": row["impressions"],
                "clicks": row["clicks"],
            },
        )
        upserted += 1
    return upserted


def sync_daily_spend_for_config(
    config: GoogleAdsConfig,
    *,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Đồng bộ chi phí theo ngày cho mọi customer active của config.

    - Có since/until → dùng đúng khoảng đó cho mọi account.
    - Không truyền → account chưa có data backfill 365 ngày, account đã có refresh 7 ngày.

    Lỗi được cách ly THEO TỪNG ACCOUNT: một tài khoản mất quyền không được phép làm
    những tài khoản còn lại mất luôn lượt đồng bộ. Nhưng cũng KHÔNG nuốt im lặng —
    số tài khoản hỏng trả về trong `failed_accounts` và ghi log, đúng bài học sự cố
    token Facebook 25–27/07: lỗi bị bỏ qua sẽ hiện thành "chi tiêu 0đ" trên báo cáo,
    không phân biệt được với "hôm nay không chạy quảng cáo".
    """
    accounts = list(
        GoogleAdsAccount.objects.filter(config=config, status=GoogleAdsAccount.STATUS_ACTIVE)
    )
    if not accounts:
        return {"accounts": 0, "rows_upserted": 0, "failed_accounts": 0}

    client = _build_client(config)

    today = timezone.localdate()
    if since and until:
        windows = {account.id: (since, until) for account in accounts}
    else:
        backfill_since = (today - timedelta(days=BACKFILL_DAYS)).isoformat()
        recent_since = (today - timedelta(days=RECENT_DAYS)).isoformat()
        until_str = today.isoformat()
        has_data_ids = set(
            GoogleAdsAccountDailySpend.objects.filter(ad_account__in=accounts)
            .values_list("ad_account_id", flat=True)
            .distinct()
        )
        windows = {
            account.id: (
                recent_since if account.id in has_data_ids else backfill_since,
                until_str,
            )
            for account in accounts
        }

    upserted = 0
    failed = 0
    for account in accounts:
        start, end = windows[account.id]
        try:
            rows = _fetch_daily_rows(client, account.customer_id, start, end)
        except GoogleAdsApiError as exc:
            failed += 1
            logger.warning(
                "google_ads sync_daily_spend bỏ qua customer %s (config %s): %s",
                account.customer_id, config.id, exc,
            )
            continue
        upserted += _upsert_daily_rows(account, rows)

    if failed:
        logger.warning(
            "google_ads sync_daily_spend: %d/%d tài khoản lỗi (config %s)",
            failed, len(accounts), config.id,
        )

    return {"accounts": len(accounts), "rows_upserted": upserted, "failed_accounts": failed}
