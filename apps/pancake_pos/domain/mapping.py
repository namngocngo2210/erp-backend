"""Ghép khuôn CRM → Pancake POS (docs/pancake-pos-product-sync.md §3.2).

- Mỗi `ProductGroup` = một sản phẩm Pancake (`custom_id = group.code`), mỗi SKU của
  nhóm = một mẫu mã (`custom_id = barcode = sku`, thuộc tính "Mẫu").
- SKU không nhóm = sản phẩm 1 mẫu mã (`custom_id = sku`), không có thuộc tính.
- Linh kiện (two-way-sync §6b): mẫu mã linh kiện luôn `is_hidden=true` (không bán,
  không nối sàn); nhóm TOÀN linh kiện gắn danh mục Pancake "Linh kiện".
- Tên lấy từ `CatalogProduct.display_name` (tên ngắn nếu có, không thì tên CRM) —
  docs/product-short-name.md.
- Payload tách phần nội dung (để hash) khỏi id Pancake (gắn lúc gửi), nên hash không
  đổi khi mới liên kết được id.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from apps.pancake_pos.domain.entities import CatalogProduct, PancakeVariation, SyncGroup
from apps.pancake_pos.domain.hashing import stable_hash

ATTRIBUTE_NAME = "Mẫu"
UNGROUPED_KEY_PREFIX = "sku:"
MIN_COMMON_NAME_LEN = 6
MAX_NAME_LEN = 255

# Token chỉ gồm dấu phân cách — bỏ ở cuối tiền tố chung và đầu phần nhãn mẫu mã.
_SEPARATOR_CHARS = frozenset("-–—|/:,;+.()[]_")


def normalize_key(value: str) -> str:
    """Khoá so khớp SKU/mã: NFC + bỏ khoảng trắng hai đầu + viết hoa."""
    return unicodedata.normalize("NFC", value or "").strip().upper()


def _fold(word: str) -> str:
    return unicodedata.normalize("NFC", word).casefold()


def _is_separator(token: str) -> bool:
    return bool(token) and all(ch in _SEPARATOR_CHARS for ch in token)


def _words(name: str) -> list[str]:
    return unicodedata.normalize("NFC", name or "").split()


def common_word_prefix(names: Sequence[str]) -> str:
    """Tiền tố chung dài nhất THEO TỪ (không cắt giữa từ), so không phân biệt hoa/thường.

    Giữ cách viết của tên đầu tiên; bỏ các token phân cách ("-", "|"...) ở cuối.
    """
    if not names:
        return ""
    tokenized = [_words(n) for n in names]
    first = tokenized[0]
    length = 0
    for idx, word in enumerate(first):
        folded = _fold(word)
        if all(idx < len(other) and _fold(other[idx]) == folded for other in tokenized[1:]):
            length = idx + 1
        else:
            break
    prefix = first[:length]
    while prefix and _is_separator(prefix[-1]):
        prefix.pop()
    return " ".join(prefix)


def _strip_prefix_words(name: str, prefix: str) -> str:
    words = _words(name)
    count = len(prefix.split())
    rest = words[count:]
    while rest and _is_separator(rest[0]):
        rest.pop(0)
    return " ".join(rest)


def _sku_suffix(sku: str, code: str) -> str:
    if code and normalize_key(sku).startswith(normalize_key(code)):
        rest = sku.strip()[len(code.strip()):].strip()
        return rest.lstrip("".join(_SEPARATOR_CHARS)).strip()
    return ""


def product_display_name(group: SyncGroup) -> str:
    """Tên sản phẩm Pancake. Nhóm: "<code> - <tiền tố chung>"; tiền tố rỗng/quá ngắn
    (< 6 ký tự) → tên SKU đầu tiên. SKU lẻ: tên của chính nó. "Tên" = `display_name`."""
    names = [p.display_name for p in group.products]
    first = names[0] if names else group.custom_id
    if not group.is_grouped:
        return (first or group.custom_id)[:MAX_NAME_LEN]
    prefix = common_word_prefix(names)
    if len(prefix) < MIN_COMMON_NAME_LEN:
        return (first or group.custom_id)[:MAX_NAME_LEN]
    return f"{group.custom_id} - {prefix}"[:MAX_NAME_LEN]


def variation_labels(group: SyncGroup) -> list[str]:
    """Giá trị thuộc tính "Mẫu" cho từng SKU (cùng thứ tự `group.products`), duy nhất
    trong nhóm: phần tên sau tiền tố chung → phần SKU sau mã nhóm → chính SKU."""
    prefix = common_word_prefix([p.display_name for p in group.products])
    labels: list[str] = []
    seen: set[str] = set()
    for product in group.products:
        label = (
            _strip_prefix_words(product.display_name, prefix)
            or _sku_suffix(product.sku, group.custom_id)
            or product.sku.strip()
        )
        if _fold(label) in seen:
            label = f"{label} ({product.sku.strip()})"
        seen.add(_fold(label))
        labels.append(label)
    return labels


def to_int_vnd(value: Decimal | int | float | None) -> int:
    if value is None:
        return 0
    rounded = Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return max(int(rounded), 0)


def weight_grams(value: Decimal | int | float | None, default_weight_g: int) -> int:
    grams = to_int_vnd(value)
    return grams if grams > 0 else default_weight_g


def size_payload(product: CatalogProduct) -> dict[str, int] | None:
    dims = {
        "length": to_int_vnd(product.length),
        "width": to_int_vnd(product.width),
        "height": to_int_vnd(product.height),
    }
    return dims if any(v > 0 for v in dims.values()) else None


def group_products(products: Iterable[CatalogProduct]) -> list[SyncGroup]:
    """Gom SKU theo nhóm CRM; SKU không nhóm thành nhóm riêng khoá `sku:<SKU>`.
    Thứ tự ổn định (theo khoá, rồi theo SKU) để payload/hash tất định."""
    buckets: dict[str, list[CatalogProduct]] = {}
    codes: dict[str, str] = {}
    for product in products:
        code = (product.group_code or "").strip()
        if code:
            key = code
            codes[key] = code
        else:
            key = f"{UNGROUPED_KEY_PREFIX}{product.sku.strip()}"
            codes[key] = product.sku.strip()
        buckets.setdefault(key, []).append(product)
    groups: list[SyncGroup] = []
    for key in sorted(buckets):
        members = tuple(sorted(buckets[key], key=lambda p: (normalize_key(p.sku), p.id)))
        groups.append(
            SyncGroup(
                key=key,
                custom_id=codes[key],
                products=members,
                is_grouped=not key.startswith(UNGROUPED_KEY_PREFIX),
            )
        )
    return groups


@dataclass(frozen=True)
class VariationPayload:
    product_id: int
    sku: str
    body: dict[str, Any]
    hash: str


@dataclass(frozen=True)
class GroupPayload:
    group_key: str
    custom_id: str
    product_body: dict[str, Any]
    variations: tuple[VariationPayload, ...]
    hash: str

    def to_request(
        self,
        variation_ids: Mapping[int, str] | None = None,
        extras: Sequence[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        """Body gửi POST/PUT. Mẫu mã đã có id Pancake → kèm `id` (sửa), chưa có → thêm mới.

        `extras` = mẫu mã ĐANG CÓ trên sản phẩm Pancake nhưng không thuộc payload CRM
        (mẫu mã lạ, mẫu mã CRM không còn đủ điều kiện) — gửi lại nguyên trạng để PUT
        không xoá chúng (§7 chưa rõ Pancake xử lý mẫu mã thiếu thế nào). Giá trị thuộc
        tính của chúng được gộp vào `product_attributes`.
        """
        ids = variation_ids or {}
        variations = []
        for v in self.variations:
            body = dict(v.body)
            if ids.get(v.product_id):
                body["id"] = ids[v.product_id]
            variations.append(body)
        product = {**self.product_body, "variations": [*variations, *extras]}
        if extras:
            product["product_attributes"] = merge_attributes(self.product_body.get("product_attributes") or [], extras)
        return {"product": product}


def passthrough_variation(variation: PancakeVariation, *, hide: bool = False) -> dict[str, Any]:
    """Mẫu mã Pancake gửi lại nguyên trạng (id + trường đọc về); `hide` → ẩn."""
    body: dict[str, Any] = {**variation.raw, "id": variation.id}
    if hide:
        body["is_hidden"] = True
    return body


def merge_attributes(
    attributes: Sequence[Mapping[str, Any]], variations: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Gộp giá trị `fields` của các mẫu mã vào `product_attributes` (giữ thứ tự, không trùng)."""
    merged: dict[str, list[str]] = {}
    for attr in attributes:
        merged.setdefault(str(attr.get("name") or ""), []).extend(str(v) for v in attr.get("values") or [])
    for variation in variations:
        for f in variation.get("fields") or []:
            name, value = str(f.get("name") or ""), str(f.get("value") or "")
            if name and value:
                merged.setdefault(name, [])
                if value not in merged[name]:
                    merged[name].append(value)
    return [{"name": name, "values": values} for name, values in merged.items() if name]


def category_id_value(category_id: str) -> int | str:
    """Spec trả id danh mục kiểu integer — gửi lại đúng kiểu nếu là số."""
    text = str(category_id).strip()
    return int(text) if text.isdigit() else text


def build_group_payload(
    group: SyncGroup, *, default_weight_g: int, component_category_id: str | None = None
) -> GroupPayload:
    """`component_category_id` = id danh mục "Linh kiện" trên Pancake (None = chưa có —
    dry-run trước lần tạo danh mục, hoặc nhóm không có linh kiện)."""
    labels = variation_labels(group)
    variations: list[VariationPayload] = []
    for product, label in zip(group.products, labels, strict=True):
        sku = product.sku.strip()
        body: dict[str, Any] = {
            "custom_id": sku,
            "barcode": sku,
            "retail_price": to_int_vnd(product.price),
            "weight": weight_grams(product.weight, default_weight_g),
            # Luôn gửi: SKU từng bị ẩn (không còn đủ điều kiện) đủ điều kiện lại thì hiện lại.
            # Linh kiện luôn ẩn (§6b).
            "is_hidden": product.is_component,
        }
        if group.is_grouped:
            body["fields"] = [{"name": ATTRIBUTE_NAME, "value": label}]
        size = size_payload(product)
        if size:
            body["size"] = size
        variations.append(VariationPayload(product_id=product.id, sku=sku, body=body, hash=stable_hash(body)))

    product_body: dict[str, Any] = {
        "name": product_display_name(group),
        "custom_id": group.custom_id,
        # Pancake bắt buộc `weight` ở cấp sản phẩm — lấy của mẫu mã đầu tiên.
        "weight": variations[0].body["weight"] if variations else default_weight_g,
    }
    if group.is_grouped:
        product_body["product_attributes"] = [{"name": ATTRIBUTE_NAME, "values": labels}]
    if component_category_id and group.products and all(p.is_component for p in group.products):
        product_body["category_ids"] = [category_id_value(component_category_id)]
    content = {**product_body, "variations": [v.body for v in variations]}
    return GroupPayload(
        group_key=group.key,
        custom_id=group.custom_id,
        product_body=product_body,
        variations=tuple(variations),
        hash=stable_hash(content),
    )
