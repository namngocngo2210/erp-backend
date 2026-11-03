"""Luồng đẩy đơn landing sang Foxia.

Ba điều được chốt ở đây, vì sai cái nào cũng hỏng im lặng:
1. Chọn id theo (kênh, cột = `order_source` của đơn) trong bảng liên kết —
   không theo SKU (đổi trục 2026-09-16); `message` mang `product_raw` của đơn.
2. Mọi nhánh đều ghi đúng MỘT dòng nhật ký, kể cả nhánh bỏ qua.
3. Lỗi phía Foxia KHÔNG được raise ra khỏi task.
"""

import logging
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import pytest
import requests
from django.db import transaction
from django.test import override_settings
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.foxia.application.services.order_push_service import (
    FoxiaOrderPushService,
    build_address,
    build_payload,
)
from apps.foxia.domain.datetime_utils import to_foxia_utc
from apps.foxia.models import (
    FoxiaLinkCell,
    FoxiaLinkChannel,
    FoxiaOrderPush,
    FoxiaPushStatus,
)
from apps.orders.models import Order, OrderItem
from apps.users.tests.helpers import make_admin

pytestmark = pytest.mark.django_db

PUSH_ON = override_settings(
    FOXIA_PUSH_ENABLED=True,
    FOXIA_PUSH_BASE_URL="https://foxia.test/duongdankhachhang",
    FOXIA_PUSH_TIMEOUT=10.0,
)


def _channel(code="kenh-a"):
    return Channel.objects.create(code=code, name=f"Kênh {code}")


def _product(sku):
    return Product.objects.create(sku=sku, name=f"SP {sku}")


def _order(channel, **overrides):
    # Order.customer là FK PROTECT không null (xem apps/orders/tests/conftest.py
    # order_factory) — bản trong brief chưa tính field này, phải dựng kèm ở đây.
    values = {
        "channel": channel,
        "order_source": "landing",
        "customer": Customer.objects.create(full_name="Khach test"),
        "name": "Nguyễn Văn A",
        "phone": "0337293367",
        "address": "9b Lê Quý Đôn",
        "ward": "Phường Bạch Đằng",
        "district": "Hai Bà Trưng",
        "province": "Hà Nội",
    }
    values.update(overrides)
    return Order.objects.create(**values)


def _item(order, product):
    return OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        sku=product.sku,
        quantity=1,
        unit_price=Decimal("100000"),
        line_total=Decimal("100000"),
    )


def _map(channel, foxia_source_id, kind="landing"):
    return FoxiaLinkCell.objects.create(
        link_channel=FoxiaLinkChannel.objects.get_or_create(channel=channel)[0],
        kind=kind,
        foxia_source_id=foxia_source_id,
    )


def _response(status_code=200, text="OK"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


@PUSH_ON
def test_day_don_mot_sku_goi_dung_url_va_payload():
    channel = _channel()
    product = _product("SKU-A")
    order = _order(channel, product_raw=" Mua 2 thảm massage giá 3.199.000 ")
    _item(order, product)
    _map(channel, 1234)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response()
        assert FoxiaOrderPushService.push(order.id) == FoxiaPushStatus.OK

    call = client_cls.return_value.__enter__.return_value.post.call_args
    assert call.args[0] == "https://foxia.test/duongdankhachhang/1234"
    assert call.kwargs["json"] == {
        "name": "Nguyễn Văn A",
        "phone": "0337293367",
        "address": "9b Lê Quý Đôn, Phường Bạch Đằng, Hai Bà Trưng, Hà Nội",
        "chance_id": 1,
        "created_at": to_foxia_utc(order.created_at),
        "message": "Mua 2 thảm massage giá 3.199.000",
    }
    log = FoxiaOrderPush.objects.get(order=order)
    assert (log.status, log.http_status, log.foxia_source_id) == (FoxiaPushStatus.OK, 200, 1234)


@PUSH_ON
def test_message_la_product_raw_khong_phai_sku_va_van_dung_id_landing_cua_kenh():
    channel = _channel()
    first, second = _product("SKU-A"), _product("SKU-B")
    order = _order(channel, product_raw="Combo thảm + bồn")
    _item(order, first)
    _item(order, second)
    _map(channel, 1111)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response()
        FoxiaOrderPushService.push(order.id)

    call = client_cls.return_value.__enter__.return_value.post.call_args
    assert call.args[0].endswith("/1111")
    assert call.kwargs["json"]["message"] == "Combo thảm + bồn"


@PUSH_ON
def test_cot_chon_theo_order_source_cua_don(caplog):
    """Đơn ingest mang `order_source=messenger` → tra cột messenger, không phải landing."""
    channel = _channel()
    order = _order(channel, order_source="messenger")
    _item(order, _product("SKU-A"))
    _map(channel, 1111, kind="landing")
    _map(channel, 2222, kind="messenger")

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response()
        result, text = _push_and_capture(caplog, order.id)

    assert result == FoxiaPushStatus.OK
    assert client_cls.return_value.__enter__.return_value.post.call_args.args[0].endswith("/2222")
    assert "nguon_don=messenger" in text


@PUSH_ON
def test_cot_cua_nguon_don_trong_thi_bo_qua_khong_roi_ve_cot_khac():
    """Kênh chỉ điền `zalo`/`resale` mà đơn là landing → bỏ qua: cột khác không
    phải id dự phòng."""
    channel = _channel()
    order = _order(channel)
    _item(order, _product("SKU-A"))
    _map(channel, 2222, kind="zalo")
    _map(channel, 3333, kind="resale")

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        assert FoxiaOrderPushService.push(order.id) == FoxiaPushStatus.SKIPPED_NO_MAPPING
        client_cls.return_value.__enter__.return_value.post.assert_not_called()


@PUSH_ON
@pytest.mark.parametrize("order_source", ["koc", "shopee", None])
def test_nguon_don_khong_co_cot_thi_bo_qua(order_source, caplog):
    """Nguồn đơn không thuộc các cột của bảng → không có gì để tra, kể cả khi
    kênh đã điền đủ mọi cột."""
    channel = _channel()
    order = _order(channel, order_source=order_source)
    _item(order, _product("SKU-A"))
    for kind in ("landing", "messenger", "hotline", "zalo", "resale"):
        _map(channel, 1000, kind=kind)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        result, text = _push_and_capture(caplog, order.id)
        client_cls.return_value.__enter__.return_value.post.assert_not_called()

    assert result == FoxiaPushStatus.SKIPPED_NO_MAPPING
    assert f"nguon_don={order_source or '-'}" in text


@PUSH_ON
def test_thieu_mapping_thi_khong_goi_va_ghi_skipped():
    channel = _channel()
    order = _order(channel)
    _item(order, _product("SKU-A"))

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        assert FoxiaOrderPushService.push(order.id) == FoxiaPushStatus.SKIPPED_NO_MAPPING
        client_cls.return_value.__enter__.return_value.post.assert_not_called()

    log = FoxiaOrderPush.objects.get(order=order)
    assert log.status == FoxiaPushStatus.SKIPPED_NO_MAPPING
    assert log.foxia_source_id is None


@PUSH_ON
def test_kenh_chua_co_trong_bang_thi_van_skipped():
    """Kênh không có hàng trong bảng → bỏ qua, dù kênh khác đã cấu hình đủ."""
    channel = _channel("kenh-la")
    order = _order(channel)
    _item(order, _product("SKU-A"))
    _map(_channel("kenh-khac"), 7777)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        assert FoxiaOrderPushService.push(order.id) == FoxiaPushStatus.SKIPPED_NO_MAPPING
        client_cls.return_value.__enter__.return_value.post.assert_not_called()


@PUSH_ON
def test_don_khong_co_dong_hang_van_day_va_khong_gui_message(caplog):
    """Id theo KÊNH nên đơn không có dòng hàng vẫn đẩy được; `message` bị bỏ hẳn
    (không gửi chuỗi rỗng)."""
    channel = _channel()
    order = _order(channel)
    _map(channel, 7777)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response()
        result, text = _push_and_capture(caplog, order.id)

    assert result == FoxiaPushStatus.OK
    call = client_cls.return_value.__enter__.return_value.post.call_args
    assert call.args[0].endswith("/7777")
    assert "message" not in call.kwargs["json"]
    assert FoxiaOrderPush.objects.get(order=order).foxia_source_id == 7777


@PUSH_ON
def test_foxia_tra_500_thi_ghi_failed_va_khong_raise():
    channel = _channel()
    product = _product("SKU-A")
    order = _order(channel)
    _item(order, product)
    _map(channel, 1234)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response(500, "Internal Server Error")
        assert FoxiaOrderPushService.push(order.id) == FoxiaPushStatus.FAILED

    log = FoxiaOrderPush.objects.get(order=order)
    assert (log.status, log.http_status) == (FoxiaPushStatus.FAILED, 500)
    assert "Internal Server Error" in log.response_body


@PUSH_ON
def test_timeout_thi_ghi_failed_kem_error_va_khong_raise():
    channel = _channel()
    product = _product("SKU-A")
    order = _order(channel)
    _item(order, product)
    _map(channel, 1234)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.__enter__.return_value.post.side_effect = requests.ReadTimeout("hết giờ")
        assert FoxiaOrderPushService.push(order.id) == FoxiaPushStatus.FAILED

    log = FoxiaOrderPush.objects.get(order=order)
    assert log.status == FoxiaPushStatus.FAILED
    assert log.http_status is None
    assert "ReadTimeout" in log.error


@override_settings(FOXIA_PUSH_ENABLED=False)
def test_tat_co_thi_khong_goi_va_khong_ghi_dong_nao():
    channel = _channel()
    product = _product("SKU-A")
    order = _order(channel)
    _item(order, product)
    _map(channel, 1234)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        assert FoxiaOrderPushService.push(order.id) == "disabled"
        client_cls.return_value.__enter__.return_value.post.assert_not_called()

    assert not FoxiaOrderPush.objects.filter(order=order).exists()


def test_ghep_dia_chi_bo_phan_rong():
    order = Order(address="9b Lê Quý Đôn", ward="", district="Hai Bà Trưng", province="Hà Nội")
    assert build_address(order) == "9b Lê Quý Đôn, Hai Bà Trưng, Hà Nội"


def test_ghep_dia_chi_rong_het_thi_dung_raw_address():
    order = Order(address="", ward="", district="", province="", raw_address="  ghi chú địa chỉ ")
    assert build_address(order) == "ghi chú địa chỉ"


def test_payload_khong_co_product_raw_va_chua_gan_sale_thi_khong_co_message_va_staff_id():
    order = _order(_channel(), name=" A ", phone=" 0337293367 ", address="9b Lê Quý Đôn")
    assert set(build_payload(order)) == {"name", "phone", "address", "chance_id", "created_at"}
    assert build_payload(order)["name"] == "A"


def test_payload_co_product_raw_thi_them_message_du_co_dong_hang():
    order = _order(_channel(), product_raw="Mua 1 bồn ngâm chân")
    _item(order, _product("SKU-A"))
    assert build_payload(order)["message"] == "Mua 1 bồn ngâm chân"


def test_payload_product_raw_chi_khoang_trang_thi_khong_gui_message():
    order = _order(_channel(), product_raw="   ")
    _item(order, _product("SKU-A"))
    assert "message" not in build_payload(order)


def _sale(foxia_id, username="sale_a"):
    from apps.users.models import User

    return User.objects.create(username=username, foxia_id=foxia_id)


def test_payload_co_sale_mang_foxia_id_thi_gui_staff_id_dang_so():
    order = _order(_channel(), staff=_sale(" 42 "))
    assert build_payload(order)["staff_id"] == 42


@pytest.mark.parametrize("foxia_id", ["", "   ", "abc", "12a"])
def test_payload_sale_chua_khai_hoac_foxia_id_khong_phai_so_thi_bo_truong(foxia_id):
    order = _order(_channel(), staff=_sale(foxia_id))
    assert "staff_id" not in build_payload(order)


def test_payload_don_chua_gan_sale_thi_bo_truong():
    assert "staff_id" not in build_payload(_order(_channel()))


@PUSH_ON
def test_day_don_da_gan_sale_gui_kem_staff_id(caplog):
    channel = _channel()
    order = _order(channel, staff=_sale("77"))
    _map(channel, 1234)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response()
        with caplog.at_level(logging.INFO, logger="apps.foxia.application.services.order_push_service"):
            assert FoxiaOrderPushService.push(order.id) == FoxiaPushStatus.OK

    sent = client_cls.return_value.__enter__.return_value.post.call_args.kwargs["json"]
    assert sent["staff_id"] == 77
    assert FoxiaOrderPush.objects.get(order=order).request_body["staff_id"] == 77
    assert "chance=1 staff_id=77" in caplog.text


def test_payload_don_moi_thi_chance_id_la_1():
    assert build_payload(_order(_channel()))["chance_id"] == 1


def test_payload_don_kenh_resale_thi_chance_id_la_2():
    channel = _channel("resale")
    # conftest patch hằng số này thành id không tồn tại cho cả suite — xem
    # docstring `resale_channel_ids()`.
    with patch("apps.orders.reporting.shared.SALE_FB_CHANNEL_IDS", (channel.id,)):
        assert build_payload(_order(channel))["chance_id"] == 2


def test_payload_created_at_la_gio_tao_don_doi_sang_utc():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    order = _order(_channel())
    Order.objects.filter(pk=order.pk).update(
        created_at=datetime(2026, 9, 25, 9, 30, 15, tzinfo=ZoneInfo("Asia/Bangkok"))
    )
    order.refresh_from_db()
    assert build_payload(order)["created_at"] == "2026-09-25 02:30:15"


def _foxia_calls(dispatch_fn):
    """Lọc đúng lệnh gọi dispatch("foxia.push_order", ...) trong call_args_list.

    Không dùng assert_called_once_with trên toàn bộ mock: `create_order_from_landing`
    còn dispatch `lark.notify_order_created` khi LARK_ORDER_BOT_WEBHOOK_URL có giá
    trị thật trong môi trường chạy test (biến này không nằm trong khối override env
    bắt buộc của test suite) — lọc theo tên task mới cô lập đúng thứ đang kiểm.
    """
    return [c for c in dispatch_fn.call_args_list if c.args and c.args[0] == "foxia.push_order"]


@override_settings(FOXIA_PUSH_ENABLED=False)
def test_schedule_khong_enqueue_khi_tat_co(django_capture_on_commit_callbacks):
    """`FoxiaOrderPushService.schedule` đã bị xoá (layering: `orders` không được
    import `foxia` — xem `OrderServiceCommands._schedule_foxia_push`, C2 review
    2026-09-09). Test này giờ đi qua call site thật, giữ nguyên ý cũ: cờ tắt thì
    không enqueue gì.
    """
    from apps.orders.application.services.order_service_commands import OrderServiceCommands

    channel = _channel("kenh-landing-off")
    product = _product("SKU-LAND-OFF")

    with patch("apps.dispatch.api.dispatch") as dispatch_fn:
        with django_capture_on_commit_callbacks(execute=True):
            OrderServiceCommands.create_order_from_landing(
                {
                    "channel_id": channel.id,
                    "name": "Nguyễn Văn A",
                    "phone": "0337293367",
                    "raw_address": "9b Lê Quý Đôn, Hai Bà Trưng, Hà Nội",
                    "product_id": product.sku,
                }
            )
    assert _foxia_calls(dispatch_fn) == []


@PUSH_ON
def test_tao_don_landing_enqueue_dung_task_va_queue(settings, django_capture_on_commit_callbacks):
    """Đơn landing lưu xong PHẢI xếp lịch đẩy — chỉ sau khi commit, và buộc chuỗi
    tên task ("foxia.push_order") + queue khớp đúng registry/settings: một lỗi gõ
    ở MỘT trong hai chuỗi này bị `except Exception` nuốt im lặng, tính năng chết
    mà không ai biết (I4, review 2026-09-09) — nên test phải khớp CHÍNH XÁC giá
    trị, không chỉ "có gọi dispatch".
    """
    from apps.orders.application.services.order_service_commands import OrderServiceCommands

    channel = _channel("kenh-landing")
    product = _product("SKU-LAND")

    with patch("apps.dispatch.api.dispatch") as dispatch_fn:
        with django_capture_on_commit_callbacks(execute=True):
            order = OrderServiceCommands.create_order_from_landing(
                {
                    "channel_id": channel.id,
                    "name": "Nguyễn Văn A",
                    "phone": "0337293367",
                    "raw_address": "9b Lê Quý Đôn, Hai Bà Trưng, Hà Nội",
                    "product_id": product.sku,
                }
            )

    calls = _foxia_calls(dispatch_fn)
    assert len(calls) == 1
    assert calls[0] == call(
        "foxia.push_order",
        queue=settings.CELERY_FOXIA_PUSH_QUEUE,
        order_id=order.id,
    )
    assert settings.CELERY_FOXIA_PUSH_QUEUE == "foxia_push"


@PUSH_ON
def test_rollback_khong_enqueue_gi(django_capture_on_commit_callbacks):
    """Test 8 của spec (2026-09-09) — chưa từng được viết trước review này: một
    transaction rollback KHÔNG được để lại việc enqueue nào, kể cả khi
    `create_order_from_landing` (tự nó `@transaction.atomic`, chạy như một
    savepoint lồng bên trong) đã đăng ký on_commit trước khi lỗi xảy ra.
    """
    from apps.orders.application.services.order_service_commands import OrderServiceCommands

    channel = _channel("kenh-landing-rb")
    product = _product("SKU-LAND-RB")

    class _Boom(Exception):
        pass

    with patch("apps.dispatch.api.dispatch") as dispatch_fn:
        with django_capture_on_commit_callbacks(execute=True):
            with pytest.raises(_Boom):
                with transaction.atomic():
                    OrderServiceCommands.create_order_from_landing(
                        {
                            "channel_id": channel.id,
                            "name": "Nguyễn Văn RB",
                            "phone": "0909000111",
                            "raw_address": "9b Lê Quý Đôn, Hai Bà Trưng, Hà Nội",
                            "product_id": product.sku,
                        }
                    )
                    raise _Boom("giả lập lỗi sau khi tạo đơn, trước khi commit ngoài cùng")

    dispatch_fn.assert_not_called()


def _create_manual(client, channel, **extra):
    body = {
        "channel_code": channel.code,
        "name": "Nguyễn Văn B",
        "phone": "0909123456",
        "raw_address": "9b Lê Quý Đôn, Hai Bà Trưng, Hà Nội",
        "total_amount": "100000",
    }
    body.update(extra)
    return client.post("/api/orders/create", body, format="json")


def _admin_client():
    client = APIClient()
    client.force_authenticate(user=make_admin("admin-foxia-scope"))
    return client


@PUSH_ON
def test_tao_don_thu_cong_trang_don_hang_chinh_KHONG_xep_lich_day(django_capture_on_commit_callbacks):
    """Đơn tạo tay ở trang Đơn hàng chính (show_orders mặc định True) không đẩy —
    quyết định 2026-09-17 chỉ mở cho nút tạo tay ở trang LANDING.

    Đi qua endpoint thật (POST /api/orders/create -> create_order_manual). Patch ở
    tầng `apps.dispatch.api.dispatch`, lọc theo tên task vì đường này còn dispatch
    việc khác (Lark).
    """
    channel = _channel("kenh-thu-cong")
    with patch("apps.dispatch.api.dispatch") as dispatch_fn:
        with django_capture_on_commit_callbacks(execute=True):
            response = _create_manual(_admin_client(), channel, order_source="messenger")
    assert response.status_code == 201
    assert _foxia_calls(dispatch_fn) == []


@PUSH_ON
@pytest.mark.parametrize("source", ["landing", "messenger", "zalo", "hotline"])
def test_tao_don_tay_o_trang_landing_xep_lich_day_theo_nguon(source, settings, django_capture_on_commit_callbacks):
    """Nút tạo đơn ở trang landing gửi `show_orders=False` + `order_source`. Bốn nguồn
    có cột bên Foxia phải xếp lịch đẩy đúng MỘT lần, sau commit, đúng task + queue
    (quyết định chủ hệ thống 2026-09-17, mở rộng spec 2026-09-09)."""
    channel = _channel(f"kenh-landing-tay-{source}")
    with patch("apps.dispatch.api.dispatch") as dispatch_fn:
        with django_capture_on_commit_callbacks(execute=True):
            response = _create_manual(_admin_client(), channel, show_orders=False, order_source=source)
    assert response.status_code == 201
    order_id = response.json()["id"]
    assert Order.objects.get(id=order_id).order_source == source
    calls = _foxia_calls(dispatch_fn)
    assert calls == [call("foxia.push_order", queue=settings.CELERY_FOXIA_PUSH_QUEUE, order_id=order_id)]


@PUSH_ON
def test_tao_don_tay_o_trang_landing_nguon_khong_co_cot_KHONG_day(django_capture_on_commit_callbacks):
    """Nguồn không có cột bên Foxia (shopee/tiktok/koc…) tạo ở trang landing → không
    xếp lịch, để khỏi đẻ dòng `skipped_no_mapping` vô nghĩa cho mọi đơn sàn."""
    channel = _channel("kenh-landing-tay-shopee")
    with patch("apps.dispatch.api.dispatch") as dispatch_fn:
        with django_capture_on_commit_callbacks(execute=True):
            response = _create_manual(_admin_client(), channel, show_orders=False, order_source="shopee")
    assert response.status_code == 201
    assert _foxia_calls(dispatch_fn) == []


def test_danh_sach_nguon_day_tay_khop_cot_foxia():
    """`orders` không import được `foxia` (layering) nên danh sách nguồn đẩy tay khai
    tay ở orders — test này giữ nó khớp với cột thật (trừ `resale` chưa có nguồn đơn)."""
    from apps.foxia.models import FoxiaLinkKind
    from apps.orders.application.services.order_service_commands import FOXIA_MANUAL_PUSH_SOURCES

    assert set(FoxiaLinkKind.values) - {"resale"} == FOXIA_MANUAL_PUSH_SOURCES


@override_settings(FOXIA_PUSH_ENABLED=False)
def test_tao_don_tay_o_trang_landing_tat_co_KHONG_day(django_capture_on_commit_callbacks):
    channel = _channel("kenh-landing-tay-off")
    with patch("apps.dispatch.api.dispatch") as dispatch_fn:
        with django_capture_on_commit_callbacks(execute=True):
            response = _create_manual(_admin_client(), channel, show_orders=False, order_source="landing")
    assert response.status_code == 201
    assert _foxia_calls(dispatch_fn) == []


# ──────────────────────────────────────────────────────────────────────────────
# Nhật ký worker: `docker compose logs worker-foxia` phải tự nói được chuyện gì
# xảy ra. Trước đây Celery chỉ in giá trị trả về ('ok'/'skipped_no_mapping'), đọc
# xong vẫn phải mở DB mới biết đơn nào, kênh nào, SKU nào.
# CỐ Ý không log tên/sđt/địa chỉ khách: log dễ bị gom đi nơi khác hơn DB, và
# `order=<id>` đủ để tra ngược mọi thứ kể cả payload đã gửi.
# ──────────────────────────────────────────────────────────────────────────────

_LOGGER = "apps.foxia.application.services.order_push_service"


def _push_and_capture(caplog, order_id):
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        result = FoxiaOrderPushService.push(order_id)
    return result, "\n".join(r.getMessage() for r in caplog.records if r.name == _LOGGER)


@PUSH_ON
def test_log_thanh_cong_neu_du_ngu_canh(caplog):
    channel = _channel()
    product = _product("SKU-A")
    order = _order(channel)
    _item(order, product)
    _map(channel, 1234)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response()
        _, text = _push_and_capture(caplog, order.id)

    assert f"order={order.id}" in text
    assert "kenh=kenh-a" in text
    assert "sku=" not in text
    assert "nguon=1234" in text
    assert "http=200" in text


@PUSH_ON
def test_log_khong_lo_thong_tin_khach(caplog):
    """Tên/sđt/địa chỉ chỉ nằm ở DB (`request_body`), không nằm trong log."""
    channel = _channel()
    product = _product("SKU-A")
    order = _order(channel)
    _item(order, product)
    _map(channel, 1234)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response()
        _, text = _push_and_capture(caplog, order.id)

    assert "0337293367" not in text
    assert "Nguyễn Văn A" not in text
    assert "9b Lê Quý Đôn" not in text


@PUSH_ON
def test_log_bo_qua_vi_thieu_mapping_noi_ro_kenh_nao(caplog):
    """Bỏ qua là chuyện của KÊNH (thiếu ô landing), không liên quan SKU."""
    channel = _channel()
    product = _product("SKU-A")
    order = _order(channel)
    _item(order, product)

    result, text = _push_and_capture(caplog, order.id)

    assert result == FoxiaPushStatus.SKIPPED_NO_MAPPING
    assert f"order={order.id}" in text
    assert "kenh=kenh-a" in text


@PUSH_ON
def test_log_that_bai_kem_url_va_http(caplog):
    channel = _channel()
    product = _product("SKU-A")
    order = _order(channel)
    _item(order, product)
    _map(channel, 1234)

    with patch("apps.foxia.application.services.order_push_service.HttpClient") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = _response(500, "Boom")
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            result = FoxiaOrderPushService.push(order.id)

    text = "\n".join(r.getMessage() for r in caplog.records if r.name == _LOGGER)
    assert result == FoxiaPushStatus.FAILED
    assert f"order={order.id}" in text
    assert "sku=" not in text
    assert "http=500" in text
    assert "/1234" in text
