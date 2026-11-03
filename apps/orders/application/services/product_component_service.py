"""Khai báo thành phần của SKU combo.

CHỈ phục vụ báo cáo (file Excel Lịch sử nhập xuất nở combo ra sản phẩm con).
Không có code nào trong luồng ghi ``StockMovement`` đọc bảng này.
"""
from __future__ import annotations

from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from apps.catalog.contracts.catalog_events import ProductsChanged
from apps.catalog.models import Product, ProductComponent
from apps.common.events import event_bus

# Trần trên cho số lượng thành phần trong 1 combo. `quantity` là
# PositiveIntegerField (int4 ở Postgres, trần ~2.1 tỷ) — không có giới hạn
# nghiệp vụ nào cần tới con số đó, và nhập nhầm một số khổng lồ (vd gõ dư số 0,
# dán nhầm giá) trước đây rơi thẳng xuống DB gây lỗi tràn int4 -> 500 khó hiểu
# cho người dùng. 100 nghìn dư sức cho mọi combo thật (combo nhiều nhất hiện có
# chưa tới vài chục), chặn sớm ở tầng service để trả 400 rõ ràng thay vì để DB
# ném lỗi.
_MAX_COMPONENT_QUANTITY = 100_000


class ProductComponentService:
    @staticmethod
    def _require_product(product_id: int) -> None:
        """Chặn 404 sớm — dùng chung cho GET (list_for) và PUT (replace) để cả
        hai trả cùng mã lỗi/thông báo khi combo không tồn tại hoặc đã xoá mềm."""
        if not Product.objects.filter(id=product_id, deleted_at__isnull=True).exists():
            raise NotFound({"detail": "Không tìm thấy sản phẩm."})

    @staticmethod
    def list_for(product_id: int) -> list[dict]:
        ProductComponentService._require_product(product_id)
        return [
            {
                "component_id": r["component_id"],
                "sku": r["component__sku"],
                "name": r["component__name"],
                "quantity": r["quantity"],
            }
            for r in ProductComponent.objects.filter(parent_id=product_id)
            .values("component_id", "component__sku", "component__name", "quantity")
            .order_by("component__sku")
        ]

    @staticmethod
    def _strict_quantity(raw) -> int:
        """Parse `quantity` NGHIÊM NGẶT — `int(1.9)` cắt ngầm thành 1 (Python
        `int()` không báo lỗi với số thập phân), người dùng gõ nhầm "1.9" thì
        combo âm thầm lưu thành 1 mà không ai biết. Chỉ chấp nhận giá trị vốn
        đã nguyên (int, hoặc float/str biểu diễn đúng số nguyên); từ chối rõ
        ràng mọi phần thập phân khác 0."""
        if isinstance(raw, bool):
            raise ValidationError({"detail": "component_id/quantity phải là số nguyên."})
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            if not raw.is_integer():
                raise ValidationError({"detail": "Số lượng thành phần phải là số nguyên, không được là số thập phân."})
            return int(raw)
        if isinstance(raw, str):
            try:
                as_float = float(raw)
            except (TypeError, ValueError):
                raise ValidationError({"detail": "component_id/quantity phải là số nguyên."}) from None
            if not as_float.is_integer():
                raise ValidationError({"detail": "Số lượng thành phần phải là số nguyên, không được là số thập phân."})
            return int(as_float)
        raise ValidationError({"detail": "component_id/quantity phải là số nguyên."})

    @staticmethod
    def _validate(
        product_id: int, rows: list[dict], current_component_ids: set[int] | None = None
    ) -> list[tuple[int, int]]:
        """`current_component_ids`: các `component_id` ĐANG có trong combo này
        TRƯỚC lần ghi này (rỗng nếu không truyền) — dùng để phân biệt "giữ
        nguyên thành phần cũ dù nó đã bị xoá mềm ở nơi khác" (cho phép) với
        "thêm mới một sản phẩm đã xoá mềm" (chặn), xem docstring class."""
        current_component_ids = current_component_ids or set()
        cleaned: list[tuple[int, int]] = []
        seen: set[int] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValidationError({"detail": "Mỗi dòng thành phần phải là object có component_id/quantity."})
            try:
                component_id = int(row.get("component_id"))
            except (TypeError, ValueError):
                raise ValidationError({"detail": "component_id/quantity phải là số nguyên."}) from None
            quantity = ProductComponentService._strict_quantity(row.get("quantity"))
            if quantity < 1:
                raise ValidationError({"detail": "Số lượng thành phần phải từ 1 trở lên."})
            if quantity > _MAX_COMPONENT_QUANTITY:
                raise ValidationError(
                    {"detail": f"Số lượng thành phần không được vượt quá {_MAX_COMPONENT_QUANTITY}."}
                )
            if component_id == product_id:
                raise ValidationError({"detail": "Combo không thể chứa chính nó."})
            if component_id in seen:
                raise ValidationError({"detail": "Một sản phẩm chỉ được khai một lần trong combo."})
            seen.add(component_id)
            cleaned.append((component_id, quantity))

        if not cleaned:
            return cleaned

        component_ids = [c for c, _ in cleaned]
        # Tải CẢ sản phẩm đã xoá mềm (khác bản cũ chỉ lọc active) để phân biệt
        # được "không tồn tại" với "tồn tại nhưng đã xoá mềm" — hai lỗi khác
        # nhau, người dùng cần biết đúng nguyên nhân để xử lý đúng chỗ.
        products = {
            p.id: p for p in Product.objects.filter(id__in=component_ids).only("id", "sku", "deleted_at")
        }
        missing_ids = [c for c in component_ids if c not in products]
        if missing_ids:
            raise ValidationError(
                {"detail": f"Không tìm thấy sản phẩm thành phần (id {missing_ids[0]})."}
            )

        # Thành phần đã xoá mềm: GIỮ LẠI nếu nó VỐN ĐÃ có trong combo này (đừng
        # khoá cứng việc sửa combo — vd chỉ đổi số lượng của SP-A — chỉ vì một
        # thành phần KHÁC đã bị ai đó xoá mềm ở trang Sản phẩm); CHẶN nếu là
        # thêm mới một sản phẩm đã ngừng bán vào combo.
        newly_added_deleted = [
            products[c] for c in component_ids
            if products[c].deleted_at is not None and c not in current_component_ids
        ]
        if newly_added_deleted:
            labels = ", ".join(f"{p.sku or p.id} (id {p.id})" for p in newly_added_deleted)
            raise ValidationError(
                {
                    "detail": (
                        "Không thể thêm sản phẩm đã bị xoá mềm vào combo: "
                        f"{labels}."
                    )
                }
            )

        # Chỉ MỘT cấp, chặn cả hai chiều: thành phần không được là combo, và một
        # SP đang là thành phần của combo khác thì không được tự khai thành phần.
        if ProductComponent.objects.filter(parent_id__in=component_ids).exists():
            raise ValidationError({"detail": "Sản phẩm thành phần không được là một combo khác."})
        if ProductComponent.objects.filter(component_id=product_id).exists():
            raise ValidationError(
                {"detail": "Sản phẩm này đang là thành phần của combo khác nên không thể tự khai thành phần."}
            )
        return cleaned

    @staticmethod
    @transaction.atomic
    def replace(product_id: int, rows: list[dict]) -> list[dict]:
        """Ghi đè trọn danh sách thành phần của một combo."""
        ProductComponentService._require_product(product_id)
        current_component_ids = set(
            ProductComponent.objects.filter(parent_id=product_id).values_list("component_id", flat=True)
        )
        cleaned = ProductComponentService._validate(product_id, rows, current_component_ids)
        ProductComponent.objects.filter(parent_id=product_id).delete()
        ProductComponent.objects.bulk_create(
            [
                ProductComponent(parent_id=product_id, component_id=component_id, quantity=quantity)
                for component_id, quantity in cleaned
            ]
        )
        # Khai/bỏ thành phần đổi tính chất combo của SKU mà không qua Product.save().
        event_bus.publish_on_commit(ProductsChanged(product_ids=(product_id,), reason="components_replaced"))
        return ProductComponentService.list_for(product_id)
