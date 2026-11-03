from django.db import connection
from django.test import TestCase

from apps.ai_mart.infrastructure import loaders
from apps.ai_mart.infrastructure.sql.san_pham import MART
from apps.catalog.models import Product, ProductComponent


class SanPhamTests(TestCase):
    def test_combo_flag_from_components(self):
        combo = Product.objects.create(name="Bộ ghế", sku="COMBO1", price=1000)
        child = Product.objects.create(name="Ghế", sku="GHE1", price=800)
        ProductComponent.objects.create(parent=combo, component=child, quantity=1)
        loaders.full_rebuild(MART)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT la_combo, thanh_phan_combo FROM ai.san_pham WHERE sku = 'COMBO1'"
            )
            la_combo, thanh_phan = cur.fetchone()
            cur.execute("SELECT la_combo FROM ai.san_pham WHERE sku = 'GHE1'")
            child_flag = cur.fetchone()[0]
        self.assertTrue(la_combo)
        self.assertEqual(thanh_phan, ["GHE1"])
        self.assertFalse(child_flag)
