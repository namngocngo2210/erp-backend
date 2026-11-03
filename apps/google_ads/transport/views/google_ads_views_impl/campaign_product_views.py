"""Gán chiến dịch Google Ads ↔ sản phẩm.

Cùng chữ ký và cùng hình dạng payload với bản Facebook — xem ghi chú ở
``apps/facebook_ads/.../campaign_product_views.py``.
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
from apps.google_ads.models import GoogleAdsCampaign, GoogleAdsCampaignDailySpend
from apps.orders._repositories_reports import GOOGLE_ADS_DAILY_COST_MULTIPLIER
from apps.orders.repositories import OrderRepository
from apps.users.permissions import require_method_permission

PLATFORM = "google"

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
    """Đọc bộ lọc TKQC — xem ghi chú ở bản Facebook.

    Giá trị có tiền tố nền tảng (``google:<customer_id>``); chọn TKQC Facebook thì
    endpoint này không trả dòng nào.
    """
    raw = (request.query_params.get("account") or "").strip()
    if not raw:
        return "", True
    platform, sep, value = raw.partition(":")
    if not sep:
        return raw, True
    return value, platform == PLATFORM


def _accounts_in_scope(scope_ids) -> list[dict]:
    """TKQC có chiến dịch, gom theo ``customer_id`` — mirror bản Facebook."""
    qs = GoogleAdsCampaign.objects.all()
    if scope_ids is not None:
        qs = qs.filter(ad_account_id__in=scope_ids)
    rows = qs.values("ad_account__customer_id", "ad_account__name").distinct()
    by_id: dict[str, str] = {}
    for row in rows:
        customer_id = row["ad_account__customer_id"]
        name = row["ad_account__name"] or ""
        if not by_id.get(customer_id):
            by_id[customer_id] = name
    return [
        {"platform": PLATFORM, "id": customer_id, "name": name or customer_id}
        for customer_id, name in sorted(by_id.items(), key=lambda item: (item[1] or "").lower())
    ]


def _cost_by_campaign(since, until, account_ids, account: str = "") -> dict[int, Decimal]:
    if since is None or until is None:
        return {}
    qs = GoogleAdsCampaignDailySpend.objects.filter(date__gte=since, date__lte=until)
    if account_ids is not None:
        qs = qs.filter(campaign__ad_account_id__in=account_ids)
    if account:
        qs = qs.filter(campaign__ad_account__customer_id=account)
    rows = qs.values("campaign_id").annotate(spend=Sum("spend"))
    return {
        row["campaign_id"]: (row["spend"] or Decimal("0")) * GOOGLE_ADS_DAILY_COST_MULTIPLIER
        for row in rows
    }


def _serialize(campaign: GoogleAdsCampaign, cost: Decimal) -> dict:
    return {
        "platform": PLATFORM,
        "id": campaign.id,
        "campaign_id": campaign.campaign_id,
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
    scope = OrderRepository.google_ads_account_scope(request.user)

    if request.method == "PATCH":
        return _patch(request, scope.ids)

    since, until, error = _parse_dates(request)
    if error:
        return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

    campaigns = GoogleAdsCampaign.objects.select_related("ad_account", "product")
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
        campaigns = campaigns.filter(product_source=GoogleAdsCampaign.SOURCE_AUTO_LOOSE)

    keyword = (request.query_params.get("q") or "").strip()
    if keyword:
        campaigns = campaigns.filter(name__icontains=keyword)

    account, same_platform = _account_param(request)
    if not same_platform:
        campaigns = campaigns.none()
    elif account:
        campaigns = campaigns.filter(ad_account__customer_id=account)

    cost_by_id = _cost_by_campaign(since, until, scope.ids, account if same_platform else "")
    rows = [_serialize(c, cost_by_id.get(c.id, Decimal("0"))) for c in campaigns]
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
        # Không phụ thuộc `assigned`/`q`/`account` — xem ghi chú ở bản Facebook.
        "accounts": _accounts_in_scope(scope.ids),
    })


def _patch(request, account_ids):
    campaign_pk = request.data.get("id")
    campaigns = GoogleAdsCampaign.objects.all()
    if account_ids is not None:
        campaigns = campaigns.filter(ad_account_id__in=account_ids)
    campaign = campaigns.filter(pk=campaign_pk).first()
    if campaign is None:
        return Response(
            {"detail": "Không tìm thấy chiến dịch."}, status=status.HTTP_404_NOT_FOUND,
        )

    raw_product = request.data.get("product_id", "__missing__")
    if raw_product == "__missing__":
        return Response({"detail": "Thiếu product_id."}, status=status.HTTP_400_BAD_REQUEST)

    if raw_product in (None, "", 0):
        campaign.product = None
        campaign.product_source = GoogleAdsCampaign.SOURCE_MANUAL
    else:
        product = Product.objects.filter(pk=raw_product, deleted_at__isnull=True).first()
        if product is None:
            return Response(
                {"detail": "Sản phẩm không tồn tại."}, status=status.HTTP_400_BAD_REQUEST,
            )
        campaign.product = product
        campaign.product_source = GoogleAdsCampaign.SOURCE_MANUAL

    campaign.product_updated_by = request.user
    campaign.product_updated_at = timezone.now()
    campaign.save(
        update_fields=["product", "product_source", "product_updated_by", "product_updated_at"]
    )
    campaign = GoogleAdsCampaign.objects.select_related("ad_account", "product").get(pk=campaign.pk)
    return Response(_serialize(campaign, Decimal("0")))
