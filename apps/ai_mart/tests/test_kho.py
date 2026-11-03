"""ai.ton_kho (mart tồn kho hiện tại) + ai.lich_su_kho (sổ cái bất biến).

lich_su_kho nạp tăng dần theo moc_id (append-only, spec Q8) — KHÁC với mọi
mart nightly khác đang TRUNCATE+INSERT toàn bộ mỗi đêm. rebuild_all() vẫn
rebuild đầy đủ cả bảng (nightly group), nhưng phải tự chốt lại moc_id = max(id)
sau đó, nếu không sync_incremental() kế tiếp sẽ cố INSERT lại các dòng vừa
rebuild (PK but_toan_id sẽ chặn, nhưng đừng dựa vào lỗi đó — spec §Q8)."""

from django.db import connection
from django.test import TestCase

from apps.ai_mart.application import etl_service
from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql import lich_su_kho
from apps.catalog.models import Product
from apps.inventory.models import StockItem, StockMovement
from apps.warehouses.models import Warehouse


class KhoTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(code="HN", name="Kho Hà Nội")
        self.p = Product.objects.create(name="Ghế", sku="GHE1", price=1)

    def test_ton_kho_translated(self):
        StockItem.objects.create(warehouse=self.wh, product=self.p, state="defect", quantity=3)
        etl_service.rebuild_all()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT kho_ten, trang_thai_ton_text, so_luong FROM ai.ton_kho WHERE sku = 'GHE1'"
            )
            self.assertEqual(cur.fetchone(), ("Kho Hà Nội", "Lỗi", 3))

    def test_lich_su_kho_incremental_append(self):
        etl_service.rebuild_all()
        StockMovement.objects.create(
            warehouse=self.wh,
            product=self.p,
            movement_type="receipt",
            qty_delta=5,
            balance_after=5,
            reference_type="document",
            reference_id=1,
        )
        n = lich_su_kho.sync_incremental()
        self.assertEqual(n, 1)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT loai_bien_dong_text, trang_thai_ton_text, ma_don"
                " FROM ai.lich_su_kho WHERE sku = 'GHE1'"
            )
            self.assertEqual(cur.fetchone(), ("Nhập kho", "Bán được", None))

    def test_sync_hot_advances_ledger_incrementally(self):
        """etl_service.sync_hot() phải tự gọi lich_su_kho.sync_incremental() —
        không phải đợi rebuild đêm mới thấy bút toán mới (spec Q8)."""
        etl_service.rebuild_all()
        StockMovement.objects.create(
            warehouse=self.wh,
            product=self.p,
            movement_type="issue",
            qty_delta=-2,
            balance_after=-2,
            reference_type="document",
            reference_id=2,
        )
        result = etl_service.sync_hot()
        self.assertEqual(result["tables"]["ai.lich_su_kho"], 1)
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai.lich_su_kho WHERE sku = 'GHE1'")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_rebuild_resets_ledger_watermark(self):
        """rebuild_all() phải tự chốt moc_id = max(stock_movements.id) — nếu
        không, sync_incremental() kế tiếp sẽ cố INSERT lại đúng các dòng vừa
        rebuild xong (dựa vào lỗi PK để chặn là sai theo spec §Q8)."""
        StockMovement.objects.create(
            warehouse=self.wh,
            product=self.p,
            movement_type="receipt",
            qty_delta=5,
            balance_after=5,
            reference_type="document",
            reference_id=1,
        )
        etl_service.rebuild_all()
        with connection.cursor() as cur:
            cur.execute("SELECT coalesce(max(id), 0) FROM stock_movements")
            max_id = cur.fetchone()[0]
        self.assertEqual(loaders.read_watermark_id("ai.lich_su_kho"), max_id)
        self.assertEqual(lich_su_kho.sync_incremental(), 0)

    def test_incremental_overlap_is_idempotent(self):
        """ID_OVERLAP tồn tại để đắp cho race: một transaction lấy id THẤP hơn
        có thể commit SAU khi ta đã đọc max(id) làm mốc, nên bị bỏ sót cho tới
        lần sync sau. Khi mốc bị lùi lại (mô phỏng lại vùng overlap), quét lại
        các dòng đã có phải VÔ HẠI — ON CONFLICT DO NOTHING, không nhân đôi."""
        etl_service.rebuild_all()
        StockMovement.objects.create(
            warehouse=self.wh,
            product=self.p,
            movement_type="receipt",
            qty_delta=5,
            balance_after=5,
            reference_type="document",
            reference_id=1,
        )
        StockMovement.objects.create(
            warehouse=self.wh,
            product=self.p,
            movement_type="issue",
            qty_delta=-2,
            balance_after=3,
            reference_type="document",
            reference_id=2,
        )
        self.assertEqual(lich_su_kho.sync_incremental(), 2)

        with connection.cursor() as cur:
            cur.execute("SELECT coalesce(max(id), 0) FROM stock_movements")
            max_id = cur.fetchone()[0]
        loaders.write_watermark_id("ai.lich_su_kho", max_id - 1)

        self.assertEqual(lich_su_kho.sync_incremental(), 0)
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM ai.lich_su_kho WHERE sku = 'GHE1'")
            self.assertEqual(cur.fetchone()[0], 2)
