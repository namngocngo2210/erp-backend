"""Đối chiếu cơ hội Foxia đã đẩy đi với đơn Foxia mà chiều đồng bộ kéo về.

Thuần LOCAL: chiều đồng bộ (`FoxiaOrderSyncService`) đã ghi `cohoi_id` lên mỗi
`FoxiaOrderLink`, nên biết một cơ hội đã lên đơn hay chưa chỉ là một phép join —
không cần thêm một đường gọi JSON-RPC nữa để nuôi.

Giới hạn đi kèm lựa chọn đó: job chỉ thấy được đơn nào chiều đồng bộ đã kéo về.
Cửa sổ cron là 3 ngày theo `ngayxacnhan`, nên cơ hội chốt muộn hơn thế nằm treo
cho tới lần `foxia_backfill` phủ được ngày đó. Chạy lại bao nhiêu lần cũng an
toàn — mỗi lượt chỉ đụng vào dòng còn trống.
"""

from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


class FoxiaLeadMatchService:
    """Điền `FoxiaLeadLink.foxia_order_id` cho những cơ hội đã thành đơn."""

    DEFAULT_LIMIT = 500

    @staticmethod
    def match(limit: int = DEFAULT_LIMIT) -> int:
        """Trả về số dòng vừa khớp được. Không bao giờ ghi đè dòng đã khớp."""
        from apps.foxia.models import FoxiaLeadLink, FoxiaOrderLink

        pending = list(
            FoxiaLeadLink.objects.filter(foxia_order_id__isnull=True).order_by("id")[:limit]
        )
        if not pending:
            return 0

        # Một cơ hội có thể sinh nhiều đơn bên Foxia. Lấy đơn có id NHỎ NHẤT —
        # đơn đầu tiên của cơ hội — để kết quả không đổi giữa hai lượt chạy.
        found: dict[int, int] = {}
        rows = (
            FoxiaOrderLink.objects.filter(cohoi_id__in={lead.cohoi_id for lead in pending})
            .order_by("foxia_id")
            .values_list("cohoi_id", "foxia_id")
        )
        for cohoi_id, foxia_id in rows:
            found.setdefault(cohoi_id, foxia_id)

        now = timezone.now()
        matched = []
        for lead in pending:
            foxia_order_id = found.get(lead.cohoi_id)
            if foxia_order_id is None:
                continue
            lead.foxia_order_id = foxia_order_id
            lead.matched_at = now
            matched.append(lead)

        if matched:
            FoxiaLeadLink.objects.bulk_update(matched, ["foxia_order_id", "matched_at"])
        logger.info(
            "Foxia doi chieu co hoi: %s/%s dong khop (con trong: %s)",
            len(matched), len(pending), len(pending) - len(matched),
        )
        return len(matched)
