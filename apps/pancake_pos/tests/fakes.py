"""Pancake POS giả trong bộ nhớ — mô phỏng hợp đồng §2 (POST không trả id, PUT biến
thể có id = sửa, không id = thêm) để test service mà không gọi API thật."""

from __future__ import annotations

import itertools
from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any

from apps.pancake_pos.domain.entities import (
    CatalogProduct,
    PancakeOrder,
    PancakeProduct,
    PancakeVariation,
    PancakeWarehouse,
)
from apps.pancake_pos.domain.mapping import normalize_key
from apps.pancake_pos.infrastructure.pancake_pos_client import PancakePosApiError, _product_raw, _variation_raw


class FakePancake:
    def __init__(self) -> None:
        self.products: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, Any]] = []
        self.fail_on: dict[str, str] = {}  # custom_id sản phẩm → message lỗi khi ghi
        self.hide_after_create = False  # GET lại sau POST không thấy (mô phỏng trễ index)
        # Mô phỏng trường hợp xấu nhất §7: PUT thiếu mẫu mã nào thì Pancake XOÁ mẫu mã đó.
        self.put_replaces_variations = True
        self._ids = itertools.count(1)
        self.categories: list[dict[str, str]] = []
        self.warehouses: list[PancakeWarehouse] = []
        # Tồn thực theo (variation_id, warehouse_id) — `update_quantities` ghi vào đây.
        self.stock: dict[tuple[str, str], int] = {}
        self.fail_quantity_batches: set[int] = set()  # số thứ tự lần gọi (1-based) sẽ lỗi 500
        self.max_quantity_batch: int | None = None  # lô lớn hơn → HTTP 500 (như API thật với lô 100)
        self.reject_variations: set[str] = set()  # dòng trả `success=false` trong `results`
        self._quantity_calls = 0

    # ── dựng dữ liệu có sẵn trên shop ────────────────────────────────────────
    def seed(self, custom_id: str, variations: Sequence[dict[str, Any]], product_id: str | None = None) -> str:
        pid = product_id or f"p{next(self._ids)}"
        self.products[pid] = {
            "custom_id": custom_id,
            "variations": [{"id": v.get("id") or f"v{next(self._ids)}", **v} for v in variations],
            "body": {},
        }
        return pid

    def write_calls(self) -> list[tuple[str, Any]]:
        return [c for c in self.calls if c[0] in ("create", "update", "create_category", "update_quantity")]

    # ── PancakePosGateway ────────────────────────────────────────────────────
    def list_shops(self) -> list[dict[str, Any]]:
        return [{"id": 42, "name": "Fake"}]

    def iter_variations(self, *, search: str | None = None) -> Iterator[PancakeVariation]:
        self.calls.append(("iter", search))
        for pid, product in self.products.items():
            if product.get("hidden"):
                continue
            for v in product["variations"]:
                if search and normalize_key(search) not in normalize_key(v.get("custom_id", "") + v.get("barcode", "")):
                    continue
                yield self._variation(pid, product, v)

    def get_product(self, sku_or_id: str) -> PancakeProduct | None:
        self.calls.append(("get", sku_or_id))
        for pid, product in self.products.items():
            if product.get("hidden"):
                continue
            if pid == sku_or_id or normalize_key(product["custom_id"]) == normalize_key(sku_or_id):
                return PancakeProduct(
                    id=pid,
                    custom_id=product["custom_id"],
                    variations=tuple(self._variation(pid, product, v) for v in product["variations"]),
                    raw=_product_raw({"custom_id": product["custom_id"], **product.get("body", {})}),
                )
        return None

    def _variation(self, pid: str, product: dict[str, Any], v: dict[str, Any]) -> PancakeVariation:
        return PancakeVariation(
            id=v["id"], product_id=pid, custom_id=v.get("custom_id", ""), barcode=v.get("barcode", ""),
            product_custom_id=product["custom_id"], is_hidden=v.get("is_hidden") is True, raw=_variation_raw(v),
            warehouse_quantities={wh: q for (vid, wh), q in self.stock.items() if vid == v["id"]},
        )

    # ── danh mục / kho / tồn ─────────────────────────────────────────────────
    def list_categories(self) -> list[dict[str, str]]:
        self.calls.append(("list_categories", None))
        return list(self.categories)

    def create_category(self, name: str, *, parent_id: str | None = None) -> str:
        self.calls.append(("create_category", name))
        category_id = str(900 + len(self.categories))
        self.categories.append({"id": category_id, "name": name, "parent_id": parent_id or ""})
        return category_id

    def list_warehouses(self) -> list[PancakeWarehouse]:
        self.calls.append(("list_warehouses", None))
        return list(self.warehouses)

    def update_quantities(self, entries: list[dict[str, Any]], *, actual: bool = True) -> list[dict[str, str]]:
        self._quantity_calls += 1
        self.calls.append(("update_quantity", {"entries": list(entries), "actual": actual}))
        if self._quantity_calls in self.fail_quantity_batches or (
            self.max_quantity_batch is not None and len(entries) > self.max_quantity_batch
        ):
            raise PancakePosApiError("HTTP 500 — lỗi giả", status_code=500)
        failures = []
        for entry in entries:
            if entry["variation_id"] in self.reject_variations:
                failures.append({"variation_id": entry["variation_id"], "warehouse_id": "", "message": "bị từ chối"})
                continue
            self.stock[(entry["variation_id"], entry["warehouse_id"])] = entry["remain_quantity"]
        return failures

    def variation(self, variation_id: str) -> dict[str, Any]:
        for product in self.products.values():
            for v in product["variations"]:
                if v["id"] == variation_id:
                    return v
        raise KeyError(variation_id)

    def create_product(self, payload: dict[str, Any]) -> None:
        body = payload["product"]
        self.calls.append(("create", payload))
        if body["custom_id"] in self.fail_on:
            raise PancakePosApiError(self.fail_on[body["custom_id"]])
        pid = f"p{next(self._ids)}"
        self.products[pid] = {
            "custom_id": body["custom_id"],
            "variations": [{**v, "id": f"v{next(self._ids)}"} for v in body["variations"]],
            "body": {k: v for k, v in body.items() if k != "variations"},
            "hidden": self.hide_after_create,
        }

    def update_product(self, product_id: str, payload: dict[str, Any]) -> None:
        body = payload["product"]
        self.calls.append(("update", (product_id, payload)))
        if body["custom_id"] in self.fail_on:
            raise PancakePosApiError(self.fail_on[body["custom_id"]])
        product = self.products[product_id]
        by_id = {v["id"]: v for v in product["variations"]}
        sent = {v.get("id") for v in body["variations"] if v.get("id")}
        if self.put_replaces_variations:
            product["variations"] = [v for v in product["variations"] if v["id"] in sent]
        for v in body["variations"]:
            if v.get("id") in by_id:
                by_id[v["id"]].update(v)
            else:
                product["variations"].append({**v, "id": f"v{next(self._ids)}"})
        product["body"] = {k: v for k, v in body.items() if k != "variations"}


class FakeReader:
    def __init__(self, products: Sequence[CatalogProduct]) -> None:
        self.products = list(products)
        self.calls: list[dict[str, Any]] = []
        self.deleted_groups: dict[int, str] = {}
        self.product_type_supported = False

    def has_product_type(self) -> bool:
        return self.product_type_supported

    def list_candidates(
        self,
        *,
        sold_since: datetime,
        skus: Sequence[str] | None = None,
        product_ids: Sequence[int] | None = None,
    ) -> list[CatalogProduct]:
        self.calls.append({"sold_since": sold_since, "skus": skus, "product_ids": product_ids})
        if product_ids is None:
            return list(self.products)
        wanted = set(product_ids)
        groups = {p.group_code for p in self.products if p.id in wanted and p.group_code}
        groups |= self.extra_groups_for(wanted)
        return [p for p in self.products if p.id in wanted or (p.group_code and p.group_code in groups)]

    def extra_groups_for(self, ids: set[int]) -> set[str]:
        """Nhóm của id đã xoá mềm (không còn trong `products`) — test đặt qua `deleted_groups`."""
        return {g for pid, g in self.deleted_groups.items() if pid in ids}


class FakePancakeOrders:
    """Phần đơn hàng của Pancake POS giả (two-way-sync A2).

    `lose_response_on_create`: POST TẠO đơn nhưng ném lỗi (ReadTimeout/5xx) — mô phỏng mất
    response để kiểm idempotent. `create_error`: POST ném lỗi mà KHÔNG tạo."""

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, Any]] = []
        self.lose_response_on_create: Exception | None = None
        self.create_error: Exception | None = None
        self.get_error: Exception | None = None
        self.find_error: Exception | None = None
        self._ids = itertools.count(1001)

    def _order(self, order_id: str) -> PancakeOrder:
        raw = self.orders[order_id]
        return PancakeOrder(id=order_id, status=raw.get("status", 0), custom_id=raw.get("custom_id", ""),
                            warehouse_id=raw.get("warehouse_id", ""))

    def create_order(self, payload: dict[str, Any]) -> PancakeOrder:
        self.calls.append(("create", payload))
        if self.create_error is not None:
            raise self.create_error
        order_id = str(next(self._ids))
        self.orders[order_id] = {**payload, "status": 0}
        if self.lose_response_on_create is not None:
            error, self.lose_response_on_create = self.lose_response_on_create, None
            raise error
        return self._order(order_id)

    def get_order(self, order_id: str) -> PancakeOrder | None:
        self.calls.append(("get", order_id))
        if self.get_error is not None:
            raise self.get_error
        return self._order(order_id) if order_id in self.orders else None

    def update_order(self, order_id: str, payload: dict[str, Any]) -> PancakeOrder | None:
        self.calls.append(("update", (order_id, payload)))
        self.orders[order_id].update(payload)
        return self._order(order_id)

    def find_orders(self, *, custom_id: str, search: str, since: int | None = None) -> list[PancakeOrder]:
        self.calls.append(("find", {"custom_id": custom_id, "search": search, "since": since}))
        if self.find_error is not None:
            raise self.find_error
        return [
            self._order(oid) for oid, raw in self.orders.items()
            if raw.get("custom_id") == custom_id and raw.get("bill_phone_number") == search
        ]

    def creates(self) -> list[dict[str, Any]]:
        return [payload for kind, payload in self.calls if kind == "create"]
