"""Báo cáo hiệu quả marketing: ghép chi phí Ads, nhóm 'Chưa ánh xạ', mẫu số 0."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.channels_core.models import Channel, ChannelAdAccount
from apps.customers.models import Customer
from apps.facebook_ads.models import FacebookAdAccount, FacebookAdAccountDailySpend, FacebookApp
from apps.foxia.application.services import report_service
from apps.foxia.models import (
    FoxiaBranch,
    FoxiaChannelMap,
    FoxiaChannelMapKind,
    FoxiaOrderLink,
    FoxiaSource,
)
from apps.orders.models import Order, OrderSource, OrderStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    channel = Channel.objects.create(code="fb-lp", name="FB Landing")
    app = FacebookApp.objects.create(name="App", app_id="1", app_secret="s", access_token="t")
    account = FacebookAdAccount.objects.create(app=app, name="TK", account_id="123", fb_id="act_123")
    ChannelAdAccount.objects.create(
        channel=channel, provider=ChannelAdAccount.Provider.FACEBOOK, account_id=account.pk
    )
    source = FoxiaSource.objects.create(foxia_id=55, name="FB-LP-01")
    FoxiaChannelMap.objects.create(
        kind=FoxiaChannelMapKind.SOURCE, foxia_id=55, channel=channel
    )
    branch = FoxiaBranch.objects.create(foxia_id=12, name="CN HN")
    customer = Customer.objects.create(code="FOXIA-12", full_name="[Foxia]")
    return {
        "channel": channel, "account": account, "source": source,
        "branch": branch, "customer": customer,
    }


def _mirror_order(setup, *, delivered=False, collected="1000000", ship_fee="0", returned=""):
    order = Order(
        channel=setup["channel"],
        customer=setup["customer"],
        order_source=OrderSource.FOXIA,
        status=OrderStatus.NEW,
        shipping_fee=Decimal(ship_fee),
    )
    order.save()
    now = timezone.now()
    return FoxiaOrderLink.objects.create(
        order=order,
        foxia_id=order.pk + 90000,
        source=setup["source"],
        branch=setup["branch"],
        ngayxacnhan=now,
        ngayhoanthanh=now if delivered else None,
        return_status=returned,
        amount_after_extra_discount=Decimal("1200000"),
        amount_collected=Decimal(collected),
    )


def _spend(setup, amount: str, day: date | None = None):
    FacebookAdAccountDailySpend.objects.create(
        ad_account=setup["account"], date=day or date.today(), spend=Decimal(amount)
    )


def _range():
    today = date.today()
    return today - timedelta(days=1), today + timedelta(days=1)


# ── chỉ tiêu ─────────────────────────────────────────────────────────────────


def test_cpo_and_cps_use_spend_over_order_counts(setup):
    _mirror_order(setup, delivered=True)
    _mirror_order(setup, delivered=False)
    _spend(setup, "600000")

    data = report_service.compute(*_range(), group_by="source")
    row = next(r for r in data["rows"] if r["mapped"])

    assert row["orders"] == 2
    assert row["delivered"] == 1
    assert row["spend"] == 600000
    assert row["cpo"] == 300000  # 600k / 2 đơn
    assert row["cps"] == 600000  # 600k / 1 đơn giao thành công


def test_delivered_is_detected_by_timestamp_not_status_code(setup):
    """Chỉ tiêu dựa trên MỐC THỜI GIAN nên chạy được khi chưa map trạng thái."""
    link = _mirror_order(setup, delivered=True)
    link.order.partner_delivery_status = "mã lạ chưa map"
    link.order.save()

    data = report_service.compute(*_range(), group_by="source")

    assert data["totals"]["delivered"] == 1


def test_roas_uses_collected_amount(setup):
    _mirror_order(setup, collected="2000000")
    _spend(setup, "500000")

    data = report_service.compute(*_range(), group_by="source")

    assert data["totals"]["roas"] == 4.0


def test_return_rate_counts_orders_with_return_status(setup):
    _mirror_order(setup, delivered=True, returned="da hoan")
    _mirror_order(setup, delivered=True)

    data = report_service.compute(*_range(), group_by="source")

    assert data["totals"]["returned"] == 1
    assert data["totals"]["return_rate"] == 50.0


def test_ratio_is_none_not_zero_when_denominator_is_zero(setup):
    """0 đọc như 'miễn phí'; None buộc giao diện hiển thị dấu gạch."""
    _mirror_order(setup, delivered=False)

    data = report_service.compute(*_range(), group_by="source")
    row = next(r for r in data["rows"] if r["mapped"])

    assert row["spend"] == 0
    assert row["cps"] is None  # không có đơn giao thành công
    assert row["roas"] is None  # không có chi tiêu


# ── ánh xạ nguồn ─────────────────────────────────────────────────────────────


def test_unmapped_source_still_shows_orders_without_spend(setup):
    """Nguồn chưa ánh xạ không được biến mất — chỉ là không có chi phí."""
    order = Order(
        channel=setup["channel"], customer=setup["customer"],
        order_source=OrderSource.FOXIA, status=OrderStatus.NEW,
    )
    order.save()
    FoxiaOrderLink.objects.create(
        order=order, foxia_id=12345, source=None, ngayxacnhan=timezone.now(),
        amount_after_extra_discount=Decimal("500000"), amount_collected=Decimal("500000"),
    )

    data = report_service.compute(*_range(), group_by="source")
    unmapped = next(r for r in data["rows"] if not r["mapped"])

    assert unmapped["label"] == report_service.UNMAPPED_LABEL
    assert unmapped["orders"] == 1
    assert unmapped["spend"] == 0
    # Đếm tường minh để không ai đọc bảng này là "đã phủ hết".
    assert data["unmapped_orders"] == 1


def test_unmapped_rows_are_merged_into_one(setup):
    for _ in range(3):
        order = Order(
            channel=setup["channel"], customer=setup["customer"],
            order_source=OrderSource.FOXIA, status=OrderStatus.NEW,
        )
        order.save()
        FoxiaOrderLink.objects.create(
            order=order, foxia_id=order.pk + 50000, source=None, ngayxacnhan=timezone.now()
        )

    data = report_service.compute(*_range(), group_by="source")

    assert len([r for r in data["rows"] if not r["mapped"]]) == 1
    assert data["unmapped_orders"] == 3


def test_source_without_channel_map_gets_no_spend(setup):
    """Nguồn có tồn tại nhưng chưa gán Channel thì không hưởng chi phí của nguồn khác."""
    other = FoxiaSource.objects.create(foxia_id=77, name="Chưa gán kênh")
    link = _mirror_order(setup)
    FoxiaOrderLink.objects.filter(pk=link.pk).update(source=other)
    _spend(setup, "900000")

    data = report_service.compute(*_range(), group_by="source")
    row = next(r for r in data["rows"] if r["label"] == "Chưa gán kênh")

    assert row["spend"] == 0


# ── lát cắt ──────────────────────────────────────────────────────────────────


def test_group_by_branch_has_no_spend_column(setup):
    """Tiền quảng cáo gắn với tài khoản Ads, không gắn với chi nhánh."""
    _mirror_order(setup)
    _spend(setup, "800000")

    data = report_service.compute(*_range(), group_by="branch")

    assert data["totals"]["orders"] == 1
    assert data["totals"]["spend"] == 0


def test_invalid_group_by_raises():
    with pytest.raises(ValueError):
        report_service.compute(*_range(), group_by="khong-ton-tai")


def test_date_range_filters_by_confirmation_date(setup):
    link = _mirror_order(setup)
    FoxiaOrderLink.objects.filter(pk=link.pk).update(
        ngayxacnhan=timezone.now() - timedelta(days=90)
    )

    data = report_service.compute(*_range(), group_by="source")

    assert data["totals"]["orders"] == 0
