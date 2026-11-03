"""Đối soát new->old KiotViet channel, gọi THẲNG SyncInvoicesService (không qua
management command). Dùng chung cho cron (apps.kiotviet.tasks) và command CLI
``reconcile_channels``.

Mỗi cặp chạy 6 mode tuần tự (thứ tự có chủ đích): dọn đơn hủy/trùng trước, tạo đơn
thiếu, rồi sync lại khi lệch giá / phí ship+vận đơn / trạng thái.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Iterator

from apps.kiotviet.infrastructure.services.sync_invoices_service import SyncInvoicesService
from apps.kiotviet.models import KiotVietChannel

logger = logging.getLogger(__name__)

# (mode_name, run-flags). Thứ tự phụ thuộc nhau: purge -> tạo thiếu -> sync lệch.
RECONCILE_MODES: list[tuple[str, dict]] = [
    ("purge_cancelled", {"purge_cancelled": True}),
    ("purge_duplicates", {"purge_duplicates": True}),
    ("reconcile_sync", {"reconcile_sync": True}),
]


def _tail(text: str, limit: int = 2000) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return f"...<bỏ {len(text) - limit} ký tự đầu> {text[-limit:]}"


def reconcile_one_pair(
    *,
    source_kv_id: int,
    source_branch_id: str,
    target_kv_id: int,
    target_branch_id: str,
    from_dt: str,
    to_dt: str,
    dry_run: bool = False,
    extra_log: Callable[[str, str], None] | None = None,
) -> dict:
    """Đối soát MỘT cặp new->old qua SyncInvoicesService. Chạy 6 mode tuần tự; mode lỗi
    KHÔNG chặn mode sau. Trả về dict {modes, failed_modes}. ``dry_run=True`` chỉ log dự
    định, KHÔNG DELETE/CREATE. ``extra_log(channel, msg)`` optional để stream ra ngoài."""
    pair_label = f"kv{source_kv_id}->kv{target_kv_id}"
    result: dict = {
        "source_kv": source_kv_id,
        "source_branch_id": source_branch_id,
        "target_kv": target_kv_id,
        "target_branch_id": target_branch_id,
        "window": {"from": from_dt, "to": to_dt},
        "modes": {},
    }
    failed_modes: list[str] = []

    total_modes = len(RECONCILE_MODES)
    for idx, (mode_name, flags) in enumerate(RECONCILE_MODES, start=1):
        captured: deque[str] = deque(maxlen=400)  # giữ tail, tránh phình RAM khi 800+ đơn

        def _cb(channel: str, msg: str) -> None:
            captured.append(msg)
            if extra_log:
                extra_log(channel, msg)

        # Banner rõ ràng đầu mỗi mode: purge_cancelled/purge_duplicates/reconcile_sync
        # đều mở đầu bằng "Pre-scan..."/"===" giống nhau nên không có banner thì khó phân biệt.
        _cb("stdout", f"\n######## [{pair_label}] MODE {idx}/{total_modes}: {mode_name} ########")

        try:
            SyncInvoicesService(log_callback=_cb).run(
                source_kiotviet_channel_id=source_kv_id,
                source_branch_id=source_branch_id,
                target_kiotviet_channel_id=target_kv_id,
                target_branch_id=target_branch_id,
                from_dt=from_dt,
                to_dt=to_dt,
                yes=True,  # cho phép các mode purge thực thi DELETE
                dry_run=dry_run,
                **flags,
            )
            result["modes"][mode_name] = "ok"
            logger.info("reconcile %s [%s] ok | %s", pair_label, mode_name, _tail("\n".join(captured)))
        except Exception as exc:  # noqa: BLE001
            result["modes"][mode_name] = f"error: {exc}"
            failed_modes.append(mode_name)
            logger.exception(
                "reconcile %s [%s] FAILED: %s | %s",
                pair_label, mode_name, exc, _tail("\n".join(captured)),
            )

    result["failed_modes"] = failed_modes
    if failed_modes:
        # Log ERROR tổng hợp để monitoring (Grafana/Loki) bắt ngay khi có mode hỏng,
        # thay vì lỗi âm thầm như khi còn nuốt output qua call_command.
        logger.error(
            "reconcile_one_pair %s: %d/%d MODE FAILED -> %s | window=%s..%s",
            pair_label, len(failed_modes), len(RECONCILE_MODES), failed_modes, from_dt, to_dt,
        )
    return result


def iter_reconcile_pairs() -> Iterator[dict]:
    """Yield cấu hình từng cặp kho new(old=False, có paired_old_channel) -> old đã ghép.

    Kênh có old_sync_branch_map: yield 1 item cho MỖI cặp kho trong map.
    Map rỗng: giữ hành vi legacy 1 cặp (branch_id -> old_sync_branch_id/old.branch_id).
    Mỗi item: {"skip": bool, "pair": label, ...}. skip=True kèm "reason" khi thiếu
    branch_id (source.branch_id hoặc target old_sync_branch_id/branch_id)."""
    from .branch_sync_map import normalize_branch_map

    new_channels = (
        KiotVietChannel.objects
        .filter(old=False, deleted_at__isnull=True, paired_old_channel__isnull=False)
        .select_related("paired_old_channel")
        .order_by("id")
    )
    for new_channel in new_channels:
        old_channel = new_channel.paired_old_channel
        if old_channel is None or old_channel.deleted_at is not None:
            continue
        pair_label = f"kv{new_channel.id}->kv{old_channel.id}"

        branch_map = normalize_branch_map(new_channel.old_sync_branch_map)
        if branch_map:
            for entry in branch_map:
                yield {
                    "skip": False,
                    "pair": f"{pair_label}[b{entry['source_branch_id']}]",
                    "source_kv_id": new_channel.id,
                    "source_branch_id": entry["source_branch_id"],
                    "target_kv_id": old_channel.id,
                    "target_branch_id": entry["target_branch_id"],
                }
            continue

        source_branch_id = (new_channel.branch_id or "").strip()
        target_branch_id = (
            (new_channel.old_sync_branch_id or "").strip()
            or (old_channel.branch_id or "").strip()
        )
        if not source_branch_id or not target_branch_id:
            yield {
                "skip": True,
                "pair": pair_label,
                "reason": "thiếu branch_id (source.branch_id hoặc target old_sync_branch_id/branch_id)",
            }
            continue
        yield {
            "skip": False,
            "pair": pair_label,
            "source_kv_id": new_channel.id,
            "source_branch_id": source_branch_id,
            "target_kv_id": old_channel.id,
            "target_branch_id": target_branch_id,
        }
