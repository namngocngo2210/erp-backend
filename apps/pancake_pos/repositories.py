"""Truy cập bảng của app `pancake_pos` — service chỉ thấy dataclass, không thấy ORM.

Bảng liên kết nhỏ (≤ vài nghìn dòng) nên nạp toàn bộ một lần mỗi lượt (2 query),
ghi theo nhóm trong một transaction (§3.3: mỗi nhóm độc lập).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.db import IntegrityError, connection, models, transaction
from django.utils import timezone

from apps.pancake_pos.models import (
    LinkStatus,
    OrderLinkStatus,
    PancakePosCategory,
    PancakePosOrderLink,
    PancakePosStatusMap,
    PancakePosProductLink,
    PancakePosSyncRun,
    PancakePosVariationLink,
    PancakePosWarehouseLink,
)

MAX_ERROR_LEN = 2000


@dataclass
class ProductLinkState:
    group_key: str
    pancake_product_id: str | None
    payload_hash: str


@dataclass
class VariationLinkState:
    product_id: int
    group_key: str
    pancake_variation_id: str | None
    status: str
    payload_hash: str
    attempts: int


@dataclass
class LinkSnapshot:
    """Ảnh chụp liên kết trong bộ nhớ + chỉ mục ngược id Pancake → chủ (O(1) tra cứu)."""

    products: dict[str, ProductLinkState] = field(default_factory=dict)
    variations: dict[int, VariationLinkState] = field(default_factory=dict)
    _product_owner: dict[str, str] = field(default_factory=dict)
    _variation_owner: dict[str, int] = field(default_factory=dict)
    _group_members: dict[str, set[int]] = field(default_factory=dict)

    def put_product(self, state: ProductLinkState) -> None:
        self.products[state.group_key] = state
        if state.pancake_product_id:
            self._product_owner[state.pancake_product_id] = state.group_key

    def put_variation(self, state: VariationLinkState) -> None:
        previous = self.variations.get(state.product_id)
        if previous is not None and previous.group_key != state.group_key:
            self._group_members.get(previous.group_key, set()).discard(state.product_id)
        self.variations[state.product_id] = state
        self._group_members.setdefault(state.group_key, set()).add(state.product_id)
        if state.pancake_variation_id:
            self._variation_owner[state.pancake_variation_id] = state.product_id

    def product_owner(self, pancake_product_id: str) -> str | None:
        """group_key đang giữ sản phẩm Pancake này (None = chưa ai giữ)."""
        return self._product_owner.get(pancake_product_id)

    def variation_owner(self, pancake_variation_id: str) -> int | None:
        return self._variation_owner.get(pancake_variation_id)

    def group_members(self, group_key: str) -> list[VariationLinkState]:
        """Liên kết mẫu mã đang thuộc nhóm (theo product_id CRM, tất định)."""
        return [self.variations[pid] for pid in sorted(self._group_members.get(group_key, ()))]


@dataclass(frozen=True)
class VariationWrite:
    product_id: int
    pancake_variation_id: str | None
    status: str
    payload_hash: str = ""
    last_error: str = ""
    attempts: int = 0
    synced_at: datetime | None = None


class LinkRepository:
    @staticmethod
    def load_snapshot() -> LinkSnapshot:
        snapshot = LinkSnapshot()
        for key, pancake_id, payload_hash in PancakePosProductLink.objects.values_list(
            "group_key", "pancake_product_id", "payload_hash"
        ):
            snapshot.put_product(ProductLinkState(key, pancake_id, payload_hash))
        for row in PancakePosVariationLink.objects.values(
            "product_id", "product_link__group_key", "pancake_variation_id", "status", "payload_hash", "attempts"
        ):
            snapshot.put_variation(
                VariationLinkState(
                    product_id=row["product_id"],
                    group_key=row["product_link__group_key"],
                    pancake_variation_id=row["pancake_variation_id"],
                    status=row["status"],
                    payload_hash=row["payload_hash"],
                    attempts=row["attempts"],
                )
            )
        return snapshot

    @staticmethod
    def save_group(
        snapshot: LinkSnapshot,
        *,
        group_key: str,
        pancake_product_id: str | None,
        payload_hash: str | None,
        synced_at: datetime | None,
        variations: list[VariationWrite],
    ) -> None:
        """Upsert liên kết của một nhóm rồi cập nhật `snapshot` cho các nhóm sau.

        `payload_hash=None` = giữ hash cũ (nhóm chưa đồng bộ thành công lần này).
        """
        with transaction.atomic():
            link, _ = PancakePosProductLink.objects.get_or_create(group_key=group_key)
            fields: list[str] = []
            if pancake_product_id and link.pancake_product_id != pancake_product_id:
                link.pancake_product_id = pancake_product_id
                fields.append("pancake_product_id")
            if payload_hash is not None:
                link.payload_hash = payload_hash
                link.synced_at = synced_at
                fields += ["payload_hash", "synced_at"]
            if fields:
                link.save(update_fields=[*fields, "updated_at"])
            for write in variations:
                defaults: dict[str, Any] = {
                    "product_link": link,
                    "status": write.status,
                    "last_error": write.last_error[:MAX_ERROR_LEN],
                    "attempts": write.attempts,
                }
                if write.pancake_variation_id:
                    defaults["pancake_variation_id"] = write.pancake_variation_id
                if write.status == LinkStatus.SYNCED:
                    defaults["payload_hash"] = write.payload_hash
                    defaults["synced_at"] = write.synced_at
                PancakePosVariationLink.objects.update_or_create(product_id=write.product_id, defaults=defaults)

        state = snapshot.products.get(group_key) or ProductLinkState(group_key, None, "")
        if pancake_product_id:
            state.pancake_product_id = pancake_product_id
        if payload_hash is not None:
            state.payload_hash = payload_hash
        snapshot.put_product(state)
        for write in variations:
            previous = snapshot.variations.get(write.product_id)
            snapshot.put_variation(VariationLinkState(
                product_id=write.product_id,
                group_key=group_key,
                pancake_variation_id=write.pancake_variation_id
                or (previous.pancake_variation_id if previous else None),
                status=write.status,
                payload_hash=write.payload_hash
                if write.status == LinkStatus.SYNCED
                else (previous.payload_hash if previous else ""),
                attempts=write.attempts,
            ))

    @staticmethod
    def start_run(mode: str, user_id: int | None) -> int:
        return PancakePosSyncRun.objects.create(mode=mode, triggered_by_id=user_id).id

    @staticmethod
    def run_exists(run_id: int) -> bool:
        return PancakePosSyncRun.objects.filter(id=run_id, finished_at__isnull=True).exists()

    @staticmethod
    def finish_run(run_id: int, *, counters: dict[str, int], report: dict[str, Any], finished_at: datetime) -> None:
        PancakePosSyncRun.objects.filter(id=run_id).update(finished_at=finished_at, report=report, **counters)


class DirtyQueue:
    """Hàng đợi sản phẩm cần đồng bộ tăng dần (`pancake_pos_dirty_products`).

    SQL thô: upsert/claim theo lô cần `ON CONFLICT` + `FOR UPDATE SKIP LOCKED` mà ORM
    không diễn đạt gọn. Bảng nhỏ (≤ số sản phẩm), PK product_id.
    """

    @staticmethod
    def mark(product_ids: list[int] | tuple[int, ...]) -> int:
        ids = sorted({int(i) for i in product_ids if i})
        if not ids:
            return 0
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO pancake_pos_dirty_products (product_id, marked_at) "
                "SELECT unnest(%s::bigint[]), now() ON CONFLICT (product_id) DO NOTHING",
                [ids],
            )
            return cur.rowcount

    @staticmethod
    def mark_group(group_id: int) -> int:
        """Mọi sản phẩm (kể cả đã xoá mềm — để ẩn) của một nhóm CRM."""
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO pancake_pos_dirty_products (product_id, marked_at) "
                "SELECT id, now() FROM products WHERE group_id = %s ON CONFLICT (product_id) DO NOTHING",
                [group_id],
            )
            return cur.rowcount

    @staticmethod
    def claim(limit: int) -> list[int]:
        """Lấy và XOÁ tối đa `limit` id cũ nhất. SKIP LOCKED: hai beat chồng nhau không
        lấy trùng. Đánh dấu lại trong lúc đang xử lý → dòng mới, lượt sau lấy tiếp."""
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "DELETE FROM pancake_pos_dirty_products WHERE product_id IN ("
                " SELECT product_id FROM pancake_pos_dirty_products"
                " ORDER BY marked_at, product_id LIMIT %s FOR UPDATE SKIP LOCKED"
                ") RETURNING product_id",
                [limit],
            )
            return sorted(row[0] for row in cur.fetchall())

    @staticmethod
    def count() -> int:
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM pancake_pos_dirty_products")
            return int(cur.fetchone()[0])


@dataclass(frozen=True)
class WarehouseLinkState:
    warehouse_id: int
    pancake_warehouse_id: str
    pancake_warehouse_name: str = ""
    is_default_for_push: bool = False
    priority: int = 0


class WarehouseLinkError(ValueError):
    """Danh sách ghép kho không hợp lệ (trùng kho, nhiều kho mặc định...)."""


class WarehouseLinkRepository:
    @staticmethod
    def all_links() -> list[WarehouseLinkState]:
        return [
            WarehouseLinkState(
                warehouse_id=row["warehouse_id"],
                pancake_warehouse_id=row["pancake_warehouse_id"],
                pancake_warehouse_name=row["pancake_warehouse_name"],
                is_default_for_push=row["is_default_for_push"],
                priority=row["priority"],
            )
            for row in PancakePosWarehouseLink.objects.order_by("priority", "id").values(
                "warehouse_id", "pancake_warehouse_id", "pancake_warehouse_name", "is_default_for_push", "priority"
            )
        ]

    @staticmethod
    def validate(links: list[WarehouseLinkState]) -> None:
        crm = [link.warehouse_id for link in links]
        pancake = [link.pancake_warehouse_id for link in links]
        if len(set(crm)) != len(crm):
            raise WarehouseLinkError("Một kho CRM chỉ được ghép một kho Pancake.")
        if len(set(pancake)) != len(pancake):
            raise WarehouseLinkError("Một kho Pancake chỉ được ghép một kho CRM.")
        if sum(1 for link in links if link.is_default_for_push) > 1:
            raise WarehouseLinkError("Chỉ được một kho mặc định để đẩy đơn.")

    @classmethod
    def replace_all(cls, links: list[WarehouseLinkState]) -> list[WarehouseLinkState]:
        """Thay TOÀN BỘ danh sách ghép (PUT) trong một transaction. Xoá trước rồi tạo:
        đổi chéo hai kho (A→X, B→Y thành A→Y, B→X) không vướng UNIQUE giữa chừng."""
        cls.validate(links)
        with transaction.atomic():
            PancakePosWarehouseLink.objects.all().delete()
            PancakePosWarehouseLink.objects.bulk_create([
                PancakePosWarehouseLink(
                    warehouse_id=link.warehouse_id,
                    pancake_warehouse_id=link.pancake_warehouse_id,
                    pancake_warehouse_name=link.pancake_warehouse_name[:255],
                    is_default_for_push=link.is_default_for_push,
                    priority=link.priority,
                )
                for link in links
            ])
        return cls.all_links()


class CategoryCache:
    """Id danh mục Pancake theo khoá nội bộ (`pancake_pos_categories`)."""

    @staticmethod
    def get(key: str) -> str | None:
        return PancakePosCategory.objects.filter(key=key).values_list("pancake_category_id", flat=True).first()

    @staticmethod
    def put(key: str, name: str, pancake_category_id: str) -> None:
        PancakePosCategory.objects.update_or_create(
            key=key, defaults={"name": name, "pancake_category_id": pancake_category_id}
        )


# ── đẩy đơn (two-way-sync §4.1, A2) ─────────────────────────────────────────
# Liên kết coi như "đơn này đã/đang được đẩy" (chống trùng landing ↔ Foxia).
ACTIVE_PUSH_STATUSES = (
    OrderLinkStatus.PENDING, OrderLinkStatus.PUSHING, OrderLinkStatus.PUSHED, OrderLinkStatus.UPDATED,
)
LIVE_STATUSES = (OrderLinkStatus.PUSHED, OrderLinkStatus.UPDATED)


@dataclass(frozen=True)
class OrderLinkState:
    id: int
    order_id: int
    part_no: int
    status: str
    pancake_order_id: str | None
    warehouse_id: int | None
    pancake_warehouse_id: str
    items: tuple[tuple[int, int], ...] | None
    payload_hash: str
    needs_lookup: bool
    attempts: int
    attempted_at: datetime | None
    checked_at: datetime | None


_LINK_FIELDS = (
    "id", "order_id", "part_no", "status", "pancake_order_id", "warehouse_id", "pancake_warehouse_id",
    "items", "payload_hash", "needs_lookup", "attempts", "attempted_at", "checked_at",
)


def _link_state(row: dict[str, Any]) -> OrderLinkState:
    raw_items = row["items"]
    items = (
        tuple((int(i["product_id"]), int(i["quantity"])) for i in raw_items)
        if isinstance(raw_items, list) else None
    )
    return OrderLinkState(**{**row, "items": items})


class OrderLinkRepository:
    """Bảng `pancake_pos_order_links`. Mọi chuyển trạng thái có tranh chấp đi qua câu
    UPDATE có điều kiện hoặc `FOR UPDATE SKIP LOCKED` — hai worker (beat + event) không
    cùng đẩy một dòng."""

    @staticmethod
    def for_orders(order_ids: list[int] | tuple[int, ...] | set[int]) -> dict[int, list[OrderLinkState]]:
        out: dict[int, list[OrderLinkState]] = {}
        if not order_ids:
            return out
        rows = PancakePosOrderLink.objects.filter(order_id__in=list(order_ids)).order_by("order_id", "part_no")
        for row in rows.values(*_LINK_FIELDS):
            out.setdefault(row["order_id"], []).append(_link_state(row))
        return out

    @staticmethod
    def get_many(link_ids: list[int]) -> list[OrderLinkState]:
        rows = PancakePosOrderLink.objects.filter(id__in=link_ids).order_by("order_id", "part_no")
        return [_link_state(row) for row in rows.values(*_LINK_FIELDS)]

    @staticmethod
    def active_push_order_ids(order_ids: list[int] | set[int]) -> set[int]:
        """Đơn (trong danh sách) đã/đang được đẩy — kể cả lần POST chưa rõ kết quả."""
        if not order_ids:
            return set()
        qs = PancakePosOrderLink.objects.filter(order_id__in=list(order_ids)).filter(
            models.Q(status__in=ACTIVE_PUSH_STATUSES)
            | models.Q(status=OrderLinkStatus.ERROR, needs_lookup=True)
        )
        return set(qs.values_list("order_id", flat=True))

    @staticmethod
    def create_first(order_id: int, *, status: str, last_error: str = "", warehouse_id: int | None = None,
                     pancake_warehouse_id: str = "") -> bool:
        """Tạo dòng part 1 cho đơn chưa có liên kết. False = đã có (worker khác vừa tạo)."""
        try:
            with transaction.atomic():
                PancakePosOrderLink.objects.create(
                    order_id=order_id, part_no=1, status=status, last_error=last_error[:MAX_ERROR_LEN],
                    warehouse_id=warehouse_id, pancake_warehouse_id=pancake_warehouse_id,
                )
        except IntegrityError:
            return False
        return True

    @staticmethod
    def claim_pushable(
        *, limit: int, order_ids: list[int] | None = None, stale_seconds: int, retry_after_seconds: int,
        max_auto_attempts: int,
    ) -> list[int]:
        """Lấy dòng cần POST và đặt `pushing` NGAY trong transaction này (trước khi gọi
        Pancake). Gồm: `pending`; `pushing` quá hạn (worker chết giữa chừng → đánh dấu
        cần tra lại); `error` đã chọn kho, chưa có id Pancake, còn lượt tự thử lại."""
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                """
                WITH picked AS (
                    SELECT id FROM pancake_pos_order_links
                     WHERE pancake_order_id IS NULL
                       AND (%(ids)s::bigint[] IS NULL OR order_id = ANY(%(ids)s))
                       AND (
                            status = 'pending'
                            OR (status = 'pushing' AND attempted_at < now() - make_interval(secs => %(stale)s))
                            OR (status = 'error' AND pancake_warehouse_id <> '' AND attempts < %(max_attempts)s
                                AND attempted_at < now() - make_interval(secs => %(retry)s))
                       )
                     ORDER BY id
                     LIMIT %(limit)s
                     FOR UPDATE SKIP LOCKED
                )
                UPDATE pancake_pos_order_links l
                   SET needs_lookup = l.needs_lookup OR l.status = 'pushing',
                       status = 'pushing', attempts = l.attempts + 1,
                       attempted_at = now(), updated_at = now()
                  FROM picked
                 WHERE l.id = picked.id
             RETURNING l.id
                """,
                {
                    "ids": sorted(set(order_ids)) if order_ids else None, "stale": stale_seconds,
                    "retry": retry_after_seconds, "max_attempts": max_auto_attempts, "limit": limit,
                },
            )
            return sorted(r[0] for r in cur.fetchall())

    @staticmethod
    def update(link_id: int, **fields: Any) -> None:
        if "last_error" in fields:
            fields["last_error"] = str(fields["last_error"] or "")[:MAX_ERROR_LEN]
        PancakePosOrderLink.objects.filter(id=link_id).update(**fields, updated_at=timezone.now())

    @staticmethod
    def order_part_count(order_id: int) -> int:
        return PancakePosOrderLink.objects.filter(order_id=order_id).count()

    @staticmethod
    def replace_routing(order_id: int, parts: list[dict[str, Any]]) -> list[int]:
        """Thay các dòng CHƯA đẩy của đơn bằng phương án xuất mới (1 kho / N kiện).
        Chặn khi đơn đã có dòng có id Pancake hoặc đang đẩy (khoá dòng trong transaction)."""
        with transaction.atomic():
            rows = list(PancakePosOrderLink.objects.select_for_update().filter(order_id=order_id))
            if any(r.pancake_order_id or r.status == OrderLinkStatus.PUSHING for r in rows):
                raise OrderLinkConflict("Đơn đã/đang được đẩy sang Pancake — không đổi cách xuất được.")
            PancakePosOrderLink.objects.filter(order_id=order_id).delete()
            created = PancakePosOrderLink.objects.bulk_create([
                PancakePosOrderLink(order_id=order_id, part_no=index, status=OrderLinkStatus.PENDING, **part)
                for index, part in enumerate(parts, start=1)
            ])
        return [link.id for link in created]

    @staticmethod
    def reset_for_reevaluation(order_id: int) -> int:
        """Xoá các dòng chưa đẩy của đơn để lượt sau đánh giá lại từ đầu (Thử lại cho
        needs_routing / skipped_duplicate / lỗi thiếu dữ liệu)."""
        with transaction.atomic():
            rows = list(PancakePosOrderLink.objects.select_for_update().filter(order_id=order_id))
            if any(r.pancake_order_id or r.status == OrderLinkStatus.PUSHING or r.needs_lookup for r in rows):
                raise OrderLinkConflict("Đơn đã/đang được đẩy (hoặc chưa rõ đã tạo) — không đánh giá lại được.")
            deleted, _ = PancakePosOrderLink.objects.filter(order_id=order_id).delete()
        return deleted


class OrderLinkConflict(Exception):
    """Thao tác trên liên kết đơn không hợp lệ ở trạng thái hiện tại."""


class StatusMapRepository:
    @staticmethod
    def locked_statuses() -> set[int]:
        return set(PancakePosStatusMap.objects.filter(locks_edit=True).values_list("pancake_status", flat=True))
