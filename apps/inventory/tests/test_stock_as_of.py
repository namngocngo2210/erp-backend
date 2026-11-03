"""Tồn kho tại một mốc ngày — service, API, export.

Cách dựng số: trừ ngược từ `stock_items` (xem
docs/superpowers/specs/2026-08-12-stock-as-of-date-design.md §3). Test đối chiếu
chéo với cách cộng xuôi từ sổ là chốt chặn quan trọng nhất ở đây.

MỌI mốc thời gian trong file này dựng TƯƠNG ĐỐI so với hôm nay — không được cắm
ngày cứng, nếu không suite tự đỏ vào ngày 1 hàng tháng.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.inventory.application.services.stock_as_of_service import StockAsOfService
from apps.inventory.models import MovementType, StockItem, StockMovement, StockState

pytestmark = pytest.mark.django_db


def today() -> date:
    return timezone.localdate()


def at(day: date, hour: int = 12, minute: int = 0):
    """Datetime aware theo GIỜ VN của một ngày tương đối."""
    naive = timezone.datetime.combine(day, timezone.datetime.min.time()).replace(
        hour=hour, minute=minute
    )
    return timezone.make_aware(naive)


def move(warehouse, product, qty, occurred, created=None, state=StockState.SELLABLE):
    """Ghi thẳng một dòng sổ + cập nhật projection, KHÔNG qua service.

    Cố ý đi đường thấp để dựng được ca `created_at` lệch `occurred_at` (quét khớp
    trễ) — `StockCommandService` luôn ép `created_at = now()`.
    """
    mv = StockMovement.objects.create(
        warehouse=warehouse,
        product=product,
        qty_delta=qty,
        movement_type=MovementType.RECEIPT if qty > 0 else MovementType.ISSUE,
        state=state,
        balance_after=0,
        occurred_at=occurred,
    )
    if created is not None:
        StockMovement.objects.filter(pk=mv.pk).update(created_at=created)
    item, _ = StockItem.objects.get_or_create(
        warehouse=warehouse, product=product, state=state, defaults={"quantity": 0}
    )
    item.quantity += qty
    item.save(update_fields=["quantity"])
    return mv


def items_of(warehouse):
    return list(
        StockItem.objects.filter(warehouse=warehouse).select_related("warehouse", "product")
    )


class TestDeltasAfter:
    def test_subtracts_only_movements_after_the_mark(self, warehouse, product):
        d5 = today() - timedelta(days=5)
        move(warehouse, product, 10, at(d5))
        move(warehouse, product, -3, at(today() - timedelta(days=2)))

        deltas = StockAsOfService.deltas_after(d5)

        assert deltas == {(warehouse.id, product.id, StockState.SELLABLE): -3}

    def test_movement_on_the_mark_day_is_included_in_balance(self, warehouse, product):
        """Bút toán lúc 23:30 ngày D thuộc về ngày D — không bị trừ ra."""
        d = today() - timedelta(days=3)
        move(warehouse, product, 10, at(d, hour=23, minute=30))

        assert StockAsOfService.deltas_after(d) == {}

    def test_movement_just_after_midnight_is_excluded(self, warehouse, product):
        d = today() - timedelta(days=3)
        move(warehouse, product, 10, at(d, hour=23, minute=30))
        move(warehouse, product, 4, at(d + timedelta(days=1), hour=0, minute=30))

        assert StockAsOfService.deltas_after(d) == {
            (warehouse.id, product.id, StockState.SELLABLE): 4
        }

    def test_late_posted_movement_lands_on_its_business_day(self, warehouse, product):
        """occurred_at ngày D, created_at ngày D+3 (quét khớp trễ) → thuộc ngày D.

        Đây là ca mà điều kiện cắt partition `created_at >= end` có thể âm thầm
        loại nhầm nếu đặt sai chiều.
        """
        d = today() - timedelta(days=6)
        move(warehouse, product, 7, at(d), created=at(d + timedelta(days=3)))

        assert StockAsOfService.deltas_after(d) == {}
        assert StockAsOfService.deltas_after(d - timedelta(days=1)) == {
            (warehouse.id, product.id, StockState.SELLABLE): 7
        }


class TestApply:
    def test_quantity_matches_forward_sum_from_ledger(self, warehouse, product):
        """Đối chiếu chéo: trừ ngược phải bằng cộng xuôi từ đầu sổ."""
        d = today() - timedelta(days=4)
        move(warehouse, product, 20, at(today() - timedelta(days=9)))
        move(warehouse, product, -5, at(today() - timedelta(days=6)))
        move(warehouse, product, 8, at(d))
        move(warehouse, product, -11, at(today() - timedelta(days=1)))

        rows = StockAsOfService.apply(items_of(warehouse), d)

        end = StockAsOfService.end_bound(d)
        forward = sum(
            m.qty_delta
            for m in StockMovement.objects.filter(
                warehouse=warehouse, product=product, state=StockState.SELLABLE
            )
            if m.occurred_at < end
        )
        assert [r.quantity for r in rows] == [forward]
        assert forward == 23

    def test_drops_pair_when_every_state_is_zero_at_the_mark(self, warehouse, product):
        """Mã chỉ nhập về SAU mốc thì không hiện ở mốc đó."""
        d = today() - timedelta(days=5)
        move(warehouse, product, 12, at(today() - timedelta(days=2)))

        assert StockAsOfService.apply(items_of(warehouse), d) == []

    def test_keeps_pair_when_states_net_to_zero_but_individually_nonzero(
        self, warehouse, product
    ):
        """SELLABLE dương + DEFECT âm cùng độ lớn → tổng 0, nhưng KHÔNG được loại.

        `StockItem.quantity` được phép âm (bán vượt hợp lệ, xem models.py) —
        loại cặp theo TỔNG các trạng thái sẽ giấu mất 5 chiếc SELLABLE còn bán
        được thật. Luật đúng là loại chỉ khi TẤT CẢ các dòng trạng thái = 0.
        """
        d = today() - timedelta(days=3)
        move(warehouse, product, 5, at(today() - timedelta(days=5)))
        move(warehouse, product, -5, at(today() - timedelta(days=5)), state=StockState.DEFECT)

        rows = StockAsOfService.apply(items_of(warehouse), d)

        assert {r.state: r.quantity for r in rows} == {
            StockState.SELLABLE: 5,
            StockState.DEFECT: -5,
        }

    def test_keeps_all_three_states_when_pair_has_any_stock(self, warehouse, product):
        d = today() - timedelta(days=2)
        move(warehouse, product, 5, at(today() - timedelta(days=4)))
        # Trạng thái lỗi về 0 tại mốc, nhưng cặp (kho, mã) vẫn có tồn → giữ đủ 3 dòng.
        move(warehouse, product, 2, at(today() - timedelta(days=4)), state=StockState.DEFECT)
        move(warehouse, product, -2, at(today() - timedelta(days=4)), state=StockState.DEFECT)
        StockItem.objects.get_or_create(
            warehouse=warehouse, product=product, state=StockState.WARRANTY,
            defaults={"quantity": 0},
        )

        rows = StockAsOfService.apply(items_of(warehouse), d)

        assert len(rows) == 3
        assert {r.state for r in rows} == {
            StockState.SELLABLE, StockState.WARRANTY, StockState.DEFECT
        }

    def test_does_not_persist_the_rewritten_quantity(self, warehouse, product):
        """apply() sửa quantity TRONG BỘ NHỚ để serializer đọc — cấm chạm DB."""
        d = today() - timedelta(days=3)
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, 6, at(today() - timedelta(days=1)))

        StockAsOfService.apply(items_of(warehouse), d)

        assert StockItem.objects.get(
            warehouse=warehouse, product=product, state=StockState.SELLABLE
        ).quantity == 16


def sale_movement(warehouse, product, qty_delta, occurred, movement_type=None):
    """Ghi thẳng một dòng SALE/SALE_REVERSAL vào sổ.

    Khác `move()`: `sales_run_rates` chỉ đọc SALE/SALE_REVERSAL, không phải
    RECEIPT/ISSUE mà `move()` gắn theo dấu số lượng.
    """
    if movement_type is None:
        movement_type = MovementType.SALE_REVERSAL if qty_delta > 0 else MovementType.SALE
    return StockMovement.objects.create(
        warehouse=warehouse,
        product=product,
        qty_delta=qty_delta,
        movement_type=movement_type,
        state=StockState.SELLABLE,
        balance_after=0,
        occurred_at=occurred,
    )


class TestRunRateAtMark:
    def test_window_ends_at_the_mark_not_today(self, warehouse, product):
        """Xem lại ngày D thì phải thấy tốc độ bán CỦA ngày D."""
        from apps.inventory.application.services.stock_query_service import (
            StockQueryService,
        )

        d = today() - timedelta(days=10)
        # 6 chiếc bán trong 3 ngày trước mốc.
        move(warehouse, product, 30, at(today() - timedelta(days=30)))
        sale_movement(warehouse, product, -6, at(d - timedelta(days=1)))
        # Bán rầm rộ SAU mốc — không được lọt vào cửa sổ của mốc.
        sale_movement(warehouse, product, -90, at(today() - timedelta(days=1)))

        rates = StockQueryService.sales_run_rates(
            {(warehouse.id, product.id)},
            as_of_end=StockAsOfService.end_bound(d),
        )

        assert rates[(warehouse.id, product.id)] == {"rr3": 2.0, "rr7": round(6 / 7, 2)}

    def test_default_call_is_unchanged(self, warehouse, product):
        from apps.inventory.application.services.stock_query_service import (
            StockQueryService,
        )

        move(warehouse, product, 30, at(today() - timedelta(days=30)))
        sale_movement(warehouse, product, -6, at(today() - timedelta(days=1)))

        rates = StockQueryService.sales_run_rates({(warehouse.id, product.id)})

        assert rates[(warehouse.id, product.id)]["rr3"] == 2.0


class TestStockListApi:
    @pytest.fixture(autouse=True)
    def _client(self):
        from rest_framework.test import APIClient

        from apps.users.tests.helpers import make_admin

        self.client = APIClient()
        self.client.force_authenticate(make_admin("stock-asof-actor"))

    def _get(self, **params):
        query = {"limit": 50, "offset": 0, **params}
        return self.client.get("/api/inventory/stock", query)

    def test_returns_balance_at_the_mark(self, warehouse, product):
        d = today() - timedelta(days=3)
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, -4, at(today() - timedelta(days=1)))

        resp = self._get(warehouse_id=warehouse.id, as_of=d.isoformat())

        assert resp.status_code == 200
        assert resp.data["as_of"] == d.isoformat()
        assert [i["quantity"] for i in resp.data["items"]] == [10]

    def test_without_param_returns_current_balance(self, warehouse, product):
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, -4, at(today() - timedelta(days=1)))

        resp = self._get(warehouse_id=warehouse.id)

        assert resp.data["as_of"] is None
        assert [i["quantity"] for i in resp.data["items"]] == [6]

    def test_today_returns_current_balance(self, warehouse, product):
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, -4, at(today(), hour=9))

        resp = self._get(warehouse_id=warehouse.id, as_of=today().isoformat())

        assert resp.data["as_of"] == today().isoformat()
        assert [i["quantity"] for i in resp.data["items"]] == [6]

    def test_future_mark_is_rejected(self, warehouse):
        resp = self._get(
            warehouse_id=warehouse.id,
            as_of=(today() + timedelta(days=1)).isoformat(),
        )

        assert resp.status_code == 400
        assert "tương lai" in resp.data["detail"]

    def test_malformed_mark_is_rejected(self, warehouse):
        resp = self._get(warehouse_id=warehouse.id, as_of="12-08-2026")

        assert resp.status_code == 400
        assert "YYYY-MM-DD" in resp.data["detail"]

    def test_pairs_with_zero_total_are_hidden(self, warehouse, product):
        d = today() - timedelta(days=5)
        move(warehouse, product, 12, at(today() - timedelta(days=2)))

        resp = self._get(warehouse_id=warehouse.id, as_of=d.isoformat())

        assert resp.data["total"] == 0
        assert resp.data["items"] == []

    def test_as_of_without_warehouse_or_search_is_rejected(self, warehouse, product):
        """Ngăn vật chất hoá cả bảng StockItem cho một request không lọc gì.

        Guard chỉ chặn NHÁNH MỐC — xem `test_without_param_returns_current_...`
        bên dưới cho nhánh không `as_of`.
        """
        move(warehouse, product, 5, at(today() - timedelta(days=4)))

        resp = self._get(as_of=(today() - timedelta(days=1)).isoformat())

        assert resp.status_code == 400
        assert "chọn kho" in resp.data["detail"]

    def test_as_of_with_search_but_no_warehouse_is_allowed(self, warehouse, product):
        d = today() - timedelta(days=3)
        move(warehouse, product, 5, at(today() - timedelta(days=5)))

        resp = self._get(as_of=d.isoformat(), search=product.sku)

        assert resp.status_code == 200

    def test_without_as_of_and_without_warehouse_still_returns_current_balance(
        self, warehouse, product
    ):
        """Guard mới chỉ áp cho nhánh mốc — nhánh tồn hiện tại giữ NGUYÊN hành vi cũ."""
        move(warehouse, product, 5, at(today() - timedelta(days=4)))

        resp = self._get()

        assert resp.status_code == 200

    def test_rejects_when_filtered_set_exceeds_cap(self, warehouse, product, monkeypatch):
        from apps.inventory.transport.views import stock_views

        move(warehouse, product, 5, at(today() - timedelta(days=4)))
        monkeypatch.setattr(stock_views, "_AS_OF_MAX_ROWS", 0)

        resp = self._get(
            warehouse_id=warehouse.id,
            as_of=(today() - timedelta(days=1)).isoformat(),
        )

        assert resp.status_code == 400
        assert "thu hẹp" in resp.data["detail"]


class TestMovementsApi:
    @pytest.fixture(autouse=True)
    def _client(self):
        from rest_framework.test import APIClient

        from apps.users.tests.helpers import make_admin

        self.client = APIClient()
        self.client.force_authenticate(make_admin("stock-asof-mv-actor"))

    def test_history_stops_at_the_mark(self, warehouse, product):
        d = today() - timedelta(days=3)
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, 3, at(d, hour=23, minute=30))
        move(warehouse, product, -4, at(today() - timedelta(days=1)))

        resp = self.client.get(
            "/api/inventory/stock/movements",
            {
                "limit": 10,
                "offset": 0,
                "warehouse_id": warehouse.id,
                "product_id": product.id,
                "as_of": d.isoformat(),
            },
        )

        assert resp.status_code == 200
        assert sorted(m["qty_delta"] for m in resp.data["items"]) == [3, 10]

    def test_without_mark_returns_full_history(self, warehouse, product):
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, -4, at(today() - timedelta(days=1)))

        resp = self.client.get(
            "/api/inventory/stock/movements",
            {"limit": 10, "offset": 0, "warehouse_id": warehouse.id, "product_id": product.id},
        )

        assert resp.data["total"] == 2

    def test_future_mark_is_rejected(self, warehouse, product):
        resp = self.client.get(
            "/api/inventory/stock/movements",
            {
                "limit": 10,
                "offset": 0,
                "warehouse_id": warehouse.id,
                "product_id": product.id,
                "as_of": (today() + timedelta(days=1)).isoformat(),
            },
        )

        assert resp.status_code == 400


class TestStockExportAsOf:
    def _definition(self):
        from apps.exports.application.registry import get_definition

        return get_definition("stock")

    def test_validate_rejects_malformed_mark(self, warehouse):
        from rest_framework.exceptions import ValidationError

        with pytest.raises(ValidationError):
            self._definition().validate(
                None, {"warehouse_id": warehouse.id, "as_of": "12-08-2026"}
            )

    def test_validate_rejects_future_mark(self, warehouse):
        from rest_framework.exceptions import ValidationError

        with pytest.raises(ValidationError):
            self._definition().validate(
                None,
                {
                    "warehouse_id": warehouse.id,
                    "as_of": (today() + timedelta(days=1)).isoformat(),
                },
            )

    def test_file_name_carries_the_mark(self, warehouse):
        d = today() - timedelta(days=2)
        definition = self._definition()
        params = definition.validate(
            None, {"warehouse_id": warehouse.id, "as_of": d.isoformat()}
        )

        assert definition.file_name(params) == f"ton-kho-{warehouse.code}-{d:%Y%m%d}.xlsx"

    def test_rows_hold_balance_at_the_mark(self, warehouse, product):
        d = today() - timedelta(days=3)
        move(warehouse, product, 10, at(today() - timedelta(days=5)))
        move(warehouse, product, -4, at(today() - timedelta(days=1)))
        definition = self._definition()
        params = definition.validate(
            None, {"warehouse_id": warehouse.id, "as_of": d.isoformat()}
        )
        rows = []

        definition.write(params, _CollectingWriter(rows), lambda _n: None)

        # [Kho, SKU, Tên, Bán được, Bảo hành, Lỗi, Tổng, RR3, RR7]
        assert [r[1] for r in rows] == [product.sku]
        assert rows[0][3] == 10


class _CollectingWriter:
    """Writer tối giản, chỉ gom dòng dữ liệu — đủ để khẳng định số trong file."""

    def __init__(self, sink: list):
        self.sink = sink
        self.headers: list | None = None

    def write_header(self, headers):
        self.headers = headers

    def write_row(self, row):
        self.sink.append(row)
