"""Chỉ mục mẫu mã Pancake để đối chiếu SKU trước khi tạo (§3.3 — không tạo trùng).

Một mẫu mã được đánh chỉ mục theo CẢ `custom_id` lẫn `barcode` (chuẩn hoá hoa);
một SKU khớp > 1 mẫu mã khác nhau = xung đột, người xem xử lý tay.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from apps.pancake_pos.domain.entities import PancakeVariation
from apps.pancake_pos.domain.mapping import normalize_key


@dataclass
class PancakeIndex:
    by_sku: dict[str, dict[str, PancakeVariation]] = field(default_factory=dict)
    products_by_custom_id: dict[str, set[str]] = field(default_factory=dict)
    product_of_variation: dict[str, str] = field(default_factory=dict)
    variations_by_product: dict[str, dict[str, PancakeVariation]] = field(default_factory=dict)
    # True = dựng từ TOÀN BỘ mẫu mã của shop (full sync); False = chỉ phần tìm theo
    # SKU/custom_id (incremental) — "không thấy" khi đó không có nghĩa "không có".
    complete: bool = True

    @property
    def total_variations(self) -> int:
        return len(self.product_of_variation)

    def add(self, variation: PancakeVariation) -> None:
        if not variation.id:
            return
        self.product_of_variation[variation.id] = variation.product_id
        if variation.product_id:
            self.variations_by_product.setdefault(variation.product_id, {})[variation.id] = variation
        for raw in (variation.custom_id, variation.barcode):
            key = normalize_key(raw)
            if key:
                self.by_sku.setdefault(key, {})[variation.id] = variation
        product_key = normalize_key(variation.product_custom_id)
        if product_key and variation.product_id:
            self.products_by_custom_id.setdefault(product_key, set()).add(variation.product_id)

    def lookup(self, sku: str) -> list[PancakeVariation]:
        """Các mẫu mã (khác id) khớp SKU; thứ tự theo id để tất định."""
        found = self.by_sku.get(normalize_key(sku), {})
        return [found[k] for k in sorted(found)]

    def product_id_of(self, variation_id: str) -> str:
        """Id sản phẩm chứa mẫu mã; "" nếu không biết."""
        return self.product_of_variation.get(variation_id, "")

    def variations_of(self, product_id: str) -> list[PancakeVariation]:
        """Mẫu mã đã biết của một sản phẩm Pancake (theo id, tất định)."""
        found = self.variations_by_product.get(product_id, {})
        return [found[k] for k in sorted(found)]

    def product_ids_for(self, custom_id: str) -> set[str]:
        return set(self.products_by_custom_id.get(normalize_key(custom_id), set()))


def build_index(variations: Iterable[PancakeVariation], *, complete: bool = True) -> PancakeIndex:
    index = PancakeIndex(complete=complete)
    for variation in variations:
        index.add(variation)
    return index
