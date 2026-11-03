"""Poll hội thoại có số điện thoại của một page Pancake → `pancake_leads` → tạo
đơn `orders` (từ 2026-09-17, xem `apps.pancake.application.services.lead_order_service`).

Mọi hội thoại vừa cập nhật đều được gọi chi tiết (không chỉ `has_phone`) — xem
chú thích trong `_scan`. Cách đi qua danh sách: `since = now − 30 ngày` (với
`order_by=updated_at` Pancake lọc theo `updated_at`, nên hội thoại tạo từ lâu vừa
có tin mới vẫn về), kết quả DESC theo `updated_at`, đọc trang 1, 2, … và DỪNG khi
gặp hội thoại có `updated_at` cũ hơn cursor (trừ 5 phút đệm). Bình thường mỗi
lượt một request cho mỗi page. Cách dừng sớm này CHỈ đúng khi danh sách thật sự
DESC theo `updated_at` — sự cố 21/09/2026: client gửi nhầm `sort_by`, Pancake trả
DESC theo `inserted_at`, poll dừng ngay ở hội thoại thứ 2 và bỏ sót mọi hội thoại
cũ vừa cho số (xem `pancake_client`).

Chỉ nhận số cho trong 24h gần nhất (`PHONE_WINDOW`, từ 22/09/2026): với
`order_by=updated_at`, hội thoại cũ vừa có tin mới cũng về, mà `recent_phone_numbers`
ở hồ sơ khách giữ cả số cho từ nhiều ngày trước → sale nhận lead cũ. Mốc "cho số"
= `inserted_at` tin nhắn có số mới nhất; không có tin nào (số gắn qua đơn POS) thì
rơi về `updated_at` hội thoại và vẫn nhận nếu hội thoại mới trong 24h. Số cũ hơn
đếm `skipped`, không tạo lead/đơn, cursor vẫn tiến.

Cursor `polled_until` = max(updated_at) của lượt, CHỈ tiến khi lượt page đó không
lỗi phía Pancake. Lỗi tạo đơn cho một lead KHÔNG chặn cursor: lead đã ghi
`order_failed`, người vận hành tạo lại bằng tay (`retry_lead_order`).

Hội thoại `type=COMMENT` (comment Facebook): response chi tiết vừa lấy cũng được
lưu thành bài + comment (`comment_store`, từ 24/09/2026) — không thêm request.
Lỗi lưu comment chỉ log, không chặn lead/cursor.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from celery.exceptions import SoftTimeLimitExceeded
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.phone_utils import normalize_phone
from apps.pancake import metrics
from apps.pancake.application.services import comment_store
from apps.pancake.application.services.lead_order_service import create_order_for_lead
from apps.pancake.infrastructure.pancake_client import (
    ConversationDetail,
    ConversationDTO,
    PancakeClient,
    PancakeError,
    PancakePageTokenRejected,
    PancakeTokenError,
)
from apps.pancake.models import PancakeLead, PancakePage, PancakePushStatus

logger = logging.getLogger(__name__)


@dataclass
class PollResult:
    page_id: int
    fetched: int = 0
    leads_created: int = 0
    orders_created: int = 0
    skipped: int = 0
    comments_saved: int = 0
    error: str = ""


class PancakePollService:
    LOOKBACK = timedelta(days=30)
    CURSOR_SLACK = timedelta(minutes=5)
    PHONE_WINDOW = timedelta(hours=24)
    PAGE_SIZE = 200

    def __init__(self, client: PancakeClient):
        self.client = client

    def now(self) -> datetime:  # tách ra để test ghim thời gian
        return timezone.now()

    def poll_page(self, page: PancakePage) -> PollResult:
        result = PollResult(page_id=page.id)
        if not page.is_enabled or page.channel_id is None or not page.page_access_token or page.foxia_kind is None:
            result.error = "page chưa sẵn sàng (chưa bật / chưa gán kênh / chưa có token / nền tảng không hỗ trợ)"
            return result

        started = time.monotonic()
        now = self.now()
        try:
            try:
                self._scan(page, page.page_access_token, now, result)
            except PancakePageTokenRejected:
                # Sinh lại token đúng MỘT lần trong lượt; vẫn hỏng thì tắt page để
                # người vận hành thấy trên bảng, không âm thầm lỗi mỗi 3 phút. Giữ
                # nguyên `result` (không tạo mới): số đếm của phần đã quét trước khi
                # bị từ chối vẫn có giá trị tham khảo.
                token = self.client.generate_page_token(page.pancake_page_id)
                page.page_access_token = token
                page.token_error = ""
                page.save(update_fields=["page_access_token", "token_error"])
                logger.info("Pancake poll: page=%s token bị từ chối, đã sinh lại token", page.id)
                self._scan(page, token, now, result)
        except (PancakeTokenError, PancakePageTokenRejected) as exc:
            # CHỈ lỗi token thật mới tắt page. `PancakeUnavailable` (Pancake 5xx khi
            # sinh lại token) rơi xuống nhánh lỗi chung bên dưới: giữ page + token cũ
            # + cursor, lượt sau thử lại (sự cố 03/10/2026).
            result.error = f"{type(exc).__name__}: {exc}"
            page.is_enabled = False
            page.page_access_token = ""
            page.token_error = str(exc)
            page.last_error = result.error
            page.last_polled_at = now
            page.save(update_fields=["is_enabled", "page_access_token", "token_error", "last_error", "last_polled_at"])
            metrics.pancake_poll_pages_total.labels(status="token_rejected").inc()
            logger.warning("Pancake poll: page=%s token bị từ chối, đã tắt page (%s)", page.id, exc)
            return result
        except SoftTimeLimitExceeded:
            # Task sắp bị Celery giết cứng (time_limit) — thoát NGAY để khối `finally`
            # bên dưới chạy và single-flight lock (`poll_phone_leads`) được nhả ra,
            # không thì lượt sau cứ báo "lượt trước chưa xong" tới khi lock tự hết TTL.
            raise
        except Exception as exc:  # noqa: BLE001 — lỗi đối tác không được thoát ra task
            result.error = f"{type(exc).__name__}: {exc}"
            page.last_error = result.error
            page.last_polled_at = now
            page.save(update_fields=["last_error", "last_polled_at"])
            metrics.pancake_poll_pages_total.labels(status="error").inc()
            logger.warning("Pancake poll: page=%s lỗi, giữ cursor (%s)", page.id, result.error)
            return result
        finally:
            metrics.pancake_poll_duration_seconds.observe(time.monotonic() - started)

        metrics.pancake_poll_pages_total.labels(status="ok").inc()
        logger.info(
            "Pancake poll ok: page=%s fetched=%s leads=%s orders=%s skipped=%s comments=%s",
            page.id, result.fetched, result.leads_created, result.orders_created, result.skipped,
            result.comments_saved,
        )
        return result

    def _scan(self, page: PancakePage, token: str, now: datetime, result: PollResult) -> None:
        cursor = page.polled_until or now
        threshold = cursor - self.CURSOR_SLACK
        max_updated = cursor
        since, until = now - self.LOOKBACK, now
        page_number = 1
        while True:
            conv_page = self.client.list_conversations(page.pancake_page_id, token, since, until, page_number)
            stop = False
            for conv in conv_page.items:
                result.fetched += 1
                if conv.updated_at is not None and conv.updated_at < threshold:
                    stop = True
                    break
                if conv.updated_at is not None and conv.updated_at > max_updated:
                    max_updated = conv.updated_at
                # KHÔNG lọc theo `has_phone`: cờ đó chỉ bật khi số xuất hiện trong tin
                # nhắn; số nhân viên gắn qua đơn Pancake POS chỉ thấy được ở hồ sơ khách
                # của endpoint chi tiết (đo 17/09/2026: 9/27 số nhập tay). Giá: 1 request
                # chi tiết cho mỗi hội thoại vừa cập nhật (~500/ngày, ~2 mỗi lượt 3 phút).
                self._handle_conversation(page, token, conv, now, result)
            if stop or not conv_page.items or page_number * self.PAGE_SIZE >= conv_page.total:
                break
            page_number += 1

        page.polled_until = max_updated
        page.last_polled_at = now
        page.last_error = ""
        page.save(update_fields=["polled_until", "last_polled_at", "last_error"])

    def _handle_conversation(
        self, page: PancakePage, token: str, conv: ConversationDTO, now: datetime, result: PollResult
    ) -> None:
        # Đã có lead của hội thoại này mà hội thoại không mới hơn lúc cho số → khỏi
        # tốn request chi tiết. Hội thoại có tin mới thì gọi lại: có thể là số thứ hai.
        # Ngoại lệ: hội thoại COMMENT chưa có comment nào trong kho (lead có trước
        # khi bắt đầu lưu comment) — vẫn gọi để lưu comment.
        latest = (
            PancakeLead.objects.filter(page=page, conversation_id=conv.id)
            .order_by("-phone_given_at")
            .values_list("phone_given_at", flat=True)
            .first()
        )
        if (
            latest is not None
            and conv.updated_at is not None
            and conv.updated_at <= latest
            and (conv.type != comment_store.COMMENT_TYPE or comment_store.has_stored_comments(page, conv.id))
        ):
            return

        try:
            detail = self.client.get_conversation_detail(page.pancake_page_id, token, conv.id)
        except PancakePageTokenRejected:
            # Để nguyên đường sinh lại token — bên gọi (poll_page) cần bắt được lỗi này.
            raise
        except PancakeError as exc:
            # MỘT hội thoại lỗi không được giữ cursor cả 30 ngày (hội thoại "độc": vẫn
            # mới hơn ngưỡng nên lượt sau lại vấp lại y hệt). Rơi về dữ liệu tóm tắt của
            # conv, không log tên/sđt.
            logger.warning(
                "Pancake poll: page=%s hội thoại=%s lỗi lấy chi tiết, dùng dữ liệu tóm tắt (%s)",
                page.id, conv.id, exc,
            )
            detail = ConversationDetail(phones=[], customer_name="", customer_id="", phone_message_at=None)

        try:
            result.comments_saved += comment_store.save_comments(page, conv, detail)
        except Exception as exc:  # noqa: BLE001 — lưu comment là phụ, không chặn lead
            logger.warning(
                "Pancake poll: page=%s hội thoại=%s lỗi lưu comment (%s)", page.id, conv.id, type(exc).__name__,
            )

        phones = detail.phones or conv.phones
        name = detail.customer_name or conv.customer_name
        customer_id = detail.customer_id or conv.customer_id
        given_at = detail.phone_message_at or conv.updated_at
        if given_at is not None and given_at < now - self.PHONE_WINDOW:
            # Số cho từ quá 24h (hội thoại cũ vừa có tin mới không chứa số) → lead cũ,
            # bỏ qua. Không ghi lead để lần sau hội thoại thật sự cho số mới còn được xét.
            result.skipped += len(phones)
            return

        for raw in phones:
            phone = normalize_phone(raw)
            if not phone:
                result.skipped += 1
                continue
            defaults = {
                "page": page,
                "conversation_id": conv.id,
                "conversation_inserted_at": conv.inserted_at,
                "pancake_customer_id": customer_id[:64],
                "customer_name": name[:255],
                "phone_raw": raw[:32],
                "tags": conv.tags,
                "phone_given_at": given_at,
                "assignee_name": conv.assignee_name[:255],
                "foxia_kind": page.foxia_kind,
            }
            try:
                with transaction.atomic():
                    lead, created = PancakeLead.objects.get_or_create(
                        channel_id=page.channel_id, phone=phone, defaults=defaults
                    )
            except IntegrityError:
                logger.warning(
                    "Pancake poll: page=%s hội thoại=%s IntegrityError khi tạo lead, bỏ qua", page.id, conv.id
                )
                created = False
            if not created:
                continue
            result.leads_created += 1
            lead = create_order_for_lead(lead)
            if lead.push_status == PancakePushStatus.ORDER_CREATED:
                result.orders_created += 1
