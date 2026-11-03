"""Gán chiến dịch Facebook ↔ sản phẩm.

Cùng hình dạng payload với bản Google (``apps.google_ads``) — frontend gộp hai nguồn
trên một màn hình nên hai payload không được lệch nhau. Hai app nằm CÙNG tầng nên
không import lẫn nhau được; phần dùng chung duy nhất là parser ở ``apps.catalog``.
"""

from datetime import date as date_cls
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.catalog.models import Product
from apps.facebook_ads.models import FacebookAdCampaign, FacebookAdDailySpend
from apps.orders._repositories_reports import facebook_ads_cost_multiplier
from apps.orders.repositories import OrderRepository
from apps.users.permissions import require_method_permission

PLATFORM = "facebook"

# Cùng trần với báo cáo theo chiến dịch. Cắt mà không nói ra thì bảng "thiếu chiến
# dịch" trông y hệt bảng "đã gán hết" — nên trả kèm cờ `truncated`.
MAX_ROWS = 500


def _parse_dates(request):
    since_raw = (request.query_params.get("since") or "").strip()
    until_raw = (request.query_params.get("until") or "").strip()
    if not since_raw or not until_raw:
        return None, None, None
    try:
        since = date_cls.fromisoformat(since_raw)
        until = date_cls.fromisoformat(until_raw)
    except ValueError:
        return None, None, "since/until phải theo định dạng YYYY-MM-DD."
    if since > until:
        return None, None, "since phải trước hoặc bằng until."
    return since, until, None


def _account_param(request) -> tuple[str, bool]:
    """Đọc bộ lọc TKQC: trả ``(id ngoài của TKQC, có thuộc nền tảng này không)``.

    Giá trị đi kèm TIỀN TỐ nền tảng (``facebook:<account_id>``) vì màn hình gộp cả
    Facebook lẫn Google trên một ô lọc và gửi CÙNG một tham số cho cả hai endpoint.
    Không có tiền tố thì so bằng id thô là ván may rủi: id TKQC Facebook và
    customer_id Google đều là chuỗi số nên có thể trùng nhau.

    Chọn TKQC của nền tảng kia → nền tảng này không có dòng nào (nhưng vẫn trả đủ
    ``accounts`` để ô lọc không bị rụng lựa chọn).
    """
    raw = (request.query_params.get("account") or "").strip()
    if not raw:
        return "", True
    platform, sep, value = raw.partition(":")
    if not sep:
        return raw, True
    return value, platform == PLATFORM


def _accounts_in_scope(scope_ids) -> list[dict]:
    """Danh sách TKQC để đổ vào ô lọc — CHỈ tính TKQC thực sự có chiến dịch.

    Gom theo ``account_id`` (id bên Facebook) chứ không theo pk: một TKQC thật có thể
    nằm ở nhiều dòng ``facebook_ad_accounts`` (mỗi FacebookApp một dòng — lớp lỗi của
    MR !1199), lọc theo pk sẽ giấu mất chiến dịch của chính TKQC đó ở dòng còn lại.
    """
    qs = FacebookAdCampaign.objects.filter(ad_account__isnull=False)
    if scope_ids is not None:
        qs = qs.filter(ad_account_id__in=scope_ids)
    rows = qs.values("ad_account__account_id", "ad_account__name").distinct()
    by_id: dict[str, str] = {}
    for row in rows:
        account_id = row["ad_account__account_id"]
        name = row["ad_account__name"] or ""
        if not by_id.get(account_id):
            by_id[account_id] = name
    return [
        {"platform": PLATFORM, "id": account_id, "name": name or account_id}
        for account_id, name in sorted(by_id.items(), key=lambda item: (item[1] or "").lower())
    ]


def _cost_by_campaign(since, until, account_ids, account: str = "") -> dict[int, Decimal]:
    """Chi tiêu theo chiến dịch trong kỳ, đã nhân hệ số hiển thị.

    Không truyền khoảng ngày thì trả rỗng: bảng gán chiến dịch luôn đi kèm bộ lọc
    thời gian, và quét cả bảng spend để lấy "mọi thời đại" là một truy vấn không ai
    cần mà lại chậm.
    """
    if since is None or until is None:
        return {}
    qs = FacebookAdDailySpend.objects.filter(
        date__gte=since, date__lte=until, ad__campaign__isnull=False,
    )
    if account_ids is not None:
        qs = qs.filter(ad__ad_account_id__in=account_ids)
    if account:
        qs = qs.filter(ad__ad_account__account_id=account)
    rows = qs.values("ad__campaign_id").annotate(spend=Sum("spend"))
    # Hệ số theo NGÀY BẮT ĐẦU kỳ (xem `facebook_ads_cost_multiplier`) — cùng một
    # hệ số cho mọi dòng của cùng một lần xem.
    multiplier = facebook_ads_cost_multiplier(since)
    return {
        row["ad__campaign_id"]: (row["spend"] or Decimal("0")) * multiplier
        for row in rows
    }


def _serialize(campaign: FacebookAdCampaign, cost: Decimal) -> dict:
    return {
        "platform": PLATFORM,
        "id": campaign.id,
        "campaign_id": campaign.fb_campaign_id,
        "campaign_name": campaign.name,
        "account_name": campaign.ad_account.name if campaign.ad_account_id else "",
        "cost": f"{cost:.2f}",
        "product_id": campaign.product_id,
        "product_sku": campaign.product.sku if campaign.product_id else "",
        "product_name": campaign.product.name if campaign.product_id else "",
        "product_source": campaign.product_source,
    }


@extend_schema(exclude=True)
@api_view(["GET", "PATCH"])
@permission_classes([
    IsAuthenticated,
    require_method_permission(GET="ads-report:view", PATCH="ads-report:edit"),
])
def campaign_products(request):
    """GET: danh sách chiến dịch + sản phẩm đã gán. PATCH: gán/gỡ sản phẩm.

    PATCH nhận ``{"id": <pk chiến dịch>, "product_id": <pk SP hoặc null>}``.
    """
    scope = OrderRepository.ads_ad_account_scope(request.user)

    if request.method == "PATCH":
        return _patch(request, scope.ids)

    since, until, error = _parse_dates(request)
    if error:
        return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

    campaigns = FacebookAdCampaign.objects.select_related("ad_account", "product")
    if scope.ids is not None:
        campaigns = campaigns.filter(ad_account_id__in=scope.ids)

    assigned = (request.query_params.get("assigned") or "").strip()
    if assigned == "yes":
        campaigns = campaigns.filter(product__isnull=False)
    elif assigned == "no":
        campaigns = campaigns.filter(product__isnull=True)
    elif assigned == "guess":
        # Chỉ phần máy phải ĐOÁN (chọn biến thể theo doanh thu / tên nhắc nhiều sản phẩm
        # / gán theo bí danh học được). Đây là danh sách soát lại, ngắn hơn hẳn "đã gán".
        campaigns = campaigns.filter(product_source=FacebookAdCampaign.SOURCE_AUTO_LOOSE)

    keyword = (request.query_params.get("q") or "").strip()
    if keyword:
        campaigns = campaigns.filter(name__icontains=keyword)

    account, same_platform = _account_param(request)
    if not same_platform:
        campaigns = campaigns.none()
    elif account:
        campaigns = campaigns.filter(ad_account__account_id=account)

    cost_by_id = _cost_by_campaign(since, until, scope.ids, account if same_platform else "")
    rows = [_serialize(c, cost_by_id.get(c.id, Decimal("0"))) for c in campaigns]
    # Chi tiêu giảm dần: việc cần làm trước luôn là chiến dịch đang đốt nhiều tiền
    # nhất mà chưa gán sản phẩm.
    rows.sort(key=lambda row: Decimal(row["cost"]), reverse=True)

    assigned_cost = sum(
        (Decimal(row["cost"]) for row in rows if row["product_id"]), Decimal("0"),
    )
    unassigned_cost = sum(
        (Decimal(row["cost"]) for row in rows if not row["product_id"]), Decimal("0"),
    )
    return Response({
        "platform": PLATFORM,
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        "assigned_cost": f"{assigned_cost:.2f}",
        "unassigned_cost": f"{unassigned_cost:.2f}",
        "accessible_accounts": scope.count,
        # Danh sách KHÔNG phụ thuộc `assigned`/`q`/`account`: ô lọc mà tự rụng lựa chọn
        # theo chính lựa chọn vừa bấm thì không quay lại được.
        "accounts": _accounts_in_scope(scope.ids),
    })


def _patch(request, account_ids):
    campaign_pk = request.data.get("id")
    campaigns = FacebookAdCampaign.objects.all()
    if account_ids is not None:
        campaigns = campaigns.filter(ad_account_id__in=account_ids)
    campaign = campaigns.filter(pk=campaign_pk).first()
    if campaign is None:
        return Response(
            {"detail": "Không tìm thấy chiến dịch."}, status=status.HTTP_404_NOT_FOUND,
        )

    raw_product = request.data.get("product_id", "__missing__")
    if raw_product == "__missing__":
        return Response(
            {"detail": "Thiếu product_id."}, status=status.HTTP_400_BAD_REQUEST,
        )

    if raw_product in (None, "", 0):
        campaign.product = None
        # Gỡ gán CŨNG là quyết định của người dùng: giữ `manual` để lượt parser sau
        # không lặng lẽ gán lại đúng cái vừa bị gỡ.
        campaign.product_source = FacebookAdCampaign.SOURCE_MANUAL
    else:
        product = Product.objects.filter(pk=raw_product, deleted_at__isnull=True).first()
        if product is None:
            return Response(
                {"detail": "Sản phẩm không tồn tại."}, status=status.HTTP_400_BAD_REQUEST,
            )
        campaign.product = product
        campaign.product_source = FacebookAdCampaign.SOURCE_MANUAL

    campaign.product_updated_by = request.user
    campaign.product_updated_at = timezone.now()
    campaign.save(
        update_fields=["product", "product_source", "product_updated_by", "product_updated_at"]
    )
    campaign = FacebookAdCampaign.objects.select_related("ad_account", "product").get(
        pk=campaign.pk
    )
    return Response(_serialize(campaign, Decimal("0")))
