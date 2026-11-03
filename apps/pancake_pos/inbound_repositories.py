"""Bảng webhook Pancake → CRM (two-way-sync §5.1, §7 B1): sự kiện thô + trạng thái
đơn Pancake phía CRM + liên kết đơn gốc Pancake. Chỉ app này ghi các bảng dưới."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Q
from django.utils import timezone

from apps.pancake_pos.domain.delivery_status import PartState
from apps.pancake_pos.models import (
    InboundMatchStatus,
    OrderLinkOrigin,
    OrderLinkStatus,
    PancakePosInboundOrder,
    PancakePosOrderLink,
    PancakePosStatusMap,
    PancakePosWebhookEvent,
    WebhookEventStatus,
    WebhookEventType,
)

MAX_ERROR_LEN = 2000


@dataclass(frozen=True)
class InboundPart:
    state: PartState
    tracking_code: str
    partner_name: str
    pancake_warehouse_id: str
    shipped_at: datetime | None


class WebhookEventRepository:
    @staticmethod
    def store(
        *, event_type: str, record_id: str, version: datetime | None, payload: Any, payload_hash: str,
        status: str = WebhookEventStatus.PENDING,
    ) -> int | None:
        """Lưu sự kiện thô. None = đã có bản y hệt (Pancake gửi lại)."""
        try:
            with transaction.atomic():
                event = PancakePosWebhookEvent.objects.create(
                    event_type=event_type, record_id=record_id[:128], record_version=version,
                    payload=payload, payload_hash=payload_hash, status=status,
                    processed_at=timezone.now() if status == WebhookEventStatus.IGNORED else None,
                )
        except IntegrityError:
            return None
        return int(event.id)

    @staticmethod
    def lock(event_id: int) -> PancakePosWebhookEvent | None:
        """Khoá sự kiện cần xử lý (gọi trong transaction). Worker khác đang giữ → None."""
        return (
            PancakePosWebhookEvent.objects.select_for_update(skip_locked=True)
            .filter(id=event_id, status__in=[WebhookEventStatus.PENDING, WebhookEventStatus.ERROR])
            .first()
        )

    @staticmethod
    def get(event_id: int) -> PancakePosWebhookEvent | None:
        return PancakePosWebhookEvent.objects.filter(id=event_id).first()

    @staticmethod
    def finish(event_id: int, *, status: str, error: str = "", count_attempt: bool = True) -> None:
        now = timezone.now()
        fields: dict[str, Any] = {"status": status, "error": (error or "")[:MAX_ERROR_LEN], "processed_at": now}
        qs = PancakePosWebhookEvent.objects.filter(id=event_id)
        if count_attempt:
            fields["attempts"] = F("attempts") + 1
        qs.update(**fields)

    @staticmethod
    def due_order_events(*, limit: int, max_attempts: int, retry_after: timedelta) -> list[int]:
        """Sự kiện đơn cần xử lý: pending + lỗi còn lượt đã quá thời gian chờ. Theo thứ tự
        nhận (index partial `ppos_webhook_todo_idx`)."""
        cutoff = timezone.now() - retry_after
        qs = PancakePosWebhookEvent.objects.filter(event_type=WebhookEventType.ORDERS).filter(
            Q(status=WebhookEventStatus.PENDING)
            | Q(status=WebhookEventStatus.ERROR, attempts__lt=max_attempts, processed_at__lt=cutoff)
        )
        return list(qs.order_by("received_at", "id").values_list("id", flat=True)[:limit])

    @staticmethod
    def reset_errors(*, limit: int = 500) -> list[int]:
        """"Xử lý lại sự kiện lỗi" (UI): đưa về pending, đếm lại lượt."""
        ids = list(
            PancakePosWebhookEvent.objects.filter(status=WebhookEventStatus.ERROR)
            .order_by("received_at", "id").values_list("id", flat=True)[:limit]
        )
        if ids:
            PancakePosWebhookEvent.objects.filter(id__in=ids).update(
                status=WebhookEventStatus.PENDING, attempts=0, error=""
            )
        return ids

    @staticmethod
    def stats_since(since: datetime) -> list[dict[str, Any]]:
        rows = (
            PancakePosWebhookEvent.objects.filter(received_at__gte=since)
            .values("event_type", "status").annotate(n=Count("id")).order_by("event_type", "status")
        )
        return [{"event_type": r["event_type"], "status": r["status"], "count": int(r["n"])} for r in rows]

    @staticmethod
    def summary() -> dict[str, Any]:
        agg = PancakePosWebhookEvent.objects.aggregate(last=Max("received_at"))
        errors = PancakePosWebhookEvent.objects.filter(status=WebhookEventStatus.ERROR)
        last_error = errors.order_by("-received_at", "-id").values(
            "id", "event_type", "record_id", "error", "attempts", "received_at"
        ).first()
        return {"last_received_at": agg["last"], "error_count": errors.count(), "last_error": last_error}

    @staticmethod
    def purge(*, older_than: datetime, limit: int = 5000) -> int:
        """Dọn sự kiện đã xử lý/bỏ qua cũ (giữ lỗi + pending). Theo lô để không khoá lâu."""
        ids = list(
            PancakePosWebhookEvent.objects.filter(
                received_at__lt=older_than,
                status__in=[WebhookEventStatus.PROCESSED, WebhookEventStatus.IGNORED],
            ).values_list("id", flat=True)[:limit]
        )
        if not ids:
            return 0
        # Dòng inbound trỏ `last_event` sang sự kiện bị dọn → SET_NULL (FK on_delete).
        deleted, _ = PancakePosWebhookEvent.objects.filter(id__in=ids).delete()
        return deleted


class InboundOrderRepository:
    @staticmethod
    def lock(pancake_order_id: str) -> PancakePosInboundOrder:
        """Lấy (tạo nếu chưa có) và KHOÁ dòng của một đơn Pancake — gọi trong transaction.
        Hai worker cùng đơn chạy nối tiếp (thứ tự `updated_at` được giữ)."""
        try:
            with transaction.atomic():
                PancakePosInboundOrder.objects.get_or_create(pancake_order_id=pancake_order_id)
        except IntegrityError:
            pass  # worker khác vừa tạo
        return PancakePosInboundOrder.objects.select_for_update().get(pancake_order_id=pancake_order_id)

    @staticmethod
    def lock_by_id(inbound_id: int) -> PancakePosInboundOrder | None:
        return PancakePosInboundOrder.objects.select_for_update().filter(id=inbound_id).first()

    @staticmethod
    def to_rematch(*, limit: int) -> list[int]:
        """Đơn chưa khớp (chờ / chờ khớp tay nhưng không phải ca nhiều ứng viên)."""
        return list(
            PancakePosInboundOrder.objects.filter(
                match_status__in=[InboundMatchStatus.WAITING, InboundMatchStatus.UNMATCHED], candidates=[]
            ).order_by("first_seen_at", "id").values_list("id", flat=True)[:limit]
        )

    @staticmethod
    def pancake_statuses(pancake_order_ids: list[str]) -> dict[str, int | None]:
        return dict(
            PancakePosInboundOrder.objects.filter(pancake_order_id__in=pancake_order_ids)
            .values_list("pancake_order_id", "pancake_status")
        )

    @staticmethod
    def parts_for_order(order_id: int) -> list[InboundPart]:
        """Các đơn Pancake đã khớp vào một đơn CRM (kiện), theo thứ tự tạo — index partial
        `ppos_inbound_order_idx`."""
        rows = (
            PancakePosInboundOrder.objects.filter(order_id=order_id, match_status=InboundMatchStatus.MATCHED)
            .order_by("inserted_at", "id")
            .values_list(
                "pancake_order_id", "pancake_status", "status_name", "dvvc_status", "tracking_code",
                "partner_name", "pancake_warehouse_id", "shipped_at",
            )
        )
        return [
            InboundPart(PartState(pid, status, name, dvvc), tracking, partner, wh, shipped)
            for pid, status, name, dvvc, tracking, partner, wh, shipped in rows
        ]

    @staticmethod
    def counts() -> dict[str, int]:
        rows = PancakePosInboundOrder.objects.values("match_status").annotate(n=Count("id"))
        return {r["match_status"]: int(r["n"]) for r in rows}


class NativeLinkRepository:
    """Liên kết đơn gốc Pancake (sàn/page) ↔ đơn CRM trong `pancake_pos_order_links`
    (origin `pancake_native`, status `linked` — ngoài mọi hàng đợi đẩy của A2)."""

    @staticmethod
    def order_for_pancake(pancake_order_id: str) -> int | None:
        return (
            PancakePosOrderLink.objects.filter(pancake_order_id=pancake_order_id)
            .values_list("order_id", flat=True).first()
        )

    @staticmethod
    def has_part(order_id: int, part_no: int) -> bool:
        return PancakePosOrderLink.objects.filter(
            order_id=order_id, part_no=part_no, origin=OrderLinkOrigin.CRM_PUSH
        ).exists()

    @staticmethod
    def pushed_elsewhere(order_id: int, pancake_order_id: str) -> bool:
        """Đơn CRM đã có đơn Pancake do CRM đẩy (mã khác) → khớp đơn gốc vào nó = đơn trùng."""
        return PancakePosOrderLink.objects.filter(
            order_id=order_id, origin=OrderLinkOrigin.CRM_PUSH
        ).exclude(pancake_order_id=pancake_order_id).exclude(
            status__in=[OrderLinkStatus.CANCELLED, OrderLinkStatus.SKIPPED_DUPLICATE]
        ).exists()

    @staticmethod
    def link(order_id: int, pancake_order_id: str, *, pancake_status: int | None) -> None:
        """Tạo liên kết (idempotent theo `pancake_order_id` UNIQUE). Kiện = số kế tiếp."""
        if PancakePosOrderLink.objects.filter(pancake_order_id=pancake_order_id).exists():
            return
        next_part = (
            PancakePosOrderLink.objects.filter(order_id=order_id).aggregate(m=Max("part_no"))["m"] or 0
        ) + 1
        with transaction.atomic():
            PancakePosOrderLink.objects.create(
                order_id=order_id, part_no=next_part, pancake_order_id=pancake_order_id,
                origin=OrderLinkOrigin.PANCAKE_NATIVE, status=OrderLinkStatus.LINKED,
                pancake_status=pancake_status,
            )

    @staticmethod
    def set_pancake_status(pancake_order_id: str, pancake_status: int | None) -> None:
        PancakePosOrderLink.objects.filter(pancake_order_id=pancake_order_id).exclude(
            pancake_status=pancake_status
        ).update(pancake_status=pancake_status, updated_at=timezone.now())

    @staticmethod
    def parts(order_id: int) -> list[tuple[str, str, int | None]]:
        """(pancake_order_id, status liên kết, pancake_status) các kiện ĐÃ có đơn Pancake."""
        return list(
            PancakePosOrderLink.objects.filter(order_id=order_id, pancake_order_id__isnull=False)
            .order_by("part_no").values_list("pancake_order_id", "status", "pancake_status")
        )


@dataclass(frozen=True)
class StatusMapEntry:
    name: str
    delivery_code: str


def status_map() -> dict[int, StatusMapEntry]:
    """{pancake_status: (tên tiếng Việt, mã "Trạng thái vận chuyển thực")} — bảng ~17 dòng,
    nạp một lần mỗi lượt."""
    return {
        int(code): StatusMapEntry(name or "", code_crm or "")
        for code, name, code_crm in PancakePosStatusMap.objects.values_list(
            "pancake_status", "pancake_name", "crm_delivery_code"
        )
    }
