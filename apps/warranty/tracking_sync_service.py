"""Cron trace Best → cập nhật trạng thái VC thô của hành trình BH (10 phút/lần).

KHÔNG đi qua store_event/apply_event của best_express (mã BH nhập tay không có
BestShipment → sinh rác event unmatched). Đọc thẳng traceLogs mới nhất.
"""

from __future__ import annotations

import logging

from django.db.models import Q
from django.utils import timezone

from apps.warranty.models import WarrantyClaimStatus, WarrantyJourney
from apps.warranty.tracking_status import (
    JOURNEY_CODE_FIELDS,
    map_best_vc_group,
    should_apply_best_status,
)

logger = logging.getLogger(__name__)


def _latest_status_by_code(results: list[dict]) -> dict[str, tuple[str, int]]:
    """TraceQuery results → {mã: (mã trạng thái mới nhất, mốc modified_at ms)}."""
    latest: dict[str, tuple[str, int]] = {}
    for item in results:
        code = str(item.get("code_best") or item.get("merchant_order_code") or "").strip()
        if not code:
            continue
        logs = [log for log in (item.get("traceLogs") or []) if isinstance(log, dict)]
        if not logs:
            continue
        newest = max(logs, key=lambda log: log.get("modified_at") or 0)
        status = str(newest.get("status") or "").strip()
        if not status:
            continue
        try:
            modified_ms = int(newest.get("modified_at") or 0)
        except (TypeError, ValueError):
            modified_ms = 0
        latest[code] = (status, modified_ms)
    return latest


class WarrantyTrackingSyncService:
    @staticmethod
    def sync() -> dict:
        # Lazy import: warranty không phụ thuộc module-level vào best_express.
        from apps.best_express.exceptions import BestExpressError
        from apps.best_express.infrastructure.best_client import (
            TRACE_MAX_CODES,
            BestExpressClient,
        )
        from apps.best_express.models import BestExpressAccount

        journeys = list(
            WarrantyJourney.objects.filter(claim__deleted_at__isnull=True)
            .exclude(
                claim__status__in=[
                    WarrantyClaimStatus.COMPLETED,
                    WarrantyClaimStatus.CANCELLED,
                ]
            )
            .exclude(
                Q(new_tracking_code="") & Q(recall_code="") & Q(return_tracking_code="")
            )
        )
        codes: set[str] = set()
        for j in journeys:
            for code_field, _, _ in JOURNEY_CODE_FIELDS:
                code = (getattr(j, code_field) or "").strip()
                if code:
                    codes.add(code)
        if not codes:
            return {"codes": 0, "updated": 0, "skipped": "no_codes"}

        account = (
            BestExpressAccount.objects.filter(is_active=True)
            .order_by("environment")  # "production" < "test" alphabet → production trước
            .first()
        )
        if account is None:
            logger.warning("warranty tracking sync: không có BestExpressAccount active — bỏ qua")
            return {"codes": 0, "updated": 0, "skipped": "no_account"}

        client = BestExpressClient(account)
        ordered = sorted(codes)
        results: list[dict] = []
        for start in range(0, len(ordered), TRACE_MAX_CODES):
            chunk = ordered[start : start + TRACE_MAX_CODES]
            try:
                data = client.trace_query(chunk)
            except (BestExpressError, Exception) as exc:  # noqa: BLE001 — cron: log rồi bỏ qua vòng này
                logger.warning(
                    "warranty tracking sync: trace lỗi (%s mã): %s", len(chunk), exc
                )
                continue
            results.extend(data.get("data") or [])

        latest = _latest_status_by_code(results)
        now = timezone.now()
        updated = 0
        # `journeys` được nạp vào bộ nhớ Ở TRÊN, TRƯỚC vòng gọi mạng trace_query
        # (mất hàng chục giây). Nếu so guard bằng `getattr(j, ms_field)` (ảnh
        # chụp cũ) rồi `j.save()` thẳng, một webhook đến trong lúc chờ mạng sẽ
        # bị đè ngược — badge tụt VÀ ms lùi lại, dễ tổn thương hơn trước (review
        # 2026-07-22 finding #1). Dùng UPDATE có điều kiện ở tầng DB như
        # subscriber để guard luôn so với giá trị ĐANG CÓ tại thời điểm ghi.
        for j in journeys:
            journey_touched = False
            for code_field, status_field, ms_field in JOURNEY_CODE_FIELDS:
                code = (getattr(j, code_field) or "").strip()
                found = latest.get(code) if code else None
                if not found:
                    continue
                new_status, new_ms = found
                # Mã lạ (kể cả "2" hủy) → giữ nguyên theo dữ liệu gần nhất đã
                # biết, không ghi status lẫn ms — cùng nguyên tắc với subscriber
                # (finding #4), tránh badge tụt về "Tạo mới".
                if map_best_vc_group(new_status) is None:
                    continue
                # Bộ lọc sớm dựa trên ảnh chụp cũ — chỉ để tránh bắn UPDATE vô
                # ích; KHÔNG phải trọng tài chống race (xem should_apply_best_status).
                if not should_apply_best_status(getattr(j, ms_field), new_ms):
                    continue
                # Đẩy `ms_field` TRƯỚC khi so status (finding #5): nếu so status
                # trước rồi mới xét ms, những row status ổn định nhưng ms=0 lúc
                # deploy sẽ KHÔNG BAO GIỜ được backfill ms — guard #4 nằm chết
                # trên các row đó vô thời hạn.
                rows = WarrantyJourney.objects.filter(
                    pk=j.pk, **{f"{ms_field}__lte": new_ms}
                ).update(
                    **{
                        status_field: new_status,
                        ms_field: new_ms,
                        "tracking_synced_at": now,
                        "updated_at": now,
                    }
                )
                if rows:
                    journey_touched = True
            if journey_touched:
                updated += 1
        return {"codes": len(codes), "updated": updated, "skipped": ""}
