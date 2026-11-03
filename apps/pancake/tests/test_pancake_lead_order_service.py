"""`create_order_for_lead` — lead Pancake tạo đơn `orders` (messenger) thay vì đẩy
thẳng sang Foxia (từ 2026-09-17). Gán sale qua `PancakeStaffMap`; lỗi tạo đơn ghi
vào chính lead, không raise."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.test import override_settings
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.models import Order, OrderStatus, PhoneBlocklist, PhoneBlocklistReason
from apps.pancake.application.services import lead_order_service
from apps.pancake.application.services.lead_order_service import (
    build_order_payload,
    create_order_for_lead,
    resolve_staff_id_for,
)
from apps.pancake.models import PancakeLead, PancakePage, PancakePushStatus, PancakeStaffMap
from apps.users.tests.helpers import make_admin, make_user_with_sections

pytestmark = pytest.mark.django_db

SERVICE = "apps.pancake.application.services.lead_order_service"


@pytest.fixture(autouse=True)
def _foxia_push_tat(settings):
    # `create_order_from_landing` xếp lịch dispatch Lark/Foxia qua on_commit — tắt
    # cờ để không phụ thuộc kết nối thật; on_commit vốn không tự chạy trong test
    # pytest-django (transaction rollback ở cuối mỗi test) nhưng tắt cho chắc.
    settings.FOXIA_PUSH_ENABLED = False


def _page(**overrides):
    channel = overrides.pop("channel", None) or Channel.objects.create(code="kenh-a", name="Kênh A")
    values = {
        "pancake_page_id": "945997011940587", "name": "DELAX", "platform": "facebook", "channel": channel,
    }
    values.update(overrides)
    return PancakePage.objects.create(**values)


def _lead(page=None, **overrides):
    page = page or _page()
    values = {
        "page": page, "channel": page.channel, "conversation_id": "c1", "phone_raw": "0901234567",
        "phone": "0901234567", "customer_name": "Tuấn Anh", "foxia_kind": "messenger",
        "assignee_name": "Nhung HN",
    }
    values.update(overrides)
    return PancakeLead.objects.create(**values)


def test_tao_don_messenger_gan_dung_kenh_ten_sdt_url_khong_gan_sale():
    lead = _lead()
    result = create_order_for_lead(lead)

    order = Order.objects.get()
    assert order.order_source == "messenger"
    assert order.channel_id == lead.page.channel_id
    assert order.name == "Tuấn Anh" and order.phone == "0901234567"
    assert order.staff_id is None
    assert order.url_page == "https://pancake.vn/945997011940587?c_id=c1"
    assert result.order_id == order.id
    assert result.push_status == PancakePushStatus.ORDER_CREATED
    assert Customer.objects.filter(phone_normalized="0901234567").exists()


def test_khach_cu_giu_ten_that_du_lead_khong_co_ten():
    Customer.objects.create(phone_normalized="0901234567", phone_raw="0901234567", full_name="Nguyễn Văn A")
    lead = _lead(customer_name="")
    create_order_for_lead(lead)
    order = Order.objects.get()
    assert order.name == "Nguyễn Văn A"
    assert Customer.objects.get(phone_normalized="0901234567").full_name == "Nguyễn Văn A"


def test_khach_cu_giu_ten_that_khong_bi_nickname_pancake_de():
    Customer.objects.create(phone_normalized="0901234567", phone_raw="0901234567", full_name="Nguyễn Văn A")
    lead = _lead(customer_name="Mẹ Bống")
    create_order_for_lead(lead)
    order = Order.objects.get()
    assert order.name == "Nguyễn Văn A"
    assert Customer.objects.get(phone_normalized="0901234567").full_name == "Nguyễn Văn A"


def test_khach_moi_ten_rong_thi_lay_sdt_lam_ten():
    """Khách MỚI (chưa có Customer) và Pancake không cho tên → fallback SỐ ĐIỆN THOẠI
    làm tên, cho cả Order lẫn Customer mới tạo — tránh khách mới bị đặt "Unknown"."""
    lead = _lead(customer_name="")
    create_order_for_lead(lead)
    order = Order.objects.get()
    assert order.name == lead.phone
    assert Customer.objects.get(phone_normalized="0901234567").full_name == lead.phone


def test_khach_cu_ten_unknown_coi_nhu_chua_co_ten_thi_lay_sdt():
    """Customer cũ còn mang placeholder "Unknown" (do một đơn trước tự đặt khi tên
    rỗng) KHÔNG được coi là "đã có tên thật" — vẫn rơi về nhánh khách MỚI, dùng tên
    Pancake hoặc sđt, không giữ nguyên "Unknown"."""
    Customer.objects.create(phone_normalized="0901234567", phone_raw="0901234567", full_name="Unknown")
    lead = _lead(customer_name="")
    create_order_for_lead(lead)
    order = Order.objects.get()
    assert order.name == lead.phone


def test_khach_moi_co_ten_thi_dung_ten_do():
    lead = _lead(customer_name="Anh Tuấn")
    create_order_for_lead(lead)
    order = Order.objects.get()
    assert order.name == "Anh Tuấn"
    assert Customer.objects.get(phone_normalized="0901234567").full_name == "Anh Tuấn"


def test_map_toi_sale_active_thi_gan_staff_id():
    sale = make_user_with_sections("sale.nhung", ["orders"])
    PancakeStaffMap.objects.create(pancake_name="Nhung HN", user=sale)
    lead = _lead(assignee_name="Nhung HN")
    create_order_for_lead(lead)
    assert Order.objects.get().staff_id == sale.id


def test_map_toi_user_role_he_thong_thi_khong_gan():
    admin = make_admin("pancake_admin_map")
    PancakeStaffMap.objects.create(pancake_name="Nhung HN", user=admin)
    lead = _lead(assignee_name="Nhung HN")
    create_order_for_lead(lead)
    assert Order.objects.get().staff_id is None


def test_map_toi_user_ngung_active_thi_khong_gan():
    """Tài khoản sale đã bị khoá (`is_active=False`) coi như không khớp — cùng luật
    `resolve_ingest_staff_id`; đơn rơi về bộ chia tự động thay vì gán một tài khoản
    không ai đăng nhập được."""
    sale = make_user_with_sections("sale.nghi", ["orders"])
    sale.is_active = False
    sale.save(update_fields=["is_active"])
    PancakeStaffMap.objects.create(pancake_name="Nhung HN", user=sale)
    lead = _lead(assignee_name="Nhung HN")
    create_order_for_lead(lead)
    assert Order.objects.get().staff_id is None


def test_ten_chua_co_map_thi_tu_tao_dong_user_none():
    assert not PancakeStaffMap.objects.filter(pancake_name="Nhung HN").exists()
    lead = _lead(assignee_name="Nhung HN")
    create_order_for_lead(lead)
    staff_map = PancakeStaffMap.objects.get(pancake_name="Nhung HN")
    assert staff_map.user_id is None and staff_map.last_seen_at is not None
    assert Order.objects.get().staff_id is None


def test_resolve_staff_id_for_ten_rong_tra_none_khong_dung_bang_map():
    assert resolve_staff_id_for("", None) is None
    assert not PancakeStaffMap.objects.exists()


def test_lead_da_ok_tu_truoc_khong_tao_don():
    lead = _lead(push_status=PancakePushStatus.OK)
    result = create_order_for_lead(lead)
    assert Order.objects.count() == 0
    assert result.order_id is None and result.push_status == PancakePushStatus.OK


def test_lead_da_co_don_khong_tao_lai():
    lead = _lead()
    create_order_for_lead(lead)
    first_order_id = lead.order_id
    with patch(f"{SERVICE}.OrderService.create_order_from_landing") as create:
        result = create_order_for_lead(lead)
    create.assert_not_called()
    assert result.order_id == first_order_id


def test_tao_don_loi_ghi_order_failed_khong_raise():
    lead = _lead()
    with patch(f"{SERVICE}.OrderService.create_order_from_landing", side_effect=ValueError("Kênh bán không tồn tại")):
        result = create_order_for_lead(lead)
    assert result.order_id is None
    assert result.push_status == PancakePushStatus.ORDER_FAILED
    assert "ValueError" in result.error
    assert Order.objects.count() == 0


def test_loi_resolve_staff_id_cung_ghi_order_failed_khong_raise():
    """resolve_staff_id_for chạy TRONG try — lỗi DB ở đó (ví dụ mất kết nối lúc
    get_or_create PancakeStaffMap) không được thoát ra ngoài, phải rơi về
    order_failed như mọi lỗi tạo đơn khác."""
    lead = _lead()
    with patch(f"{SERVICE}.resolve_staff_id_for", side_effect=RuntimeError("db down")):
        result = create_order_for_lead(lead)
    assert result.push_status == PancakePushStatus.ORDER_FAILED
    assert "RuntimeError" in result.error
    assert Order.objects.count() == 0


def test_tao_don_loi_chi_log_loai_loi_khong_log_thong_diep_day_du(caplog):
    """Log warning chỉ mang tên lớp lỗi (không PII/nội dung); thông điệp đầy đủ nằm
    trong lead.error — cột DB đã có audit trail, log không cần lặp lại."""
    lead = _lead()
    with patch(f"{SERVICE}.OrderService.create_order_from_landing", side_effect=ValueError("Kênh bán không tồn tại")):
        with caplog.at_level("WARNING"):
            result = create_order_for_lead(lead)
    assert "Kênh bán không tồn tại" not in caplog.text
    assert "ValueError" in caplog.text
    assert "Kênh bán không tồn tại" in result.error


def test_lead_trung_don_trong_ngay_thi_order_exists_khong_tao_don_thu_hai():
    """`create_order_from_landing` tự chặn trùng (cùng phone+kênh+ngày, chưa
    terminal) qua `claim_or_reject` — raise `DuplicateOrderError`. Đây KHÔNG phải
    lỗi: lead gắn vào đơn đã có, không tạo đơn rỗng thứ hai khi retry."""
    lead = _lead()
    existing = OrderService.create_order_from_landing(
        {"channel_id": lead.channel_id, "phone": lead.phone, "order_source": "messenger"}
    )
    result = create_order_for_lead(lead)
    lead.refresh_from_db()
    assert result.push_status == PancakePushStatus.ORDER_EXISTS
    assert result.order_id == existing.id
    assert result.error == ""
    assert Order.objects.count() == 1
    assert lead.push_status == PancakePushStatus.ORDER_EXISTS
    assert lead.order_id == existing.id


def test_soft_time_limit_exceeded_duoc_re_raise_khong_ghi_order_failed():
    """Timeout của TASK (`SoftTimeLimitExceeded`, celery giết lúc `create_order_
    from_landing` đang chạy) không phải lỗi nghiệp vụ của lead — phải thoát ra
    NGOÀI `create_order_for_lead` để task tự xử lý (log/không đánh dấu vĩnh viễn),
    khác hẳn `except Exception` (ghi order_failed, nuốt lỗi) áp dụng cho lỗi thường."""
    lead = _lead()
    with patch(
        f"{SERVICE}.OrderService.create_order_from_landing", side_effect=SoftTimeLimitExceeded()
    ):
        with pytest.raises(SoftTimeLimitExceeded):
            create_order_for_lead(lead)
    lead.refresh_from_db()
    assert lead.push_status == PancakePushStatus.PENDING
    assert lead.order_id is None
    assert Order.objects.count() == 0


@override_settings(FOXIA_PUSH_ENABLED=True)
def test_don_tao_tu_lead_xep_lich_day_foxia_dung_mot_lan_qua_duong_don_thuong(
    django_capture_on_commit_callbacks, settings
):
    """`create_order_for_lead` KHÔNG tự đẩy Foxia — đơn tạo ra đi qua đúng đường đơn
    thường (`OrderServiceCommands._schedule_foxia_push`, xếp lịch `apps.dispatch.
    dispatch("foxia.push_order", ...)` sau commit). Patch đúng import mà closure
    `_enqueue` dùng (`from apps.dispatch import dispatch`, cục bộ trong hàm) —
    patch `apps.dispatch.api.dispatch` sẽ KHÔNG chặn được lệnh gọi này."""
    lead = _lead()
    with patch("apps.dispatch.dispatch") as dispatch_fn:
        with django_capture_on_commit_callbacks(execute=True):
            result = create_order_for_lead(lead)
    order = Order.objects.get()
    dispatch_fn.assert_called_once_with(
        "foxia.push_order",
        queue=settings.CELERY_FOXIA_PUSH_QUEUE,
        order_id=order.id,
    )
    assert result.order_id == order.id


def test_module_khong_tu_dinh_nghia_push_customer():
    """`apps.pancake` không tự đẩy Foxia nữa (từ 17/09) — không còn lý do để module
    này mang một symbol `push_customer` của riêng nó."""
    assert not hasattr(lead_order_service, "push_customer")


def test_so_bi_blocklist_thi_order_failed_khong_tao_don():
    PhoneBlocklist.objects.create(phone_normalized="0901234567", reason=PhoneBlocklistReason.BLACKLIST)
    lead = _lead()
    result = create_order_for_lead(lead)
    assert result.push_status == PancakePushStatus.ORDER_FAILED
    assert result.error == "Số điện thoại nằm trong danh sách chặn."
    assert result.order_id is None
    assert Order.objects.count() == 0


def test_build_order_payload_staff_id_none_thi_khong_co_key():
    lead = _lead()
    payload = build_order_payload(lead, None)
    assert "staff_id" not in payload
    assert payload == {
        "name": "Tuấn Anh", "phone": "0901234567", "channel_id": lead.channel_id,
        "order_source": "messenger", "url_page": "https://pancake.vn/945997011940587?c_id=c1",
    }


def test_build_order_payload_staff_id_co_thi_dua_vao():
    lead = _lead()
    payload = build_order_payload(lead, 42)
    assert payload["staff_id"] == 42


def test_page_zalo_thi_don_tao_ra_co_order_source_zalo():
    """`order_source` lấy snapshot `lead.foxia_kind` (nền tảng page lúc bắt số) —
    page Zalo phải tạo đơn `order_source=zalo`, KHÔNG cứng "messenger" cho mọi lead."""
    lead = _lead(foxia_kind="zalo")
    create_order_for_lead(lead)
    order = Order.objects.get()
    assert order.order_source == "zalo"


# ── Số đã TỪNG có đơn (mọi kênh, mọi trạng thái, mọi thời điểm) → không tạo đơn ──


def _don_cu(phone="0901234567", channel=None, **fields):
    """Đơn có sẵn của số này, rồi ghi đè các cột cần cho ca test (kênh, trạng thái,
    ngày tạo, xoá mềm) bằng update để không đi qua luật nghiệp vụ của Order."""
    channel = channel or Channel.objects.create(code=f"kenh-khac-{Order.objects.count()}", name="Kênh khác")
    order = OrderService.create_order_from_landing({"channel_id": channel.id, "phone": phone, "order_source": "messenger"})
    if fields:
        Order.objects.filter(id=order.id).update(**fields)
    return order


def _ket_luan_da_co_don(result, order_id):
    assert result.push_status == PancakePushStatus.ORDER_EXISTS
    assert result.order_id == order_id and result.error == ""
    assert PancakeLead.objects.get(id=result.id).order_id == order_id


def test_so_da_co_don_o_kenh_khac_thi_gan_don_cu_khong_tao_don():
    existing = _don_cu()
    lead = _lead()
    result = create_order_for_lead(lead)
    _ket_luan_da_co_don(result, existing.id)
    assert Order.objects.count() == 1


def test_so_co_don_da_giao_tu_lau_van_khong_tao_don():
    """Khác cửa sổ trùng 24h của LeadFilterService: đơn đã giao (terminal) từ hàng
    tháng trước vẫn chặn — quyết định vận hành 24/09/2026."""
    existing = _don_cu(status=OrderStatus.DELIVERED_SUCCESS, created_at=timezone.now() - timedelta(days=200))
    result = create_order_for_lead(_lead())
    _ket_luan_da_co_don(result, existing.id)
    assert Order.objects.count() == 1


def test_so_co_don_huy_van_khong_tao_don():
    existing = _don_cu(status=OrderStatus.CANCELLED)
    _ket_luan_da_co_don(create_order_for_lead(_lead()), existing.id)


def test_nhieu_don_cu_thi_gan_don_moi_nhat():
    channel = Channel.objects.create(code="kenh-x", name="X")
    _don_cu(channel=channel, created_at=timezone.now() - timedelta(days=30))
    newest = _don_cu(channel=channel, status=OrderStatus.CANCELLED)
    _ket_luan_da_co_don(create_order_for_lead(_lead()), newest.id)


def test_don_luu_so_dang_84_cu_van_khop():
    existing = _don_cu()
    Order.objects.filter(id=existing.id).update(phone="+84901234567", phone_normalized="+84901234567")
    _ket_luan_da_co_don(create_order_for_lead(_lead()), existing.id)


def test_don_da_xoa_mem_khong_tinh_la_da_co_don():
    _don_cu(deleted_at=timezone.now())
    result = create_order_for_lead(_lead())
    assert result.push_status == PancakePushStatus.ORDER_CREATED
    assert Order.objects.filter(deleted_at__isnull=True).count() == 1


def test_so_khac_khong_bi_chan():
    _don_cu(phone="0912345678")
    assert create_order_for_lead(_lead()).push_status == PancakePushStatus.ORDER_CREATED


def test_so_bi_blocklist_van_order_failed_du_da_co_don():
    _don_cu()
    PhoneBlocklist.objects.create(phone_normalized="0901234567", reason=PhoneBlocklistReason.BLACKLIST)
    assert create_order_for_lead(_lead()).push_status == PancakePushStatus.ORDER_FAILED


def test_dua_hai_lead_cung_so_van_bat_duplicate_order_error():
    """Hai lead cùng số lọt qua bước kiểm tra cùng lúc → `claim_or_reject` bên trong
    `create_order_from_landing` vẫn chặn đơn thứ hai (DuplicateOrderError)."""
    lead = _lead()
    existing = _don_cu(channel=lead.channel)
    with patch(f"{SERVICE}.find_existing_order_id", return_value=None):
        result = create_order_for_lead(lead)
    _ket_luan_da_co_don(result, existing.id)
