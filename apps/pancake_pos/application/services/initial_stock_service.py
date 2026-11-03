"""Đẩy tồn đầu kỳ Hàng hoá CRM → Pancake theo (kho, SKU) (two-way-sync §7 A1).

Một lượt:
1. Ghép kho (`pancake_pos_warehouse_links`, lọc `warehouse_ids` CRM nếu có). Chưa ghép
   kho nào → dừng (fatal).
2. Tồn CRM `state='sellable'` theo (kho, SKU) + SKU đã liên kết mẫu mã — 2 câu SQL.
3. Tồn thực hiện tại trên Pancake (`variations_warehouses`): có `skus` thì tìm theo
   từng SKU, không thì duyệt toàn bộ mẫu mã của shop (phân trang).
4. `domain.initial_stock.plan_initial_stock` → dòng cần ghi (số đặt ≠ số Pancake).
5. Ghi thật: `POST variations/update_quantity` với `is_actual_remain_quantity=true`
   theo lô (mặc định 20 — PANCAKE_POS_STOCK_BATCH_SIZE, trần 100). API thật trả 500
   ổn định cho lô 100 (30/09/2026) → lô lỗi (trừ 429) được CHIA NHỎ gửi từng dòng;
   response có `results[]` từng dòng → dòng `success=false` ghi vào `line_errors`.
   Đặt tuyệt đối → chạy lại / gửi lại cho cùng kết quả. Lỗi không dừng các lô sau.
6. Nhật ký `pancake_pos_sync_runs` mode `initial_stock_dry` / `initial_stock`, report
   gồm tổng, bảng lệch, âm→0, thiếu liên kết, lỗi lô.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.utils import timezone

from apps.pancake_pos.contracts.ports import PancakePosGateway, StockReader, WarehouseReader
from apps.pancake_pos.domain.initial_stock import (
    InitialStockPlan,
    StockLine,
    WarehousePair,
    batches,
    plan_initial_stock,
)
from apps.pancake_pos.domain.mapping import normalize_key
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosError, PancakePosRateLimited
from apps.pancake_pos.models import SyncRunMode
from apps.pancake_pos.repositories import LinkRepository, WarehouseLinkRepository

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 20
MAX_BATCH_SIZE = 100
# Báo cáo lưu ở JSONB: ~1.500 dòng lệch (500 SKU × 3 kho) ≈ 250 KB — trần để không phình.
MAX_DIFFS = 5000
MAX_LISTED = 1000


class InitialStockError(PancakePosError):
    """Lỗi chặn cả lượt (chưa ghép kho, cấu hình...)."""


@dataclass
class InitialStockReport:
    run_id: int
    dry_run: bool
    scope: dict[str, Any] = field(default_factory=dict)
    warehouses: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)
    diffs: list[dict[str, Any]] = field(default_factory=list)
    negatives: list[dict[str, Any]] = field(default_factory=list)
    missing_links: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)  # lỗi cả lô (không chia được)
    line_errors: list[dict[str, Any]] = field(default_factory=list)  # lỗi từng dòng
    fatal: str = ""

    def add_line_error(self, line: StockLine, message: str) -> None:
        self.totals["failed"] = self.totals.get("failed", 0) + 1
        if len(self.line_errors) < MAX_LISTED:
            self.line_errors.append({
                "sku": line.sku,
                "warehouse_name": line.warehouse_name,
                "variation_id": line.variation_id,
                "push_qty": line.push_qty,
                "message": message,
            })

    def counters(self) -> dict[str, int]:
        """Cột đếm của `pancake_pos_sync_runs`: `updated` = dòng (kho, SKU) cần ghi
        (dry-run) / đã ghi (thật), `skipped` = SKU có tồn mà chưa liên kết, `errors` =
        dòng thuộc lô lỗi."""
        return {
            "created": 0,
            "updated": self.totals.get("written" if not self.dry_run else "to_write", 0),
            "linked": 0,
            "skipped": self.totals.get("missing_link", 0),
            "conflicts": 0,
            "errors": self.totals.get("failed", 0),
            "hidden": 0,
        }

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": "initial_stock",
            "dry_run": self.dry_run,
            "scope": self.scope,
            "warehouses": self.warehouses,
            "totals": self.totals,
            "diffs": self.diffs,
            "negatives": self.negatives,
            "missing_links": self.missing_links,
            "errors": self.errors,
            "line_errors": self.line_errors,
            **({"fatal": self.fatal} if self.fatal else {}),
        }


def normalize_scope(warehouse_ids: Sequence[int] | None, skus: Sequence[str] | None) -> dict[str, Any]:
    """Phạm vi chuẩn hoá (so được giữa dry-run và lượt ghi): None = tất cả."""
    return {
        "warehouse_ids": sorted({int(i) for i in warehouse_ids}) if warehouse_ids else None,
        "skus": sorted({normalize_key(s) for s in skus if normalize_key(s)}) if skus else None,
    }


class InitialStockPushService:
    def __init__(
        self,
        *,
        gateway: PancakePosGateway,
        stock_reader: StockReader,
        warehouse_reader: WarehouseReader,
        warehouse_links: WarehouseLinkRepository | None = None,
        runs: LinkRepository | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        clock: Callable[[], datetime] = timezone.now,
    ) -> None:
        self._gateway = gateway
        self._stock = stock_reader
        self._warehouses = warehouse_reader
        self._links = warehouse_links or WarehouseLinkRepository()
        self._runs = runs or LinkRepository()
        self._batch_size = min(max(int(batch_size), 1), MAX_BATCH_SIZE)
        self._clock = clock

    def run(
        self,
        *,
        dry_run: bool,
        warehouse_ids: Sequence[int] | None = None,
        skus: Sequence[str] | None = None,
        user_id: int | None = None,
        run_id: int | None = None,
    ) -> InitialStockReport:
        mode = SyncRunMode.INITIAL_STOCK_DRY_RUN if dry_run else SyncRunMode.INITIAL_STOCK
        run_id = run_id or self._runs.start_run(mode, user_id)
        scope = normalize_scope(warehouse_ids, skus)
        report = InitialStockReport(run_id=run_id, dry_run=dry_run, scope=scope)
        try:
            self._run(report, dry_run=dry_run, scope=scope)
        except Exception as exc:
            report.fatal = str(exc) if isinstance(exc, PancakePosError) else type(exc).__name__
            logger.exception("pancake_pos: lượt tồn đầu kỳ #%s dừng giữa chừng", run_id)
            raise
        finally:
            self._runs.finish_run(
                run_id, counters=report.counters(), report=report.as_json(), finished_at=self._clock()
            )
        return report

    # ── các bước ────────────────────────────────────────────────────────────
    def _pairs(self, warehouse_ids: list[int] | None) -> list[WarehousePair]:
        names = {w.id: w.name for w in self._warehouses.list_warehouses()}
        links = [
            link for link in self._links.all_links()
            if warehouse_ids is None or link.warehouse_id in warehouse_ids
        ]
        if not links:
            raise InitialStockError("Chưa ghép kho CRM ↔ Pancake (hoặc kho đã chọn chưa được ghép)")
        return [
            WarehousePair(
                warehouse_id=link.warehouse_id,
                warehouse_name=names.get(link.warehouse_id, f"#{link.warehouse_id}"),
                pancake_warehouse_id=link.pancake_warehouse_id,
                pancake_warehouse_name=link.pancake_warehouse_name,
            )
            for link in links
        ]

    def _pancake_quantities(self, variation_ids: set[str], skus: list[str] | None) -> dict[str, dict[str, int]]:
        """Tồn thực trên Pancake của các mẫu mã đã liên kết. Có `skus` → tìm theo SKU
        (vài request); không → duyệt toàn bộ (≈ số mẫu mã / 100 request)."""
        found: dict[str, dict[str, int]] = {}
        searches: list[str | None] = list(skus) if skus else [None]
        for search in searches:
            for variation in self._gateway.iter_variations(search=search):
                if variation.id in variation_ids:
                    found[variation.id] = dict(variation.warehouse_quantities)
        return found

    def _run(self, report: InitialStockReport, *, dry_run: bool, scope: dict[str, Any]) -> None:
        pairs = self._pairs(scope["warehouse_ids"])
        report.warehouses = [
            {
                "warehouse_id": p.warehouse_id,
                "warehouse_name": p.warehouse_name,
                "pancake_warehouse_id": p.pancake_warehouse_id,
                "pancake_warehouse_name": p.pancake_warehouse_name,
            }
            for p in pairs
        ]
        skus = scope["skus"]
        stock = self._stock.sellable_stock(warehouse_ids=[p.warehouse_id for p in pairs], skus=skus)
        linked = self._stock.linked_variations(skus=skus)
        pancake = self._pancake_quantities({v.pancake_variation_id for v in linked}, skus)
        plan = plan_initial_stock(warehouses=pairs, stock=stock, linked=linked, pancake_quantities=pancake)
        self._fill(report, plan)

        chunks = batches(plan.to_write, self._batch_size)
        report.totals.update({"batches": len(chunks), "batch_size": self._batch_size, "split_batches": 0,
                              "written": 0, "failed": 0})
        if dry_run:
            return
        for number, chunk in enumerate(chunks, start=1):
            self._write_chunk(report, number, len(chunks), chunk)

    # ── ghi ─────────────────────────────────────────────────────────────────
    def _send(self, report: InitialStockReport, chunk: list[StockLine]) -> None:
        """Gửi một lô; dòng Pancake báo `success=false` → lỗi dòng. Lỗi cả request → raise."""
        failures = self._gateway.update_quantities([line.as_entry() for line in chunk], actual=True)
        failed: dict[int, str] = {}
        for failure in failures:
            for idx, line in enumerate(chunk):
                if line.variation_id == failure.get("variation_id") and failure.get("warehouse_id") in (
                    "", None, line.pancake_warehouse_id,
                ):
                    failed.setdefault(idx, failure.get("message") or "success=false")
        for idx, line in enumerate(chunk):
            if idx in failed:
                report.add_line_error(line, failed[idx])
            else:
                report.totals["written"] += 1

    def _write_chunk(self, report: InitialStockReport, number: int, total: int, chunk: list[StockLine]) -> None:
        try:
            self._send(report, chunk)
            return
        except PancakePosError as exc:
            if len(chunk) == 1 or isinstance(exc, PancakePosRateLimited):
                # 429 hết lượt retry: chia nhỏ chỉ làm tăng số request → bỏ cả lô.
                logger.warning("pancake_pos: lô tồn đầu kỳ %d/%d lỗi: %s", number, total, exc)
                report.errors.append({
                    "batch": number,
                    "size": len(chunk),
                    "skus": sorted({line.sku for line in chunk})[:20],
                    "message": str(exc),
                })
                for line in chunk:
                    report.add_line_error(line, str(exc))
                return
            logger.warning("pancake_pos: lô tồn đầu kỳ %d/%d lỗi (%s) — gửi từng dòng", number, total, exc)
        report.totals["split_batches"] += 1
        for line in chunk:
            try:
                self._send(report, [line])
            except PancakePosError as exc:
                report.add_line_error(line, str(exc))

    @staticmethod
    def _fill(report: InitialStockReport, plan: InitialStockPlan) -> None:
        to_write = plan.to_write
        negatives = plan.negatives
        report.totals = {
            "lines": len(plan.lines),
            "skus": len({line.product_id for line in plan.lines}),
            "to_write": len(to_write),
            "unchanged": len(plan.lines) - len(to_write),
            "unknown_on_pancake": plan.unknown_on_pancake,
            "negative": len(negatives),
            "missing_link": len(plan.missing_links),
            "crm_total": sum(line.push_qty for line in plan.lines),
            "pancake_total": sum(line.pancake_qty or 0 for line in plan.lines),
        }
        report.diffs = [line.as_json() for line in to_write[:MAX_DIFFS]]
        report.negatives = [
            {"sku": line.sku, "warehouse_id": line.warehouse_id, "warehouse_name": line.warehouse_name,
             "crm_qty": line.crm_qty}
            for line in negatives[:MAX_LISTED]
        ]
        report.missing_links = [m.as_json() for m in plan.missing_links[:MAX_LISTED]]
