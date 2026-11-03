import logging
from datetime import timedelta
from collections.abc import Iterator

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.common.db_guards import single_flight
from apps.kiotviet.infrastructure.services.reconcile import (
    iter_reconcile_pairs,
    reconcile_one_pair,
)
from apps.kiotviet.infrastructure.services.returns_sync_service import sync_returns_all_channels
from apps.kiotviet.infrastructure.services.revenue_report_service import (
    sync_daily_revenue_all_channels,
)
from apps.kiotviet.models import KiotVietChannel

logger = logging.getLogger(__name__)

# Khóa single-flight: chống 2 lần sync trả hàng chạy chồng (cron 15' trễ / worker restart).
# TTL là van an toàn > time_limit để tự nhả nếu worker chết giữa chừng.
_SYNC_RETURNS_LOCK_KEY = "kv:sync_returns:lock"
_SYNC_RETURNS_LOCK_TTL = 360  # > time_limit=300


@shared_task(
    bind=True,
    name="apps.kiotviet.tasks.sync_daily_revenue_all_channels",
    soft_time_limit=240,
    time_limit=300,
)
def sync_daily_revenue_all_channels_task(self, time_range: str = "yesterday") -> dict:
    """Cron entrypoint: sync daily revenue for every KiotViet channel."""
    try:
        summary = sync_daily_revenue_all_channels(time_range=time_range)
        if summary.get("errors"):
            logger.warning(
                "Daily revenue sync finished with errors: %s",
                summary["errors"],
            )
        return summary
    except Exception:
        logger.exception("sync_daily_revenue_all_channels failed")
        raise


@shared_task(
    bind=True,
    name="apps.kiotviet.tasks.reconcile_one_pair",
    soft_time_limit=3600,
    time_limit=3900,
)
def reconcile_one_pair_task(
    self,
    *,
    source_kv_id: int,
    source_branch_id: str,
    target_kv_id: int,
    target_branch_id: str,
    window_from: str,
    window_to: str,
) -> dict:
    """Đối soát MỘT cặp new→old qua ``SyncInvoicesService`` (gọi THẲNG service, KHÔNG
    qua management command). Sub-task fan-out bởi ``reconcile_paired_channels_task`` →
    các cặp chạy song song qua queue ``kiotviet``. Logic 6-mode nằm ở
    ``infrastructure.services.reconcile.reconcile_one_pair`` để dùng chung với command CLI.

    Lock per-cặp (cache.add) chống chạy CHỒNG cùng một cặp (cron 00:00 trùng lần chạy hôm trước
    còn dang dở, hoặc trùng CLI reconcile_channels chạy tay) — hai run cùng quét → cùng pre-scan
    trống → cùng tạo mirror = đơn TRÙNG. Lock tự hết hạn nếu worker chết giữa chừng."""
    # Key gồm cả branch: 1 cặp kênh giờ có thể có NHIỀU cặp kho (old_sync_branch_map),
    # các cặp kho khác nhau phải chạy song song được, chỉ chặn chạy chồng CÙNG cặp kho.
    lock_key = (
        f"kv:reconcile_pair:lock:{source_kv_id}:{source_branch_id}"
        f":{target_kv_id}:{target_branch_id}"
    )
    lock_ttl = 3900  # = time_limit
    if not cache.add(lock_key, 1, lock_ttl):
        logger.info(
            "reconcile_one_pair skip kv%s->kv%s: lock held (đang chạy)",
            source_kv_id, target_kv_id,
        )
        return {"skipped": "lock_held", "source_kv": source_kv_id, "target_kv": target_kv_id}
    try:
        result = reconcile_one_pair(
            source_kv_id=source_kv_id,
            source_branch_id=source_branch_id,
            target_kv_id=target_kv_id,
            target_branch_id=target_branch_id,
            from_dt=window_from,
            to_dt=window_to,
        )
    finally:
        cache.delete(lock_key)
    logger.info(
        "reconcile_one_pair done: kv%s->kv%s, modes=%s",
        source_kv_id, target_kv_id, result["modes"],
    )
    return result


@shared_task(
    bind=True,
    name="apps.kiotviet.tasks.reconcile_paired_channels",
    soft_time_limit=120,
    time_limit=180,
)
def reconcile_paired_channels_task(
    self,
    from_dt: str | None = None,
    to_dt: str | None = None,
) -> dict:
    """Cron entrypoint: fan-out đối soát từng cặp new(old=False) ↔ paired_old_channel.

    Mỗi cặp dispatch thành một ``reconcile_one_pair`` task riêng → các cặp chạy SONG SONG
    qua queue ``kiotviet``. Trong cùng một cặp, 6 mode chạy tuần tự (phụ thuộc thứ tự).

    Window mặc định = TRONG THÁNG hiện tại (cron chạy 00:00 mỗi ngày, quét cả tháng để bắt
    mọi lệch tích lũy); override qua from_dt/to_dt "YYYY-MM-DD HH:MM:SS".
    """
    if from_dt and to_dt:
        window_from, window_to = from_dt, to_dt
    else:
        window_from, window_to = _current_month_window()

    summary: dict = {
        "window": {"from": window_from, "to": window_to},
        "dispatched": [],
        "skipped": [],
    }

    for pair in iter_reconcile_pairs():
        if pair["skip"]:
            summary["skipped"].append({"pair": pair["pair"], "reason": pair["reason"]})
            logger.warning("reconcile_paired_channels skip %s: %s", pair["pair"], pair["reason"])
            continue
        reconcile_one_pair_task.apply_async(
            kwargs={
                "source_kv_id": pair["source_kv_id"],
                "source_branch_id": pair["source_branch_id"],
                "target_kv_id": pair["target_kv_id"],
                "target_branch_id": pair["target_branch_id"],
                "window_from": window_from,
                "window_to": window_to,
            },
        )
        summary["dispatched"].append(pair["pair"])

    logger.info(
        "reconcile_paired_channels fan-out: dispatched=%s, skipped=%s, window=%s..%s",
        len(summary["dispatched"]), len(summary["skipped"]), window_from, window_to,
    )
    return summary


def _current_month_window() -> tuple[str, str]:
    """(from, to) = đầu "tháng làm việc" → hiện tại, theo timezone cấu hình.

    Đầu tháng neo theo **now − 1 ngày** chứ không phải now: run lúc 00:00 ngày 1
    (reconcile đổi lịch sang midnight) mà neo theo now thì window = tháng MỚI rỗng —
    cả ngày cuối tháng trước (đúng ngày chốt sổ) vĩnh viễn không được đối soát, lặp
    lại mỗi tháng (audit 2026-07-18). Neo theo hôm qua: run ngày 1 quét trọn tháng
    cũ tới hiện tại; mọi ngày khác hành vi y hệt trước.
    """
    now = timezone.localtime()
    anchor = now - timedelta(days=1)
    start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    fmt = "%Y-%m-%d %H:%M:%S"
    return start.strftime(fmt), now.strftime(fmt)


def _update_status_for_target(
    *,
    channel_id: int,
    branch_ids: list[str],
    from_dt: str,
    to_dt: str,
) -> dict:
    """Gọi method CÔNG KHAI ``update_status_phone`` của ``ImportInvoicesService``
    (business logic đã move khỏi management command — WP8.1; KHÔNG qua ``call_command``).

    stdout/stderr mặc định của service đổ về log worker (sys.stdout/sys.stderr).
    """
    # Import cục bộ: tránh nạp module service nặng khi app khởi động.
    from apps.kiotviet.infrastructure.services.import_invoices_service import (
        ImportInvoicesService,
    )

    kv_channel = KiotVietChannel.objects.get(id=channel_id, deleted_at__isnull=True)

    service = ImportInvoicesService()
    service.update_status_phone(
        kv_channel=kv_channel,
        branch_ids=[str(b).strip() for b in branch_ids if str(b).strip()],
        invoice_codes=[],
        dry_run=False,
        do_status=True,
        do_phone=False,
        from_dt=from_dt,
        to_dt=to_dt,
    )
    return {"channel_id": channel_id, "branch_ids": branch_ids, "from": from_dt, "to": to_dt}


_UPDATE_STATUS_LOCK_KEY = "lock:update_invoice_status_targets"


def _run_update_invoice_status(window_from: str, window_to: str) -> dict:
    """Cập nhật ``invoice_status`` + ``status`` cho mọi (kênh, chi nhánh) trong
    ``settings.KIOTVIET_STATUS_SYNC_TARGETS`` theo window purchaseDate đã cho.
    Caller PHẢI giữ ``_UPDATE_STATUS_LOCK_KEY`` — tick 30' và quét sâu dùng chung lock,
    2 run cùng ghi Order.status/invoice_status (audit 2026-07-14 vòng 5)."""
    targets = getattr(settings, "KIOTVIET_STATUS_SYNC_TARGETS", []) or []
    summary: dict = {
        "window": {"from": window_from, "to": window_to},
        "done": [],
        "errors": [],
    }

    for target in targets:
        channel_id = int(target.get("channel_id"))
        branch_ids = target.get("branch_ids") or []
        try:
            result = _update_status_for_target(
                channel_id=channel_id,
                branch_ids=branch_ids,
                from_dt=window_from,
                to_dt=window_to,
            )
            summary["done"].append(result)
        except SoftTimeLimitExceeded:
            # KHÔNG nuốt chung với lỗi 1 kênh: nuốt là task chạy tiếp tới hard-kill,
            # đúng lúc tick sau bắt đầu (audit 2026-07-14 vòng 5).
            logger.warning("update_invoice_status hit soft time limit — stopping early")
            raise
        except Exception as exc:  # noqa: BLE001 — 1 kênh lỗi không chặn kênh còn lại
            logger.exception("update_invoice_status failed channel_id=%s", channel_id)
            summary["errors"].append({"channel_id": channel_id, "error": str(exc)})

    logger.info(
        "update_invoice_status done: ok=%s, errors=%s, window=%s..%s",
        len(summary["done"]), len(summary["errors"]), window_from, window_to,
    )
    return summary


@shared_task(
    bind=True,
    name="apps.kiotviet.tasks.update_invoice_status_targets",
    soft_time_limit=1500,
    time_limit=1800,
)
def update_invoice_status_targets_task(
    self,
    from_dt: str | None = None,
    to_dt: str | None = None,
) -> dict:
    """Cron entrypoint 30': cập nhật ``invoice_status`` + ``status`` (giao hàng) cho đơn của
    các (kênh, chi nhánh) cấu hình trong ``settings.KIOTVIET_STATUS_SYNC_TARGETS``.

    Thay cho việc chạy tay ``import_kiotviet_invoices --update-status``; gọi THẲNG
    handler ``_handle_update_status_phone`` (không call_command). Window mặc định = trong
    tháng hiện tại; override qua from_dt/to_dt "YYYY-MM-DD HH:MM:SS". Hoá đơn mua THÁNG
    TRƯỚC đổi trạng thái sang tháng này do ``update_invoice_status_deep_task`` quét.
    """
    if from_dt and to_dt:
        window_from, window_to = from_dt, to_dt
    else:
        window_from, window_to = _current_month_window()

    # Window = cả tháng nên cuối tháng task dài dần; không lock thì tick 30' sau chạy
    # chồng. TTL = time_limit để lock tự nhả nếu worker bị SIGKILL.
    with single_flight(_UPDATE_STATUS_LOCK_KEY, 1800) as acquired:
        if not acquired:
            logger.warning("update_invoice_status_targets already running — skip this tick")
            return {"skipped": "already_running"}
        return _run_update_invoice_status(window_from, window_to)


@shared_task(
    bind=True,
    name="apps.kiotviet.tasks.update_invoice_status_deep",
    soft_time_limit=3300,
    time_limit=3600,
)
def update_invoice_status_deep_task(self, lookback_days: int = 62) -> dict:
    """Quét sâu 1 lần/ngày: cùng logic tick 30' nhưng window lùi ``lookback_days`` ngày.

    Filter API là purchaseDate nên tick 30' (từ ngày 1 tháng này) KHÔNG BAO GIỜ thấy lại
    hoá đơn mua tháng trước — mà giao xong / hoàn / huỷ / thu tiền của chúng rơi sang
    tháng này rất nhiều (sự cố 2026-09-16: kênh 6 tháng 8 còn 34 đơn lệch, vận hành phải
    chạy tay ``--update-status --from <tháng trước>``). 62 ngày = 31 + 31 để chạy ngày 31
    vẫn tới ngày 1 tháng trước. Limit riêng (55') vì window ~3× tick thường.
    """
    window_from, window_to = _import_window(int(lookback_days))

    with single_flight(_UPDATE_STATUS_LOCK_KEY, 3600) as acquired:
        if not acquired:
            # Thua lock với tick 30' mà bỏ luôn là mất nguyên đêm quét sâu, im lặng
            # (cùng bài học import create-only deep, audit 2026-07-18). Đợi rồi thử lại.
            logger.warning("update_invoice_status_deep: lock held — retry sau 5'")
            raise self.retry(countdown=300, max_retries=6)
        return _run_update_invoice_status(window_from, window_to)


def _import_window(lookback_days: int = 0) -> tuple[str, str]:
    """(from, to) = 00:00 của (hôm nay − lookback_days) → hiện tại (localtime).

    Filter API là fromPurchaseDate/toPurchaseDate (NGÀY MUA, không phải ngày tạo
    trong KiotViet): hóa đơn tạo hôm nay nhưng purchaseDate lùi ngày (đơn sàn sync
    trễ, bấm quanh nửa đêm, hủy-xuất lại giữ ngày cũ) nằm ngoài window "hôm nay"
    VĨNH VIỄN — nên cron 5' phải lùi thêm N ngày. Backdate sâu hơn N ngày do cron
    quét sâu hằng ngày vớt (kv-import-create-only-deep)."""
    now = timezone.localtime()
    start = (now - timedelta(days=lookback_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    fmt = "%Y-%m-%d %H:%M:%S"
    return start.strftime(fmt), now.strftime(fmt)


def _parse_import_create_only_targets(raw: str) -> tuple[tuple[int, str], ...]:
    """Parse ``KIOTVIET_IMPORT_CREATE_ONLY_TARGETS`` ("channel_id:branch_id,...").

    Chuỗi rỗng → không target nào. Phần tử malformed (thiếu ":", channel_id
    không phải số) bị SKIP kèm log warning thay vì raise — setting này đọc từ
    env ở task runtime nên một giá trị lỗi không được phép làm crash cron."""
    out: list[tuple[int, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        channel_id_raw, sep, branch_id = part.partition(":")
        if not sep:
            logger.warning(
                "KIOTVIET_IMPORT_CREATE_ONLY_TARGETS: bỏ qua phần tử thiếu ':' — %r",
                part,
            )
            continue
        try:
            channel_id = int(channel_id_raw.strip())
        except ValueError:
            logger.warning(
                "KIOTVIET_IMPORT_CREATE_ONLY_TARGETS: bỏ qua channel_id không hợp lệ — %r",
                part,
            )
            continue
        out.append((channel_id, branch_id.strip()))
    return tuple(out)


def iter_import_channels() -> Iterator[dict]:
    """Yield các kênh cấu hình trong ``settings.KIOTVIET_IMPORT_CREATE_ONLY_TARGETS``
    để import create-only.

    Mỗi item: {"skip": bool, "channel_id": int, "branch_id": str, "label": str}.
    skip=True kèm "reason" khi kênh không tồn tại trong DB hoặc đã xóa."""
    targets = _parse_import_create_only_targets(settings.KIOTVIET_IMPORT_CREATE_ONLY_TARGETS)
    existing_ids = set(
        KiotVietChannel.objects
        .filter(
            id__in=[channel_id for channel_id, _ in targets],
            deleted_at__isnull=True,
        )
        .values_list("id", flat=True)
    )
    for channel_id, branch_id in targets:
        label = f"kv{channel_id}"
        if channel_id not in existing_ids:
            yield {
                "skip": True,
                "channel_id": channel_id,
                "branch_id": "",
                "label": label,
                "reason": "kênh không tồn tại hoặc đã xóa",
            }
            continue
        yield {
            "skip": False,
            "channel_id": channel_id,
            "branch_id": branch_id,
            "label": label,
        }


def _import_invoices_for_target(
    *,
    channel_id: int,
    branch_ids: list[str],
    create_only: bool,
    from_dt: str,
    to_dt: str,
) -> dict:
    """Gọi method CÔNG KHAI ``import_invoices`` của ``ImportInvoicesService``
    (business logic đã move khỏi management command — WP8.1; KHÔNG qua ``call_command``)."""
    from apps.kiotviet.infrastructure.services.import_invoices_service import (
        ImportInvoicesService,
    )

    kv_channel = KiotVietChannel.objects.get(id=channel_id, deleted_at__isnull=True)
    service = ImportInvoicesService()
    return service.import_invoices(
        kv_channel=kv_channel,
        branch_ids=[str(b).strip() for b in branch_ids if str(b).strip()],
        dry_run=False,
        create_only=create_only,
        list_missing=False,
        from_dt=from_dt,
        to_dt=to_dt,
        target_sale_channel_value="",
    )


@shared_task(
    bind=True,
    name="apps.kiotviet.tasks.import_create_only_one_channel",
    soft_time_limit=600,
    time_limit=660,
)
def import_create_only_one_channel_task(
    self,
    channel_id: int,
    branch_id: str,
    window_from: str,
    window_to: str,
    deep: bool = False,
    lock_ttl: int = 660,
) -> dict:
    """Import create-only MỘT kênh. Sub-task fan-out bởi ``import_invoices_create_only_task``
    → các kênh chạy song song qua queue ``kiotviet``.

    Lock per-(channel, branch) (cache.add) chống chồng tick: tick 5' sau (hoặc gọi tay)
    trùng lần chạy trước còn dang dở → hai run cùng pre-scan trống → cùng tạo Order = đơn
    TRÙNG. Chưa chạy xong thì tick sau SKIP; lock tự hết hạn nếu worker chết giữa chừng.
    Key PHẢI gồm branch: 1 kênh khai nhiều branch trong targets thì các subtask dispatch
    cùng lúc — key chỉ theo channel làm branch sau luôn dính "lock held" và bị bỏ đói."""
    lock_key = f"kv:import_create:lock:{channel_id}:{branch_id}"
    if not cache.add(lock_key, 1, lock_ttl):
        if deep:
            # Deep sweep chạy 1 lần/đêm — thua lock với tick 5' mà bỏ luôn là mất
            # nguyên đêm quét sâu, im lặng (audit 2026-07-18). Đợi rồi thử lại.
            logger.warning(
                "import_create_only DEEP kv%s: lock held — retry sau 5'", channel_id
            )
            raise self.retry(countdown=300, max_retries=6)
        logger.info("import_create_only skip kv%s: lock held", channel_id)
        return {"channel_id": channel_id, "skipped_reason": "đang chạy (lock)"}

    try:
        result = _import_invoices_for_target(
            channel_id=channel_id,
            branch_ids=[branch_id],
            create_only=True,
            from_dt=window_from,
            to_dt=window_to,
        )
    except Exception:
        logger.exception("import_create_only failed channel_id=%s", channel_id)
        raise
    finally:
        cache.delete(lock_key)

    summary = {
        "channel_id": channel_id,
        "created": result.get("created", 0),
        "skipped": result.get("skipped", 0),
        "errors": result.get("errors", 0),
    }
    logger.info(
        "import_create_only kv%s done: created=%s, skipped=%s, errors=%s, window=%s..%s",
        channel_id, summary["created"], summary["skipped"], summary["errors"],
        window_from, window_to,
    )
    return summary


@shared_task(
    bind=True,
    name="apps.kiotviet.tasks.import_invoices_create_only",
    soft_time_limit=120,
    time_limit=180,
)
def import_invoices_create_only_task(
    self,
    from_dt: str | None = None,
    to_dt: str | None = None,
    lookback_days: int | None = None,
) -> dict:
    """Cron entrypoint: fan-out import create-only từng kênh trong
    ``settings.KIOTVIET_IMPORT_CREATE_ONLY_TARGETS`` và CHỈ tạo Order mới.

    Mỗi kênh dispatch thành một ``import_create_only_one_channel`` task riêng → các kênh
    chạy SONG SONG qua queue ``kiotviet``, kênh chậm không chặn kênh khác. Window mặc định
    = lùi ``KIOTVIET_IMPORT_CREATE_ONLY_LOOKBACK_DAYS`` ngày tới hiện tại (xem
    ``_import_window`` vì sao phải lùi); override qua from_dt/to_dt hoặc lookback_days
    (beat entry quét sâu ``kv-import-create-only-deep`` truyền lookback_days lớn)."""
    if from_dt and to_dt:
        window_from, window_to = from_dt, to_dt
    else:
        if lookback_days is None:
            lookback_days = int(
                getattr(settings, "KIOTVIET_IMPORT_CREATE_ONLY_LOOKBACK_DAYS", 1)
            )
        window_from, window_to = _import_window(lookback_days)

    summary: dict = {
        "window": {"from": window_from, "to": window_to},
        "dispatched": [],
        "skipped": [],
    }

    for item in iter_import_channels():
        channel_id = item["channel_id"]
        if item["skip"]:
            summary["skipped"].append({"channel_id": channel_id, "reason": item["reason"]})
            logger.warning("import_create_only skip %s: %s", item["label"], item["reason"])
            continue
        # Deep sweep (lookback lớn) cần limit RIÊNG: subtask mặc định 600/660s size cho
        # window 1-2 ngày; 31 ngày ≈ 155 page — sort id Asc là deterministic nên timeout
        # là đêm nào cũng chết đúng cùng offset, phần đuôi window không bao giờ được
        # quét (audit 2026-07-18). Truyền limit per-dispatch + lock_ttl khớp hard limit.
        deep = bool(lookback_days and lookback_days >= 7)
        soft_limit, hard_limit = (1800, 1860) if deep else (600, 660)
        import_create_only_one_channel_task.apply_async(
            kwargs={
                "channel_id": channel_id,
                "branch_id": item["branch_id"],
                "window_from": window_from,
                "window_to": window_to,
                "deep": deep,
                "lock_ttl": hard_limit,
            },
            soft_time_limit=soft_limit,
            time_limit=hard_limit,
        )
        summary["dispatched"].append(channel_id)

    logger.info(
        "import_invoices_create_only fan-out: dispatched=%s, skipped=%s, window=%s..%s",
        len(summary["dispatched"]), len(summary["skipped"]), window_from, window_to,
    )
    return summary


@shared_task(
    bind=True,
    name="apps.kiotviet.tasks.sync_kiotviet_returns",
    soft_time_limit=240,
    time_limit=300,
)
def sync_kiotviet_returns_task(self, lookback_hours: int = 2) -> dict:
    """Cron entrypoint: kéo phiếu trả hàng mới/cập nhật từ MỌI KiotViet channel.

    Cửa sổ lastModifiedFrom = now - lookback_hours (chồng lấn để không mất bản ghi khi
    lệch giờ / KiotViet cập nhật trễ). Lock single-flight chống chồng tick 15'."""
    with single_flight(_SYNC_RETURNS_LOCK_KEY, _SYNC_RETURNS_LOCK_TTL) as acquired:
        if not acquired:
            logger.info("sync_kiotviet_returns skip: lock held (đang chạy)")
            return {"skipped": "lock_held"}

        last_modified_from = (
            timezone.localtime() - timedelta(hours=lookback_hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
        summary = sync_returns_all_channels(last_modified_from=last_modified_from)
        if summary.get("errors"):
            logger.warning("sync_kiotviet_returns finished with errors: %s", summary["errors"])
        return summary
