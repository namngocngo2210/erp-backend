"""Bảng `foxia_lead_links` — cơ hội Foxia sinh ra từ một đơn landing của ta.

Hai nửa được chốt ở đây:
1. Chiều ĐẨY: response của Foxia mang `data.cohoi_id` → ghi lại, và việc ghi này
   KHÔNG được phép làm hỏng hợp đồng cũ của push (không raise, luôn có dòng
   `FoxiaOrderPush`).
2. Chiều ĐỐI CHIẾU: khi chiều đồng bộ kéo về `FoxiaOrderLink` cùng `cohoi_id`,
   job local điền `foxia_order_id` — không gọi mạng.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.foxia.application.services.order_push_service import FoxiaOrderPushService
from apps.foxia.models import (
    FoxiaLeadLink,
    FoxiaLinkCell,
    FoxiaLinkChannel,
    FoxiaOrderLink,
    FoxiaOrderPush,
    FoxiaPushStatus,
)
from apps.orders.models import Order, OrderItem

pytestmark = pytest.mark.django_db

PUSH_ON = override_settings(
    FOXIA_PUSH_ENABLED=True,
    FOXIA_PUSH_BASE_URL="https://foxia.test/duongdankhachhang",
    FOXIA_PUSH_TIMEOUT=10.0,
)

# Đúng phản hồi thật Foxia trả cho một lần đẩy thành công.
FOXIA_OK_BODY = '{"success": true, "message": false, "data": {"khachhang_id": 169, "cohoi_id": 167}}'


def _order(channel=None, **overrides):
    values = {
        "channel": channel or Channel.objects.create(code="kenh-a", name="Kênh A"),
        "order_source": "landing",
        "customer": Customer.objects.create(full_name="Khach test"),
        "name": "Nguyễn Văn A",
        "phone": "0337293367",
        "address": "9b Lê Quý Đôn",
    }
    values.update(overrides)
    return Order.objects.create(**values)


def _pushable_order():
    """Đơn landing đã đủ điều kiện đẩy: kênh có ô `landing` trong bảng liên kết."""
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")
    product = Product.objects.create(sku="SKU-A", name="SP A")
    order = _order(channel)
    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        sku=product.sku,
        quantity=1,
        unit_price=Decimal("100000"),
        line_total=Decimal("100000"),
    )
    FoxiaLinkCell.objects.create(
        link_channel=FoxiaLinkChannel.objects.get_or_create(channel=channel)[0],
        kind="landing",
        foxia_source_id=1234,
    )
    return order


def _response(status_code=200, text="OK"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def _push(order, response):
    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = response
        return FoxiaOrderPushService.push(order.id)


def _mirror_link(cohoi_id, foxia_id):
    """Đơn mirror + dòng liên kết mà chiều đồng bộ JSON-RPC dựng nên."""
    mirror = _order(
        Channel.objects.create(code=f"foxia-{foxia_id}", name="Kênh mirror"),
        order_source="foxia",
    )
    return FoxiaOrderLink.objects.create(order=mirror, foxia_id=foxia_id, cohoi_id=cohoi_id)


# ── chiều đẩy ────────────────────────────────────────────────────────────────


@PUSH_ON
def test_push_thanh_cong_luu_cohoi_va_khachhang():
    order = _pushable_order()

    assert _push(order, _response(200, FOXIA_OK_BODY)) == FoxiaPushStatus.OK

    lead = FoxiaLeadLink.objects.get(order=order)
    assert (lead.cohoi_id, lead.khachhang_id) == (167, 169)
    assert lead.foxia_order_id is None
    assert lead.matched_at is None


@PUSH_ON
def test_response_khong_phai_json_van_ok_va_khong_de_dong_rac():
    order = _pushable_order()

    assert _push(order, _response(200, "OK")) == FoxiaPushStatus.OK

    assert FoxiaLeadLink.objects.count() == 0
    assert FoxiaOrderPush.objects.get(order=order).status == FoxiaPushStatus.OK


@PUSH_ON
def test_push_that_bai_thi_khong_luu_co_hoi():
    """Body có cohoi_id nhưng HTTP 500 — chưa chắc Foxia đã tạo, không ghi nhận."""
    order = _pushable_order()

    assert _push(order, _response(500, FOXIA_OK_BODY)) == FoxiaPushStatus.FAILED

    assert FoxiaLeadLink.objects.count() == 0


@PUSH_ON
def test_day_hai_lan_cung_cohoi_chi_con_mot_dong():
    """Task Celery bị redeliver không được đẻ dòng trùng — `cohoi_id` là khoá."""
    order = _pushable_order()

    _push(order, _response(200, FOXIA_OK_BODY))
    _push(order, _response(200, FOXIA_OK_BODY))

    assert FoxiaLeadLink.objects.filter(cohoi_id=167).count() == 1
    assert FoxiaOrderPush.objects.filter(order=order).count() == 2


# ── chiều đối chiếu ──────────────────────────────────────────────────────────


def test_doi_chieu_dien_foxia_order_id_theo_cohoi():
    from apps.foxia.application.services.lead_match_service import FoxiaLeadMatchService

    lead = FoxiaLeadLink.objects.create(order=_order(), cohoi_id=167, khachhang_id=169)
    _mirror_link(cohoi_id=167, foxia_id=555)

    assert FoxiaLeadMatchService.match() == 1

    lead.refresh_from_db()
    assert lead.foxia_order_id == 555
    assert lead.matched_at is not None


def test_doi_chieu_bo_qua_co_hoi_chua_len_don():
    from apps.foxia.application.services.lead_match_service import FoxiaLeadMatchService

    lead = FoxiaLeadLink.objects.create(order=_order(), cohoi_id=167)

    assert FoxiaLeadMatchService.match() == 0

    lead.refresh_from_db()
    assert lead.foxia_order_id is None
    assert lead.matched_at is None


def test_doi_chieu_khong_dong_vao_dong_da_khop():
    """Foxia đổi đơn của cơ hội cũ không được ghi đè lịch sử đã chốt."""
    from apps.foxia.application.services.lead_match_service import FoxiaLeadMatchService

    lead = FoxiaLeadLink.objects.create(order=_order(), cohoi_id=167, foxia_order_id=111)
    _mirror_link(cohoi_id=167, foxia_id=555)

    assert FoxiaLeadMatchService.match() == 0

    lead.refresh_from_db()
    assert lead.foxia_order_id == 111


# ── task ─────────────────────────────────────────────────────────────────────


def test_task_doi_chieu_chay_service_va_bao_so_dong():
    from apps.foxia.tasks import match_lead_orders

    lead = FoxiaLeadLink.objects.create(order=_order(), cohoi_id=167)
    _mirror_link(cohoi_id=167, foxia_id=555)

    assert match_lead_orders() == "ok:1"

    lead.refresh_from_db()
    assert lead.foxia_order_id == 555


def test_task_doi_chieu_bo_qua_khi_luot_truoc_chua_xong():
    """Khoá single-flight giữ đúng một lượt chạy — như mọi task Foxia khác."""
    from apps.foxia.tasks import _LEAD_MATCH_LOCK_KEY, match_lead_orders

    FoxiaLeadLink.objects.create(order=_order(), cohoi_id=167)
    _mirror_link(cohoi_id=167, foxia_id=555)

    from django.core.cache import cache

    cache.add(_LEAD_MATCH_LOCK_KEY, "khoa-cua-luot-khac", 60)
    try:
        assert match_lead_orders() == "skipped"
    finally:
        cache.delete(_LEAD_MATCH_LOCK_KEY)

    assert FoxiaLeadLink.objects.get(cohoi_id=167).foxia_order_id is None
