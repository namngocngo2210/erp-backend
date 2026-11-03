"""Ràng buộc schema của apps.pancake — khoá chống trùng (channel, phone) và ánh
xạ platform → cột Foxia. Sai cái nào cũng hỏng im lặng: trùng thì khách bị đẩy
hai lần sang Foxia, sai cột thì cơ hội rơi vào nguồn khác."""

import pytest
from django.db import IntegrityError, transaction

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order
from apps.pancake.models import PancakeLead, PancakePage, PancakePushStatus, PancakeStaffMap
from apps.users.models import User

pytestmark = pytest.mark.django_db


def _page(**overrides):
    values = {"pancake_page_id": "945997011940587", "name": "DELAX", "platform": "facebook"}
    values.update(overrides)
    return PancakePage.objects.create(**values)


def _order(channel, **overrides):
    # Order.customer là FK PROTECT không null — dựng kèm Customer như
    # apps/foxia/tests/test_foxia_order_push.py::_order.
    values = {
        "channel": channel,
        "order_source": "messenger",
        "customer": Customer.objects.create(full_name="Khach test"),
        "name": "Nguyễn Văn A",
        "phone": "0901234567",
        "address": "9b Lê Quý Đôn",
        "ward": "Phường Bạch Đằng",
        "district": "Hai Bà Trưng",
        "province": "Hà Nội",
    }
    values.update(overrides)
    return Order.objects.create(**values)


def _lead(page, channel, phone="0901234567", **overrides):
    values = {
        "page": page,
        "channel": channel,
        "conversation_id": f"{page.pancake_page_id}_1",
        "phone_raw": phone,
        "phone": phone,
        "foxia_kind": "messenger",
    }
    values.update(overrides)
    return PancakeLead.objects.create(**values)


def test_foxia_kind_theo_platform():
    assert _page().foxia_kind == "messenger"
    assert _page(pancake_page_id="zl_1", platform="zalo").foxia_kind == "zalo"
    assert _page(pancake_page_id="pzl_1", platform="personal_zalo").foxia_kind == "zalo"
    assert _page(pancake_page_id="tts_1", platform="tiktok_shop").foxia_kind is None


def test_cung_kenh_cung_sdt_khong_tao_dong_thu_hai():
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")
    page = _page()
    _lead(page, channel)
    with pytest.raises(IntegrityError), transaction.atomic():
        _lead(page, channel, conversation_id="hoi-thoai-khac")


def test_cung_sdt_khac_kenh_thi_duoc():
    a = Channel.objects.create(code="kenh-a", name="Kênh A")
    b = Channel.objects.create(code="kenh-b", name="Kênh B")
    page = _page()
    _lead(page, a)
    _lead(page, b)
    assert PancakeLead.objects.count() == 2


def test_lead_mac_dinh_pending_va_tags_rong():
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")
    lead = _lead(_page(), channel)
    assert lead.push_status == PancakePushStatus.PENDING
    assert lead.tags == []
    assert lead.foxia_source_id is None


def test_staff_map_pancake_name_unique():
    PancakeStaffMap.objects.create(pancake_name="Nhung HN")
    with pytest.raises(IntegrityError), transaction.atomic():
        PancakeStaffMap.objects.create(pancake_name="Nhung HN")


def test_staff_map_gan_duoc_user_va_cho_null():
    user = User.objects.create_user(username="nhung-hn", password="x")
    mapped = PancakeStaffMap.objects.create(pancake_name="Nhung HN", user=user)
    unmapped = PancakeStaffMap.objects.create(pancake_name="Tên chưa gặp")
    assert mapped.user_id == user.id
    assert unmapped.user_id is None


def test_lead_luu_duoc_order_va_truy_nguoc_qua_related_name():
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")
    order = _order(channel)
    lead = _lead(_page(), channel, order=order)
    assert lead.order_id == order.id
    assert order.pancake_leads.count() == 1


def test_trang_thai_moi_co_trong_push_status_values():
    assert "order_created" in PancakePushStatus.values
    assert "order_failed" in PancakePushStatus.values
