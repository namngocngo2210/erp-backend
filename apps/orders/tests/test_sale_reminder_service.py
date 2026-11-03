"""Lịch nhắc sale theo `sales_status_code` + template tin Lark.

Bảng chiến lược trong docstring `sale_reminder_service.py`:

| code                   | lịch                                        |
|------------------------|---------------------------------------------|
| need_more_consulting   | 3 nhắc D+2, D+4, D+7 lúc 09:00 giờ địa phương|
| scheduled              | 1 nhắc tại `next_followup_at` sale chọn     |
| call_back_later        | 1 nhắc tại `next_followup_at`               |
| closed_scheduled_date  | 1 nhắc tại `next_followup_at`               |
| closed_express         | 1 nhắc ngay (due_at = now)                  |
| khác                   | huỷ hết nhắc đang chờ, không tạo            |

Mốc thời gian được neo cố định bằng patch `django.utils.timezone.now` (không có
freezegun trong image) để ngày D+n và giờ 09:00 không phụ thuộc giờ chạy test.
"""

from datetime import datetime, timedelta, UTC
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from apps.orders.application.services import sale_reminder_service as svc
from apps.orders.infrastructure import sale_reminder_templates as tpl
from apps.orders.models import (
    OrderAssessment,
    SaleReminder,
    SaleReminderKind,
    SaleReminderStatus,
)
from apps.users.models import User

pytestmark = pytest.mark.django_db

LOCAL_TZ = ZoneInfo("Asia/Bangkok")
# 21/09/2026 10:30 giờ VN = 03:30 UTC — giữa buổi, không dính mép ngày.
FROZEN_NOW = datetime(2026, 9, 21, 3, 30, tzinfo=UTC)


@pytest.fixture
def frozen_now():
    with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
        yield FROZEN_NOW


@pytest.fixture
def sale():
    return User.objects.create_user(username="sale-reminder", password="x", display_name="Sale A")


@pytest.fixture
def actor():
    return User.objects.create_user(username="actor-reminder", password="x", display_name="Actor")


@pytest.fixture
def landing(order_factory, sale):
    order = order_factory(order_source="landing", staff=sale, name="Nguyễn Văn B", phone="0912345678")
    assessment = OrderAssessment.objects.create(order=order, internal_note="  Khách lo giá  ")
    return order, assessment


def _reschedule(order, assessment, code, *, actor=None, next_followup_at=None):
    return svc.reschedule_for_assessment(
        order=order,
        assessment=assessment,
        actor=actor,
        new_sales_status_code=code,
        next_followup_at=next_followup_at,
    )


def _pending(order):
    return list(SaleReminder.objects.filter(order=order, status=SaleReminderStatus.PENDING).order_by("due_at"))


# ── bảng lịch nhắc ───────────────────────────────────────────────────────────


def test_need_more_consulting_tao_3_nhac_d2_d4_d7_luc_9h_dia_phuong(landing, frozen_now, settings):
    settings.SALE_REMINDER_FOLLOWUP_HOUR = 9
    order, assessment = landing
    assessment.sales_status_code = "need_more_consulting"

    created = _reschedule(order, assessment, "need_more_consulting")

    assert [(r.kind, r.due_at) for r in _pending(order)] == [
        (SaleReminderKind.NEED_MORE_CONSULTING_D2, datetime(2026, 9, 23, 9, 0, tzinfo=LOCAL_TZ)),
        (SaleReminderKind.NEED_MORE_CONSULTING_D4, datetime(2026, 9, 25, 9, 0, tzinfo=LOCAL_TZ)),
        (SaleReminderKind.NEED_MORE_CONSULTING_D7, datetime(2026, 9, 28, 9, 0, tzinfo=LOCAL_TZ)),
    ]
    assert len(created) == 3
    # Snapshot: staff của đơn, mã trạng thái, ghi chú đã strip.
    for reminder in created:
        assert reminder.staff_id == order.staff_id
        assert reminder.sales_status_code_snapshot == "need_more_consulting"
        assert reminder.note_snapshot == "Khách lo giá"
        assert reminder.status == SaleReminderStatus.PENDING


def test_gio_nhac_theo_setting_sale_reminder_followup_hour(landing, frozen_now, settings):
    settings.SALE_REMINDER_FOLLOWUP_HOUR = 14
    order, assessment = landing

    _reschedule(order, assessment, "need_more_consulting")

    assert [r.due_at for r in _pending(order)] == [
        datetime(2026, 9, 23, 14, 0, tzinfo=LOCAL_TZ),
        datetime(2026, 9, 25, 14, 0, tzinfo=LOCAL_TZ),
        datetime(2026, 9, 28, 14, 0, tzinfo=LOCAL_TZ),
    ]


def test_ngay_d_plus_n_tinh_theo_ngay_dia_phuong_khong_theo_utc(landing, settings):
    """23:30 giờ VN = 16:30 UTC cùng ngày; 01:00 VN hôm sau = 18:00 UTC hôm trước.
    D+2 phải cộng từ NGÀY ĐỊA PHƯƠNG."""
    settings.SALE_REMINDER_FOLLOWUP_HOUR = 9
    order, assessment = landing
    late_local = datetime(2026, 9, 21, 18, 0, tzinfo=UTC)  # 22/09 01:00 giờ VN

    with patch("django.utils.timezone.now", return_value=late_local):
        _reschedule(order, assessment, "need_more_consulting")

    assert _pending(order)[0].due_at == datetime(2026, 9, 24, 9, 0, tzinfo=LOCAL_TZ)


@pytest.mark.parametrize(
    "code, kind",
    [
        ("scheduled", SaleReminderKind.SCHEDULED),
        ("call_back_later", SaleReminderKind.CALL_BACK_LATER),
        ("closed_scheduled_date", SaleReminderKind.CLOSED_SCHEDULED_DATE),
    ],
)
def test_ma_hen_ngay_tao_1_nhac_tai_next_followup_at(landing, frozen_now, code, kind):
    order, assessment = landing
    picked = FROZEN_NOW + timedelta(days=3, hours=2)

    created = _reschedule(order, assessment, code, next_followup_at=picked)

    assert [(r.kind, r.due_at) for r in created] == [(kind, picked)]
    assert [(r.kind, r.due_at) for r in _pending(order)] == [(kind, picked)]


@pytest.mark.parametrize("code", ["scheduled", "call_back_later", "closed_scheduled_date"])
def test_ma_hen_ngay_thieu_next_followup_at_thi_khong_tao(landing, frozen_now, code):
    order, assessment = landing

    assert _reschedule(order, assessment, code, next_followup_at=None) == []
    assert _pending(order) == []


def test_closed_express_nhac_ngay_lap_tuc(landing, frozen_now):
    order, assessment = landing

    created = _reschedule(order, assessment, "closed_express")

    assert [(r.kind, r.due_at) for r in created] == [(SaleReminderKind.CLOSED_EXPRESS, FROZEN_NOW)]


@pytest.mark.parametrize("code", ["closed", "no_answer_1", "", None, "  "])
def test_ma_ngoai_bang_huy_nhac_dang_cho_va_khong_tao_moi(landing, frozen_now, code):
    order, assessment = landing
    _reschedule(order, assessment, "need_more_consulting")
    assert len(_pending(order)) == 3

    created = _reschedule(order, assessment, code)

    assert created == []
    assert _pending(order) == []
    cancelled = SaleReminder.objects.filter(order=order, status=SaleReminderStatus.CANCELLED)
    assert cancelled.count() == 3
    expected_reason = f"status→{(code or '').strip() or 'empty'}"
    assert set(cancelled.values_list("cancel_reason", flat=True)) == {expected_reason}
    assert set(cancelled.values_list("cancelled_at", flat=True)) == {FROZEN_NOW}


def test_doi_trang_thai_huy_nhac_cu_roi_tao_nhac_moi(landing, frozen_now):
    order, assessment = landing
    old = _reschedule(order, assessment, "need_more_consulting")
    picked = FROZEN_NOW + timedelta(days=1)

    new = _reschedule(order, assessment, "scheduled", next_followup_at=picked)

    assert [r.kind for r in _pending(order)] == [SaleReminderKind.SCHEDULED]
    for reminder in old:
        reminder.refresh_from_db()
        assert reminder.status == SaleReminderStatus.CANCELLED
        assert reminder.cancel_reason == "status→scheduled"
    assert new[0].status == SaleReminderStatus.PENDING
    # Nhắc đã SENT không bị đụng — chỉ huỷ PENDING.
    SaleReminder.objects.filter(pk=new[0].pk).update(status=SaleReminderStatus.SENT)
    _reschedule(order, assessment, "closed")
    new[0].refresh_from_db()
    assert new[0].status == SaleReminderStatus.SENT


def test_don_khong_phai_landing_chi_huy_khong_tao(order_factory, sale, frozen_now):
    order = order_factory(order_source="messenger", staff=sale)
    assessment = OrderAssessment.objects.create(order=order)
    SaleReminder.objects.create(
        order=order,
        staff=sale,
        kind=SaleReminderKind.SCHEDULED,
        due_at=FROZEN_NOW + timedelta(days=1),
        status=SaleReminderStatus.PENDING,
    )

    created = _reschedule(order, assessment, "need_more_consulting")

    assert created == []
    leftover = SaleReminder.objects.get(order=order)
    assert leftover.status == SaleReminderStatus.CANCELLED
    assert leftover.cancel_reason == "non_landing_source"


def test_staff_lay_tu_don_neu_co_khong_thi_lay_actor(order_factory, actor, frozen_now):
    order = order_factory(order_source="landing", staff=None)
    assessment = OrderAssessment.objects.create(order=order)

    created = _reschedule(order, assessment, "closed_express", actor=actor)

    assert created[0].staff_id == actor.id


# ── resale appointment ───────────────────────────────────────────────────────


def test_resale_appointment_tao_1_nhac_khong_phu_thuoc_landing(order_factory, sale, frozen_now):
    order = order_factory(order_source="shopee", staff=sale)
    at = FROZEN_NOW + timedelta(days=2)

    first = svc.schedule_resale_appointment_reminder(order=order, appointment_at=at, note=" ghé lại ")
    second = svc.schedule_resale_appointment_reminder(order=order, appointment_at=at + timedelta(hours=1))

    assert first.kind == SaleReminderKind.RESALE_APPOINTMENT
    assert first.note_snapshot == "ghé lại"
    first.refresh_from_db()
    assert first.status == SaleReminderStatus.CANCELLED
    assert first.cancel_reason == "resale_appointment_changed"
    assert [r.pk for r in _pending(order)] == [second.pk]
    assert second.due_at == at + timedelta(hours=1)


@pytest.mark.parametrize("appointment_at", [None, FROZEN_NOW, FROZEN_NOW - timedelta(minutes=1)])
def test_resale_appointment_rong_hoac_qua_khu_chi_huy(order_factory, sale, frozen_now, appointment_at):
    order = order_factory(order_source="landing", staff=sale)
    svc.schedule_resale_appointment_reminder(order=order, appointment_at=FROZEN_NOW + timedelta(days=1))
    # Nhắc loại khác trên cùng đơn phải được giữ nguyên.
    other = SaleReminder.objects.create(
        order=order, staff=sale, kind=SaleReminderKind.SCHEDULED, due_at=FROZEN_NOW + timedelta(days=1)
    )

    result = svc.schedule_resale_appointment_reminder(order=order, appointment_at=appointment_at)

    assert result is None
    assert [r.pk for r in _pending(order)] == [other.pk]


# ── helper đọc ───────────────────────────────────────────────────────────────


def test_get_next_reminder_at_va_map_chi_lay_pending_som_nhat(order_factory, sale, frozen_now):
    order_a = order_factory(order_source="landing", staff=sale)
    order_b = order_factory(order_source="landing", staff=sale)
    order_c = order_factory(order_source="landing", staff=sale)
    soon, later = FROZEN_NOW + timedelta(hours=1), FROZEN_NOW + timedelta(days=1)
    SaleReminder.objects.create(order=order_a, kind=SaleReminderKind.SCHEDULED, due_at=later)
    SaleReminder.objects.create(order=order_a, kind=SaleReminderKind.CALL_BACK_LATER, due_at=soon)
    SaleReminder.objects.create(
        order=order_b, kind=SaleReminderKind.SCHEDULED, due_at=soon, status=SaleReminderStatus.SENT
    )

    assert svc.get_next_reminder_at(order_a.id) == soon
    assert svc.get_next_reminder_at(order_b.id) is None
    assert svc.get_next_reminder_at_map([order_a.id, order_b.id, order_c.id, 0, None]) == {order_a.id: soon}
    assert svc.get_next_reminder_at_map([]) == {}
    assert svc.cancel_all_for_order(order_a, reason="manual") == 2
    assert svc.cancel_all_for_order(order_a) == 0


# ── template ─────────────────────────────────────────────────────────────────


def _reminder(order, staff, kind, *, note="", due_at=None):
    return SaleReminder.objects.create(
        order=order,
        staff=staff,
        kind=kind,
        due_at=due_at or FROZEN_NOW,
        note_snapshot=note,
    )


def test_mention_dung_lark_open_id_khi_co_khong_thi_ten_trong_ngoac(sale):
    assert tpl._mention(None) == "[Sale]"
    assert tpl._mention(sale) == "[Sale A]"
    sale.lark_open_id = " ou_abc "
    assert tpl._mention(sale) == '<at user_id="ou_abc">Sale A</at>'
    sale.display_name = ""
    assert tpl._mention(sale) == '<at user_id="ou_abc">sale-reminder</at>'


@pytest.mark.parametrize(
    "kind, head, must_contain",
    [
        (SaleReminderKind.NEED_MORE_CONSULTING_D2, "📞 [Sale A] — Follow-up Tư vấn thêm hôm nay:", "đổi trả 30 ngày"),
        (SaleReminderKind.NEED_MORE_CONSULTING_D4, "🔄 [Sale A] — Nguyễn Văn B / 0912345678 — đã 3 ngày, gọi lần 2 hôm nay.", "Ghi chú lần trước: lo giá"),
        (SaleReminderKind.NEED_MORE_CONSULTING_D7, "⚡ [Sale A] — Cuộc gọi cuối: Nguyễn Văn B / 0912345678", "Lý do chưa chốt: lo giá"),
        (SaleReminderKind.SCHEDULED, "📅 [Sale A] — Hôm nay có lịch hẹn gọi lại:", "Lý do hẹn: lo giá"),
        (SaleReminderKind.CALL_BACK_LATER, "🔁 [Sale A] — Khách yêu cầu gọi lại hôm nay:", "Lý do dời: lo giá"),
        (SaleReminderKind.RESALE_APPOINTMENT, "🤝 [Sale A] — Lịch hẹn resale hôm nay:", "trang theo dõi resale"),
    ],
)
def test_build_reminder_message_theo_kind(landing, sale, kind, head, must_contain):
    order, _ = landing
    reminder = _reminder(order, sale, kind, note="lo giá")

    message = tpl.build_reminder_message(reminder)

    assert message.splitlines()[0] == head
    assert must_contain in message
    assert "Nguyễn Văn B / 0912345678" in message


def test_template_closed_express_va_closed_scheduled_date_dung_dia_chi_va_san_pham(order_factory, sale):
    order = order_factory(order_source="landing", staff=sale, name="A", phone="0900000001", raw_address=" 5 Trần Phú ", product="DC12")
    due = FROZEN_NOW
    when = "10:30 21/09"  # 03:30 UTC → giờ VN, bất kể TZ của container

    express = tpl.build_reminder_message(_reminder(order, sale, SaleReminderKind.CLOSED_EXPRESS, due_at=due))
    scheduled = tpl.build_reminder_message(
        _reminder(order, sale, SaleReminderKind.CLOSED_SCHEDULED_DATE, due_at=due)
    )

    assert express.splitlines()[0] == "🚀 [Sale A] — Đơn hoả tốc cần xử lý ngay:"
    assert f"• A / 0900000001 / 5 Trần Phú — Chốt lúc {when}" in express
    assert scheduled == (
        "📦 [Sale A] — Lên đơn gửi hàng:\n"
        f"• A / 0900000001 — Ngày lên đơn: {when} — Sản phẩm: DC12"
    )


def test_template_thieu_du_lieu_thi_in_gach_ngang(order_factory):
    order = order_factory(order_source="landing", staff=None, name="", phone="")
    reminder = _reminder(order, None, SaleReminderKind.SCHEDULED, note="   ")

    message = tpl.build_scheduled_message(reminder)

    assert "• — / — — Hẹn lúc" in message
    assert message.endswith("cập nhật trạng thái.")
    assert "[Sale]" in message
    assert "Lý do hẹn: —" in message
    assert tpl._format_time(None) == "—"


def test_format_time_theo_mui_gio_vn_khong_theo_utc():
    """Giờ in ra cho sale phải là giờ VN (settings.TIME_ZONE); container chạy
    UTC nên `astimezone()` mặc định từng in lệch 7 tiếng."""
    assert tpl._format_time(FROZEN_NOW) == "10:30 21/09"
    # 20/09 20:00 UTC = 21/09 03:00 VN — qua ngày theo giờ VN.
    assert tpl._format_time(datetime(2026, 9, 20, 20, 0, tzinfo=UTC)) == "03:00 21/09"


def test_build_reminder_message_kind_la_fallback(landing, sale):
    order, _ = landing
    reminder = _reminder(order, sale, SaleReminderKind.SCHEDULED)
    reminder.kind = "kind-la"

    assert tpl.build_reminder_message(reminder) == f"Reminder #{reminder.id} kind=kind-la"


@pytest.mark.parametrize(
    "slot, body_tail, footer",
    [
        (tpl.NO_ANSWER_SLOT_START_OF_SHIFT, " từ hôm qua cần gọi lại.", "Hãy xử lý ngay sau khi xử lý xong data mới."),
        (tpl.NO_ANSWER_SLOT_MORNING_MID, " từ hôm qua và trong ca cần gọi lại.", "Đã gần hết ca sáng, bạn hãy xử lý luôn cho kịp."),
        (tpl.NO_ANSWER_SLOT_AFTERNOON_MID, " từ hôm qua và trong ca cần gọi lại.", "Đã gần hết ca chiều, bạn hãy xử lý luôn cho kịp."),
    ],
)
def test_no_answer_post_3_slot_dau_ca_giua_ca(sale, slot, body_tail, footer):
    sale.lark_open_id = "ou_1"

    payload = tpl.build_no_answer_summary_post(staff=sale, count=4, slot=slot)

    assert payload["msg_type"] == "post"
    body, foot = payload["content"]["post"]["zh_cn"]["content"]
    assert body == [
        {"tag": "at", "user_id": "ou_1", "user_name": "Sale A"},
        {"tag": "text", "text": ", bạn có "},
        {"tag": "text", "text": "4 số KNM lần 1, 2, 3", "style": ["bold"]},
        {"tag": "text", "text": body_tail},
    ]
    assert foot == [{"tag": "text", "text": footer}]


def test_no_answer_post_cuoi_ca_va_khong_co_lark_id(sale):
    payload = tpl.build_no_answer_summary_post(staff=sale, count=2, slot=tpl.NO_ANSWER_SLOT_END_OF_SHIFT)

    body, foot = payload["content"]["post"]["zh_cn"]["content"]
    assert body[0] == {"tag": "text", "text": "🔔 Còn 30 phút — "}
    assert body[1] == {"tag": "text", "text": "[Sale A]"}
    assert body[3] == {"tag": "text", "text": "2 Số KNM lần 1, 2, 3", "style": ["bold"]}
    assert foot == [{"tag": "text", "text": "Cuối ca nhớ cập nhật đủ trạng thái + báo cáo quản lý nhé."}]
    assert tpl._at_mention_post_segment(None) == {"tag": "text", "text": "[Sale]"}


def test_no_answer_post_slot_la_raise():
    with pytest.raises(ValueError, match="Unknown no-answer slot: 'toi'"):
        tpl.build_no_answer_summary_post(staff=None, count=1, slot="toi")
