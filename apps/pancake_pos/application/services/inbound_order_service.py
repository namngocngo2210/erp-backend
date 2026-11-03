"""Áp sự kiện `orders` từ webhook Pancake lên đơn CRM (two-way-sync §5.1, §6b, §7 B1).

Mỗi sự kiện một transaction; dòng `pancake_pos_inbound_orders` của đơn Pancake bị khoá
`FOR UPDATE` → hai worker cùng đơn chạy nối tiếp, và bản có `updated_at` CŨ hơn bản đã
áp bị bỏ qua (webhook có thể tới sai thứ tự). Idempotent: áp lại cùng bản không đổi gì.

Khớp đơn CRM theo thứ tự §6b:
1. liên kết do CRM đẩy (`order_links.pancake_order_id`, hoặc `custom_id` = `<id>`),
2. mã đơn sàn (đơn KiotViet `HDSPE_/HDTTS_<mã>`),
3. mã vận đơn (`orders.shipping_code`),
4. không khớp: cờ `PANCAKE_POS_INBOUND_CREATE_ORDERS` bật → tạo đơn CRM; tắt → chờ (hiện
   đơn sàn vẫn về CRM qua KiotViet, chưa chốt tắt — tạo lúc này sẽ ra đơn trùng).
Khớp nhiều đơn / đơn CRM đã đẩy sang Pancake với mã khác → `unmatched` ngay; không khớp quá
N giờ → `unmatched`. Cả hai + cảnh báo Lark (màn "Chờ khớp").

Ghi đơn CRM (MR D, quyết định 01/10): `orders.status` do KiotViet quyết định — webhook
Pancake KHÔNG ghi `status`. Chỉ ghi `partner_delivery_status` (tên trạng thái Pancake tiếng
Việt) + `partner_dvvc_delivery_status` (ĐVVC mới nhất) bằng UPDATE có điều kiện, điền mã vận
đơn/ĐVVC khi CRM còn trống (đơn một kiện). Đơn tách N kiện: 2 field hiện KIỆN CHẬM NHẤT
(`delivery_status.slowest_part`). Mã "Trạng thái vận chuyển thực" xử lý SAU commit ở
`DeliverySyncService` (`on_matched`) — có thể chạy luồng xuất kho (HTTP KiotViet) nên không
được nằm trong transaction này.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from django.db import transaction
from django.utils import timezone

from apps.pancake_pos.domain import delivery_status as delivery
from apps.pancake_pos.domain import inbound_order as rules
from apps.pancake_pos.domain.inbound_order import NewCrmOrder, PancakeOrderSnapshot, ResolvedProduct
from apps.pancake_pos.inbound_repositories import (
    InboundOrderRepository,
    NativeLinkRepository,
    StatusMapEntry,
    WebhookEventRepository,
    status_map,
)
from apps.pancake_pos.infrastructure.crm_order_gateway import CrmOrderState
from apps.pancake_pos.metrics import webhook_processed_total
from apps.pancake_pos.models import (
    InboundMatchMethod,
    InboundMatchStatus,
    PancakePosInboundOrder,
    WebhookEventStatus,
    WebhookEventType,
)

logger = logging.getLogger(__name__)

# Đơn tạo từ Pancake (cờ tạo đơn) luôn "Chờ xử lý": Pancake không quyết `orders.status`.
DEFAULT_CRM_STATUS = 1


class CrmOrderPort(Protocol):
    def find_by_codes(self, codes: Sequence[str]) -> list[int]: ...

    def find_by_tracking(self, code: str) -> list[int]: ...

    def load(self, order_id: int) -> CrmOrderState | None: ...

    def resolve_channel(self, *, keys: Sequence[str], account_name: str, mapping: dict[str, int]) -> int | None: ...

    def resolve_products(self, *, variation_ids: Sequence[str], skus: Sequence[str]) -> dict[str, ResolvedProduct]: ...

    def lock_order(self, order_id: int) -> None: ...

    def write_partner_status(
        self, order_id: int, *, partner_status: str, dvvc_status: str, shipping_code: str = "",
        shipping_provider: str = "",
    ) -> bool: ...

    def create_order(self, new: NewCrmOrder) -> int: ...


class Alerter(Protocol):
    def __call__(self, *, key: str, title: str, lines: Sequence[str] = ()) -> Any: ...


@dataclass(frozen=True)
class InboundConfig:
    create_orders: bool = False
    unmatched_after: timedelta = timedelta(hours=6)
    source_map: dict[str, str] = field(default_factory=dict)
    channel_map: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class MatchResult:
    order_id: int | None = None
    method: str = ""
    candidates: tuple[int, ...] = ()
    reason: str = ""
    # True = ca cần người xử lý ngay (nhiều ứng viên / trùng đơn đã đẩy).
    conflict: bool = False


class InboundOrderService:
    def __init__(
        self, *, crm: CrmOrderPort, alert: Alerter, config: InboundConfig,
        clock: Callable[[], datetime] = timezone.now, on_matched: Callable[[int], Any] | None = None,
    ) -> None:
        self._crm = crm
        self._alert = alert
        self._config = config
        self._clock = clock
        # Gọi SAU commit với id đơn CRM vừa áp — đồng bộ mã "Trạng thái vận chuyển thực".
        self._on_matched = on_matched
        self._status_map: dict[int, StatusMapEntry] | None = None

    # ── sự kiện ─────────────────────────────────────────────────────────────
    def process_event(self, event_id: int) -> str:
        """Xử lý một sự kiện `orders`. Trả kết quả (metric/log): processed | ignored:<lý do>
        | error | busy (worker khác đang giữ / đã xử lý)."""
        with transaction.atomic():
            event = WebhookEventRepository.lock(event_id)
            if event is None:
                return "busy"
            if event.event_type != WebhookEventType.ORDERS:
                return "busy"  # B1 chỉ xử lý đơn; tồn/sản phẩm chờ B2
            try:
                with transaction.atomic():
                    outcome = self._apply_payload(event.id, event.payload)
            except Exception as exc:  # noqa: BLE001 — lỗi nằm ở sự kiện, lượt sau thử lại
                logger.exception("pancake_pos: xử lý webhook #%s lỗi", event_id)
                WebhookEventRepository.finish(event_id, status=WebhookEventStatus.ERROR,
                                              error=f"{type(exc).__name__}: {exc}")
                webhook_processed_total.labels("orders", "error").inc()
                return "error"
            status = WebhookEventStatus.IGNORED if outcome.startswith("ignored") else WebhookEventStatus.PROCESSED
            WebhookEventRepository.finish(event_id, status=status, error=outcome if status == "ignored" else "")
            webhook_processed_total.labels("orders", status).inc()
            return outcome

    def _apply_payload(self, event_id: int, payload: Any) -> str:
        classified = rules.classify_payload(payload)
        snapshot = rules.parse_order_snapshot(classified.record) if classified.record else None
        if snapshot is None:
            return "ignored:payload thiếu id đơn"
        row = InboundOrderRepository.lock(snapshot.id)
        if rules.is_stale(snapshot.updated_at, row.pancake_updated_at):
            return "ignored:bản cũ hơn bản đã áp"
        self._remember(row, snapshot, event_id)
        return self._match_and_apply(row, snapshot)

    def preview_match(self, payload: Any) -> MatchResult | None:
        """Chỉ đọc (backfill --dry-run): đơn Pancake này sẽ khớp đơn CRM nào. None = payload
        không phải đơn."""
        classified = rules.classify_payload(payload)
        snapshot = rules.parse_order_snapshot(classified.record) if classified.record else None
        return self._match(snapshot) if snapshot is not None else None

    # ── khớp lại đơn chờ (beat) ──────────────────────────────────────────────
    def rematch(self, *, limit: int = 100) -> dict[str, int]:
        """Đơn chưa khớp: thử khớp lại (đơn KiotViet có thể về sau webhook Pancake) và
        chuyển `unmatched` + Lark khi quá hạn. Dùng payload của sự kiện gần nhất."""
        counters: dict[str, int] = {}
        for inbound_id in InboundOrderRepository.to_rematch(limit=limit):
            try:
                with transaction.atomic():
                    row = InboundOrderRepository.lock_by_id(inbound_id)
                    if row is None or row.match_status not in (InboundMatchStatus.WAITING, InboundMatchStatus.UNMATCHED):
                        continue
                    snapshot = self._snapshot_of(row)
                    if snapshot is None:
                        continue
                    outcome = self._match_and_apply(row, snapshot)
            except Exception:  # noqa: BLE001 — một đơn lỗi không chặn đơn khác
                logger.exception("pancake_pos: khớp lại đơn Pancake (inbound #%s) lỗi", inbound_id)
                outcome = "error"
            counters[outcome] = counters.get(outcome, 0) + 1
        return counters

    @staticmethod
    def _snapshot_of(row: PancakePosInboundOrder) -> PancakeOrderSnapshot | None:
        event = WebhookEventRepository.get(row.last_event_id) if row.last_event_id else None
        if event is None:
            return None
        classified = rules.classify_payload(event.payload)
        return rules.parse_order_snapshot(classified.record) if classified.record else None

    # ── khớp tay (màn "Chờ khớp") ───────────────────────────────────────────
    def resolve_manual(self, inbound_id: int, *, order_id: int | None, ignore: bool = False) -> PancakePosInboundOrder:
        with transaction.atomic():
            row = InboundOrderRepository.lock_by_id(inbound_id)
            if row is None:
                raise LookupError("Không tìm thấy đơn Pancake.")
            if ignore:
                row.match_status, row.reason = InboundMatchStatus.IGNORED, "Bỏ qua tay"
                row.save(update_fields=["match_status", "reason", "updated_at"])
                return row
            state = self._crm.load(int(order_id or 0))
            if state is None or state.deleted:
                raise ValueError("Đơn CRM không tồn tại hoặc đã xoá.")
            owner = NativeLinkRepository.order_for_pancake(row.pancake_order_id)
            if owner is not None and owner != state.id:
                raise ValueError(f"Đơn Pancake đã liên kết với đơn CRM #{owner}.")
            snapshot = self._snapshot_of(row)
            match = MatchResult(order_id=state.id, method=InboundMatchMethod.MANUAL)
            if snapshot is None:
                self._set_matched(row, match)
                NativeLinkRepository.link(state.id, row.pancake_order_id, pancake_status=row.pancake_status)
                return row
            self._finish_match(row, snapshot, match)
            return row

    # ── lõi ─────────────────────────────────────────────────────────────────
    def _remember(self, row: PancakePosInboundOrder, snap: PancakeOrderSnapshot, event_id: int) -> None:
        marks = rules.milestones(snap)
        row.last_event_id = event_id
        row.pancake_status = snap.status
        row.pancake_updated_at = snap.updated_at or row.pancake_updated_at
        row.order_sources, row.source_name = snap.order_sources[:64], snap.source_name[:255]
        row.account_name = snap.account_name[:255]
        row.tracking_code, row.partner_name = snap.tracking_code[:100], snap.partner_name[:100]
        row.total_price, row.inserted_at = snap.total_price, snap.inserted_at
        row.shipped_at = row.shipped_at or marks.shipped_at
        row.delivered_at = row.delivered_at or marks.delivered_at
        row.returned_at = row.returned_at or marks.returned_at
        row.status_name = self._status_name(snap)
        # Payload không kèm hành trình ĐVVC (vd webhook rút gọn) → giữ giá trị đã biết.
        row.dvvc_status = snap.dvvc.label or row.dvvc_status
        row.last_partner_status_at = snap.dvvc.at or row.last_partner_status_at
        row.status_changed_at = snap.status_changed_at
        row.pancake_warehouse_id = snap.warehouse_id[:64] or row.pancake_warehouse_id
        row.warehouse_name = snap.warehouse_name[:255] or row.warehouse_name
        row.save()

    def _status_name(self, snap: PancakeOrderSnapshot) -> str:
        names = {code: entry.name for code, entry in self._map().items()}
        return delivery.status_label(snap.status, names, snap.status_name)

    def _match_and_apply(self, row: PancakePosInboundOrder, snap: PancakeOrderSnapshot) -> str:
        if row.match_status in (InboundMatchStatus.MATCHED,) and row.order_id:
            match = MatchResult(order_id=row.order_id, method=row.match_method)
        else:
            match = self._match(snap)
        if match.order_id is not None:
            return self._finish_match(row, snap, match)
        if match.conflict:
            return self._mark_unmatched(row, snap, match)
        if snap.is_dead:
            row.match_status, row.reason = InboundMatchStatus.IGNORED, "Đơn Pancake đã huỷ/xoá, không có đơn CRM"
            row.save(update_fields=["match_status", "reason", "updated_at"])
            return "ignored:đơn huỷ chưa khớp"
        if self._config.create_orders:
            created = self._create(snap)
            if created.order_id is not None:
                return self._finish_match(row, snap, created)
            match = created
        if self._clock() - (row.first_seen_at or self._clock()) >= self._config.unmatched_after:
            return self._mark_unmatched(row, snap, match)
        row.match_status, row.reason = InboundMatchStatus.WAITING, match.reason or "Chưa có đơn CRM khớp"
        row.save(update_fields=["match_status", "reason", "updated_at"])
        return "waiting"

    def _match(self, snap: PancakeOrderSnapshot) -> MatchResult:
        owner = NativeLinkRepository.order_for_pancake(snap.id)
        if owner is not None:
            return MatchResult(order_id=owner, method=InboundMatchMethod.ORDER_LINK)
        ref = rules.crm_ref(snap.custom_id)
        if ref is not None and NativeLinkRepository.has_part(*ref):
            # Đơn CRM đẩy mà response POST chưa kịp lưu id (A2 tra lại theo custom_id).
            return MatchResult(order_id=ref[0], method=InboundMatchMethod.ORDER_LINK)
        for method, ids in (
            (InboundMatchMethod.MARKETPLACE_CODE, lambda: self._crm.find_by_codes(rules.marketplace_codes(snap))),
            (InboundMatchMethod.TRACKING_CODE, lambda: self._crm.find_by_tracking(snap.tracking_code)),
        ):
            found = ids()
            if len(found) > 1:
                return MatchResult(candidates=tuple(found), conflict=True,
                                   reason=f"Mã khớp {len(found)} đơn CRM ({_method_label(method)})")
            if len(found) == 1:
                if NativeLinkRepository.pushed_elsewhere(found[0], snap.id):
                    return MatchResult(candidates=(found[0],), conflict=True,
                                       reason=f"Đơn CRM #{found[0]} đã được CRM đẩy sang Pancake với mã khác — nghi trùng")
                return MatchResult(order_id=found[0], method=method)
        return MatchResult(reason="Chưa có đơn CRM khớp (mã sàn / mã vận đơn)")

    def _finish_match(self, row: PancakePosInboundOrder, snap: PancakeOrderSnapshot, match: MatchResult) -> str:
        order_id = int(match.order_id or 0)
        if match.method != InboundMatchMethod.ORDER_LINK:
            NativeLinkRepository.link(order_id, snap.id, pancake_status=snap.status)
        NativeLinkRepository.set_pancake_status(snap.id, snap.status)
        self._set_matched(row, match)
        state = self._crm.load(order_id)
        if state is None or state.deleted:
            row.reason = "Đơn CRM đã xoá — không cập nhật"
            row.save(update_fields=["reason", "updated_at"])
            return "matched:deleted"
        changed = self._write_partner(order_id)
        if self._on_matched is not None:
            callback = self._on_matched
            transaction.on_commit(lambda: callback(order_id))
        return "updated" if changed else "matched"

    def _write_partner(self, order_id: int) -> bool:
        """2 field partner của đơn CRM = kiện chậm nhất trong các đơn Pancake đã khớp. Khoá
        dòng đơn CRM trước khi đọc các kiện → hai kiện cùng đơn xử lý song song không ghi đè
        nhau bằng ảnh cũ. Mã vận đơn/ĐVVC chỉ điền cho đơn MỘT kiện còn sống."""
        self._crm.lock_order(order_id)
        parts = InboundOrderRepository.parts_for_order(order_id)
        chosen = delivery.slowest_part([p.state for p in parts])
        if chosen is None:
            return False
        alive = [p for p in parts if p.state.pancake_status not in delivery.DEAD]
        # "Một kiện" theo cả liên kết (A2 có thể đã đẩy kiện khác mà Pancake chưa báo về).
        linked = [ps for _pid, _st, ps in NativeLinkRepository.parts(order_id) if ps not in delivery.DEAD]
        single = alive[0] if len(alive) == 1 and len(linked) <= 1 else None
        return self._crm.write_partner_status(
            order_id,
            partner_status=chosen.status_name,
            dvvc_status=chosen.dvvc_status,
            shipping_code=single.tracking_code if single else "",
            shipping_provider=rules.provider_code(single.partner_name) if single and single.partner_name else "",
        )

    def _set_matched(self, row: PancakePosInboundOrder, match: MatchResult) -> None:
        row.order_id, row.match_status = match.order_id, InboundMatchStatus.MATCHED
        row.match_method, row.candidates, row.reason = match.method, [], match.reason
        row.save(update_fields=["order", "match_status", "match_method", "candidates", "reason", "updated_at"])

    def _mark_unmatched(self, row: PancakePosInboundOrder, snap: PancakeOrderSnapshot, match: MatchResult) -> str:
        first_time = row.match_status != InboundMatchStatus.UNMATCHED
        row.match_status, row.reason = InboundMatchStatus.UNMATCHED, match.reason or "Không khớp được đơn CRM"
        row.candidates = list(match.candidates)
        fields = ["match_status", "reason", "candidates", "updated_at"]
        if first_time or row.alerted_at is None:
            self._alert(
                key=f"pancake_pos:unmatched:{snap.id}",
                title=f"Pancake POS: đơn {snap.id} chưa khớp đơn CRM",
                lines=[
                    f"Nguồn: {snap.source_name or snap.order_sources or '?'} · {snap.account_name or '-'}",
                    f"Lý do: {row.reason}",
                    *([f"Ứng viên CRM: {', '.join(f'#{i}' for i in match.candidates)}"] if match.candidates else []),
                    "Xử lý ở Cài đặt → Pancake POS → Chờ khớp đơn.",
                ],
            )
            row.alerted_at = self._clock()
            fields.append("alerted_at")
        row.save(update_fields=fields)
        return "unmatched"

    def _create(self, snap: PancakeOrderSnapshot) -> MatchResult:
        source = self._config.source_map.get(snap.order_sources) or self._config.source_map.get(snap.marketplace_id)
        if not source:
            return MatchResult(reason=f"Nguồn Pancake {snap.order_sources or '?'} chưa ghép order_source CRM")
        channel_id = self._crm.resolve_channel(
            keys=(snap.account, snap.page_id), account_name=snap.account_name, mapping=self._config.channel_map
        )
        if channel_id is None:
            return MatchResult(reason=f"Chưa ghép kênh CRM cho shop/page «{snap.account_name or snap.page_id}»")
        products = self._crm.resolve_products(
            variation_ids=[i.variation_id for i in snap.items if i.variation_id],
            skus=[i.sku for i in snap.items if i.sku and not i.variation_id],
        )
        new = rules.build_new_order(snap, channel_id=channel_id, order_source=source, status=DEFAULT_CRM_STATUS,
                                    products=products)
        order_id = self._crm.create_order(new)
        reason = f"Thiếu sản phẩm CRM: {'; '.join(new.missing_items)}" if new.missing_items else ""
        return MatchResult(order_id=order_id, method=InboundMatchMethod.CREATED, reason=reason)

    def _map(self) -> dict[int, StatusMapEntry]:
        if self._status_map is None:
            self._status_map = status_map()
        return self._status_map


def _method_label(method: str) -> str:
    return dict(InboundMatchMethod.choices).get(method, method).lower()
