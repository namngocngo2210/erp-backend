"""Export tồn kho streaming theo lô (mở từ audit 2026-08-19, đóng 2026-09-23):
lô không được cắt đôi một cặp (kho, mã) — pivot 3 state vẫn ra đúng 1 dòng/cặp."""
import pytest

from apps.catalog.models import Product
from apps.exports.application.registry import get_definition
from apps.inventory import exports_definitions
from apps.inventory.models import StockItem, StockState
from apps.warehouses.models import Warehouse

pytestmark = pytest.mark.django_db


class _RecordingWriter:
    def __init__(self):
        self.headers = None
        self.rows = []

    def write_header(self, headers):
        self.headers = list(headers)

    def write_row(self, row):
        self.rows.append(list(row))


def _write(definition, params):
    writer = _RecordingWriter()
    definition.write(params, writer, lambda n: None)
    return writer


def test_chunk_boundary_never_splits_a_pair(monkeypatch):
    monkeypatch.setattr(exports_definitions, "_STOCK_CHUNK_SIZE", 2)  # < 3 state/cặp
    warehouse = Warehouse.objects.create(code="WH-STR", name="Kho Stream")
    for i in range(4):
        product = Product.objects.create(name=f"SP {i}", sku=f"SKU-STR-{i}")
        for state, qty in ((StockState.SELLABLE, 10), (StockState.WARRANTY, 3), (StockState.DEFECT, 2)):
            StockItem.objects.create(warehouse=warehouse, product=product, state=state, quantity=qty)
    definition = get_definition("stock")
    params = definition.validate(None, {"warehouse_id": warehouse.id})

    writer = _write(definition, params)

    assert [r[1] for r in writer.rows] == [f"SKU-STR-{i}" for i in range(4)]
    assert all(r[3:7] == [10, 3, 2, 15] for r in writer.rows), writer.rows


def test_stock_chunks_overflow_sentinel():
    class _Item:
        def __init__(self, w, p):
            self.warehouse_id, self.product_id = w, p

    items = [_Item(1, p) for p in range(3) for _ in range(3)]
    chunks = list(exports_definitions._stock_chunks(iter(items), limit=6))
    assert chunks[-1] is exports_definitions._OVERFLOW
    assert sum(len(c) for c in chunks[:-1]) == 6
