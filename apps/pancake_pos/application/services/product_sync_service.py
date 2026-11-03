"""Đồng bộ danh mục CRM → Pancake POS (docs/pancake-pos-product-sync.md §3.3, §3.5).

Luồng một lượt (full hoặc tăng dần):
1. Đọc ứng viên CRM (1 query) → lọc theo `domain.eligibility` → gom nhóm.
2. Chỉ mục mẫu mã Pancake: full = kéo TOÀN BỘ mẫu mã của shop; tăng dần = chỉ tìm
   theo SKU chưa liên kết + custom_id nhóm chưa liên kết (đa số nhóm: 0 request).
3. Mỗi nhóm (độc lập, lỗi một nhóm không dừng lượt):
   - đã có liên kết → cập nhật nếu hash payload đổi;
   - chưa có nhưng thấy trên Pancake theo SKU → liên kết rồi cập nhật;
   - không có ở đâu → PUT thêm mẫu mã vào sản phẩm Pancake của nhóm (nếu có) hoặc
     POST sản phẩm mới rồi GET lại theo custom_id để lấy id (POST không trả id);
   - SKU khớp > 1 mẫu mã / id đã thuộc SKU khác → `conflict`, không tự xử lý.
4. SKU đã liên kết nhưng hết đủ điều kiện (xoá mềm, `never`, hết tồn/bán…) → KHÔNG xoá
   trên Pancake, chỉ ẩn mẫu mã (`is_hidden=true`), liên kết chuyển `skipped`.

Mọi PUT gửi ĐỦ mẫu mã đang có của sản phẩm Pancake (GET ngay trước khi PUT): mẫu mã
không thuộc payload CRM được gửi lại nguyên trạng — chưa kiểm chứng PUT thiếu mẫu
mã có xoá mẫu mã đó hay không (§7), nên không bao giờ để thiếu.

Linh kiện (two-way-sync §6b): khi DB có `products.product_type` và
PANCAKE_POS_INCLUDE_COMPONENTS bật, SKU linh kiện vẫn đủ điều kiện, đẩy ẩn và gắn
danh mục "Linh kiện" (tạo trên Pancake nếu chưa có, id cache ở `pancake_pos_categories`).
Chưa có cột → bỏ nhánh này, ghi vào báo cáo (`components.status=unsupported`).

Dry-run: chạy y hệt phần quyết định nhưng KHÔNG gọi API ghi, KHÔNG ghi liên kết.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from django.utils import timezone

from apps.pancake_pos.application.services.sync_report import SyncReport
from apps.pancake_pos.contracts.ports import CatalogReader, PancakePosGateway
from apps.pancake_pos.domain.eligibility import DEFAULT_SALES_WINDOW_DAYS, ineligible_reason
from apps.pancake_pos.domain.entities import CatalogProduct, PancakeVariation, SyncGroup
from apps.pancake_pos.domain.mapping import (
    GroupPayload,
    build_group_payload,
    group_products,
    normalize_key,
    passthrough_variation,
)
from apps.pancake_pos.domain.pancake_index import PancakeIndex, build_index
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosError
from apps.pancake_pos.models import LinkStatus, SyncRunMode
from apps.pancake_pos.repositories import (
    CategoryCache,
    LinkRepository,
    LinkSnapshot,
    VariationLinkState,
    VariationWrite,
)

logger = logging.getLogger(__name__)

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_SKIP = "skip"
ACTION_CONFLICT = "conflict"
ACTION_HIDE = "hide"

REASON_DELETED = "deleted"  # không còn trong danh mục đọc được (xoá mềm/xoá cứng)

COMPONENT_CATEGORY_KEY = "component"
COMPONENT_CATEGORY_NAME = "Linh kiện"

COMPONENTS_UNSUPPORTED = "unsupported"  # DB chưa có products.product_type (MR !1504)
COMPONENTS_EXCLUDED = "excluded"  # PANCAKE_POS_INCLUDE_COMPONENTS tắt
COMPONENTS_INCLUDED = "included"


class SyncGroupError(Exception):
    """Lỗi nghiệp vụ của một nhóm (vd POST xong không đọc lại được id)."""


@dataclass
class GroupPlan:
    group: SyncGroup
    action: str
    payload: GroupPayload | None = None
    product_id: str | None = None
    resolved: dict[int, str] = field(default_factory=dict)  # product_id CRM → variation id Pancake
    newly_linked: set[int] = field(default_factory=set)
    conflicts: dict[int, str] = field(default_factory=dict)
    hide: dict[int, str] = field(default_factory=dict)  # SKU CRM hết đủ điều kiện → variation id cần ẩn

    @property
    def new_product_ids(self) -> list[int]:
        if self.payload is None:
            return []
        return [v.product_id for v in self.payload.variations if v.product_id not in self.resolved]


@dataclass
class RunContext:
    """Trạng thái dùng chung của một lượt."""

    report: SyncReport
    dry_run: bool
    index: PancakeIndex
    snapshot: LinkSnapshot
    eligible_ids: set[int]
    reasons: dict[int, str]  # product_id → lý do không đủ điều kiện
    hidden_in_plans: set[int] = field(default_factory=set)
    component_category_id: str | None = None


def _hideable(link: VariationLinkState | None) -> bool:
    """Đã liên kết một mẫu mã Pancake và chưa ẩn."""
    return bool(link and link.pancake_variation_id and link.status != LinkStatus.SKIPPED)


def _hidden_message(reason: str) -> str:
    return f"Đã ẩn trên Pancake — không còn đủ điều kiện ({reason})"


def _error_message(exc: Exception) -> str:
    return str(exc) if isinstance(exc, PancakePosError | SyncGroupError) else f"{type(exc).__name__}: {exc}"


class ProductSyncService:
    def __init__(
        self,
        *,
        gateway: PancakePosGateway,
        catalog_reader: CatalogReader,
        repository: LinkRepository | None = None,
        default_weight_g: int = 500,
        sales_window_days: int = DEFAULT_SALES_WINDOW_DAYS,
        include_components: bool = True,
        category_cache: CategoryCache | None = None,
        clock: Callable[[], datetime] = timezone.now,
    ) -> None:
        self._gateway = gateway
        self._reader = catalog_reader
        self._repo = repository or LinkRepository()
        self._include_components = include_components
        self._categories = category_cache or CategoryCache()
        self._default_weight_g = default_weight_g
        self._window = sales_window_days
        self._clock = clock

    # ── API ─────────────────────────────────────────────────────────────────
    def full_sync(
        self,
        *,
        dry_run: bool,
        limit: int | None = None,
        skus: Sequence[str] | None = None,
        user_id: int | None = None,
        run_id: int | None = None,
    ) -> SyncReport:
        """`run_id` = dòng nhật ký API đã tạo sẵn (để trả id ngay cho người bấm)."""
        mode = SyncRunMode.DRY_RUN if dry_run else SyncRunMode.FULL
        return self._execute(
            mode, dry_run=dry_run, user_id=user_id, run_id=run_id,
            body=lambda report: self._run_full(report, dry_run=dry_run, limit=limit, skus=skus),
        )

    def sync_products(
        self, product_ids: Iterable[int], *, user_id: int | None = None, run_id: int | None = None
    ) -> SyncReport:
        """Đồng bộ tăng dần các sản phẩm vừa đổi (kèm cả nhóm của chúng). Luôn ghi thật."""
        ids = sorted({int(i) for i in product_ids})
        return self._execute(
            SyncRunMode.INCREMENTAL, dry_run=False, user_id=user_id, run_id=run_id,
            body=lambda report: self._run_incremental(report, ids),
        )

    # ── khung một lượt ──────────────────────────────────────────────────────
    def _execute(
        self,
        mode: str,
        *,
        dry_run: bool,
        user_id: int | None,
        run_id: int | None,
        body: Callable[[SyncReport], None],
    ) -> SyncReport:
        run_id = run_id or self._repo.start_run(mode, user_id)
        report = SyncReport(run_id=run_id, mode=str(mode), dry_run=dry_run)
        try:
            body(report)
        except Exception as exc:
            report.fatal = str(exc) if isinstance(exc, PancakePosError) else type(exc).__name__
            logger.exception("pancake_pos: lượt đồng bộ #%s dừng giữa chừng", run_id)
            raise
        finally:
            self._repo.finish_run(
                run_id, counters=report.counters.as_dict(), report=report.as_json(), finished_at=self._clock()
            )
        return report

    def _select(
        self, report: SyncReport, candidates: list[CatalogProduct]
    ) -> tuple[list[CatalogProduct], dict[int, str]]:
        now = self._clock()
        report.candidates = len(candidates)
        eligible: list[CatalogProduct] = []
        reasons: dict[int, str] = {}
        for product in candidates:
            reason = ineligible_reason(
                product, now=now, sales_window_days=self._window, include_components=self._include_components
            )
            if reason is None:
                eligible.append(product)
            else:
                reasons[product.id] = reason
                report.ineligible[reason] += 1
        report.eligible = len(eligible)
        return eligible, reasons

    def _run_full(self, report: SyncReport, *, dry_run: bool, limit: int | None, skus: Sequence[str] | None) -> None:
        candidates = self._reader.list_candidates(sold_since=self._clock() - timedelta(days=self._window), skus=skus)
        eligible, reasons = self._select(report, candidates)
        groups = group_products(eligible)
        if limit:
            groups = groups[:limit]
        report.groups = len(groups)

        index = build_index(self._gateway.iter_variations())
        report.pancake_variations = index.total_variations
        ctx = RunContext(
            report=report, dry_run=dry_run, index=index, snapshot=self._repo.load_snapshot(),
            eligible_ids={p.id for p in eligible}, reasons=reasons,
            component_category_id=self._components(report, eligible, dry_run=dry_run),
        )
        self._sync_groups(ctx, groups)
        # `--limit` là chạy thử theo lô nhỏ → không ẩn hàng loạt ngoài các nhóm đã chọn.
        if limit is None:
            self._hide_orphans(ctx, {c.id for c in candidates} if skus else None)

    def _run_incremental(self, report: SyncReport, ids: list[int]) -> None:
        report.requested = len(ids)
        if not ids:
            return
        candidates = self._reader.list_candidates(
            sold_since=self._clock() - timedelta(days=self._window), product_ids=ids
        )
        eligible, reasons = self._select(report, candidates)
        groups = group_products(eligible)
        report.groups = len(groups)
        snapshot = self._repo.load_snapshot()
        index = self._partial_index(groups, snapshot)
        report.pancake_variations = index.total_variations
        ctx = RunContext(
            report=report, dry_run=False, index=index, snapshot=snapshot,
            eligible_ids={p.id for p in eligible}, reasons=reasons,
            component_category_id=self._components(report, eligible, dry_run=False),
        )
        self._sync_groups(ctx, groups)
        self._hide_orphans(ctx, set(ids) | {c.id for c in candidates})

    # ── linh kiện (§6b) ─────────────────────────────────────────────────────
    def _components(self, report: SyncReport, eligible: list[CatalogProduct], *, dry_run: bool) -> str | None:
        """Ghi trạng thái nhánh linh kiện vào báo cáo; trả id danh mục "Linh kiện" khi
        có linh kiện đủ điều kiện (None = chưa có danh mục — dry-run hoặc lỗi)."""
        if not self._reader.has_product_type():
            logger.info("pancake_pos: DB chưa có products.product_type — bỏ qua nhánh linh kiện")
            report.components = {"status": COMPONENTS_UNSUPPORTED}
            return None
        if not self._include_components:
            report.components = {"status": COMPONENTS_EXCLUDED}
            return None
        count = sum(1 for p in eligible if p.is_component)
        report.components = {"status": COMPONENTS_INCLUDED, "eligible": count}
        if not count:
            return None
        try:
            category_id = self._component_category(report, dry_run=dry_run)
        except PancakePosError as exc:
            # Thiếu danh mục không chặn lượt: mẫu mã vẫn ẩn; lượt sau gắn danh mục (hash đổi → PUT).
            logger.warning("pancake_pos: không lấy/tạo được danh mục Linh kiện: %s", exc)
            report.components["category_error"] = str(exc)
            return None
        report.components["category_id"] = category_id
        return category_id

    def _component_category(self, report: SyncReport, *, dry_run: bool) -> str | None:
        cached = self._categories.get(COMPONENT_CATEGORY_KEY)
        if cached:
            return cached
        wanted = normalize_key(COMPONENT_CATEGORY_NAME)
        found = next(
            (c["id"] for c in self._gateway.list_categories() if normalize_key(c.get("name", "")) == wanted and c.get("id")),
            None,
        )
        if found is None and dry_run:
            report.components["category"] = "would_create"
            return None
        if found is None:
            found = self._gateway.create_category(COMPONENT_CATEGORY_NAME)
            report.components["category"] = "created"
        if not dry_run:
            self._categories.put(COMPONENT_CATEGORY_KEY, COMPONENT_CATEGORY_NAME, found)
        return found

    def _partial_index(self, groups: list[SyncGroup], snapshot: LinkSnapshot) -> PancakeIndex:
        """Chỉ mục tối thiểu cho lượt tăng dần: tìm theo SKU chưa liên kết và theo
        custom_id của nhóm chưa có sản phẩm Pancake. Nhóm đã liên kết đủ: 0 request."""
        found: list[PancakeVariation] = []
        for group in groups:
            unlinked = [
                p for p in group.products
                if not ((link := snapshot.variations.get(p.id)) and link.pancake_variation_id)
            ]
            for product in unlinked:
                found.extend(self._gateway.iter_variations(search=product.sku))
            product_link = snapshot.products.get(group.key)
            if unlinked and not (product_link and product_link.pancake_product_id):
                pancake_product = self._gateway.get_product(group.custom_id)
                if pancake_product and normalize_key(pancake_product.custom_id) == normalize_key(group.custom_id):
                    found.extend(pancake_product.variations)
        return build_index(found, complete=False)

    # ── nhóm ────────────────────────────────────────────────────────────────
    def _sync_groups(self, ctx: RunContext, groups: list[SyncGroup]) -> None:
        for group in groups:
            try:
                plan = self._plan(ctx, group)
                self._apply(ctx, plan)
            except Exception as exc:  # noqa: BLE001 — một nhóm lỗi không chặn cả lượt
                message = _error_message(exc)
                logger.warning("pancake_pos: nhóm %s lỗi: %s", group.key, message)
                ctx.report.add_error(group.key, [p.sku for p in group.products], message)
                if not ctx.dry_run:
                    self._record_error(group, ctx.snapshot, message)

    def _plan(self, ctx: RunContext, group: SyncGroup) -> GroupPlan:
        index, snapshot = ctx.index, ctx.snapshot
        plan = GroupPlan(group=group, action=ACTION_CONFLICT)
        for product in group.products:
            link = snapshot.variations.get(product.id)
            if link and link.pancake_variation_id:
                plan.resolved[product.id] = link.pancake_variation_id
                continue
            matches = index.lookup(product.sku)
            if len(matches) > 1:
                ids = ", ".join(m.id for m in matches[:5])
                plan.conflicts[product.id] = f"SKU khớp {len(matches)} mẫu mã Pancake ({ids})"
                continue
            if matches:
                owner = snapshot.variation_owner(matches[0].id)
                if owner is not None and owner != product.id:
                    plan.conflicts[product.id] = f"Mẫu mã Pancake {matches[0].id} đã liên kết với sản phẩm CRM #{owner}"
                    continue
                plan.resolved[product.id] = matches[0].id
                plan.newly_linked.add(product.id)

        product_link = snapshot.products.get(group.key)
        plan.product_id = product_link.pancake_product_id if product_link else None
        if plan.product_id:
            # Mẫu mã mới liên kết nằm ở sản phẩm Pancake khác sản phẩm của nhóm → xung đột.
            for pid in sorted(plan.newly_linked):
                other = index.product_id_of(plan.resolved[pid])
                if other and other != plan.product_id:
                    plan.conflicts[pid] = f"Mẫu mã {plan.resolved.pop(pid)} thuộc sản phẩm Pancake khác ({other})"
                    plan.newly_linked.discard(pid)
        else:
            found = {index.product_id_of(v) for v in plan.resolved.values()} - {""}
            if not found:
                found = index.product_ids_for(group.custom_id)
            if len(found) > 1:
                return self._whole_group_conflict(plan, f"Nhóm nằm ở {len(found)} sản phẩm Pancake: {', '.join(sorted(found))}")
            if found:
                plan.product_id = next(iter(found))
                owner = snapshot.product_owner(plan.product_id)
                if owner is not None and owner != group.key:
                    return self._whole_group_conflict(plan, f"Sản phẩm Pancake {plan.product_id} đã liên kết với nhóm {owner}")
            elif plan.resolved:
                return self._whole_group_conflict(plan, "Không xác định được sản phẩm Pancake chứa mẫu mã đã khớp")

        sendable = tuple(p for p in group.products if p.id not in plan.conflicts)
        if not sendable:
            return plan
        plan.payload = build_group_payload(
            replace(group, products=sendable),
            default_weight_g=self._default_weight_g,
            component_category_id=ctx.component_category_id,
        )
        if not plan.product_id:
            plan.action = ACTION_CREATE
            return plan
        # SKU từng thuộc nhóm nhưng nay hết đủ điều kiện → ẩn trong cùng lần PUT.
        members = {p.id for p in group.products}
        plan.hide = {
            link.product_id: link.pancake_variation_id or ""
            for link in snapshot.group_members(group.key)
            if link.product_id not in members and link.product_id not in ctx.eligible_ids and _hideable(link)
        }
        changed = (
            product_link is None
            or product_link.payload_hash != plan.payload.hash
            or bool(plan.new_product_ids)
            or bool(plan.newly_linked)
            or bool(plan.hide)
            or any(
                (vl := snapshot.variations.get(v.product_id)) is None
                or vl.status != LinkStatus.SYNCED
                or vl.payload_hash != v.hash
                for v in plan.payload.variations
            )
        )
        plan.action = ACTION_UPDATE if changed else ACTION_SKIP
        return plan

    @staticmethod
    def _whole_group_conflict(plan: GroupPlan, message: str) -> GroupPlan:
        for product in plan.group.products:
            plan.conflicts.setdefault(product.id, message)
        plan.resolved.clear()
        plan.newly_linked.clear()
        plan.action = ACTION_CONFLICT
        return plan

    def _existing_variations(self, product_id: str, index: PancakeIndex, *, fetch: bool) -> list[PancakeVariation]:
        """Mẫu mã đang có của sản phẩm Pancake: chỉ mục ∪ GET product (mới hơn, ghi đè).

        `fetch=False` (dry-run) chỉ dùng chỉ mục. GET không thấy sản phẩm → dừng nhóm:
        PUT mà không biết đủ mẫu mã hiện có là có thể xoá mất mẫu mã (§7)."""
        known = {v.id: v for v in index.variations_of(product_id)}
        if fetch:
            product = self._gateway.get_product(product_id)
            if product is None:
                raise SyncGroupError(f"Không đọc được sản phẩm Pancake {product_id} trước khi PUT")
            for variation in product.variations:
                known[variation.id] = variation
        return [known[k] for k in sorted(known)]

    def _update_request(self, ctx: RunContext, plan: GroupPlan) -> dict[str, Any]:
        """Body PUT: mẫu mã CRM (ghi đè trường) + MỌI mẫu mã khác đang có (nguyên trạng,
        mẫu mã CRM hết đủ điều kiện thì ẩn)."""
        if plan.payload is None or not plan.product_id:
            raise SyncGroupError("Thiếu payload/sản phẩm Pancake cho lần cập nhật")
        mine = set(plan.resolved.values())
        hide_ids = set(plan.hide.values())
        existing = self._existing_variations(plan.product_id, ctx.index, fetch=not ctx.dry_run)
        extras = [passthrough_variation(v, hide=v.id in hide_ids) for v in existing if v.id not in mine]
        return plan.payload.to_request(plan.resolved, extras)

    def _apply(self, ctx: RunContext, plan: GroupPlan) -> None:
        group, snapshot, report, dry_run = plan.group, ctx.snapshot, ctx.report, ctx.dry_run
        sku_of = {p.id: p.sku for p in group.products}
        for pid, message in plan.conflicts.items():
            report.add_conflict(group.key, sku_of[pid], message)

        counters = report.counters
        new_ids = plan.new_product_ids
        request: dict[str, Any] | None = None
        if plan.payload is not None and plan.action == ACTION_CREATE:
            request = plan.payload.to_request()
        elif plan.payload is not None and plan.action == ACTION_UPDATE:
            request = self._update_request(ctx, plan)
        if request is not None:
            report.add_sample({
                "group_key": group.key,
                "action": plan.action,
                "pancake_product_id": plan.product_id,
                "request": request,
            })

        created_ids: dict[str, str] = {}
        if not dry_run and request is not None and plan.payload is not None:
            if plan.action == ACTION_CREATE:
                self._gateway.create_product(request)
                plan.product_id, created_ids = self._read_back(group.custom_id, [v.sku for v in plan.payload.variations])
                if not plan.product_id:
                    raise SyncGroupError(f"Đã POST nhưng không đọc lại được sản phẩm custom_id={group.custom_id}")
            else:
                self._gateway.update_product(plan.product_id or "", request)
                if new_ids:
                    _, created_ids = self._read_back(plan.product_id or "", [sku_of[i] for i in new_ids])

        writes: list[VariationWrite] = [
            VariationWrite(
                product_id=pid,
                pancake_variation_id=None,
                status=LinkStatus.CONFLICT,
                last_error=message,
                attempts=snapshot.variations[pid].attempts if pid in snapshot.variations else 0,
            )
            for pid, message in plan.conflicts.items()
        ]
        if plan.action == ACTION_UPDATE and plan.hide:
            counters.hidden += len(plan.hide)
            ctx.hidden_in_plans.update(plan.hide)
            writes.extend(
                VariationWrite(
                    product_id=pid,
                    pancake_variation_id=vid or None,
                    status=LinkStatus.SKIPPED,
                    last_error=_hidden_message(ctx.reasons.get(pid, REASON_DELETED)),
                )
                for pid, vid in plan.hide.items()
            )
        now = self._clock()
        all_synced = True
        for variation in plan.payload.variations if plan.payload else ():
            pid = variation.product_id
            if plan.action == ACTION_SKIP:
                counters.skipped += 1
                continue
            if pid in plan.newly_linked:
                counters.linked += 1
            elif pid in plan.resolved:
                counters.updated += 1
            variation_id = plan.resolved.get(pid) or created_ids.get(normalize_key(variation.sku))
            if pid not in plan.resolved:
                if dry_run:
                    counters.created += 1
                    continue
                owner = snapshot.variation_owner(variation_id) if variation_id else None
                if not variation_id or (owner is not None and owner != pid):
                    all_synced = False
                    message = "Không đọc lại được id mẫu mã sau khi tạo" if not variation_id else (
                        f"Mẫu mã Pancake {variation_id} đã liên kết với sản phẩm CRM #{owner}"
                    )
                    report.add_error(group.key, [variation.sku], message)
                    writes.append(self._error_write(snapshot, pid, message))
                    continue
                counters.created += 1
            writes.append(VariationWrite(
                product_id=pid,
                pancake_variation_id=variation_id,
                status=LinkStatus.SYNCED,
                payload_hash=variation.hash,
                synced_at=now,
            ))

        if dry_run or (plan.action == ACTION_SKIP and not writes):
            return
        synced_ok = plan.payload is not None and plan.action != ACTION_SKIP and all_synced
        self._repo.save_group(
            snapshot,
            group_key=group.key,
            pancake_product_id=plan.product_id,
            payload_hash=plan.payload.hash if synced_ok and plan.payload else None,
            synced_at=now,
            variations=writes,
        )

    # ── ẩn SKU hết đủ điều kiện ở nhóm không có lần PUT nào ─────────────────
    def _hide_orphans(self, ctx: RunContext, scope: set[int] | None) -> None:
        """Ẩn mẫu mã của SKU đã liên kết nhưng hết đủ điều kiện, ở những nhóm KHÔNG
        có lần PUT nào trong lượt này (nhóm có PUT đã ẩn luôn trong `_plan`).

        `scope=None` = mọi liên kết (full sync không lọc); ngược lại chỉ các id đó."""
        by_group: dict[str, dict[int, str]] = {}
        for pid, link in ctx.snapshot.variations.items():
            if (scope is not None and pid not in scope) or pid in ctx.eligible_ids or pid in ctx.hidden_in_plans:
                continue
            if _hideable(link):
                by_group.setdefault(link.group_key, {})[pid] = link.pancake_variation_id or ""
        for group_key in sorted(by_group):
            targets = by_group[group_key]
            try:
                self._hide_group(ctx, group_key, targets)
            except Exception as exc:  # noqa: BLE001 — một nhóm lỗi không chặn cả lượt
                message = _error_message(exc)
                logger.warning("pancake_pos: ẩn nhóm %s lỗi: %s", group_key, message)
                ctx.report.add_error(group_key, [f"#{pid}" for pid in sorted(targets)], message)

    def _hide_group(self, ctx: RunContext, group_key: str, targets: dict[int, str]) -> None:
        product_link = ctx.snapshot.products.get(group_key)
        product_id = (product_link.pancake_product_id if product_link else None) or next(
            (ctx.index.product_id_of(v) for v in targets.values() if ctx.index.product_id_of(v)), None
        )
        ctx.report.add_sample({
            "group_key": group_key,
            "action": ACTION_HIDE,
            "pancake_product_id": product_id,
            "variation_ids": sorted(targets.values()),
        })
        if not ctx.dry_run:
            if product_id:
                product = self._gateway.get_product(product_id)
                if product is None:
                    raise SyncGroupError(f"Không đọc được sản phẩm Pancake {product_id} để ẩn mẫu mã")
                hide_ids = set(targets.values())
                if any(v.id in hide_ids and not v.is_hidden for v in product.variations):
                    # Gửi ĐỦ mẫu mã (nguyên trạng), chỉ lật is_hidden của mẫu mã cần ẩn.
                    body = {
                        **product.raw,
                        "variations": [passthrough_variation(v, hide=v.id in hide_ids) for v in product.variations],
                    }
                    self._gateway.update_product(product_id, {"product": body})
            self._repo.save_group(
                ctx.snapshot,
                group_key=group_key,
                pancake_product_id=None,
                payload_hash=None,
                synced_at=None,
                variations=[
                    VariationWrite(
                        product_id=pid,
                        pancake_variation_id=vid or None,
                        status=LinkStatus.SKIPPED,
                        last_error=_hidden_message(ctx.reasons.get(pid, REASON_DELETED)),
                    )
                    for pid, vid in sorted(targets.items())
                ],
            )
        ctx.report.counters.hidden += len(targets)

    # ── tiện ích ────────────────────────────────────────────────────────────
    def _read_back(self, ref: str, skus: list[str]) -> tuple[str | None, dict[str, str]]:
        """Đọc lại id sau khi ghi: GET products/{ref}, SKU còn thiếu thì tìm trong
        danh sách mẫu mã (search=SKU). Trả (id sản phẩm, {SKU chuẩn hoá: id mẫu mã})."""
        ids: dict[str, str] = {}
        product = self._gateway.get_product(ref)
        product_id = product.id if product else None
        for variation in product.variations if product else ():
            for key in (variation.custom_id, variation.barcode):
                if normalize_key(key):
                    ids.setdefault(normalize_key(key), variation.id)
        for sku in skus:
            wanted = normalize_key(sku)
            if wanted in ids:
                continue
            for variation in self._gateway.iter_variations(search=sku):
                if wanted in (normalize_key(variation.custom_id), normalize_key(variation.barcode)):
                    ids[wanted] = variation.id
                    product_id = product_id or variation.product_id or None
                    break
        return product_id, ids

    @staticmethod
    def _error_write(snapshot: LinkSnapshot, product_id: int, message: str) -> VariationWrite:
        previous = snapshot.variations.get(product_id)
        return VariationWrite(
            product_id=product_id,
            pancake_variation_id=None,
            status=LinkStatus.ERROR,
            last_error=message,
            attempts=(previous.attempts if previous else 0) + 1,
        )

    def _record_error(self, group: SyncGroup, snapshot: LinkSnapshot, message: str) -> None:
        try:
            self._repo.save_group(
                snapshot,
                group_key=group.key,
                pancake_product_id=None,
                payload_hash=None,
                synced_at=None,
                variations=[self._error_write(snapshot, p.id, message) for p in group.products],
            )
        except Exception:  # noqa: BLE001 — ghi lỗi hỏng không được làm hỏng cả lượt
            logger.exception("pancake_pos: không ghi được trạng thái lỗi cho nhóm %s", group.key)
