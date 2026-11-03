"""Plain-text templates for Lark sale-status reminders.

Each builder accepts the `SaleReminder` (or a small shape for KNM aggregate)
and returns the message body to post. Mentions use `lark_open_id` when the
sale staff has one configured; otherwise the staff display name is rendered
as plain text in square brackets.
"""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone

from apps.orders.models import Order, SaleReminder, SaleReminderKind


def _mention(staff) -> str:
    if staff is None:
        return "[Sale]"
    lark_open_id = (getattr(staff, "lark_open_id", "") or "").strip()
    display = (getattr(staff, "display_name", "") or "").strip() or staff.get_username()
    if lark_open_id:
        return f'<at user_id="{lark_open_id}">{display}</at>'
    return f"[{display}]"


def _customer_label(order: Order) -> str:
    name = (order.name or "").strip() or "—"
    phone = (order.phone or "").strip() or "—"
    return f"{name} / {phone}"


def _format_time(dt: datetime | None) -> str:
    """Giờ hiển thị cho sale theo múi giờ VN (settings.TIME_ZONE), không theo
    múi giờ hệ điều hành của container (thường là UTC)."""
    if dt is None:
        return "—"
    return timezone.localtime(dt).strftime("%H:%M %d/%m")


def build_need_more_consulting_message(reminder: SaleReminder) -> str:
    order = reminder.order
    note = (reminder.note_snapshot or "").strip() or "—"
    sale = _mention(reminder.staff)

    if reminder.kind == SaleReminderKind.NEED_MORE_CONSULTING_D2:
        return (
            f"📞 {sale} — Follow-up Tư vấn thêm hôm nay:\n"
            f"• {_customer_label(order)} — {note}\n"
            "Hướng gọi: Không tư vấn lại từ đầu. Gọi đúng vào điểm còn băn khoăn hôm qua. "
            "Nếu khách lo giá → nhắc chương trình hiện tại + đổi trả 30 ngày. "
            "Nếu khách cần hỏi người thân → 'Anh/chị đã hỏi được chưa ạ, em hỗ trợ thêm gì không?' "
            "Nếu chốt được → cập nhật Sheet. Nếu chưa → ghi kết quả gọi vào cột ghi chú."
        )
    if reminder.kind == SaleReminderKind.NEED_MORE_CONSULTING_D4:
        return (
            f"🔄 {sale} — {_customer_label(order)} — đã 3 ngày, gọi lần 2 hôm nay.\n"
            f"Ghi chú lần trước: {note}\n"
            "Lần này đổi góc — đừng hỏi 'quyết chưa ạ': "
            "→ Mở bằng: 'Em gọi hỏi thăm [vấn đề sức khoẻ khách đề cập] anh/chị dạo này có đỡ hơn không ạ?' "
            "→ Nếu khách vẫn phân vân: hỏi thẳng 'Anh/chị còn băn khoăn điều gì để em giải đáp ạ?' "
            "→ Nếu khách nói bận: hẹn giờ cụ thể, đừng để mở."
        )
    # D+7
    return (
        f"⚡ {sale} — Cuộc gọi cuối: {_customer_label(order)}\n"
        f"Đã gọi ngày 2 và ngày 4. Lý do chưa chốt: {note}\n"
        "Script lần cuối: 'Dạ em gọi lần này để hỏi thăm anh/chị lần cuối ạ — "
        "nếu anh/chị vẫn chưa sẵn sàng thì em không làm phiền nữa. "
        "Nhưng nếu còn băn khoăn điều gì thì cho em biết để em hỗ trợ ạ.'\n"
        "Sau cuộc gọi này bắt buộc cập nhật 1 trong 2:\n"
        "✅ Chốt được → 'Đã lên đơn'\n"
        "❌ Không chốt → 'Không mua' hoặc 'Gọi lại sau [ngày cụ thể]'\n"
        "Không được giữ trạng thái 'Tư vấn thêm' quá 7 ngày."
    )


def build_scheduled_message(reminder: SaleReminder) -> str:
    order = reminder.order
    note = (reminder.note_snapshot or "").strip() or "—"
    sale = _mention(reminder.staff)
    return (
        f"📅 {sale} — Hôm nay có lịch hẹn gọi lại:\n"
        f"• {_customer_label(order)} — Hẹn lúc {_format_time(reminder.due_at)} — Lý do hẹn: {note}\n"
        "Lưu ý: Khách đã đồng ý hẹn giờ cụ thể — gọi đúng giờ, không trễ quá 15 phút. "
        "Nếu khách không nghe → gọi lại sau 30 phút rồi cập nhật trạng thái."
    )


def build_call_back_later_message(reminder: SaleReminder) -> str:
    order = reminder.order
    note = (reminder.note_snapshot or "").strip() or "—"
    sale = _mention(reminder.staff)
    return (
        f"🔁 {sale} — Khách yêu cầu gọi lại hôm nay:\n"
        f"• {_customer_label(order)} — Lý do dời: {note}\n"
        "Khách này chủ động dời lịch — khả năng mua vẫn còn. Gọi nhẹ nhàng, không hỏi ngay "
        "'anh/chị chốt chưa' — mở bằng 'Em gọi theo lịch hẹn hôm [ngày] ạ' để khách nhớ bối cảnh.\n"
        "Nếu hôm nay vẫn chưa chốt → hỏi rõ ngày cụ thể tiếp theo hoặc chuyển sang 'Tư vấn thêm' để theo dõi."
    )


def build_closed_express_message(reminder: SaleReminder) -> str:
    order = reminder.order
    addr = (order.raw_address or order.address or "—").strip()
    sale = _mention(reminder.staff)
    return (
        f"🚀 {sale} — Đơn hoả tốc cần xử lý ngay:\n"
        f"• {_customer_label(order)} / {addr} — Chốt lúc {_format_time(reminder.due_at)}\n"
        "Việc cần làm ngay:\n"
        "✅ Xác nhận lại địa chỉ giao với khách\n"
        "• Nhắn khách khung giờ giao dự kiến\n"
        "Không để đơn hoả tốc chờ quá 30 phút kể từ khi chốt."
    )


def build_closed_scheduled_date_message(reminder: SaleReminder) -> str:
    order = reminder.order
    sale = _mention(reminder.staff)
    product = (order.product or "—").strip() or "—"
    return (
        f"📦 {sale} — Lên đơn gửi hàng:\n"
        f"• {_customer_label(order)} — Ngày lên đơn: {_format_time(reminder.due_at)} — Sản phẩm: {product}"
    )


def build_resale_appointment_message(reminder: SaleReminder) -> str:
    order = reminder.order
    sale = _mention(reminder.staff)
    note = (reminder.note_snapshot or "").strip() or "—"
    return (
        f"🤝 {sale} — Lịch hẹn resale hôm nay:\n"
        f"• {_customer_label(order)} — Hẹn lúc {_format_time(reminder.due_at)} — {note}\n"
        "Đây là khách cũ đã mua hàng — không tư vấn lại từ đầu. Mở bằng lời hỏi thăm "
        "tình hình sử dụng sản phẩm trước, rồi mới giới thiệu sản phẩm resale phù hợp. "
        "Sau cuộc hẹn nhớ cập nhật kết quả vào trang theo dõi resale."
    )


_BUILDERS = {
    SaleReminderKind.NEED_MORE_CONSULTING_D2: build_need_more_consulting_message,
    SaleReminderKind.NEED_MORE_CONSULTING_D4: build_need_more_consulting_message,
    SaleReminderKind.NEED_MORE_CONSULTING_D7: build_need_more_consulting_message,
    SaleReminderKind.SCHEDULED: build_scheduled_message,
    SaleReminderKind.CALL_BACK_LATER: build_call_back_later_message,
    SaleReminderKind.CLOSED_EXPRESS: build_closed_express_message,
    SaleReminderKind.CLOSED_SCHEDULED_DATE: build_closed_scheduled_date_message,
    SaleReminderKind.RESALE_APPOINTMENT: build_resale_appointment_message,
}


def build_reminder_message(reminder: SaleReminder) -> str:
    builder = _BUILDERS.get(reminder.kind)
    if builder is None:
        return f"Reminder #{reminder.id} kind={reminder.kind}"
    return builder(reminder)


# -------- No-answer aggregate (KNM) --------

# 4 fixed slots per day. Go task-cron enqueues one job per slot.
NO_ANSWER_SLOT_START_OF_SHIFT = "start_of_shift"  # 08:00 — đầu ca
NO_ANSWER_SLOT_MORNING_MID = "morning_mid"        # 11:00 — cuối ca sáng
NO_ANSWER_SLOT_AFTERNOON_MID = "afternoon_mid"    # 15:00 — giữa ca chiều
NO_ANSWER_SLOT_END_OF_SHIFT = "end_of_shift"      # 16:30 — 30' trước hết ca

NO_ANSWER_SLOTS = (
    NO_ANSWER_SLOT_START_OF_SHIFT,
    NO_ANSWER_SLOT_MORNING_MID,
    NO_ANSWER_SLOT_AFTERNOON_MID,
    NO_ANSWER_SLOT_END_OF_SHIFT,
)


def _at_mention_post_segment(staff) -> dict:
    """Return a single post-content segment that @-mentions the staff.

    Falls back to a plain `[Display Name]` text segment when no lark_open_id
    is configured for the user.
    """
    if staff is None:
        return {"tag": "text", "text": "[Sale]"}
    lark_open_id = (getattr(staff, "lark_open_id", "") or "").strip()
    display = (getattr(staff, "display_name", "") or "").strip() or staff.get_username()
    if lark_open_id:
        return {"tag": "at", "user_id": lark_open_id, "user_name": display}
    return {"tag": "text", "text": f"[{display}]"}


def build_no_answer_summary_post(*, staff, count: int, slot: str) -> dict:
    """Build a Lark `msg_type=post` payload — count rendered bold.

    Per spec we only print the aggregate count, not the per-order list. The
    slot picks the lead-in and footer wording (đầu ca / 11h / 15h / cuối ca).
    """
    at_segment = _at_mention_post_segment(staff)
    count_segment = {
        "tag": "text",
        "text": f"{count} số KNM lần 1, 2, 3",
        "style": ["bold"],
    }

    if slot == NO_ANSWER_SLOT_START_OF_SHIFT:
        body_line = [
            at_segment,
            {"tag": "text", "text": ", bạn có "},
            count_segment,
            {"tag": "text", "text": " từ hôm qua cần gọi lại."},
        ]
        footer_line = [{"tag": "text", "text": "Hãy xử lý ngay sau khi xử lý xong data mới."}]
    elif slot == NO_ANSWER_SLOT_MORNING_MID:
        body_line = [
            at_segment,
            {"tag": "text", "text": ", bạn có "},
            count_segment,
            {"tag": "text", "text": " từ hôm qua và trong ca cần gọi lại."},
        ]
        footer_line = [
            {"tag": "text", "text": "Đã gần hết ca sáng, bạn hãy xử lý luôn cho kịp."}
        ]
    elif slot == NO_ANSWER_SLOT_AFTERNOON_MID:
        body_line = [
            at_segment,
            {"tag": "text", "text": ", bạn có "},
            count_segment,
            {"tag": "text", "text": " từ hôm qua và trong ca cần gọi lại."},
        ]
        footer_line = [
            {"tag": "text", "text": "Đã gần hết ca chiều, bạn hãy xử lý luôn cho kịp."}
        ]
    elif slot == NO_ANSWER_SLOT_END_OF_SHIFT:
        body_line = [
            {"tag": "text", "text": "🔔 Còn 30 phút — "},
            at_segment,
            {"tag": "text", "text": " check lại nhé: "},
            {"tag": "text", "text": f"{count} Số KNM lần 1, 2, 3", "style": ["bold"]},
            {"tag": "text", "text": " tồn lại → đã gọi lại chưa?"},
        ]
        footer_line = [
            {"tag": "text", "text": "Cuối ca nhớ cập nhật đủ trạng thái + báo cáo quản lý nhé."}
        ]
    else:
        raise ValueError(f"Unknown no-answer slot: {slot!r}")

    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": "",
                    "content": [body_line, footer_line],
                }
            }
        },
    }
