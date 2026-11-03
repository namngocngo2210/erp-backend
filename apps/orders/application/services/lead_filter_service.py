"""Lead filter: drop spam/duplicate/claimed phones before an order is created.

Called at every ingest entry point (landing API, Lark, KiotViet webhook, Excel
import). First channel to claim a phone keeps it permanently; later attempts
from other channels are rejected.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from collections.abc import Iterable

from django.db import transaction
from django.utils import timezone

from apps.channels_core.models import Channel
from apps.customers.services import normalize_phone, phone_lookup_candidates
from apps.orders.models import (
    Order,
    OrderStatus,
    PhoneBlocklist,
    PhoneBlocklistReason,
    PhoneClaim,
)

# Codes on OrderAssessment that should auto-insert a blocklist entry.
SPAM_ASSESSMENT_CODES = {"customer_classification_code", "sales_status_code"}

logger = logging.getLogger(__name__)

# Orders past these statuses are considered closed/terminal; a new order with
# the same phone is allowed after one of these.
TERMINAL_STATUSES = (
    OrderStatus.DELIVERED_SUCCESS,
    OrderStatus.RETURNED,
    OrderStatus.CANCELLED,
)


# Cửa sổ trùng TRƯỢT tính từ thời điểm ingest (đổi 2026-09-21, trước đó neo mốc
# 00:00 hôm nay nên lead 23:50 rồi 00:10 lọt qua). Tạm là hằng số; kế hoạch
# đưa vào Cấu hình hệ thống (apps.sysconfig) khi nhánh đó xong.
DUPLICATE_WINDOW_HOURS = 24

DUPLICATE_REASON = f"Đã có đơn đang xử lý cho số này trong {DUPLICATE_WINDOW_HOURS} giờ qua."


def _window_start():
    """Mốc sớm nhất mà một đơn còn bị coi là trùng với lead đang vào."""
    return timezone.now() - timedelta(hours=DUPLICATE_WINDOW_HOURS)


class DuplicateOrderError(Exception):
    """Raised khi dedup gate (chạy trong transaction có khóa) phát hiện đã có
    đơn trùng cho cùng phone+product (hoặc phone+channel) trong cửa sổ
    `DUPLICATE_WINDOW_HOURS`. Caller nên bỏ qua việc tạo đơn.
    """

    def __init__(self, decision: FilterDecision):
        self.decision = decision
        super().__init__(decision.reason or "Đơn trùng đã tồn tại.")


class FilterAction(str, Enum):
    ACCEPT = "accept"
    REJECT_INVALID_PHONE = "reject_invalid_phone"
    REJECT_BLOCKED = "reject_blocked"
    REJECT_DUPLICATE = "reject_duplicate"


@dataclass
class FilterDecision:
    action: FilterAction
    phone_normalized: str = ""
    reason: str = ""
    existing_channel_id: int | None = None
    existing_channel_code: str = ""
    existing_order_id: int | None = None
    blocklist_reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.action == FilterAction.ACCEPT

    def to_response_dict(self) -> dict:
        """Serialize the decision into a stable JSON body for API clients."""
        payload: dict = {
            "accepted": self.accepted,
            "action": self.action.value,
            "reason": self.reason,
            "phone_normalized": self.phone_normalized,
        }
        if self.existing_channel_code:
            payload["existing_channel_code"] = self.existing_channel_code
        if self.existing_channel_id:
            payload["existing_channel_id"] = self.existing_channel_id
        if self.existing_order_id:
            payload["existing_order_id"] = self.existing_order_id
        if self.blocklist_reason:
            payload["blocklist_reason"] = self.blocklist_reason
        return payload


class LeadFilterService:
    """Three-step pipeline: normalize → blocklist → duplicate."""

    @staticmethod
    def evaluate(
        phone_raw: str,
        channel: Channel,
        product_ids: Iterable[int] | None = None,
    ) -> FilterDecision:
        """Decide whether an ingest attempt should be accepted.

        Duplicate scope (xem `_find_duplicate_order`):
        - Cùng phone + cùng product trong 24h qua → trùng, KỂ CẢ khác kênh:
          cùng một số đặt cùng sản phẩm rơi vào hai kênh thì chỉ giữ một đơn.
        - Cùng phone nhưng KHÁC product vẫn được chấp nhận — khách có thể mua
          product A ở kênh X và product B ở kênh Y trong cùng khoảng đó.
        - Khi không xác định được product, chỉ chặn trùng trong cùng kênh.

        `PhoneClaim` is recorded by `register_claim` for first-touch analytics
        but does not gate ingest.
        """
        normalized = normalize_phone(phone_raw or "")
        if not normalized:
            return FilterDecision(
                action=FilterAction.REJECT_INVALID_PHONE,
                reason="Số điện thoại không hợp lệ.",
            )

        lookup = phone_lookup_candidates(phone_raw)

        blocked = PhoneBlocklist.objects.filter(phone_normalized__in=lookup).first()
        if blocked is not None:
            return FilterDecision(
                action=FilterAction.REJECT_BLOCKED,
                phone_normalized=normalized,
                reason="Số điện thoại nằm trong danh sách chặn.",
                blocklist_reason=blocked.reason,
            )

        duplicate = LeadFilterService._find_duplicate_order(lookup, channel, product_ids)
        if duplicate is not None:
            return FilterDecision(
                action=FilterAction.REJECT_DUPLICATE,
                phone_normalized=normalized,
                reason=DUPLICATE_REASON,
                existing_channel_id=duplicate.channel_id,
                existing_channel_code=getattr(duplicate.channel, "code", ""),
                existing_order_id=duplicate.id,
            )

        return FilterDecision(
            action=FilterAction.ACCEPT,
            phone_normalized=normalized,
        )

    @staticmethod
    def _find_duplicate_order(
        lookup: Iterable[str],
        channel: Channel,
        product_ids: Iterable[int] | None = None,
    ) -> Order | None:
        """Tìm đơn trùng (cùng phone, tạo trong `DUPLICATE_WINDOW_HOURS` giờ qua,
        chưa terminal). Dùng chung bởi `evaluate` (không khóa) và
        `claim_or_reject` (trong transaction có khóa).

        Phạm vi trùng:
        - Khi xác định được product: trùng nếu cùng phone + cùng product trong
          cửa sổ, KỂ CẢ khác kênh — một số chỉ nên có một đơn đang xử lý cho
          cùng sản phẩm dù lead rơi vào nhiều kênh cùng lúc.
        - Khi không xác định được product (không gửi/không map được product_id):
          chỉ chặn trùng trong CÙNG kênh, vì thiếu product không thể so khớp
          chéo kênh một cách an toàn.
        """
        product_id_list = [pid for pid in (product_ids or []) if pid]
        duplicate_qs = (
            Order.objects.filter(
                phone__in=lookup,
                deleted_at__isnull=True,
                created_at__gte=_window_start(),
            )
            .exclude(status__in=TERMINAL_STATUSES)
        )
        if product_id_list:
            duplicate_qs = duplicate_qs.filter(items__product_id__in=product_id_list)
        else:
            duplicate_qs = duplicate_qs.filter(channel_id=channel.id)
        return duplicate_qs.order_by("-created_at").distinct().first()

    @staticmethod
    def auto_block_from_spam(
        phone_raw: str,
        *,
        source_field: str,
        order_id: int | None = None,
        created_by=None,
    ) -> PhoneBlocklist | None:
        """Add phone to blocklist when an OrderAssessment marks it as spam.

        Idempotent — safe to call repeatedly from the assessment save path or
        from a backfill. Returns the blocklist row when newly inserted; None
        if the phone is invalid or already blocked.
        """
        normalized = normalize_phone(phone_raw or "")
        if not normalized:
            return None
        note = f"auto: {source_field}=spam"
        if order_id:
            note += f" order_id={order_id}"
        entry, created = PhoneBlocklist.objects.get_or_create(
            phone_normalized=normalized,
            defaults={
                "reason": PhoneBlocklistReason.SPAM,
                "notes": note,
                "created_by": created_by,
            },
        )
        if not created:
            return None
        return entry

    @staticmethod
    def claim_or_reject(
        phone_raw: str,
        channel: Channel,
        product_ids: Iterable[int] | None = None,
    ) -> str:
        """Cổng dedup nguyên tử — PHẢI gọi BÊN TRONG transaction tạo đơn.

        Khóa hàng `PhoneClaim` của số điện thoại bằng `select_for_update`
        (get_or_create), khiến hai ingest đồng thời cho cùng một số phải nối
        tiếp nhau. Sau khi giành được khóa, kiểm tra lại đơn trùng: nếu một
        transaction trước đã tạo đơn (và commit), transaction sau sẽ thấy đơn
        đó và bị từ chối — chặn việc tạo đơn trùng do TOCTOU.

        Trả về normalized phone khi hợp lệ để tạo đơn. Raise `DuplicateOrderError`
        khi đã có đơn trùng. Việc cập nhật first_order/hit_count do
        `register_claim` đảm nhiệm sau khi đơn đã được tạo.
        """
        normalized = normalize_phone(phone_raw or "")
        if not normalized:
            # Số không hợp lệ thì không có gì để khóa; để luồng tạo đơn xử lý
            # như cũ (evaluate đã chặn từ trước, đây chỉ là phòng vệ).
            return normalized

        # Giành khóa hàng claim của riêng số này (lock hẹp theo phone).
        PhoneClaim.objects.select_for_update().get_or_create(
            phone_normalized=normalized,
            defaults={"channel": channel},
        )

        lookup = phone_lookup_candidates(phone_raw)
        duplicate = LeadFilterService._find_duplicate_order(lookup, channel, product_ids)
        if duplicate is not None:
            raise DuplicateOrderError(
                FilterDecision(
                    action=FilterAction.REJECT_DUPLICATE,
                    phone_normalized=normalized,
                    reason=DUPLICATE_REASON,
                    existing_channel_id=duplicate.channel_id,
                    existing_channel_code=getattr(duplicate.channel, "code", ""),
                    existing_order_id=duplicate.id,
                )
            )
        return normalized

    @staticmethod
    def register_claim(phone_raw: str, channel: Channel, order: Order | None = None) -> PhoneClaim | None:
        """Record/refresh the first-touch phone→channel binding.

        Call after the order row is committed. Uses `get_or_create` so a race
        between two ingests doesn't double-claim. If a claim already exists for
        the same channel, updates `last_seen_at` and `hit_count`.
        """
        normalized = normalize_phone(phone_raw or "")
        if not normalized:
            return None
        try:
            with transaction.atomic():
                claim, created = PhoneClaim.objects.select_for_update().get_or_create(
                    phone_normalized=normalized,
                    defaults={
                        "channel": channel,
                        "first_order": order,
                    },
                )
                if not created:
                    update_fields = ["last_seen_at"]
                    claim.hit_count = (claim.hit_count or 0) + 1
                    update_fields.append("hit_count")
                    if claim.first_order_id is None and order is not None and claim.channel_id == channel.id:
                        claim.first_order = order
                        update_fields.append("first_order")
                    claim.save(update_fields=update_fields)
                return claim
        except Exception:
            logger.exception(
                "Failed to register PhoneClaim (phone=%s, channel_id=%s, order_id=%s)",
                normalized,
                channel.id if channel else None,
                getattr(order, "id", None),
            )
            return None
