"""Ghi bảng "Cấu hình liên kết Foxia" (kênh × loại nguồn → idnguonkhachhang).

Chiều GHI của bảng cấu hình; chiều đọc (serialize lưới) vẫn ở
``transport/link_config_views.py``. Tách ra theo audit 2026-09-23 T3/P2-1: view chỉ
validate payload → gọi đây → serialize; ``bulk_create``/``transaction.atomic`` không
được nằm ở tầng transport (guard ``apps/common/tests/test_no_atomic_in_transport.py``).
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max

from apps.foxia.models import FoxiaLinkCell, FoxiaLinkChannel


class FoxiaLinkConfigService:
    """Thêm hàng kênh và lưu hàng loạt ô của lưới cấu hình."""

    @staticmethod
    def add_channels(channel_ids: list[int]) -> int:
        """Thêm hàng kênh cho các id chưa có mặt (idempotent). Trả về số hàng mới.

        ``dict.fromkeys`` khử trùng lặp NGAY TRONG payload (giữ thứ tự) — thiếu bước
        này, id lặp hai lần trong cùng một mảng sinh hai object cùng giá trị FK
        OneToOne rồi vỡ ``IntegrityError`` ở ``bulk_create`` thay vì bị bỏ qua như hợp
        đồng idempotent đã hứa.
        """
        ids = list(dict.fromkeys(channel_ids))
        existing = set(
            FoxiaLinkChannel.objects.filter(channel_id__in=ids).values_list("channel_id", flat=True)
        )
        # ``count()`` sai sau khi đã xoá hàng: sort_order còn lại có thể VƯỢT count()
        # (vd 3 hàng sort_order 0,1,5 sau khi xoá bớt — count()=3 nhưng max=5), hàng
        # mới thêm bị chèn LẪN vào giữa dãy cũ thay vì luôn ở cuối. Max(sort_order)+1
        # mới đúng. So sánh tường minh với ``None`` (KHÔNG ``or -1``): sort_order hợp lệ
        # có thể là 0, và ``0 or -1`` sẽ sai thành -1 rồi next_order=0 đụng hàng đầu.
        current_max = FoxiaLinkChannel.objects.aggregate(m=Max("sort_order"))["m"]
        next_order = 0 if current_max is None else current_max + 1
        created = []
        for value in ids:
            if value in existing:
                continue
            created.append(FoxiaLinkChannel(channel_id=value, sort_order=next_order))
            next_order += 1
        FoxiaLinkChannel.objects.bulk_create(created)
        return len(created)

    @staticmethod
    def save_cells(
        *,
        deletes: list[tuple[int, str]],
        upserts: list[tuple[int, str, int]],
        actor,
    ) -> None:
        """Áp một lô thay đổi ô đã validate: ``deletes`` = (link_channel_id, kind),
        ``upserts`` = (link_channel_id, kind, foxia_source_id). Cả lô trong một
        transaction — bảng cấu hình lưu nửa vời nguy hiểm hơn là không lưu.
        """
        with transaction.atomic():
            for row, kind in deletes:
                FoxiaLinkCell.objects.filter(link_channel_id=row, kind=kind).delete()
            for row, kind, value in upserts:
                FoxiaLinkCell.objects.update_or_create(
                    link_channel_id=row,
                    kind=kind,
                    defaults={"foxia_source_id": value, "updated_by": actor},
                )
