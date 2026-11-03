"""Luồng poll một page. Chốt: dừng theo cursor, bỏ hội thoại không có sđt, tạo
lead + tạo đơn (từ 2026-09-17, qua `create_order_for_lead`), chống trùng (channel,
phone), cursor chỉ tiến khi không lỗi, token bị từ chối → sinh lại 1 lần rồi tắt page.
Chi tiết tạo đơn (payload, gán sale…) có test riêng ở test_pancake_lead_order_service —
ở đây chỉ cần patch/kiểm `create_order_for_lead` được gọi đúng lúc, đúng lead."""

from datetime import datetime, timedelta, UTC
from unittest.mock import patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.test import override_settings

from apps.channels_core.models import Channel
from apps.orders.models import Order
from apps.pancake.application.services.poll_service import PancakePollService
from apps.pancake.infrastructure.pancake_client import (
    ConversationDTO,
    ConversationDetail,
    ConversationPage,
    PancakeError,
    PancakePageTokenRejected,
    PancakeTokenError,
    PancakeUnavailable,
)
from apps.pancake.models import PancakeLead, PancakePage, PancakePushStatus

pytestmark = pytest.mark.django_db

CREATE_ORDER = "apps.pancake.application.services.poll_service.create_order_for_lead"
T0 = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)


def _khong_doi(lead):
    """Double dùng khi test không quan tâm kết quả tạo đơn — trả nguyên lead."""
    return lead


def _conv(id, updated_at, has_phone=True, phones=(), name="Khách", tags=("BOT",), inserted_at=None):
    return ConversationDTO(
        id=id, type="INBOX", inserted_at=inserted_at or updated_at - timedelta(minutes=10),
        updated_at=updated_at, has_phone=has_phone, phones=list(phones), customer_name=name,
        customer_id=f"c-{id}", tags=list(tags), assignee_name="Nhung HN",
    )


class FakeClient:
    """Trang hội thoại cho sẵn; ghi lại lời gọi để test đếm request."""

    def __init__(self, pages=None, details=None, total=None):
        self.pages = pages or {}          # page_number -> list[ConversationDTO]
        self.details = details or {}      # conversation_id -> ConversationDetail
        self.total = total
        self.calls = []
        self.token_calls = 0
        self.reject_tokens = set()
        self.token_error = None

    def list_conversations(self, page_id, page_token, since, until, page_number):
        self.calls.append(("list", page_number, page_token))
        if page_token in self.reject_tokens:
            raise PancakePageTokenRejected("bad token")
        items = self.pages.get(page_number, [])
        total = self.total if self.total is not None else sum(len(v) for v in self.pages.values())
        return ConversationPage(total=total, items=items)

    def get_conversation_detail(self, page_id, page_token, conversation_id):
        self.calls.append(("detail", conversation_id))
        return self.details.get(conversation_id, ConversationDetail(phones=[], customer_name="", customer_id="", phone_message_at=None))

    def generate_page_token(self, page_id):
        self.token_calls += 1
        if self.token_error:
            raise self.token_error
        return "new-token"


@pytest.fixture
def page():
    channel = Channel.objects.create(code="kenh-a", name="Kênh A")
    return PancakePage.objects.create(
        pancake_page_id="945997011940587", name="DELAX", platform="facebook", channel=channel,
        is_enabled=True, page_access_token="ptok", polled_until=T0 - timedelta(minutes=30),
    )


def _service(client, now=T0):
    service = PancakePollService(client)
    service.now = lambda: now
    return service


@override_settings(FOXIA_PUSH_ENABLED=False)
def test_tao_lead_va_tao_don(page):
    client = FakeClient(
        pages={1: [_conv("c1", T0 - timedelta(minutes=1), phones=["0901234567"])]},
        details={"c1": ConversationDetail(phones=["+84 901 234 567"], customer_name="Tuan Anh", customer_id="cust-1",
                                          phone_message_at=T0 - timedelta(minutes=2))},
    )
    result = _service(client).poll_page(page)

    assert (result.fetched, result.leads_created, result.orders_created, result.skipped, result.error) == (1, 1, 1, 0, "")
    lead = PancakeLead.objects.get()
    assert (lead.phone, lead.phone_raw, lead.customer_name, lead.pancake_customer_id) == ("0901234567", "+84 901 234 567", "Tuan Anh", "cust-1")
    assert (lead.channel_id, lead.foxia_kind) == (page.channel_id, "messenger")
    assert lead.push_status == PancakePushStatus.ORDER_CREATED
    assert lead.order_id is not None
    order = Order.objects.get(id=lead.order_id)
    assert order.order_source == "messenger" and order.channel_id == page.channel_id
    assert order.name == "Tuan Anh" and order.phone == "0901234567"
    assert lead.phone_given_at == T0 - timedelta(minutes=2)
    assert lead.tags == ["BOT"] and lead.assignee_name == "Nhung HN"
    assert lead.conversation_inserted_at == T0 - timedelta(minutes=11)
    page.refresh_from_db()
    assert page.polled_until == T0 - timedelta(minutes=1)
    assert page.last_polled_at == T0 and page.last_error == ""


def test_khong_co_tin_nhan_co_sdt_thi_phone_given_at_la_updated_at(page):
    client = FakeClient(pages={1: [_conv("c1", T0, phones=["0901234567"])]})
    with patch(CREATE_ORDER, side_effect=_khong_doi):
        _service(client).poll_page(page)
    assert PancakeLead.objects.get().phone_given_at == T0


def test_hoi_thoai_khong_has_phone_van_goi_detail_de_bat_sdt_don_pos(page):
    """has_phone=False không có nghĩa là không có số: số gắn theo đơn Pancake POS chỉ
    nằm ở hồ sơ khách trong endpoint chi tiết (đo 17/09/2026: 9/27 số nhập tay chỉ
    thấy được bằng cách này). Không có số thật → không tạo lead, không tạo đơn."""
    client = FakeClient(pages={1: [_conv("c1", T0, has_phone=False)]})
    with patch(CREATE_ORDER) as create:
        result = _service(client).poll_page(page)
    assert result.leads_created == 0
    assert ("detail", "c1") in client.calls
    create.assert_not_called()


def test_sdt_gan_theo_don_pos_khong_has_phone_van_tao_lead(page):
    client = FakeClient(
        pages={1: [_conv("c1", T0, has_phone=False, name="Đoàn Cường")]},
        details={"c1": ConversationDetail(phones=["0978000001"], customer_name="Đoàn Cường", customer_id="cust-9",
                                          phone_message_at=None)},
    )
    with patch(CREATE_ORDER, side_effect=lambda lead: lead):
        result = _service(client).poll_page(page)
    assert result.leads_created == 1
    lead = PancakeLead.objects.get()
    assert (lead.phone, lead.customer_name) == ("0978000001", "Đoàn Cường")
    # Không có tin nhắn chứa số → "ngày cho số" = lúc hội thoại cập nhật.
    assert lead.phone_given_at == T0


def test_so_cho_qua_24h_khong_tao_lead(page):
    """Hội thoại cũ vừa có tin mới (về nhờ order_by=updated_at) nhưng tin nhắn chứa
    số mới nhất đã hơn 24h → số cũ, bỏ qua (đếm skipped), không tạo lead/đơn.
    Cursor vẫn tiến bình thường."""
    client = FakeClient(
        pages={1: [_conv("c1", T0 - timedelta(minutes=1), phones=["0901234567"])]},
        details={"c1": ConversationDetail(phones=["0901234567"], customer_name="Khách cũ", customer_id="cust-1",
                                          phone_message_at=T0 - timedelta(hours=25))},
    )
    with patch(CREATE_ORDER) as create:
        result = _service(client).poll_page(page)
    assert (result.leads_created, result.orders_created, result.skipped) == (0, 0, 1)
    assert not PancakeLead.objects.exists()
    create.assert_not_called()
    page.refresh_from_db()
    assert page.polled_until == T0 - timedelta(minutes=1)


def test_so_cho_trong_24h_van_tao_lead(page):
    client = FakeClient(
        pages={1: [_conv("c1", T0 - timedelta(minutes=1), phones=["0901234567"])]},
        details={"c1": ConversationDetail(phones=["0901234567"], customer_name="Khách", customer_id="cust-1",
                                          phone_message_at=T0 - timedelta(hours=23))},
    )
    with patch(CREATE_ORDER, side_effect=_khong_doi):
        result = _service(client).poll_page(page)
    assert (result.leads_created, result.skipped) == (1, 0)
    assert PancakeLead.objects.get().phone_given_at == T0 - timedelta(hours=23)


def test_so_don_pos_khong_co_tin_nhan_ma_hoi_thoai_moi_van_tao_lead(page):
    """Số gắn qua đơn POS không có tin nhắn nào chứa số → mốc rơi về updated_at hội
    thoại; hội thoại mới cập nhật trong 24h thì vẫn nhận (quyết định A, 22/09/2026)."""
    page.polled_until = T0 - timedelta(hours=3)
    page.save(update_fields=["polled_until"])
    client = FakeClient(
        pages={1: [_conv("c1", T0 - timedelta(hours=2), has_phone=False)]},
        details={"c1": ConversationDetail(phones=["0978000001"], customer_name="Đoàn Cường", customer_id="cust-9",
                                          phone_message_at=None)},
    )
    with patch(CREATE_ORDER, side_effect=_khong_doi):
        result = _service(client).poll_page(page)
    assert result.leads_created == 1


def test_hoi_thoai_cap_nhat_qua_24h_khong_co_tin_nhan_co_so_thi_bo_qua(page):
    """Sau sự cố dài (cursor lùi nhiều ngày): hội thoại cập nhật hơn 24h trước, không
    có tin nhắn chứa số → mốc = updated_at → cũ → bỏ qua."""
    page.polled_until = T0 - timedelta(days=5)
    page.save(update_fields=["polled_until"])
    client = FakeClient(
        pages={1: [_conv("c1", T0 - timedelta(days=3), has_phone=False)]},
        details={"c1": ConversationDetail(phones=["0978000001"], customer_name="Khách", customer_id="cust-9",
                                          phone_message_at=None)},
    )
    with patch(CREATE_ORDER) as create:
        result = _service(client).poll_page(page)
    assert (result.leads_created, result.skipped) == (0, 1)
    create.assert_not_called()


def test_dung_khi_gap_hoi_thoai_cu_hon_cursor(page):
    old = page.polled_until - timedelta(minutes=10)
    client = FakeClient(
        pages={1: [_conv("new", T0, phones=["0901234567"]), _conv("old", old, phones=["0912345678"])],
               2: [_conv("older", old - timedelta(hours=1), phones=["0923456789"])]},
        total=400,
    )
    with patch(CREATE_ORDER, side_effect=_khong_doi):
        result = _service(client).poll_page(page)
    assert result.leads_created == 1
    assert [c for c in client.calls if c[0] == "list"] == [("list", 1, "ptok")]


def test_lat_trang_khi_chua_gap_cursor(page):
    client = FakeClient(
        pages={1: [_conv(f"a{i}", T0 - timedelta(seconds=i), phones=[f"09000000{i:02d}"]) for i in range(2)],
               2: [_conv("b", T0 - timedelta(minutes=2), phones=["0911111111"])]},
        total=202,
    )
    # Trang 1 chỉ có 2 phần tử nhưng total=202 → phải sang trang 2 (Pancake không có has_next).
    service = _service(client)
    service.PAGE_SIZE = 2
    with patch(CREATE_ORDER, side_effect=_khong_doi):
        result = service.poll_page(page)
    assert result.leads_created == 3
    # Trang 3 rỗng là tín hiệu hết (Pancake không có has_next): 3 request list.
    assert [c[1] for c in client.calls if c[0] == "list"] == [1, 2, 3]


def test_trung_kenh_va_sdt_khong_day_lai(page):
    PancakeLead.objects.create(page=page, channel=page.channel, conversation_id="x", phone_raw="0901234567",
                               phone="0901234567", foxia_kind="messenger", push_status=PancakePushStatus.OK)
    client = FakeClient(pages={1: [_conv("c1", T0, phones=["0901234567"])]})
    with patch(CREATE_ORDER) as create:
        result = _service(client).poll_page(page)
    assert (result.leads_created, result.orders_created) == (0, 0)
    assert PancakeLead.objects.count() == 1
    create.assert_not_called()


def test_sdt_khong_hop_le_dem_skipped(page):
    client = FakeClient(pages={1: [_conv("c1", T0, phones=["123", "0901234567"])]})
    with patch(CREATE_ORDER, side_effect=_khong_doi):
        result = _service(client).poll_page(page)
    assert (result.leads_created, result.skipped) == (1, 1)


def test_loi_tao_don_khong_chan_cursor(page):
    """Một lead tạo đơn lỗi (order_failed) không được giữ cursor lại — người vận
    hành tạo lại đơn bằng tay qua `retry_lead_order`, không cần đẩy lại cả lượt."""
    client = FakeClient(pages={1: [_conv("c1", T0, phones=["0901234567"])]})

    def _mark_order_failed(lead):
        lead.push_status = PancakePushStatus.ORDER_FAILED
        lead.error = "ValueError: Kênh bán không tồn tại"
        lead.save(update_fields=["push_status", "error"])
        return lead

    with patch(CREATE_ORDER, side_effect=_mark_order_failed):
        result = _service(client).poll_page(page)
    lead = PancakeLead.objects.get()
    assert lead.push_status == PancakePushStatus.ORDER_FAILED
    assert result.orders_created == 0
    page.refresh_from_db()
    assert page.polled_until == T0


def test_pancake_loi_giu_cursor_ghi_last_error(page):
    cursor = page.polled_until

    class Boom(FakeClient):
        def list_conversations(self, *a, **k):
            raise PancakeError("HTTP 502")

    result = _service(Boom()).poll_page(page)
    assert result.error == "PancakeError: HTTP 502"
    page.refresh_from_db()
    assert page.polled_until == cursor
    assert page.last_error == "PancakeError: HTTP 502"
    assert page.last_polled_at == T0


def test_token_bi_tu_choi_sinh_lai_mot_lan_roi_chay_tiep(page):
    client = FakeClient(pages={1: [_conv("c1", T0, phones=["0901234567"])]})
    client.reject_tokens = {"ptok"}
    with patch(CREATE_ORDER, side_effect=_khong_doi):
        result = _service(client).poll_page(page)
    assert result.leads_created == 1
    assert client.token_calls == 1
    page.refresh_from_db()
    assert page.page_access_token == "new-token" and page.is_enabled is True


def test_token_bi_tu_choi_sinh_lai_that_bai_thi_tat_page(page):
    client = FakeClient()
    client.reject_tokens = {"ptok"}
    client.token_error = PancakeTokenError("Thiếu quyền Admin")
    result = _service(client).poll_page(page)
    assert "Thiếu quyền Admin" in result.error
    page.refresh_from_db()
    assert (page.is_enabled, page.page_access_token, page.token_error) == (False, "", "Thiếu quyền Admin")


def test_token_bi_tu_choi_pancake_5xx_khi_sinh_lai_thi_giu_page_va_cursor(page):
    """Pancake 5xx là lỗi tạm thời: không tắt page, giữ token cũ + cursor, lượt sau thử lại."""
    cursor = page.polled_until
    client = FakeClient()
    client.reject_tokens = {"ptok"}
    client.token_error = PancakeUnavailable("HTTP 500")
    result = _service(client).poll_page(page)
    assert "HTTP 500" in result.error
    page.refresh_from_db()
    assert (page.is_enabled, page.page_access_token, page.token_error) == (True, "ptok", "")
    assert page.polled_until == cursor
    assert page.last_error == "PancakeUnavailable: HTTP 500"


def test_token_moi_van_bi_tu_choi_thi_tat_page(page):
    client = FakeClient()
    client.reject_tokens = {"ptok", "new-token"}
    result = _service(client).poll_page(page)
    assert result.error
    page.refresh_from_db()
    assert page.is_enabled is False and page.page_access_token == ""


def test_soft_time_limit_thoat_ngay_khong_nuot(page):
    """Task sắp bị Celery giết cứng phải thoát để nhả single-flight lock — không được
    nuốt gọn thành 'error' như mọi ngoại lệ khác của đối tác."""
    class Boom(FakeClient):
        def list_conversations(self, *a, **k):
            raise SoftTimeLimitExceeded()

    with pytest.raises(SoftTimeLimitExceeded):
        _service(Boom()).poll_page(page)


def test_page_chua_san_sang_khong_goi_mang(page):
    page.is_enabled = False
    page.save()
    client = FakeClient()
    result = _service(client).poll_page(page)
    assert result.error and client.calls == []


def test_hoi_thoai_da_co_lead_ma_khong_moi_hon_khong_goi_detail(page):
    PancakeLead.objects.create(page=page, channel=page.channel, conversation_id="c1", phone_raw="0901234567",
                               phone="0901234567", foxia_kind="messenger", phone_given_at=T0)
    client = FakeClient(pages={1: [_conv("c1", T0 - timedelta(minutes=1), phones=["0901234567"])]})
    _service(client).poll_page(page)
    assert ("detail", "c1") not in client.calls


def test_loi_lay_chi_tiet_mot_hoi_thoai_khong_chan_cursor(page):
    class DetailBoom(FakeClient):
        def get_conversation_detail(self, page_id, page_token, conversation_id):
            raise PancakeError("HTTP 502")

    client = DetailBoom(pages={1: [_conv("c1", T0, phones=["0901234567"])]})
    with patch(CREATE_ORDER, side_effect=_khong_doi):
        result = _service(client).poll_page(page)
    assert result.error == ""
    lead = PancakeLead.objects.get()
    assert lead.phone == "0901234567" and lead.phone_given_at == T0
    page.refresh_from_db()
    assert page.polled_until == T0


def test_loi_lay_chi_tiet_do_token_bi_tu_choi_van_sinh_lai_token(page):
    class DetailTokenBoom(FakeClient):
        def get_conversation_detail(self, page_id, page_token, conversation_id):
            if page_token == "ptok":
                raise PancakePageTokenRejected("bad token")
            return super().get_conversation_detail(page_id, page_token, conversation_id)

    client = DetailTokenBoom(pages={1: [_conv("c1", T0, phones=["0901234567"])]})
    with patch(CREATE_ORDER, side_effect=_khong_doi):
        result = _service(client).poll_page(page)
    assert result.leads_created == 1
    assert client.token_calls == 1
    page.refresh_from_db()
    assert page.page_access_token == "new-token" and page.is_enabled is True
