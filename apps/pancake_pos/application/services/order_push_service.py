"""Đẩy đơn CRM → Pancake POS (docs/pancake-pos-two-way-sync.md §4.1, §6b, §7 A2).

Ba pha, mỗi pha một hàm, mỗi đơn độc lập (lỗi một đơn không chặn đơn khác):

1. `discover` — đơn đã chốt chưa có liên kết → đánh giá (`domain.order_push.evaluate`)
   → tạo dòng liên kết part 1: `pending` (đã chọn được kho), `needs_routing` (không
   kho nào đủ hàng), `skipped_duplicate` (+ cảnh báo Lark), `error` (thiếu dữ liệu).
   Đơn bị bỏ qua (chưa chốt, sai nguồn…) KHÔNG tạo dòng.
2. `push` — `claim_pushable` đặt `pushing` trong transaction riêng (FOR UPDATE SKIP
   LOCKED) rồi mới POST. POST không retry 5xx/ReadTimeout (client); khi đó dòng về
   `error` + `needs_lookup` và lượt sau TRA LẠI theo `custom_id` trước khi tạo → mất
   response không sinh đơn trùng.
3. `sync_changes` — đơn đã đẩy mà CRM đổi (địa chỉ, dòng hàng, COD) hoặc huỷ → GET
   trạng thái Pancake: kho chưa xử lý thì PUT/huỷ; đã xử lý (`status_map.locks_edit`)
   thì chỉ cảnh báo, không sửa.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from django.utils import timezone

from apps.pancake_pos.contracts.ports import PancakeOrderGateway
from apps.pancake_pos.domain import order_push as rules
from apps.pancake_pos.domain.hashing import stable_hash
from apps.pancake_pos.domain.order_push import CrmOrder, OrderLine, Verdict, WarehouseOption
from apps.pancake_pos.infrastructure.pancake_pos_client import (
    PancakePosApiError,
    PancakePosError,
    PancakePosRateLimited,
    PancakePosTransportError,
)
from apps.pancake_pos.models import OrderLinkStatus
from apps.pancake_pos.repositories import (
    LIVE_STATUSES,
    OrderLinkRepository,
    OrderLinkState,
    StatusMapRepository,
    WarehouseLinkRepository,
)

logger = logging.getLogger(__name__)

# `pushing` lâu hơn mức này = worker chết giữa chừng → coi như chưa rõ đã tạo, tra lại.
STALE_PUSHING_SECONDS = 600
AUTO_RETRY_AFTER_SECONDS = 300
MAX_AUTO_ATTEMPTS = 3
# Cửa sổ tra lại đơn theo mã tham chiếu (trước lần POST gần nhất).
LOOKUP_WINDOW = timedelta(hours=2)
# Chỉ đối chiếu sửa/huỷ cho đơn đẩy trong N ngày (sau đó đơn đã giao/hoàn — MR B lo).
CHANGE_WINDOW = timedelta(days=14)


class OrderReader(Protocol):
    def load_orders(self, order_ids: Sequence[int]) -> dict[int, CrmOrder]: ...

    def scan_candidates(self, *, sources: Sequence[str], since: datetime, limit: int) -> list[int]: ...

    def changed_linked_orders(
        self, *, pushed_since: datetime, limit: int, order_ids: Sequence[int] | None = None
    ) -> list[int]: ...

    def components(self, product_ids: Sequence[int]) -> dict[int, list[tuple[int, int]]]: ...

    def sellable_stock(
        self, *, warehouse_ids: Sequence[int], product_ids: Sequence[int]
    ) -> dict[tuple[int, int], int]: ...

    def warehouse_names(self) -> dict[int, str]: ...


class Alerter(Protocol):
    def __call__(self, *, key: str, title: str, lines: Sequence[str] = ()) -> Any: ...


@dataclass(frozen=True)
class PushConfig:
    sources: tuple[str, ...] = rules.DEFAULT_PUSH_SOURCES
    lookback: timedelta = timedelta(hours=72)
    # Mốc bắt đầu đẩy đơn (`PANCAKE_POS_PUSH_ORDERS_SINCE`, rỗng = lần đầu bật cờ đẩy đơn).
    # Chỉ lượt quét tự động áp mốc; người vận hành bấm Thử lại/Vẫn đẩy thì bỏ qua như lookback.
    push_since: datetime | None = None
    source_map: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass
class Counters:
    values: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, n: int = 1) -> None:
        self.values[key] = self.values.get(key, 0) + n


class OrderPushService:
    def __init__(
        self,
        *,
        gateway: PancakeOrderGateway,
        reader: OrderReader,
        alert: Alerter,
        config: PushConfig | None = None,
        clock: Callable[[], datetime] = timezone.now,
    ) -> None:
        self._gateway = gateway
        self._reader = reader
        self._alert = alert
        self._config = config or PushConfig()
        self._clock = clock

    # ── pha 1: phát hiện đơn mới ──────────────────────────────────────────────
    def warehouse_options(self) -> list[WarehouseOption]:
        names = self._reader.warehouse_names()
        return [
            WarehouseOption(
                warehouse_id=link.warehouse_id,
                pancake_warehouse_id=link.pancake_warehouse_id,
                priority=link.priority,
                is_default=link.is_default_for_push,
                name=names.get(link.warehouse_id, link.pancake_warehouse_name),
            )
            for link in WarehouseLinkRepository.all_links()
        ]

    def discover(
        self, order_ids: Sequence[int] | None = None, *, limit: int = 200, ignore_lookback: bool = False,
        force_ids: Iterable[int] = (),
    ) -> dict[str, int]:
        """`ignore_lookback`: người vận hành bấm Thử lại → không áp cửa sổ quét.
        `force_ids`: đơn người điều phối bấm "Vẫn đẩy" → bỏ luật chống trùng."""
        force = set(force_ids)
        counters = Counters()
        options = self.warehouse_options()
        if not options:
            # Chưa ghép kho → mọi đơn sẽ thành needs_routing; dừng sớm, không tạo rác.
            counters.add("no_warehouse_links")
            return counters.values
        since = self._clock() - self._config.lookback
        push_since = None if ignore_lookback else self._config.push_since
        if push_since is not None:
            since = max(since, push_since)
        ids = list(order_ids) if order_ids else self._reader.scan_candidates(
            sources=self._config.sources, since=since, limit=limit
        )
        if not ids:
            return counters.values
        existing = OrderLinkRepository.for_orders(ids)
        orders = self._reader.load_orders([i for i in ids if i not in existing])
        related = {rid for o in orders.values() for rid in (*o.foxia_order_ids, *o.origin_order_ids)}
        pushed = OrderLinkRepository.active_push_order_ids(related)

        decisions = {
            oid: rules.evaluate(
                order, sources=self._config.sources, closed_since=None if ignore_lookback else since,
                pushed_after=push_since,
                pushed_order_ids=pushed, allow_duplicate=oid in force,
            )
            for oid, order in orders.items()
        }
        eligible = [orders[oid] for oid, d in decisions.items() if d.verdict == Verdict.ELIGIBLE]
        stock, components = self._stock_for(eligible, options)

        for oid, decision in decisions.items():
            order = orders[oid]
            counters.add(decision.reason or decision.verdict)
            if decision.verdict == Verdict.IGNORE:
                continue
            if decision.verdict == Verdict.BLOCKED:
                OrderLinkRepository.create_first(oid, status=OrderLinkStatus.ERROR, last_error=decision.detail)
                continue
            if decision.verdict == Verdict.DUPLICATE:
                if OrderLinkRepository.create_first(
                    oid, status=OrderLinkStatus.SKIPPED_DUPLICATE, last_error=decision.detail
                ):
                    self._alert_duplicate(order, decision.detail)
                continue
            demand = rules.demand_of(order.lines, components)
            option = rules.choose_warehouse(demand, options, stock)
            if option is None:
                OrderLinkRepository.create_first(
                    oid, status=OrderLinkStatus.NEEDS_ROUTING,
                    last_error="Không kho nào đủ hàng cho cả đơn — cần chọn một kho hoặc tách kiện.",
                )
                counters.add("needs_routing")
                continue
            if OrderLinkRepository.create_first(
                oid, status=OrderLinkStatus.PENDING,
                warehouse_id=option.warehouse_id, pancake_warehouse_id=option.pancake_warehouse_id,
            ):
                counters.add("queued")
        return counters.values

    def _stock_for(
        self, orders: Iterable[CrmOrder], options: Sequence[WarehouseOption]
    ) -> tuple[dict[tuple[int, int], int], dict[int, list[tuple[int, int]]]]:
        product_ids = {line.product_id for order in orders for line in order.lines}
        if not product_ids:
            return {}, {}
        components = self._reader.components(sorted(product_ids))
        needed = product_ids | {cid for parts in components.values() for cid, _ in parts}
        stock = self._reader.sellable_stock(
            warehouse_ids=[o.warehouse_id for o in options], product_ids=sorted(needed)
        )
        return stock, components

    def _alert_duplicate(self, order: CrmOrder, detail: str) -> None:
        self._alert(
            key=f"pancake_pos:order_duplicate:{order.id}",
            title="Pancake POS: đơn trùng landing ↔ Foxia — KHÔNG đẩy sang Pancake",
            lines=[f"Đơn CRM #{order.id} ({order.order_source}) — {order.name}", detail,
                   "Kiểm tra để không giao hai lần; cần đẩy thì Thử lại ở trang Pancake POS."],
        )

    # ── pha 2: đẩy ────────────────────────────────────────────────────────────
    def push(self, order_ids: Sequence[int] | None = None, *, limit: int = 50) -> dict[str, int]:
        counters = Counters()
        claimed = OrderLinkRepository.claim_pushable(
            limit=limit, order_ids=list(order_ids) if order_ids else None,
            stale_seconds=STALE_PUSHING_SECONDS, retry_after_seconds=AUTO_RETRY_AFTER_SECONDS,
            max_auto_attempts=MAX_AUTO_ATTEMPTS,
        )
        if not claimed:
            return counters.values
        links = OrderLinkRepository.get_many(claimed)
        order_ids_needed = sorted({link.order_id for link in links})
        orders = self._reader.load_orders(order_ids_needed)
        all_parts = OrderLinkRepository.for_orders(order_ids_needed)
        for link in links:
            try:
                result = self._push_one(link, orders.get(link.order_id), all_parts.get(link.order_id, [link]))
            except Exception as exc:  # noqa: BLE001 — một đơn lỗi không chặn cả lô
                logger.exception("pancake_pos: đẩy đơn %s#%s lỗi bất ngờ", link.order_id, link.part_no)
                OrderLinkRepository.update(link.id, status=OrderLinkStatus.ERROR, needs_lookup=True,
                                           last_error=f"Lỗi bất ngờ: {type(exc).__name__}")
                result = "error"
            counters.add(result)
        return counters.values

    def _part_payload(
        self, order: CrmOrder, link: OrderLinkState, parts: Sequence[OrderLinkState]
    ) -> dict[str, Any]:
        """Payload của một kiện. Kiện tách (`items`) chia dòng + tiền theo mọi kiện."""
        parts = sorted(parts, key=lambda p: p.part_no)
        if len(parts) == 1 and not parts[0].items:
            part_lines: list[tuple[OrderLine, ...]] = [tuple(order.lines)]
        else:
            part_lines = rules.split_lines(order.lines, [p.items or () for p in parts])
        index = next(i for i, p in enumerate(parts) if p.id == link.id)
        money = rules.part_money(order, part_lines)[index]
        return rules.build_payload(
            order, part_lines[index],
            pancake_warehouse_id=link.pancake_warehouse_id, money=money,
            part_no=link.part_no, part_count=len(parts),
            source_ref=self._config.source_map.get(order.order_source.lower()),
        )

    def _push_one(self, link: OrderLinkState, order: CrmOrder | None, parts: Sequence[OrderLinkState]) -> str:
        now = self._clock()
        if order is None or order.is_cancelled:
            return self._cancel_before_push(link, order, now)
        missing = sorted({line.sku for line in order.lines if not line.variation_id})
        if missing or not order.lines:
            OrderLinkRepository.update(link.id, status=OrderLinkStatus.ERROR,
                                       last_error=f"SKU chưa ghép mẫu mã Pancake: {', '.join(missing) or '—'}")
            return "missing_variation"
        try:
            payload = self._part_payload(order, link, parts)
        except rules.RoutingError as exc:
            OrderLinkRepository.update(link.id, status=OrderLinkStatus.ERROR,
                                       last_error=f"Dòng hàng đơn đổi sau khi tách kiện: {exc}")
            return "routing_mismatch"
        custom_id = payload["custom_id"]

        if link.needs_lookup:
            try:
                since = int(((link.attempted_at or now) - LOOKUP_WINDOW).timestamp())
                found = self._gateway.find_orders(custom_id=custom_id, search=order.phone, since=since)
            except PancakePosError as exc:
                OrderLinkRepository.update(link.id, status=OrderLinkStatus.ERROR, needs_lookup=True,
                                           last_error=f"Không tra lại được đơn {custom_id}: {exc}")
                return "lookup_failed"
            if found:
                self._relink(link, found[0].id, found[0].status)
                return "relinked"

        try:
            created = self._gateway.create_order(payload)
        except PancakePosRateLimited as exc:
            # 429: Pancake từ chối, chắc chắn chưa tạo.
            OrderLinkRepository.update(link.id, status=OrderLinkStatus.ERROR, needs_lookup=False, last_error=str(exc))
            return "rate_limited"
        except PancakePosTransportError as exc:
            self._uncertain(link, custom_id, exc)
            return "uncertain"
        except PancakePosApiError as exc:
            if exc.status_code is not None and exc.status_code >= 500:
                self._uncertain(link, custom_id, exc)
                return "uncertain"
            OrderLinkRepository.update(link.id, status=OrderLinkStatus.ERROR, needs_lookup=False, last_error=str(exc))
            return "rejected"
        self._mark_pushed(link, created.id, created.status, payload, now)
        return "pushed"

    def _cancel_before_push(self, link: OrderLinkState, order: CrmOrder | None, now: datetime) -> str:
        """Đơn huỷ trước khi đẩy. Nếu lần POST trước chưa rõ kết quả thì tra lại: có đơn
        trên Pancake → nối vào để pha đối chiếu huỷ nó."""
        if link.needs_lookup and order is not None:
            since = int(((link.attempted_at or now) - LOOKUP_WINDOW).timestamp())
            custom_id = rules.custom_id_for(order.id, link.part_no, OrderLinkRepository.order_part_count(order.id))
            try:
                found = self._gateway.find_orders(custom_id=custom_id, search=order.phone, since=since)
            except PancakePosError as exc:
                OrderLinkRepository.update(link.id, status=OrderLinkStatus.ERROR, needs_lookup=True,
                                           last_error=f"Đơn CRM đã huỷ; không tra lại được {custom_id}: {exc}")
                return "lookup_failed"
            if found:
                self._relink(link, found[0].id, found[0].status)
                return "relinked"
        OrderLinkRepository.update(link.id, status=OrderLinkStatus.CANCELLED, needs_lookup=False,
                                   last_error="Đơn CRM đã huỷ/xoá trước khi đẩy.")
        return "cancelled_before_push"

    @staticmethod
    def _relink(link: OrderLinkState, pancake_id: str, pancake_status: int | None) -> None:
        """Tìm lại được đơn đã tạo (mất response). Hash rỗng + checked_at NULL → pha đối
        chiếu so lại với CRM và PUT/huỷ nếu cần."""
        OrderLinkRepository.update(
            link.id, status=OrderLinkStatus.PUSHED, pancake_order_id=pancake_id, pancake_status=pancake_status,
            payload_hash="", needs_lookup=False, last_error="", pushed_at=timezone.now(), checked_at=None,
        )

    @staticmethod
    def _uncertain(link: OrderLinkState, custom_id: str, exc: Exception) -> None:
        OrderLinkRepository.update(
            link.id, status=OrderLinkStatus.ERROR, needs_lookup=True,
            last_error=f"{exc} — chưa rõ đã tạo; lượt sau tra lại theo mã {custom_id} trước khi tạo.",
        )

    @staticmethod
    def _mark_pushed(link: OrderLinkState, pancake_id: str, pancake_status: int | None,
                     payload: dict[str, Any], now: datetime) -> None:
        OrderLinkRepository.update(
            link.id, status=OrderLinkStatus.PUSHED, pancake_order_id=pancake_id, pancake_status=pancake_status,
            payload_hash=stable_hash(payload), needs_lookup=False, last_error="", pushed_at=now, checked_at=now,
        )

    # ── pha 3: sửa / huỷ ──────────────────────────────────────────────────────
    def sync_changes(self, order_ids: Sequence[int] | None = None, *, limit: int = 100) -> dict[str, int]:
        counters = Counters()
        now = self._clock()
        changed = self._reader.changed_linked_orders(
            pushed_since=now - CHANGE_WINDOW, limit=limit, order_ids=list(order_ids) if order_ids else None
        )
        if not changed:
            return counters.values
        orders = self._reader.load_orders(changed)
        links_by_order = OrderLinkRepository.for_orders(changed)
        locked = StatusMapRepository.locked_statuses()
        for oid in changed:
            parts = links_by_order.get(oid, [])
            for link in parts:
                if link.status not in LIVE_STATUSES or not link.pancake_order_id:
                    continue
                try:
                    result = self._sync_one(link, orders.get(oid), parts, locked, now)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("pancake_pos: đối chiếu đơn %s#%s lỗi bất ngờ", oid, link.part_no)
                    OrderLinkRepository.update(link.id, last_error=f"Lỗi bất ngờ khi đối chiếu: {type(exc).__name__}")
                    result = "error"
                counters.add(result)
        return counters.values

    def _sync_one(
        self, link: OrderLinkState, order: CrmOrder | None, parts: Sequence[OrderLinkState],
        locked: set[int], now: datetime,
    ) -> str:
        cancelled = order is None or order.is_cancelled
        new_hash = link.payload_hash
        payload: dict[str, Any] | None = None
        if not cancelled:
            try:
                payload = self._part_payload(order, link, parts)
            except rules.RoutingError as exc:
                OrderLinkRepository.update(link.id, checked_at=now,
                                           last_error=f"Dòng hàng CRM đổi sau khi tách kiện — sửa tay trên Pancake ({exc}).")
                self._alert_locked(link, order, "dòng hàng đổi sau khi tách kiện, CRM không tự sửa")
                return "split_mismatch"
            new_hash = stable_hash(payload)
        changed = new_hash != link.payload_hash
        if not cancelled and not changed:
            OrderLinkRepository.update(link.id, checked_at=now)
            return "unchanged"

        assert link.pancake_order_id is not None
        try:
            remote = self._gateway.get_order(link.pancake_order_id)
        except PancakePosError as exc:
            # Không cập nhật checked_at → lượt sau thử lại.
            OrderLinkRepository.update(link.id, last_error=f"Không đọc được đơn Pancake: {exc}")
            return "get_failed"
        if remote is None:
            OrderLinkRepository.update(link.id, checked_at=now,
                                       last_error="Không thấy đơn trên Pancake (đã xoá?) — kiểm tra tay.")
            return "missing_remote"

        action = rules.plan_change(crm_cancelled=cancelled, payload_changed=changed,
                                   pancake_status=remote.status, locked_statuses=locked)
        if action == rules.ChangeAction.GONE:
            OrderLinkRepository.update(link.id, status=OrderLinkStatus.CANCELLED, pancake_status=remote.status,
                                       checked_at=now, last_error="")
            return "gone"
        if action == rules.ChangeAction.LOCKED:
            what = "huỷ đơn" if cancelled else "sửa đơn"
            OrderLinkRepository.update(
                link.id, pancake_status=remote.status, checked_at=now, payload_hash=new_hash,
                last_error=f"CRM {what} nhưng kho Pancake đã xử lý (trạng thái {remote.status}) — không tự sửa.",
            )
            self._alert_locked(link, order, f"CRM {what} sau khi kho Pancake đã xử lý (trạng thái {remote.status})")
            return "locked"
        try:
            if action == rules.ChangeAction.CANCEL:
                self._gateway.update_order(link.pancake_order_id, {"status": rules.PANCAKE_STATUS_CANCELLED})
                OrderLinkRepository.update(link.id, status=OrderLinkStatus.CANCELLED,
                                           pancake_status=rules.PANCAKE_STATUS_CANCELLED, checked_at=now, last_error="")
                return "cancelled"
            assert payload is not None
            updated = self._gateway.update_order(link.pancake_order_id, payload)
        except PancakePosError as exc:
            OrderLinkRepository.update(link.id, last_error=f"Không cập nhật được đơn Pancake: {exc}")
            return "update_failed"
        OrderLinkRepository.update(
            link.id, status=OrderLinkStatus.UPDATED, payload_hash=new_hash, checked_at=now, last_error="",
            pancake_status=updated.status if updated and updated.status is not None else remote.status,
        )
        return "updated"

    def _alert_locked(self, link: OrderLinkState, order: CrmOrder | None, what: str) -> None:
        self._alert(
            key=f"pancake_pos:order_locked:{link.id}:{link.payload_hash[:12]}",
            title="Pancake POS: đơn CRM đổi nhưng Pancake không tự sửa được",
            lines=[f"Đơn CRM #{link.order_id} kiện {link.part_no} → Pancake {link.pancake_order_id}",
                   what, f"Khách: {order.name if order else '—'}"],
        )
