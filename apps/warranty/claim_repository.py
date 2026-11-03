from __future__ import annotations

from django.db.models import Q

from apps.common import db_lookups  # noqa: F401  (đăng ký lookup `__ilike_contains`)
from apps.warranty.models import WarrantyClaim


class WarrantyClaimRepository:
    """Truy vấn phiếu bảo hành (chỉ tầng đọc/ghi DB)."""

    @staticmethod
    def base_qs():
        return (
            WarrantyClaim.objects.filter(deleted_at__isnull=True)
            .select_related("order", "order__kiotviet_channel", "created_by")
            .prefetch_related("journeys", "journeys__product")
        )

    @classmethod
    def _apply_filters(cls, qs, q: str, status: int | None):
        if status is not None:
            qs = qs.filter(status=status)
        q = (q or "").strip()
        if q:
            # `phone` / `phone_normalized` / `shipping_code` / `order_code` đi qua
            # `__ilike_contains` để planner còn dùng được index GIN trigram bare-column
            # trên `orders` (`order_phone_trgm_idx`, `order_phone_norm_trgm_idx`,
            # `order_ship_code_trgm_idx`, `order_order_code_trgm_idx`); `icontains`
            # sinh `UPPER(col) LIKE UPPER(%s)` và vô hiệu hoá chúng — EXPLAIN prod
            # 23/09: Parallel Seq Scan `orders` (audit 2026-09-23 C1;
            # apps/common/db_lookups.py). Comment cũ "phone không có trgm" là sai.
            # Riêng `name` GIỮ `icontains`: cột này có functional index
            # `order_name_upper_trgm_idx` trên `UPPER(name::text)` — đúng biểu thức
            # `icontains` sinh ra, đổi sang ILIKE mới là mất index.
            qs = qs.filter(
                Q(order__phone__ilike_contains=q)
                | Q(order__phone_normalized__ilike_contains=q)
                | Q(order__shipping_code__ilike_contains=q)
                | Q(order__order_code__ilike_contains=q)
                | Q(order__name__icontains=q)
            )
        return qs

    @classmethod
    def list_claims(
        cls, q: str, status: int | None, limit: int, offset: int
    ) -> tuple[list[WarrantyClaim], int]:
        qs = cls._apply_filters(cls.base_qs(), q, status)
        total = qs.count()
        rows = list(qs.order_by("-created_at")[offset : offset + limit])
        return rows, total

    @classmethod
    def list_all_claims(cls, q: str, status: int | None) -> list[WarrantyClaim]:
        """Không phân trang — dùng cho export Excel (áp cùng filter q/status với list)."""
        qs = cls._apply_filters(cls.base_qs(), q, status)
        return list(qs.order_by("-created_at"))

    @classmethod
    def claims_queryset(cls, q: str, status: int | None):
        """QuerySet CHƯA evaluate, đã áp filter + order — dùng cho nơi gọi cần tự
        LIMIT ở tầng SQL (ví dụ export async cần `qs[:cap+1]` để phát hiện vượt
        trần mà không tải hết vào RAM). Khác `list_all_claims` (materialize hẳn
        thành `list`, không cho LIMIT thêm sau đó). Mirror khuôn
        `StockQueryService.list_stock` mà `inventory/exports_definitions.py` dùng
        — public API thay vì gọi thẳng `_apply_filters` (private, chỉ nên dùng
        nội bộ class này).
        """
        return cls._apply_filters(cls.base_qs(), q, status).order_by("-created_at")

    @classmethod
    def list_by_ids(cls, ids: list[int]) -> list[WarrantyClaim]:
        """Dùng cho export theo lựa chọn — giữ nguyên thứ tự -created_at, không áp filter."""
        return list(cls.base_qs().filter(id__in=ids).order_by("-created_at"))

    @classmethod
    def get_claim(cls, claim_id: int) -> WarrantyClaim | None:
        return cls.base_qs().filter(id=claim_id).first()
