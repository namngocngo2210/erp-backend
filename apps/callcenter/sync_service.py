"""Sync lịch sử cuộc gọi Zenify → CallRecord + khớp đơn theo SĐT khách.

Luồng 1 của pipeline (docs/zenify-call-transcripts.md §4):
- upsert theo call_id (dedupe cửa sổ cron chồng lấn),
- match caller_normalized → Order.phone_normalized, LOẠI đơn sàn shopee/tiktok,
- chọn đơn có created_at gần start_time nhất trong ±MATCH_WINDOW ngày (cuộc gọi
  tư vấn tới TRƯỚC khi lên đơn vẫn khớp),
- không khớp được thì match_attempts += 1; đủ ZENIFY_CALL_MATCH_MAX_ATTEMPTS lần
  thì các vòng sau bỏ qua (user chốt 18/07: dừng sau 7 lần quét),
- đơn XOÁ MỀM rồi tạo lại cùng SĐT: ghi âm đang kẹt ở đơn xoá được chuyển sang
  đơn còn sống (rematch_records_of_deleted_orders, chạy cùng tick).
"""

from __future__ import annotations

import logging
import zoneinfo
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from apps.common.phone_utils import normalize_phone
from apps.orders.models import Order, OrderSource

from .call_history import fetch_call_histories, format_zenify_time
from .models import CallRecord, TranscribeStatus

logger = logging.getLogger(__name__)

# Zenify trả giờ VN naive ("YYYY-MM-DD HH:mm:ss") — đồng bộ CELERY_TIMEZONE.
ZENIFY_TZ = zoneinfo.ZoneInfo("Asia/Bangkok")
MARKETPLACE_SOURCES = (OrderSource.SHOPEE, OrderSource.TIKTOK)


def _empty_summary() -> dict:
    return {
        "read": 0,
        "created": 0,
        "existing": 0,
        "skipped_no_call_id": 0,
        "matched": 0,
        "match_scanned": 0,
        "match_exhausted": 0,
        "rematched_deleted": 0,
    }


def parse_zenify_datetime(raw) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=ZENIFY_TZ)
        except ValueError:
            continue
    return None


def _parse_int(raw) -> int:
    try:
        return max(int(float(raw)), 0)
    except (TypeError, ValueError):
        return 0


def _initial_status(recording_url: str, acd_time: int) -> int:
    if not recording_url or acd_time < settings.ZENIFY_CALL_MIN_ACD_SECONDS:
        return TranscribeStatus.SKIPPED
    return TranscribeStatus.PENDING


def sync_call_histories(
    *,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    dry_run: bool = False,
) -> dict:
    """Kéo call-histories trong cửa sổ và upsert CallRecord, rồi chạy vòng match.

    Mặc định cửa sổ = [now - ZENIFY_CALL_SYNC_WINDOW_MINUTES, now] (chồng lấn giữa
    các tick — dedupe bằng unique call_id).
    """
    now = timezone.now().astimezone(ZENIFY_TZ)
    window_from = (from_dt or now - timedelta(minutes=settings.ZENIFY_CALL_SYNC_WINDOW_MINUTES)).astimezone(ZENIFY_TZ)
    window_to = (to_dt or now).astimezone(ZENIFY_TZ)

    summary = _empty_summary()
    items = fetch_call_histories(format_zenify_time(window_from), format_zenify_time(window_to))

    for item in items:
        summary["read"] += 1
        call_id = str(item.get("callId") or "").strip()
        if not call_id:
            summary["skipped_no_call_id"] += 1
            continue

        caller = str(item.get("caller") or "").strip()
        recording_url = str(item.get("url") or "").strip()
        acd_time = _parse_int(item.get("acdTime"))

        if dry_run:
            exists = CallRecord.objects.filter(call_id=call_id).exists()
            summary["existing" if exists else "created"] += 1
            continue

        record, created = CallRecord.objects.get_or_create(
            call_id=call_id,
            defaults={
                "call_type": str(item.get("callType") or "").strip(),
                "agent_id": str(item.get("agentId") or "").strip(),
                "agent_name": str(item.get("username") or "").strip(),
                "caller": caller,
                "caller_normalized": normalize_phone(caller),
                "called": str(item.get("called") or "").strip(),
                "recording_url": recording_url,
                "start_time": parse_zenify_datetime(item.get("startTime")),
                "end_time": parse_zenify_datetime(item.get("endTime")),
                "acd_time": acd_time,
                "transcribe_status": _initial_status(recording_url, acd_time),
            },
        )
        if created:
            summary["created"] += 1
            continue

        summary["existing"] += 1
        # Zenify có thể bổ sung url ghi âm muộn (file upload sau khi cuộc gọi kết
        # thúc) — record SKIPPED vì thiếu url được mở lại thành PENDING. Record đã
        # done/failed/pending giữ nguyên, không đè transcript.
        if (
            recording_url
            and not record.recording_url
            and record.transcribe_status == TranscribeStatus.SKIPPED
        ):
            record.recording_url = recording_url
            record.acd_time = acd_time
            record.transcribe_status = _initial_status(recording_url, acd_time)
            record.save(update_fields=["recording_url", "acd_time", "transcribe_status", "updated_at"])

    if not dry_run:
        match_summary = match_unmatched_records()
        summary["matched"] = match_summary["matched"]
        summary["match_scanned"] = match_summary["scanned"]
        summary["match_exhausted"] = match_summary["exhausted"]
        summary["rematched_deleted"] = rematch_records_of_deleted_orders()["moved"]
    return summary


def _candidate_orders_by_phone(phones: set[str]) -> dict[str, list[tuple[int, datetime]]]:
    """Map phone_normalized -> [(order_id, created_at)] MỘT query cho cả lô (không N+1).

    Loại đơn sàn (shopee/tiktok) theo phạm vi đã chốt; đơn order_source null vẫn
    thuộc phạm vi (landing/hotline/... chưa gán nguồn).
    """
    if not phones:
        return {}
    rows = (
        Order.objects.filter(phone_normalized__in=phones, deleted_at__isnull=True)
        .exclude(order_source__in=MARKETPLACE_SOURCES)
        .values_list("phone_normalized", "id", "created_at")
    )
    by_phone: dict[str, list[tuple[int, datetime]]] = {}
    for phone, order_id, created_at in rows:
        by_phone.setdefault(phone, []).append((order_id, created_at))
    return by_phone


def pick_order_for_call(
    candidates: list[tuple[int, datetime]], call_time: datetime
) -> int | None:
    """Đơn có created_at gần call_time nhất trong ±ZENIFY_CALL_MATCH_WINDOW_DAYS."""
    window = timedelta(days=settings.ZENIFY_CALL_MATCH_WINDOW_DAYS)
    best_id: int | None = None
    best_delta: timedelta | None = None
    for order_id, created_at in candidates:
        delta = abs(created_at - call_time)
        if delta > window:
            continue
        if best_delta is None or delta < best_delta:
            best_id = order_id
            best_delta = delta
    return best_id


# Lịch BACKOFF cho lượt khớp thứ n (giờ tính từ lúc record được tạo): giữ đúng chốt
# "dừng sau ZENIFY_CALL_MATCH_MAX_ATTEMPTS lượt quét" của user nhưng GIÃN DẦN thay vì
# mỗi tick 15' đốt 1 lượt — kiểu cũ 7 lượt = 1h45, trong khi use-case thiết kế §2 là
# "gọi tư vấn TRƯỚC khi lên đơn" (khách lên đơn sau vài giờ tới vài ngày). Với 7 lượt,
# lịch này phủ ~3 ngày (user chốt 18/07: giãn dần). Attempt thứ n chỉ được tiêu khi
# record đã tồn tại đủ THRESHOLDS[n] giờ — không cần thêm cột/migration.
_MATCH_BACKOFF_HOURS = (0, 0.25, 0.75, 2, 5, 12, 24, 48, 72)


def _match_backoff_ready(record, now) -> bool:
    attempts = record.match_attempts
    if attempts >= len(_MATCH_BACKOFF_HOURS):
        return True  # vượt lịch (max_attempts cấu hình cao hơn) → không chặn thêm
    anchor = record.created_at or now
    return now >= anchor + timedelta(hours=_MATCH_BACKOFF_HOURS[attempts])


def rematch_records_of_deleted_orders(*, dry_run: bool = False) -> dict:
    """Chuyển ghi âm đang gắn đơn XOÁ MỀM sang đơn còn sống cùng SĐT.

    Xoá đơn trong app chỉ set deleted_at nên FK CallRecord.order vẫn trỏ vào
    đơn xoá, còn vòng match_unmatched_records chỉ quét order IS NULL — sale xoá
    đơn rồi tạo lại (cùng SĐT) thì đơn mới không thấy ghi âm nào (19/09/2026).

    Nằm ở callcenter thay vì móc vào chỗ xoá đơn: orders là tầng DƯỚI callcenter
    (import-linter), và xoá lẻ/xoá hàng loạt là hai đường khác nhau. Chỉ chuyển
    khi CÓ đơn thay thế theo đúng luật pick_order_for_call; không có thì để
    nguyên ở đơn xoá (không gỡ về NULL để khỏi đốt lượt backoff) — tick sau đơn
    mới xuất hiện vẫn chuyển được. Tập "đơn xoá còn ghi âm" nhỏ (vài chục) nên
    quét lại mỗi tick là rẻ.
    """
    stuck = list(
        CallRecord.objects.filter(order__deleted_at__isnull=False)
        .exclude(caller_normalized="")
        .values_list("id", "order_id", "caller_normalized", "start_time", "created_at")
    )
    summary: dict = {"scanned": len(stuck), "moved": 0}
    if dry_run:
        summary["moves"] = []
    if not stuck:
        return summary

    by_phone = _candidate_orders_by_phone({phone for _, _, phone, _, _ in stuck})
    now = timezone.now()
    for record_id, old_order_id, phone, start_time, created_at in stuck:
        call_time = start_time or created_at or now
        new_order_id = pick_order_for_call(by_phone.get(phone, []), call_time)
        if new_order_id is None:
            continue
        summary["moved"] += 1
        if dry_run:
            summary["moves"].append((record_id, old_order_id, new_order_id))
            continue
        CallRecord.objects.filter(pk=record_id).update(
            order_id=new_order_id, matched_at=now, updated_at=now
        )
    return summary


def match_unmatched_records() -> dict:
    """Khớp các record chưa có đơn (match_attempts < max). Chạy cuối mỗi lượt sync."""
    summary = {"scanned": 0, "matched": 0, "exhausted": 0}
    pending = list(
        CallRecord.objects.filter(
            order__isnull=True,
            match_attempts__lt=settings.ZENIFY_CALL_MATCH_MAX_ATTEMPTS,
        ).exclude(caller_normalized="")
    )
    if not pending:
        return summary

    by_phone = _candidate_orders_by_phone({r.caller_normalized for r in pending})
    now = timezone.now()
    for record in pending:
        # Chưa tới lượt theo lịch backoff → bỏ qua tick này, KHÔNG đốt attempt.
        if not _match_backoff_ready(record, now):
            continue
        summary["scanned"] += 1
        call_time = record.start_time or record.created_at or now
        order_id = pick_order_for_call(by_phone.get(record.caller_normalized, []), call_time)
        if order_id is None:
            record.match_attempts += 1
            if record.match_attempts >= settings.ZENIFY_CALL_MATCH_MAX_ATTEMPTS:
                summary["exhausted"] += 1
            record.save(update_fields=["match_attempts", "updated_at"])
            continue
        record.order_id = order_id
        record.matched_at = now
        record.save(update_fields=["order", "matched_at", "updated_at"])
        summary["matched"] += 1
    return summary
