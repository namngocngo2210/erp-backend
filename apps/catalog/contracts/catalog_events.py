"""Domain event app catalog phát cho module khác qua event bus in-process.

`catalog` KHÔNG biết ai tiêu thụ — consumer tự subscribe ở `ready()` của app
mình. Xem `apps/common/events.py` và `docs/architecture-standard.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from apps.common.events import DomainEvent


@dataclass(frozen=True)
class ProductSaved(DomainEvent):
    """Phát sau MỖI ``Product.save()`` (tạo lẫn sửa), sau commit.

    Thay cho ``post_save`` mà app khác từng móc thẳng vào model (audit 2026-09-23
    T3/P2-4). ``bulk_*``/``queryset.update()`` không phát — y như signal cũ.
    """

    event_type: ClassVar[str] = "catalog.product_saved"
    product_id: int = 0
    created: bool = False
    is_deleted: bool = False


@dataclass(frozen=True)
class ProductCategorySaved(DomainEvent):
    """Phát sau MỖI ``ProductCategory.save()`` (tạo lẫn sửa), sau commit."""

    event_type: ClassVar[str] = "catalog.product_category_saved"
    category_id: int = 0
    created: bool = False
    is_deleted: bool = False


@dataclass(frozen=True)
class ProductGroupSaved(DomainEvent):
    """Phát sau MỖI ``ProductGroup.save()`` (tạo, sửa, xoá mềm), sau commit.

    Đổi mã/xoá nhóm làm đổi đơn vị đồng bộ của mọi sản phẩm trong nhóm — consumer
    tự đọc danh sách sản phẩm theo ``group_id`` nếu cần.
    """

    event_type: ClassVar[str] = "catalog.product_group_saved"
    group_id: int = 0
    created: bool = False
    is_deleted: bool = False


@dataclass(frozen=True)
class ProductsChanged(DomainEvent):
    """Một nhóm sản phẩm đổi qua đường KHÔNG đi ``Product.save()`` (bulk/queryset,
    bảng phụ như thành phần combo) — lấp khoảng trống của ``ProductSaved``.

    Nơi ghi bulk phát bằng ``event_bus.publish_on_commit``. ``reason`` chỉ để log.
    """

    event_type: ClassVar[str] = "catalog.products_changed"
    product_ids: tuple[int, ...] = ()
    reason: str = ""
