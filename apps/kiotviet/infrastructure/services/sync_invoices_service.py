"""
Service đồng bộ hóa đơn giữa 2 KiotVietChannel — ENGINE DUY NHẤT dùng chung cho:
  - luồng reconcile hàng giờ (tasks.py / reconcile.py, mode ``reconcile_sync``),
  - API views (urls.py),
  - CLI ``manage.py sync_invoices_between_channels`` (adapter mỏng, BE-1 audit 2026-07).

Trước 2026-07 command là monolith fork tự chứa (revert bc2dd1e5) — mọi bugfix
tiền/hóa đơn phải sửa 2 nơi. Hành vi CLI được ghim bằng characterization tests
(test_sync_between_channels_command.py) trước khi hợp nhất; các khác biệt thật
của monolith được giữ qua option tường minh (``exact_mirror``) hoặc chuẩn hóa có
chủ đích với WHY-comment tại chỗ (cancelled==2 theo e59650dc, public list thay
web list, PUT giá ship 1 lần theo 980e8602, check-price chỉ so total theo a782572d).

So với chạy trong BaseCommand chỉ thay:
  - self.stdout.write(...) → self._log("stdout", ...)
  - self.stderr.write(...) → self._log("stderr", ...)
  - self.style.X(...)      → plain string (frontend tự render màu)
  - raise CommandError(...) → raise SyncError(...)
"""
from __future__ import annotations

import json
import re as _re
import time
from dataclasses import dataclass, field
from collections.abc import Callable

from django.utils.dateparse import parse_datetime

from apps.kiotviet.infrastructure.services import datetime_utils as dt_utils
from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import (
    _kiotviet_request,
    _kiotviet_request_with_retry,
)
from apps.kiotviet.infrastructure.services.invoice_mutations_push import delete_invoice_by_id
from apps.kiotviet.infrastructure.services.invoice_payload_builder import build_invoice_details
from apps.kiotviet.infrastructure.services.invoice_sync_queries import (
    _extract_invoice_items,
    get_invoice_by_code,
)
from apps.kiotviet.models import KiotVietChannel, channel_vat_tax_id


from ._reconcile_decision import decide_reconcile_action
from ._sync_service_shared import (  # re-exported for callers
    KIOTVIET_LIST_MAX_PAGE_SIZE,
    SyncError,
    _SkipInvoice,
)
from ._sync_service_check_mixin import SyncCheckMixin
from ._sync_service_purge_mixin import SyncPurgeMixin
from ._sync_service_sync_mixin import SyncCreateMixin

LogCallback = Callable[[str, str], None]


def _norm_price(v):
    """int(float(v)) hoặc None — chuẩn hóa giá ship để so A/B (tách từ closure trong run())."""
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


@dataclass
class _RunContext:
    """Trạng thái chia sẻ giữa các bước của ``SyncInvoicesService.run()``.

    Tách từ các biến local của run() monolith (audit C2/P1-1); tên field giữ nguyên
    tên biến cũ để dễ đối chiếu. Nhóm "mode sync" chỉ được điền ở
    ``_prepare_sync_mode`` (reconcile_sync chỉ dùng ``synced_source_map``).
    """

    options: dict
    source_channel: KiotVietChannel
    target_channel: KiotVietChannel
    from_dt: str
    to_dt: str | None
    source_branch_id: int
    target_branch_id: int
    page_size: int
    limit: int
    dry_run: bool
    sleep_pages: float
    sleep_pushes: float
    purge_target: bool
    yes_flag: bool
    summary: dict
    failed_invoices: list[str] = field(default_factory=list)
    # --- mode sync (điền ở _prepare_sync_mode) ---
    specific_codes: list[str] = field(default_factory=list)
    update_sale_channel: bool = False
    include_empty_sale_channel: bool = False
    update_delivery_detail: bool = False
    channel_new_to_old: dict[str, str] = field(default_factory=dict)
    filter_new_sale_channel_ids: set[str] = field(default_factory=set)
    synced_source_codes: set[str] = field(default_factory=set)
    synced_source_map: dict[str, dict] = field(default_factory=dict)


class SyncInvoicesService(SyncPurgeMixin, SyncCheckMixin, SyncCreateMixin):
    """
    Service đồng bộ hóa đơn giữa 2 KiotVietChannel.

    Sử dụng:
        service = SyncInvoicesService(log_callback=my_logger)
        result = service.run(
            source_kiotviet_channel_id=1,
            source_branch_id="231174",
            target_kiotviet_channel_id=2,
            target_branch_id="415922",
            from_dt="2026-04-01 00:00:00",
            to_dt="2026-04-09 23:59:59",
            ...
        )
    """

    def __init__(self, log_callback: LogCallback | None = None):
        self._log_cb = log_callback or (lambda channel, msg: None)
        self._summary: dict = {}
        self._current_code: str = ""
        self._current_fallbacks: list[str] = []

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------
    def _log(self, channel: str, msg: str = "") -> None:
        try:
            self._log_cb(channel, msg)
        except Exception:  # noqa: BLE001
            pass

    def _stdout(self, msg: str = "") -> None:
        self._log("stdout", str(msg))

    def _stderr(self, msg: str = "") -> None:
        self._log("stderr", str(msg))

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------
    def run(self, **options) -> dict:
        """Điều phối các bước của một lần sync (audit C2/P1-1 — tách từ run() 868 dòng).

        1. ``_parse_run_options``   : validate options → ``_RunContext`` + summary rỗng.
        2. ``_run_reconcile_sync``  : mode reconcile hàng giờ (prescan B → paginate A → so
                                       khớp → tạo/tạo lại), trả summary sớm.
        3. ``_run_maintenance_mode``: các mode bảo trì độc lập (purge/check/compare/
                                       update-status/...), trả summary sớm nếu có mode.
        4. ``_prepare_sync_mode``   : purge target (nếu có), resolve --channel-ids,
                                       validate update-only, pre-scan B.
        5. ``_run_sync_loop``       : duyệt A (--invoice-codes hoặc paginate) → ``_process_one``.
        6. ``_report_sync_summary`` : BREAKDOWN + dump JSON summary.
        Hành vi/log/giá trị trả về giữ nguyên như bản monolith.
        """
        ctx = self._parse_run_options(options)

        if bool(options.get("reconcile_sync")):
            return self._run_reconcile_sync(ctx)

        handled = self._run_maintenance_mode(ctx)
        if handled is not None:
            return handled

        self._prepare_sync_mode(ctx)
        self._run_sync_loop(ctx)
        return self._report_sync_summary(ctx)

    # ------------------------------------------------------------------
    # Bước 1 — parse/validate options, khởi tạo summary + cờ exact_mirror
    # ------------------------------------------------------------------
    def _parse_run_options(self, options: dict) -> _RunContext:
        """Validate options, load 2 channel, chuẩn hóa from/to, kẹp page_size, dựng summary."""
        source_id = int(options["source_kiotviet_channel_id"])
        target_id = int(options["target_kiotviet_channel_id"])
        if source_id == target_id:
            raise SyncError("source và target không được trùng nhau.")

        try:
            source_channel = KiotVietChannel.objects.get(id=source_id, deleted_at__isnull=True)
        except KiotVietChannel.DoesNotExist as exc:
            raise SyncError(f"Không tìm thấy KiotVietChannel source id={source_id}") from exc

        try:
            target_channel = KiotVietChannel.objects.get(id=target_id, deleted_at__isnull=True)
        except KiotVietChannel.DoesNotExist as exc:
            raise SyncError(f"Không tìm thấy KiotVietChannel target id={target_id}") from exc

        from_dt = self._normalize_dt_arg(options.get("from_dt"), "from")
        to_dt = self._normalize_dt_arg(options.get("to_dt"), "to") if options.get("to_dt") else None

        source_branch_id_raw = str(options["source_branch_id"]).strip()
        target_branch_id_raw = str(options["target_branch_id"]).strip()
        try:
            source_branch_id = int(source_branch_id_raw)
        except ValueError as exc:
            raise SyncError(f"source-branch-id không hợp lệ: {source_branch_id_raw}") from exc
        try:
            target_branch_id = int(target_branch_id_raw)
        except ValueError as exc:
            raise SyncError(f"target-branch-id không hợp lệ: {target_branch_id_raw}") from exc

        # Chặn tại cửa: KiotViet cắt pageSize > 100 trong im lặng, mọi vòng phân trang dừng theo
        # "trang ngắn hơn số đã xin" sẽ thoát sớm. Kẹp ngay đây để --page-size 500 không phá.
        page_size = min(
            max(int(options.get("page_size") or 100), 1), KIOTVIET_LIST_MAX_PAGE_SIZE
        )
        limit = int(options.get("limit") or 0)
        dry_run = bool(options.get("dry_run"))
        sleep_pages = float(options.get("sleep_between_pages") or 0.5)
        sleep_pushes = float(options.get("sleep_between_pushes") or 0.3)
        purge_target = bool(options.get("purge_target"))
        yes_flag = bool(options.get("yes"))

        summary = {
            "source_channel_id": source_id,
            "source_branch_id": source_branch_id,
            "target_channel_id": target_id,
            "target_branch_id": target_branch_id,
            "from": from_dt,
            "to": to_dt,
            "dry_run": dry_run,
            "purge_target": purge_target,
            "purged": 0,
            "purge_errors": 0,
            "pages": 0,
            "fetched": 0,
            "created": 0,
            "clean": 0,
            "fallback": 0,
            "skipped": 0,
            "errors": 0,
            "fallback_details": [],
            "missing_sale_channel_codes": [],
            "unmapped_sale_channel_codes": [],
            "unmapped_sold_by_codes": [],
        }
        self._summary = summary

        from apps.sysconfig import sysconfig as _sysconfig
        # exact_mirror: mặc định theo cài đặt hệ thống (luồng reconcile hàng giờ). CLI
        # adapter (sync_invoices_between_channels) truyền exact_mirror=False cứng vì bản
        # command lịch sử (snapshot bc2dd1e5) không đọc cờ RECONCILE_DELIVERY_EXACT_MIRROR —
        # giữ nguyên hành vi patch-status của CLI qua đợt hợp nhất engine (BE-1).
        if options.get("exact_mirror") is not None:
            self._exact_mirror = bool(options.get("exact_mirror"))
        else:
            self._exact_mirror = bool(_sysconfig.get("RECONCILE_DELIVERY_EXACT_MIRROR"))

        return _RunContext(
            options=options,
            source_channel=source_channel,
            target_channel=target_channel,
            from_dt=from_dt,
            to_dt=to_dt,
            source_branch_id=source_branch_id,
            target_branch_id=target_branch_id,
            page_size=page_size,
            limit=limit,
            dry_run=dry_run,
            sleep_pages=sleep_pages,
            sleep_pushes=sleep_pushes,
            purge_target=purge_target,
            yes_flag=yes_flag,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Bước 2 — mode reconcile_sync (tasks.py / reconcile.py hàng giờ)
    # ------------------------------------------------------------------
    def _run_reconcile_sync(self, ctx: _RunContext) -> dict:
        """reconcile_sync: prescan B → paginate A → ``_reconcile_one`` từng đơn → tổng kết."""
        summary = ctx.summary
        for k in ("reconcile_created", "reconcile_recreated", "reconcile_skipped", "reconcile_errors"):
            summary[k] = 0

        ctx.synced_source_map = self._prescan_target(
            target_channel=ctx.target_channel,
            target_branch_id=ctx.target_branch_id,
            from_dt=ctx.from_dt,
            to_dt=ctx.to_dt,
            page_size=ctx.page_size,
            sleep_pages=ctx.sleep_pages,
        )

        self._paginate_source(
            source_channel=ctx.source_channel,
            source_branch_id=ctx.source_branch_id,
            from_dt=ctx.from_dt,
            to_dt=ctx.to_dt,
            page_size=ctx.page_size,
            sleep_pages=ctx.sleep_pages,
            sleep_pushes=ctx.sleep_pushes,
            limit=ctx.limit,
            on_invoice=lambda invoice_detail: self._reconcile_one(ctx, invoice_detail),
            summary=summary,
        )
        self._stdout(
            f"reconcile_sync done: created={summary['reconcile_created']}, "
            f"recreated={summary['reconcile_recreated']}, "
            f"skipped={summary['reconcile_skipped']}, errors={summary['reconcile_errors']}"
        )
        return summary

    def _reconcile_one(self, ctx: _RunContext, invoice_detail: dict) -> None:
        """So khớp 1 đơn A với mirror B (decide_reconcile_action) → skip/create/delete_recreate."""
        summary = ctx.summary
        target_channel = ctx.target_channel
        code = self._first_non_empty(invoice_detail.get("code"), invoice_detail.get("Code"))
        inv_del = invoice_detail.get("invoiceDelivery") or invoice_detail.get("InvoiceDelivery") or {}
        inv_del = inv_del if isinstance(inv_del, dict) else {}
        a = {
            "_code": code,
            "invoice_status": invoice_detail.get("status") or invoice_detail.get("Status"),
            "delivery_status": inv_del.get("status") or inv_del.get("Status"),
            "total": self._first_non_empty(invoice_detail.get("total"), invoice_detail.get("Total")),
            "delivery_price": (
                inv_del.get("price") if inv_del.get("price") is not None else inv_del.get("Price")
            ),
        }

        # Guard: A đã hủy thì bỏ qua hoàn toàn (không tạo, không xóa B) — mirror guard
        # của _process_one, tránh reconcile "hồi sinh" hóa đơn đã hủy trên A.
        status_value = str(
            invoice_detail.get("statusValue") or invoice_detail.get("StatusValue") or ""
        ).strip().lower()
        if (
            a["invoice_status"] == 2
            or status_value in {"đã hủy", "đã huỷ", "cancelled"}
            or a["delivery_status"] == 6
        ):
            summary["reconcile_skipped"] += 1
            self._stdout(f"  - skip {code}: đã hủy")
            return

        b_info = ctx.synced_source_map.get(code)
        b = None if not b_info else {
            "invoice_status": b_info.get("invoiceStatus"),
            "delivery_status": b_info.get("deliveryStatus"),
            "total": b_info.get("total"),
            "delivery_price": b_info.get("deliveryPrice"),
        }
        try:
            action = decide_reconcile_action(a=a, b=b, exact_mirror=self._exact_mirror)
            if action == "skip":
                summary["reconcile_skipped"] += 1
                self._stdout(f"  - skip {code}: đã khớp B")
                return
            if action == "delete_recreate":
                # An toàn: chỉ xóa B khi chắc chắn dựng lại được từ A. Nếu build_invoice_details
                # rỗng (thiếu invoiceDetails, vd persistent _SkipInvoice) thì B sẽ bị mất vĩnh viễn
                # nếu xóa trước — bỏ qua, KHÔNG xóa, để lần chạy sau còn cơ hội retry.
                if not build_invoice_details(
                    invoice_detail, vat_tax_id=channel_vat_tax_id(target_channel)
                ):
                    summary["reconcile_skipped"] += 1
                    self._stdout(
                        f"  - skip {code}: A không dựng được invoiceDetails, "
                        f"KHÔNG xóa B để tránh mất dữ liệu"
                    )
                    return
                if not ctx.dry_run:
                    delete_invoice_by_id(
                        invoice_id=int(b_info["id"]), order=None, channel=target_channel,
                    )
            if not ctx.dry_run:
                self._sync_invoice(
                    source_invoice=invoice_detail,
                    source_channel=ctx.source_channel,
                    target_channel=target_channel,
                    target_branch_id=ctx.target_branch_id,
                    dry_run=False,
                )
            counter_key = "reconcile_recreated" if action == "delete_recreate" else "reconcile_created"
            summary[counter_key] += 1
            self._stdout(f"  - {action} {code}")
        except Exception as exc:  # noqa: BLE001
            summary["reconcile_errors"] += 1
            self._stderr(f"  - reconcile failed {code}: {exc}")

    # ------------------------------------------------------------------
    # Bước 3 — các mode bảo trì độc lập (mỗi mode return summary ngay)
    # ------------------------------------------------------------------
    def _run_maintenance_mode(self, ctx: _RunContext) -> dict | None:
        """Thứ tự ưu tiên giữ nguyên bản cũ: purge_wrong_date → check_missing →
        delete_target_entries → check_price → check_status → compare_invoice →
        update_status → purge_cancelled → purge_duplicates. Trả None nếu không mode nào bật."""
        options = ctx.options
        summary = ctx.summary
        source_channel = ctx.source_channel
        target_channel = ctx.target_channel
        source_branch_id = ctx.source_branch_id
        target_branch_id = ctx.target_branch_id
        from_dt = ctx.from_dt
        to_dt = ctx.to_dt
        page_size = ctx.page_size
        dry_run = ctx.dry_run
        yes_flag = ctx.yes_flag
        sleep_pages = ctx.sleep_pages
        sleep_pushes = ctx.sleep_pushes

        purge_wrong_date = bool(options.get("purge_wrong_date"))
        if purge_wrong_date:
            self._purge_wrong_date(
                source_channel=source_channel,
                source_branch_id=int(source_branch_id),
                target_channel=target_channel,
                target_branch_id=int(target_branch_id),
                from_dt=from_dt,
                to_dt=to_dt,
                page_size=page_size,
                dry_run=dry_run,
                yes_flag=yes_flag,
                sleep_pages=sleep_pages,
                sleep_between=sleep_pushes,
            )
            return summary

        check_missing = bool(options.get("check_missing"))
        if check_missing:
            cm_result = self._check_missing(
                source_channel=source_channel,
                source_branch_id=int(source_branch_id),
                target_channel=target_channel,
                target_branch_id=int(target_branch_id),
                from_dt=from_dt,
                to_dt=to_dt,
                page_size=page_size,
                sleep_pages=sleep_pages,
            )
            missing = cm_result["missing"]
            if missing:
                self._stdout(f"\nMissing invoices ({len(missing)}): {','.join(missing)}")
                self._stdout(
                    "\nĐể sync lại, chạy command với:\n"
                    f'  --invoice-codes "{",".join(missing)}"'
                )
            else:
                self._stdout("\nKhông thiếu đơn nào.")
            summary["missing_codes"] = missing
            summary["extras_to_delete"] = cm_result["extras_to_delete"]
            summary["duplicates_to_delete"] = cm_result["duplicates_to_delete"]
            return summary

        delete_target_entries = options.get("delete_target_entries")
        if delete_target_entries:
            result = self._delete_target_by_ids(
                target_channel=target_channel,
                entries=list(delete_target_entries),
                sleep_between=sleep_pushes,
                dry_run=dry_run,
            )
            summary["delete_target_result"] = result
            return summary

        check_price = bool(options.get("check_price"))
        if check_price:
            price_mismatches = self._check_price(
                source_channel=source_channel,
                source_branch_id=int(source_branch_id),
                target_channel=target_channel,
                target_branch_id=int(target_branch_id),
                from_dt=from_dt,
                to_dt=to_dt,
                page_size=page_size,
                sleep_pages=sleep_pages,
            )
            summary["price_mismatch_codes"] = price_mismatches
            return summary

        check_status = bool(options.get("check_status"))
        if check_status:
            status_mismatches = self._check_status(
                source_channel=source_channel,
                source_branch_id=int(source_branch_id),
                target_channel=target_channel,
                target_branch_id=int(target_branch_id),
                from_dt=from_dt,
                to_dt=to_dt,
                page_size=page_size,
                sleep_pages=sleep_pages,
            )
            summary["status_mismatch_codes"] = status_mismatches
            return summary

        compare_code = str(options.get("compare_invoice") or "").strip()
        if compare_code:
            self._compare_invoice(
                source_code=compare_code,
                source_channel=source_channel,
                source_branch_id=int(source_branch_id),
                target_channel=target_channel,
                target_branch_id=int(target_branch_id),
                from_dt=from_dt,
                to_dt=to_dt,
                page_size=page_size,
                sleep_pages=sleep_pages,
            )
            summary["compare_invoice"] = compare_code
            return summary

        # --update-status: mode vận hành tay port từ command monolith khi hợp nhất engine
        # (BE-1). Từng bị gỡ khỏi service ở c7ed08fb vì "dead" — đúng ở thời điểm 2 stack
        # tách đôi (CLI tự chứa), nay CLI chạy chung engine nên mode sống lại ở đây.
        update_status = bool(options.get("update_status"))
        if update_status:
            us_codes_arg = str(options.get("invoice_codes") or "").strip()
            us_codes = (
                [c.strip() for c in us_codes_arg.split(",") if c.strip()]
                if us_codes_arg else None
            )
            us_target_arg = str(options.get("target_invoice_codes") or "").strip()
            us_target_codes = (
                [c.strip() for c in us_target_arg.split(",") if c.strip()]
                if us_target_arg else None
            )
            self._update_status(
                source_channel=source_channel,
                source_branch_id=int(source_branch_id),
                target_channel=target_channel,
                target_branch_id=int(target_branch_id),
                from_dt=from_dt,
                to_dt=to_dt,
                page_size=page_size,
                sleep_pages=sleep_pages,
                sleep_between=sleep_pushes,
                dry_run=dry_run,
                specific_codes=us_codes,
                target_specific_codes=us_target_codes,
            )
            return summary

        purge_cancelled = bool(options.get("purge_cancelled"))
        if purge_cancelled:
            self._purge_cancelled(
                source_channel=source_channel,
                source_branch_id=int(source_branch_id),
                target_channel=target_channel,
                target_branch_id=int(target_branch_id),
                from_dt=from_dt,
                to_dt=to_dt,
                page_size=page_size,
                dry_run=dry_run,
                yes_flag=yes_flag,
                sleep_pages=sleep_pages,
                sleep_between=sleep_pushes,
            )
            return summary

        purge_duplicates = bool(options.get("purge_duplicates"))
        if purge_duplicates:
            self._purge_duplicates(
                target_channel=target_channel,
                target_branch_id=int(target_branch_id),
                from_dt=from_dt,
                to_dt=to_dt,
                page_size=page_size,
                dry_run=dry_run,
                yes_flag=yes_flag,
                sleep_pages=sleep_pages,
                sleep_between=sleep_pushes,
            )
            return summary

        return None

    # ------------------------------------------------------------------
    # Bước 4 — chuẩn bị mode sync: purge target, --channel-ids, validate, pre-scan B
    # ------------------------------------------------------------------
    def _prepare_sync_mode(self, ctx: _RunContext) -> None:
        """Điền các field mode sync vào ctx (specific_codes, filter, cờ update-only,
        synced_source_map/codes) và ghi các key summary của mode sync."""
        options = ctx.options
        summary = ctx.summary

        invoice_codes_arg = str(options.get("invoice_codes") or "").strip()
        specific_codes = (
            [c.strip() for c in invoice_codes_arg.split(",") if c.strip()]
            if invoice_codes_arg else []
        )
        ctx.specific_codes = specific_codes

        if ctx.purge_target:
            self._purge_target_branch(
                target_channel=ctx.target_channel,
                target_branch_id=int(ctx.target_branch_id),
                from_dt=ctx.from_dt,
                to_dt=ctx.to_dt,
                page_size=ctx.page_size,
                dry_run=ctx.dry_run,
                yes_flag=ctx.yes_flag,
                sleep_pages=ctx.sleep_pages,
                sleep_between=ctx.sleep_pushes,
                summary=summary,
                only_source_codes=set(specific_codes) if specific_codes else None,
            )

        update_sale_channel = bool(options.get("update_sale_channel"))
        include_empty_sale_channel = bool(options.get("include_empty_sale_channel"))
        ctx.update_sale_channel = update_sale_channel
        ctx.include_empty_sale_channel = include_empty_sale_channel
        self._resolve_channel_filter(ctx)
        filter_new_sale_channel_ids = ctx.filter_new_sale_channel_ids

        if update_sale_channel and not filter_new_sale_channel_ids and not include_empty_sale_channel:
            raise SyncError(
                "--update-sale-channel yêu cầu --channel-ids hoặc --include-empty-sale-channel."
            )
        if include_empty_sale_channel and not update_sale_channel:
            raise SyncError("--include-empty-sale-channel chỉ dùng kèm --update-sale-channel.")
        update_delivery_detail = bool(options.get("update_delivery_detail"))
        ctx.update_delivery_detail = update_delivery_detail

        update_only_modes = [update_sale_channel, update_delivery_detail]
        if sum(bool(m) for m in update_only_modes) > 1:
            raise SyncError(
                "Chỉ dùng MỘT trong các chế độ update-only "
                "(--update-sale-channel / --update-delivery-detail)."
            )

        summary["include_empty_sale_channel"] = include_empty_sale_channel
        summary["updated_cleared"] = 0
        summary["update_sale_channel_mode"] = update_sale_channel
        summary["update_delivery_detail_mode"] = update_delivery_detail
        summary["filter_channel_ids"] = sorted(filter_new_sale_channel_ids)
        summary["updated"] = 0
        summary["delivery_detail_updated"] = 0
        summary["delivery_detail_skipped_same"] = 0
        summary["update_not_found_on_b"] = []

        # Pre-scan B
        if specific_codes and not update_sale_channel and not update_delivery_detail:
            self._stdout("Skip pre-scan (--invoice-codes mode)")
        else:
            ctx.synced_source_map = self._prescan_target(
                target_channel=ctx.target_channel,
                target_branch_id=ctx.target_branch_id,
                from_dt=ctx.from_dt,
                to_dt=ctx.to_dt,
                page_size=ctx.page_size,
                sleep_pages=ctx.sleep_pages,
            )
            ctx.synced_source_codes = set(ctx.synced_source_map)

    def _resolve_channel_filter(self, ctx: _RunContext) -> None:
        """--channel-ids: lọc A theo saleChannelId của source_channel, map sang target_channel
        qua bảng ChannelKiotvietMapping. Điền ctx.filter_new_sale_channel_ids / channel_new_to_old."""
        source_channel = ctx.source_channel
        target_channel = ctx.target_channel
        channel_ids_arg = str(ctx.options.get("channel_ids") or "").strip()
        channel_new_to_old: dict[str, str] = ctx.channel_new_to_old
        filter_new_sale_channel_ids: set[str] = ctx.filter_new_sale_channel_ids
        if not channel_ids_arg:
            return
        from apps.channels_core.models import ChannelKiotvietMapping
        ids = [x.strip() for x in channel_ids_arg.split(",") if x.strip()]
        source_mappings = (
            ChannelKiotvietMapping.objects
            .filter(
                channel_id__in=ids,
                channel__deleted_at__isnull=True,
                kiotviet_channel=source_channel,
            )
            .only("channel_id", "external_sale_channel_id")
        )
        target_by_channel = {
            m.channel_id: (m.external_sale_channel_id or "").strip()
            for m in ChannelKiotvietMapping.objects
            .filter(
                channel_id__in=ids,
                channel__deleted_at__isnull=True,
                kiotviet_channel=target_channel,
            )
            .only("channel_id", "external_sale_channel_id")
        }
        for m in source_mappings:
            src_ext = (m.external_sale_channel_id or "").strip()
            if not src_ext:
                continue
            filter_new_sale_channel_ids.add(src_ext)
            tgt_ext = target_by_channel.get(m.channel_id, "")
            if tgt_ext:
                channel_new_to_old[src_ext] = tgt_ext
        self._stdout(
            f"Channel filter: source_ext_ids={sorted(filter_new_sale_channel_ids)} "
            f"map_source_to_target={channel_new_to_old}"
        )
        if not filter_new_sale_channel_ids:
            raise SyncError(
                f"--channel-ids={channel_ids_arg} không resolve được mapping nào "
                f"cho source_kv id={source_channel.id}."
            )

    # ------------------------------------------------------------------
    # Bước 5 — xử lý từng đơn A (dispatch theo mode) + vòng duyệt
    # ------------------------------------------------------------------
    def _process_one(self, ctx: _RunContext, invoice_detail: dict) -> None:
        """1 đơn A: đếm fetched → lọc saleChannelId → dispatch:
        update_sale_channel | update_delivery_detail | sync tạo mới."""
        summary = ctx.summary
        source_code = self._first_non_empty(
            invoice_detail.get("code"),
            invoice_detail.get("Code"),
            invoice_detail.get("invoiceCode"),
        )
        summary["fetched"] += 1

        src_sale_channel_id = str(self._first_non_empty(
            invoice_detail.get("saleChannelId"),
            invoice_detail.get("SaleChannelId"),
        ) or "").strip()
        filter_new_sale_channel_ids = ctx.filter_new_sale_channel_ids
        include_empty_sale_channel = ctx.include_empty_sale_channel
        if filter_new_sale_channel_ids or include_empty_sale_channel:
            is_empty = not src_sale_channel_id
            in_filter = (
                bool(filter_new_sale_channel_ids)
                and src_sale_channel_id in filter_new_sale_channel_ids
            )
            allow_empty = include_empty_sale_channel and is_empty
            if not (in_filter or allow_empty):
                summary["skipped"] += 1
                self._stdout(
                    f"  - skip {source_code}: saleChannelId={src_sale_channel_id or '(empty)'} "
                    f"không thuộc filter"
                )
                return

        if ctx.update_sale_channel:
            self._update_sale_channel_one(ctx, source_code, src_sale_channel_id)
            return

        if ctx.update_delivery_detail:
            self._update_delivery_detail_one(ctx, invoice_detail, source_code)
            return

        self._sync_new_one(ctx, invoice_detail, source_code)

    def _update_sale_channel_one(
        self, ctx: _RunContext, source_code: str, src_sale_channel_id: str,
    ) -> None:
        """Mode --update-sale-channel: PUT saleChannelId (map A→B hoặc null) lên mirror B."""
        summary = ctx.summary
        failed_invoices = ctx.failed_invoices
        target_channel = ctx.target_channel
        include_empty_sale_channel = ctx.include_empty_sale_channel
        b_info = ctx.synced_source_map.get(source_code)
        if not b_info or not b_info.get("id"):
            summary["update_not_found_on_b"].append(source_code)
            self._stdout(
                f"  - skip {source_code}: chưa có bên B, không update được"
            )
            return
        clear_channel = include_empty_sale_channel and not src_sale_channel_id
        mapped_old_value: int | None
        if clear_channel:
            mapped_old_value = None
        else:
            mapped_old = ctx.channel_new_to_old.get(src_sale_channel_id)
            if not mapped_old:
                summary["errors"] += 1
                failed_invoices.append(source_code)
                self._stderr(
                    f"  - failed {source_code}: không map được saleChannelId "
                    f"source={src_sale_channel_id} sang target_kv id={target_channel.id}"
                )
                return
            try:
                mapped_old_value = int(mapped_old)
            except (TypeError, ValueError):
                summary["errors"] += 1
                failed_invoices.append(source_code)
                self._stderr(
                    f"  - failed {source_code}: target saleChannelId không phải int ({mapped_old})"
                )
                return
        b_id = int(b_info["id"])
        b_code = b_info.get("code") or ""
        display_new_value = "null" if mapped_old_value is None else str(mapped_old_value)
        if ctx.dry_run:
            if clear_channel:
                summary["updated_cleared"] += 1
            else:
                summary["updated"] += 1
            self._stdout(
                f"  - [dry-run] update {source_code} -> B:{b_code}(id={b_id}) "
                f"saleChannelId={src_sale_channel_id or '(empty)'} -> {display_new_value}"
            )
            return
        try:
            resp = _kiotviet_request(
                order=None,
                channel=target_channel,
                method="PUT",
                endpoint=f"/invoices/{b_id}",
                json={"saleChannelId": mapped_old_value},
            )
            if resp.status_code in {200, 201, 204}:
                if clear_channel:
                    summary["updated_cleared"] += 1
                else:
                    summary["updated"] += 1
                self._stdout(
                    f"  - update {source_code} -> B:{b_code}(id={b_id}) "
                    f"saleChannelId={src_sale_channel_id or '(empty)'} -> {display_new_value}"
                )
            else:
                summary["errors"] += 1
                failed_invoices.append(source_code)
                self._stderr(
                    f"  - failed {source_code}: PUT status={resp.status_code} "
                    f"body={(resp.text or '').strip()[:300]}"
                )
        except Exception as exc:  # noqa: BLE001
            summary["errors"] += 1
            failed_invoices.append(source_code)
            self._stderr(f"  - failed {source_code}: {exc}")

    def _resync_after_delete(
        self,
        ctx: _RunContext,
        invoice_detail: dict,
        source_code: str,
        b_id: int,
        b_code: str,
        changes_str: str,
        counter_key: str,
    ) -> None:
        """update_delivery_detail dùng cơ chế delete+resync: KiotViet PUT silently
        ignore deliveryDetail trên hóa đơn đã tạo (nhất là đã "Hoàn thành") nên khi
        lệch thì DELETE đơn B rồi tạo lại từ A (parity với command
        sync_invoices_between_channels)."""
        summary = ctx.summary
        failed_invoices = ctx.failed_invoices
        target_channel = ctx.target_channel
        try:
            delete_invoice_by_id(invoice_id=b_id, order=None, channel=target_channel)
            self._stdout(f"  - deleted B:{b_code}(id={b_id}) for {source_code} [{changes_str}]")
        except Exception as exc:  # noqa: BLE001
            summary["errors"] += 1
            failed_invoices.append(source_code)
            self._stderr(f"  - failed {source_code}: delete B:{b_code}(id={b_id}) error: {exc}")
            return
        ctx.synced_source_codes.discard(source_code)
        ctx.synced_source_map.pop(source_code, None)
        try:
            res = self._sync_invoice(
                source_invoice=invoice_detail,
                source_channel=ctx.source_channel,
                target_channel=target_channel,
                target_branch_id=ctx.target_branch_id,
                dry_run=False,
            )
            summary[counter_key] += 1
            reasons = list(getattr(self, "_current_fallbacks", []) or [])
            self._current_fallbacks = []
            fallback_note = f" [FALLBACK: {', '.join(reasons)}]" if reasons else ""
            self._stdout(f"  - resynced {source_code} -> {res} [{changes_str}]{fallback_note}")
        except _SkipInvoice as skip_exc:
            summary["errors"] += 1
            failed_invoices.append(source_code)
            self._stderr(f"  - failed {source_code}: B đã xóa nhưng resync skip: {skip_exc}")
        except Exception as exc:  # noqa: BLE001
            summary["errors"] += 1
            failed_invoices.append(source_code)
            self._stderr(f"  - failed {source_code}: B đã xóa nhưng resync error: {exc}")

    def _update_delivery_detail_one(
        self, ctx: _RunContext, invoice_detail: dict, source_code: str,
    ) -> None:
        """Mode --update-delivery-detail: so deliveryCode/status/price A vs B;
        chỉ lệch giá → PUT 1 lần; lệch code/status → delete B + resync."""
        summary = ctx.summary
        target_channel = ctx.target_channel
        b_info = ctx.synced_source_map.get(source_code)
        if not b_info or not b_info.get("id"):
            summary["update_not_found_on_b"].append(source_code)
            self._stdout(f"  - skip {source_code}: chưa có bên B, không update được")
            return
        src_delivery = (
            invoice_detail.get("invoiceDelivery")
            or invoice_detail.get("InvoiceDelivery")
            or {}
        )
        src_delivery = src_delivery if isinstance(src_delivery, dict) else {}
        src_delivery_code = str(
            src_delivery.get("deliveryCode") or src_delivery.get("DeliveryCode") or ""
        ).strip()
        src_delivery_status = src_delivery.get("status") or src_delivery.get("Status")
        src_delivery_price_raw = (
            src_delivery.get("price")
            if src_delivery.get("price") is not None
            else src_delivery.get("Price")
        )
        b_delivery_code = b_info.get("deliveryCode") or ""
        b_delivery_status = b_info.get("deliveryStatus")
        b_delivery_price_raw = b_info.get("deliveryPrice")
        b_id = int(b_info["id"])
        b_code = b_info.get("code") or ""

        src_delivery_price = _norm_price(src_delivery_price_raw)
        b_delivery_price = _norm_price(b_delivery_price_raw)
        code_same = src_delivery_code == b_delivery_code
        status_same = src_delivery_status == b_delivery_status
        price_same = src_delivery_price == b_delivery_price
        if code_same and status_same and price_same:
            summary["delivery_detail_skipped_same"] += 1
            self._stdout(
                f"  - skip {source_code}: delivery giống nhau "
                f"(code={src_delivery_code or '(empty)'}, status={src_delivery_status}, "
                f"price={src_delivery_price})"
            )
            return
        changes: list[str] = []
        if not code_same:
            changes.append(f"code: {b_delivery_code or '(empty)'} -> {src_delivery_code or '(empty)'}")
        if not status_same:
            changes.append(f"status: {b_delivery_status} -> {src_delivery_status}")
        if not price_same:
            changes.append(f"price: {b_delivery_price} -> {src_delivery_price}")
        changes_str = ", ".join(changes)
        if ctx.dry_run:
            summary["delivery_detail_updated"] += 1
            self._stdout(f"  - [dry-run] {source_code}: {changes_str}")
            return
        # Chỉ lệch GIÁ ship → PUT trực tiếp 1 lần (delete-resync không sửa được giá).
        # tới đây chắc chắn lệch (đã return ở trên nếu cả 3 giống); code+status giống => chỉ lệch GIÁ
        if code_same and status_same:
            from apps.kiotviet.infrastructure.services.invoice_mutations_push import (  # noqa: PLC0415
                update_invoice_delivery_price_by_code,
            )
            from apps.kiotviet.infrastructure.services.delivery_price_sync import (  # noqa: PLC0415
                sync_delivery_price_once,
            )

            def _put(code, price):
                update_invoice_delivery_price_by_code(
                    invoice_code=code, price=price, order=None, channel=target_channel,
                )

            def _read_b():
                d = get_invoice_by_code(b_code, order=None, channel=target_channel)
                return _norm_price((d.get("invoiceDelivery") or {}).get("price"))

            sync_delivery_price_once(
                invoice_code=b_code, a_price=src_delivery_price, b_price=b_delivery_price,
                put_price=_put, read_b_price=_read_b,
            )
            summary["delivery_detail_updated"] += 1
            self._stdout(f"  - PUT ship {source_code}: {changes_str}")
            return
        self._resync_after_delete(
            ctx, invoice_detail, source_code, b_id, b_code, changes_str, "delivery_detail_updated",
        )

    def _sync_new_one(self, ctx: _RunContext, invoice_detail: dict, source_code: str) -> None:
        """Mode sync thường: bỏ qua đơn đã có bên B / đã hủy, còn lại ``_sync_invoice``
        với retry nghiệp vụ 3 lần."""
        summary = ctx.summary
        failed_invoices = ctx.failed_invoices
        if source_code in ctx.synced_source_codes:
            summary["skipped"] += 1
            self._stdout(f"  - skip {source_code}: đã có bên B")
            return

        invoice_status = invoice_detail.get("status") or invoice_detail.get("Status")
        invoice_status_value = str(
            invoice_detail.get("statusValue") or invoice_detail.get("StatusValue") or ""
        ).strip().lower()
        inv_delivery = (
            invoice_detail.get("invoiceDelivery") or invoice_detail.get("InvoiceDelivery") or {}
        )
        delivery_status = (
            (inv_delivery.get("status") or inv_delivery.get("Status"))
            if isinstance(inv_delivery, dict) else None
        )
        if invoice_status == 2 or invoice_status_value in {"đã hủy", "đã huỷ", "cancelled"} or delivery_status == 6:
            summary["skipped"] += 1
            self._stdout(
                f"  - skip {source_code}: đã hủy "
                f"(invoice status={invoice_status}, statusValue={invoice_status_value}, "
                f"delivery status={delivery_status})"
            )
            return

        self._stdout("")
        self._stdout(f"==== [invoice {source_code}] ====")
        self._stdout(
            f"[1/3] invoice A: "
            f"{json.dumps(invoice_detail, ensure_ascii=False, default=str)}"
        )

        # Retry NGHIỆP VỤ (không phải HTTP): chạy lại cả _sync_invoice (resolve
        # customer + build + create) khi bất kỳ bước nào ném exception. Cố tình
        # đứng ngoài _kiotviet_request_with_retry (BE-5) — helper đó chỉ retry 5xx
        # của MỘT request; ở đây lỗi có thể đến từ create_customer/create_invoice
        # và cần làm lại từ đầu với dữ liệu tươi.
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                result = self._sync_invoice(
                    source_invoice=invoice_detail,
                    source_channel=ctx.source_channel,
                    target_channel=ctx.target_channel,
                    target_branch_id=ctx.target_branch_id,
                    dry_run=ctx.dry_run,
                )
                if result == "skipped":
                    summary["skipped"] += 1
                    self._stdout(f"  - skip {source_code}")
                else:
                    summary["created"] += 1
                    reasons = list(getattr(self, "_current_fallbacks", []) or [])
                    if reasons:
                        summary["fallback"] += 1
                        summary["fallback_details"].append(
                            {"code": source_code, "reasons": reasons}
                        )
                        self._stdout(
                            f"  - sync {source_code} -> {result} "
                            f"[FALLBACK: {', '.join(reasons)}]"
                        )
                    else:
                        summary["clean"] += 1
                        self._stdout(f"  - sync {source_code} -> {result} [CLEAN]")
                self._current_fallbacks = []
                return
            except _SkipInvoice as skip_exc:
                summary["skipped"] += 1
                self._stdout(f"  - skip {source_code}: {skip_exc}")
                return
            except Exception as exc:  # noqa: BLE001
                if attempt < max_retries:
                    self._stderr(
                        f"  - failed {source_code} (attempt {attempt}/{max_retries}): {exc}, retrying..."
                    )
                    time.sleep(2 * attempt)
                    continue
                summary["errors"] += 1
                failed_invoices.append(source_code)
                self._stderr(f"  - failed {source_code}: {exc}")
                return

    def _run_sync_loop(self, ctx: _RunContext) -> None:
        """Duyệt A: --invoice-codes (fetch từng mã) hoặc paginate A; mỗi đơn → ``_process_one``."""
        summary = ctx.summary
        failed_invoices = ctx.failed_invoices
        specific_codes = ctx.specific_codes
        sleep_pushes = ctx.sleep_pushes

        if specific_codes:
            self._stdout(f"Mode: --invoice-codes ({len(specific_codes)} codes)")
            for source_code in specific_codes:
                try:
                    fetched = get_invoice_by_code(source_code, order=None, channel=ctx.source_channel)
                    if isinstance(fetched, dict) and fetched:
                        self._process_one(ctx, fetched)
                    else:
                        summary["errors"] += 1
                        failed_invoices.append(source_code)
                        self._stderr(f"  -- skip {source_code}: cannot fetch detail")
                except Exception as exc:  # noqa: BLE001
                    summary["errors"] += 1
                    failed_invoices.append(source_code)
                    self._stderr(f"  -- skip {source_code}: {exc}")
                if sleep_pushes > 0:
                    time.sleep(sleep_pushes)
        else:
            self._paginate_source(
                source_channel=ctx.source_channel,
                source_branch_id=ctx.source_branch_id,
                from_dt=ctx.from_dt,
                to_dt=ctx.to_dt,
                page_size=ctx.page_size,
                sleep_pages=ctx.sleep_pages,
                sleep_pushes=sleep_pushes,
                limit=ctx.limit,
                on_invoice=lambda invoice_detail: self._process_one(ctx, invoice_detail),
                summary=summary,
            )

    # ------------------------------------------------------------------
    # Bước 6 — báo cáo BREAKDOWN + dump summary
    # ------------------------------------------------------------------
    def _report_sync_summary(self, ctx: _RunContext) -> dict:
        """In "Sync invoices completed.", failed codes, BREAKDOWN theo mode, JSON summary."""
        summary = ctx.summary
        failed_invoices = ctx.failed_invoices

        self._stdout("Sync invoices completed.")
        if failed_invoices:
            summary["failed_codes"] = failed_invoices
            self._stdout(
                f"\nFailed invoices ({len(failed_invoices)}): {', '.join(failed_invoices)}"
            )

        self._stdout("")
        self._stdout("==== BREAKDOWN ====")
        if summary.get("update_delivery_detail_mode"):
            self._stdout("  mode                : update-delivery-detail")
            self._stdout(f"  updated             : {summary.get('delivery_detail_updated', 0)}")
            self._stdout(f"  skipped (same)      : {summary.get('delivery_detail_skipped_same', 0)}")
            if summary.get("update_not_found_on_b"):
                self._stdout(
                    f"  not found on B ({len(summary['update_not_found_on_b'])}):"
                )
                for code in summary["update_not_found_on_b"]:
                    self._stdout(f"    - {code}")
        elif summary.get("update_sale_channel_mode"):
            self._stdout("  mode                : update-sale-channel")
            self._stdout(f"  updated             : {summary.get('updated', 0)}")
            self._stdout(f"  updated (cleared)   : {summary.get('updated_cleared', 0)}")
            if summary.get("update_not_found_on_b"):
                self._stdout(
                    f"  not found on B ({len(summary['update_not_found_on_b'])}):"
                )
                for code in summary["update_not_found_on_b"]:
                    self._stdout(f"    - {code}")
        else:
            self._stdout(
                f"  created total       : {summary['created']} "
                f"(clean={summary['clean']}, fallback={summary['fallback']})"
            )
        self._stdout(f"  skipped             : {summary['skipped']}")
        self._stdout(f"  errors              : {summary['errors']}")

        if summary["fallback_details"]:
            self._stdout("")
            self._stdout(f"  Fallback invoices ({len(summary['fallback_details'])}):")
            for item in summary["fallback_details"]:
                self._stdout(f"    - {item['code']}: {', '.join(item['reasons'])}")

        if summary["missing_sale_channel_codes"]:
            self._stdout("")
            self._stdout(
                f"  Invoices không có saleChannelId "
                f"({len(summary['missing_sale_channel_codes'])}):"
            )
            for code in summary["missing_sale_channel_codes"]:
                self._stdout(f"    - {code}")

        if summary["unmapped_sale_channel_codes"]:
            self._stdout("")
            self._stdout(
                f"  Invoices không map được saleChannelId "
                f"({len(summary['unmapped_sale_channel_codes'])}):"
            )
            for code in summary["unmapped_sale_channel_codes"]:
                self._stdout(f"    - {code}")

        if summary["unmapped_sold_by_codes"]:
            self._stdout("")
            self._stdout(
                f"  Invoices không map được soldById "
                f"({len(summary['unmapped_sold_by_codes'])}):"
            )
            for code in summary["unmapped_sold_by_codes"]:
                self._stdout(f"    - {code}")

        self._stdout("")
        self._stdout(json.dumps(summary, ensure_ascii=False))
        return summary

    # ------------------------------------------------------------------
    # Pre-scan target B — phát hiện đơn đã sync từ A (theo description
    # "SYNC từ kênh ... - <source_code>"). Trích từ run() để dùng lại ở
    # reconcile_sync và test bằng cách mock I/O.
    # ------------------------------------------------------------------
    def _prescan_target(
        self,
        *,
        target_channel,
        target_branch_id,
        from_dt,
        to_dt,
        page_size,
        sleep_pages,
    ) -> dict[str, dict]:
        synced_source_map: dict[str, dict] = {}
        self._stdout("Pre-scan target B to detect already synced invoices...")
        prescan_current_item = 0
        while True:
            try:
                params = {
                    "pageSize": page_size,
                    "currentItem": prescan_current_item,
                    "branchIds": str(target_branch_id),
                    "fromPurchaseDate": from_dt,
                    "includeInvoiceDelivery": "true",
                    # Sort theo id (bất biến) để phân trang currentItem ỔN ĐỊNH. Không có sort
                    # này, public /invoices mặc định theo modifiedDate — đơn bị sửa trong lúc
                    # quét (chính reconcile đang sửa) làm hàng xê dịch giữa trang → vừa lặp vừa
                    # bỏ sót (~449/15485 đơn). Pre-scan sót mirror → tạo lại → lặp đơn.
                    "orderBy": "id",
                    "orderDirection": "Asc",
                }
                if to_dt:
                    params["toPurchaseDate"] = to_dt
                response = _kiotviet_request(
                    order=None,
                    channel=target_channel,
                    method="GET",
                    endpoint="/invoices",
                    params=params,
                )
                if response.status_code != 200:
                    self._stderr(f"  pre-scan B failed: status={response.status_code}")
                    break
                payload = response.json() if response.content else {}
                pd = _extract_invoice_items(payload)
                pt = payload.get("total") or payload.get("Total") or 0
            except Exception as exc:  # noqa: BLE001
                self._stderr(f"  pre-scan B failed: {exc}")
                break
            self._stdout(
                f"  pre-scan B: currentItem={prescan_current_item}, fetched={len(pd)}, total={pt}"
            )
            if not pd:
                break
            for inv in pd:
                inv_status = inv.get("status") or inv.get("Status")
                inv_status_value = str(
                    inv.get("statusValue") or inv.get("StatusValue") or ""
                ).strip().lower()
                inv_code = inv.get("code") or inv.get("Code") or ""
                if inv_status == 2 or inv_status_value in {"đã hủy", "đã huỷ", "cancelled"}:
                    continue
                desc = str(inv.get("description") or inv.get("Description") or "")
                m = _re.search(r"SYNC từ kênh \S+ - (\S+)", desc)
                if m:
                    src_code_match = m.group(1)
                    inv_delivery = inv.get("invoiceDelivery") or inv.get("InvoiceDelivery") or {}
                    inv_delivery = inv_delivery if isinstance(inv_delivery, dict) else {}
                    synced_source_map[src_code_match] = {
                        "id": inv.get("id") or inv.get("Id"),
                        "code": inv_code,
                        "invoiceStatus": inv_status,
                        "deliveryCode": str(
                            inv_delivery.get("deliveryCode")
                            or inv_delivery.get("DeliveryCode")
                            or ""
                        ).strip(),
                        "deliveryStatus": inv_delivery.get("status") or inv_delivery.get("Status"),
                        "deliveryPrice": (
                            inv_delivery.get("price")
                            if inv_delivery.get("price") is not None
                            else inv_delivery.get("Price")
                        ),
                        "total": self._first_non_empty(inv.get("total"), inv.get("Total")),
                    }
                    self._stdout(
                        f"    matched: {src_code_match} -> B:{inv_code} (status={inv_status})"
                    )
            prescan_current_item += len(pd)
            if len(pd) < page_size or (pt and prescan_current_item >= int(pt)):
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)
        self._stdout(f"  already synced: {len(synced_source_map)} invoices")
        return synced_source_map

    # ------------------------------------------------------------------
    # Paginate source A (GET /invoices, orderBy=id/Asc) và gọi on_invoice
    # cho từng hóa đơn. Trích từ run() để dùng lại ở reconcile_sync và
    # test bằng cách mock I/O (on_invoice thay _process_one cứng).
    # ------------------------------------------------------------------
    def _paginate_source(
        self,
        *,
        source_channel,
        source_branch_id,
        from_dt,
        to_dt,
        page_size,
        sleep_pages,
        sleep_pushes,
        limit,
        on_invoice,
        summary,
    ) -> None:
        current_item = 0
        api_total: int | None = None

        while True:
            if limit and summary["fetched"] >= limit:
                break

            params = {
                "pageSize": page_size,
                "currentItem": current_item,
                "branchIds": str(source_branch_id),
                "fromPurchaseDate": from_dt,
                "includeInvoiceDelivery": "true",
                # Sort id bất biến → phân trang ổn định, không bỏ sót đơn nguồn (xem pre-scan).
                "orderBy": "id",
                "orderDirection": "Asc",
            }
            if to_dt:
                params["toPurchaseDate"] = to_dt

            # Retry 5xx qua helper dùng chung (BE-5) — semantics giữ nguyên vòng tay cũ.
            response = _kiotviet_request_with_retry(
                order=None,
                channel=source_channel,
                method="GET",
                endpoint="/invoices",
                params=params,
            )
            if response.status_code != 200:
                raise SyncError(
                    f"GET /invoices failed: status={response.status_code}, "
                    f"body={(response.text or '').strip()[:500]}"
                )

            payload = response.json() if response.content else {}
            invoices = _extract_invoice_items(payload)
            total = payload.get("total") or payload.get("Total") or 0

            if api_total is None:
                api_total = int(total)
                self._stdout(f"Source API total={api_total} (branch={source_branch_id})")

            summary["pages"] += 1
            self._stdout(
                f"Page {summary['pages']}: currentItem={current_item}, fetched={len(invoices)}"
            )

            if not invoices:
                break

            for invoice_detail in invoices:
                if limit and summary["fetched"] >= limit:
                    break
                on_invoice(invoice_detail)
                if sleep_pushes > 0:
                    time.sleep(sleep_pushes)

            current_item += len(invoices)
            if len(invoices) < page_size:
                break
            if api_total and current_item >= api_total:
                break
            if sleep_pages > 0:
                time.sleep(sleep_pages)

    # ------------------------------------------------------------------
    # Helpers — pure logic lives in infrastructure.services.datetime_utils
    # (thin wrappers keep the existing self._x / cls._x call sites unchanged).
    # ------------------------------------------------------------------
    _first_non_empty = staticmethod(dt_utils.first_non_empty)
    _to_decimal = staticmethod(dt_utils.to_decimal)
    _parse_dt_loose = staticmethod(dt_utils.parse_dt_loose)
    _is_after = staticmethod(dt_utils.is_after)
    _shift_dt = staticmethod(dt_utils.shift_dt)

    # ------------------------------------------------------------------
    # List invoices — PUBLIC API (GET /invoices)
    # ------------------------------------------------------------------
    def _web_list_invoices(
        self,
        *,
        channel: KiotVietChannel,
        branch_id: int,
        from_dt: str,
        to_dt: str | None,
        top: int,
        skip: int,
    ) -> tuple[int, list[dict]]:
        """[GỘP PUBLIC API] Trả về 1 trang invoice qua PUBLIC API ``GET /invoices`` (đã bỏ web API
        ``POST /api/invoices/list``).

        Lý do gộp: trước đây pre-scan/sync dùng public API còn purge/check dùng web API
        — hai nguồn khác filter (web convert UTC −7h, OData) nên có thể trả tập đơn KHÁC nhau → pre-scan
        bỏ sót mirror đang tồn tại → tạo lại = đơn TRÙNG. Đã verify public ≡ web cùng tập đơn (949≡949,
        545≡545). Giữ nguyên signature (``top``→pageSize, ``skip``→currentItem) để mọi caller dùng chung.

        Chuẩn hoá ``deliveryCode`` phẳng từ ``invoiceDelivery`` lồng để tương thích các chỗ đọc field phẳng.

        ``top`` bị KẸP về ``KIOTVIET_LIST_MAX_PAGE_SIZE`` (=100) — trần cứng của public API.
        Caller phải so ``len(page)`` với chính trần đó, KHÔNG phải với số mình xin.
        """
        if not str(from_dt or "").strip():
            raise SyncError("--from không được rỗng")

        # Kẹp cứng: xin > 100 thì KiotViet trả về 100 mà không báo gì, caller so
        # len(page) với số đã xin sẽ tưởng hết dữ liệu và dừng sau trang đầu.
        params: dict = {
            "pageSize": min(max(int(top), 1), KIOTVIET_LIST_MAX_PAGE_SIZE),
            "currentItem": int(skip),
            "branchIds": str(branch_id),
            "fromPurchaseDate": from_dt,
            "includeInvoiceDelivery": "true",
            # BẮT BUỘC: sort theo id bất biến để phân trang currentItem ổn định. Thiếu nó, public
            # /invoices mặc định sort theo modifiedDate — quét nhiều trang trên tập lớn (15k đơn) trong
            # lúc reconcile đang sửa đơn sẽ lặp+bỏ sót (~449/15485), khiến purge/check thiếu đơn.
            "orderBy": "id",
            "orderDirection": "Asc",
        }
        if to_dt:
            params["toPurchaseDate"] = to_dt

        # Retry 5xx qua helper dùng chung (BE-5) — semantics giữ nguyên vòng tay cũ.
        response = _kiotviet_request_with_retry(
            order=None,
            channel=channel,
            method="GET",
            endpoint="/invoices",
            params=params,
        )
        if response.status_code != 200:
            raise SyncError(
                f"GET /invoices failed: status={response.status_code}, "
                f"body={(response.text or '').strip()[:500]}"
            )

        try:
            payload = response.json() if response.content else {}
        except ValueError as exc:
            raise SyncError(f"GET /invoices non-JSON response: {exc}") from exc

        items = _extract_invoice_items(payload)
        total = payload.get("total") or payload.get("Total") or 0

        clean: list[dict] = []
        for inv in items:
            if not isinstance(inv, dict):
                continue
            if (inv.get("id") or inv.get("Id")) in (None, -1, 0):
                continue
            # Web API có deliveryCode phẳng; public API chỉ có nested invoiceDelivery → bù field phẳng
            # cho các chỗ đọc inv.get("deliveryCode") (vd _check_missing).
            if not inv.get("deliveryCode"):
                deliv = inv.get("invoiceDelivery") or inv.get("InvoiceDelivery") or {}
                if isinstance(deliv, dict):
                    dc = deliv.get("deliveryCode") or deliv.get("DeliveryCode")
                    if dc:
                        inv["deliveryCode"] = dc
            clean.append(inv)
        dropped = len(items) - len(clean)
        if dropped:
            # Trả về ít hơn số dòng thật -> caller cộng currentItem thiếu và có thể dừng sớm.
            # Chưa gặp trên public API; log để lần sau không phải đoán.
            self._stdout(
                f"  [WARN] GET /invoices: bỏ {dropped}/{len(items)} dòng id<=0 "
                f"(currentItem={skip}) — phân trang có thể lệch"
            )
        return int(total), clean

    @staticmethod
    def _normalize_dt_arg(value: str | None, label: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise SyncError(f"--{label} không được rỗng")
        parsed = parse_datetime(text)
        if parsed is not None:
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        if len(text) == 10:
            return f"{text} 00:00:00"
        return text
