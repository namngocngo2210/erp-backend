"""Nhận dạng sản phẩm trong tên chiến dịch quảng cáo.

Mọi ca ở đây lấy từ tên chiến dịch THẬT trên prod (khảo sát 2026-08-24) — xem
docs/ads-campaign-product-mapping.md.
"""

from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.catalog.models import Product
from apps.catalog.sku_text_match import (
    build_sku_index,
    learn_code_aliases,
    match_product,
    tokenize,
)


class TokenizeTests(SimpleTestCase):
    def test_strips_leading_zeros_and_keeps_original(self):
        """Phòng Ads viết SKU thành `0DC30`, mã thật là `DC30` — phải nhận cả hai."""
        tokens = tokenize("Desiglobal_BonNgamChan_XYZ_LDP_0DC30_030826_Page 2")

        self.assertIn("0DC30", tokens)
        self.assertIn("DC30", tokens)

    def test_treats_leading_capital_o_as_zero(self):
        """Prod có cả `ODC12` (chữ O) lẫn `0DC12` (số 0) cho cùng một sản phẩm."""
        self.assertIn("DC12", tokenize("Desiglobal_MayMassageBapChan_XYZ_LDP_ODC12_050826_TK2"))

    def test_does_not_eat_the_o_of_a_plain_word(self):
        """Chỉ bỏ chữ O khi token có chứa số — nếu không sẽ nuốt chữ O của từ thật."""
        self.assertIn("OLD", tokenize("Desiglobal_X_150826_Old"))

    def test_keeps_order_of_appearance(self):
        tokens = tokenize("DMG - RMK 2 - TMS01-DT11-BNC02")

        self.assertLess(tokens.index("TMS01"), tokens.index("BNC02"))


class MatchProductTests(TestCase):
    def setUp(self):
        self.dc30 = Product.objects.create(name="Bồn ngâm chân DC30", sku="DC30")
        self.bnc02 = Product.objects.create(name="Bồn ngâm chân BNC02", sku="BNC02")
        # Ca thật của prod: `TMS01` vừa là SKU đầy đủ (SKU RÁC, tên đúng bằng mã, gần như
        # không bán) vừa là mã gốc của biến thể đang chạy quảng cáo.
        self.tms_rac = Product.objects.create(name="TMS01", sku="TMS01")
        self.tms_promax = Product.objects.create(name="Thảm massage TMS01", sku="TMS01-PROMAX")
        # Ca thật: `SQA03` KHÔNG tồn tại như SKU đầy đủ, chỉ có 2 biến thể.
        self.sqa_15 = Product.objects.create(name="Tủ sấy 1.5m", sku="SQA03-1.5M")
        self.sqa_17 = Product.objects.create(name="Tủ sấy 1.7m", sku="SQA03-1.7M")
        self.rank = {
            self.tms_rac.id: Decimal("1699000"),
            self.tms_promax.id: Decimal("47545901520"),
            self.sqa_15.id: Decimal("8477872"),
            self.sqa_17.id: Decimal("1462478739"),
        }
        self.index = build_sku_index(self.rank)

    def _match(self, name, loose=False):
        return match_product(name, self.index, loose=loose)

    def test_single_variant_is_a_confident_match(self):
        self.assertEqual(
            self._match("Desiglobal_BonNgamChan_XYZ_LDP_0DC30_030826"), (self.dc30.id, False)
        )

    def test_junk_sku_never_wins_over_the_variant_actually_selling(self):
        """Bug suýt phát trên prod: 657 chiến dịch thảm massage sẽ về SKU rác `TMS01`.

        Khớp SKU đầy đủ cho ra `TMS01` (1,7tr/90 ngày). Gom theo mã gốc rồi xếp theo
        doanh thu cho ra `TMS01-PROMAX` (47,5 tỉ) — đúng thứ phòng Ads đang chạy.
        """
        product_id, guessed = self._match("Desiglobal_ThamMassagePromax_XYZ_LDP_TMS01_220826")

        self.assertEqual(product_id, self.tms_promax.id)
        self.assertTrue(guessed, "phải đánh dấu là ĐOÁN vì mã gốc có nhiều biến thể")

    def test_base_code_without_a_full_sku_still_matches(self):
        """`SQA03` không phải SKU nào cả — trước đây rơi thẳng vào nhóm chưa gán."""
        self.assertEqual(
            self._match("Desiglobal_Tusayquanao_XYZ_LDP_SQA03_220826"), (self.sqa_17.id, True)
        )

    def test_two_products_left_alone_in_strict_mode(self):
        self.assertEqual(self._match("DMG - RMK 2 - DC30-DT11-BNC02"), (None, False))

    def test_two_products_take_the_first_in_loose_mode(self):
        """Mã xuất hiện đầu tiên thường là sản phẩm chính của chiến dịch."""
        self.assertEqual(
            self._match("DMG - RMK 2 - DC30-DT11-BNC02", loose=True), (self.dc30.id, True)
        )

    def test_no_code_in_name_returns_none(self):
        for name in ("Shopping Thảm Massage", "Pmax 3", "06/05 bài 3", "remarketing"):
            self.assertEqual(self._match(name, loose=True), (None, False), name)

    def test_soft_deleted_product_is_ignored(self):
        Product.objects.filter(sku="DC30").update(deleted_at=timezone.now())

        self.assertEqual(match_product("LDP_0DC30", build_sku_index(self.rank)), (None, False))

    def test_short_codes_never_match(self):
        """Mã 1-2 ký tự khớp nhầm với `TK8`, `B1`… nên bị loại khỏi bảng tra."""
        Product.objects.create(name="Mã ngắn", sku="B1")

        self.assertEqual(
            match_product("Desiglobal_X_TK8-SQA03-B1-THA", build_sku_index(self.rank))[0],
            self.sqa_17.id,
        )


class LearnCodeAliasesTests(TestCase):
    """Học bí danh từ chiến dịch đã gán — vớt các tên không mang mã SKU nào."""

    def setUp(self):
        self.bnc02 = Product.objects.create(name="Bồn ngâm chân BNC02", sku="BNC02")
        self.index = build_sku_index({})

    def test_token_always_paired_with_one_product_becomes_an_alias(self):
        rows = [
            (f"Desiglobal_BonNgamChanGapGon_XYZ_LDP_BNC02_{day}0826", self.bnc02.id)
            for day in range(1, 4)
        ]

        aliases = learn_code_aliases(rows, self.index)

        self.assertEqual(aliases.get("BONNGAMCHANGAPGON"), self.bnc02.id)

    def test_token_seen_with_two_products_is_not_learned(self):
        other = Product.objects.create(name="Khác", sku="DC30")
        rows = [
            ("Desiglobal_ChungChung_XYZ_BNC02_1", self.bnc02.id),
            ("Desiglobal_ChungChung_XYZ_BNC02_2", self.bnc02.id),
            ("Desiglobal_ChungChung_XYZ_DC30_3", other.id),
        ]

        self.assertNotIn("CHUNGCHUNG", learn_code_aliases(rows, self.index))

    def test_rare_token_is_not_learned(self):
        rows = [("Desiglobal_XuatHienMotLan_XYZ_BNC02_1", self.bnc02.id)]

        self.assertEqual(learn_code_aliases(rows, self.index), {})
