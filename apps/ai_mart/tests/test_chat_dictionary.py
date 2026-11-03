"""Từ điển chat: nhóm tĩnh seed đủ, nhóm nhu cầu bám theo bảng need_types.

need_types trên prod có 36 mã trong khi crm_catalog.NEED_TYPE_SEED chỉ có 29 —
seed tĩnh sẽ để 7 mã thành mồ côi (cột _ma có giá trị, _text NULL). Vì vậy
nhu_cau/nhom_nhu_cau PHẢI đọc từ bảng, không chép hằng số.
"""

from django.db import connection
from django.test import TestCase

from apps.ai_mart.domain.chat_dictionary import CHAT_DICTIONARY
from apps.ai_mart.domain.need_dictionary import refresh_need_dictionary
from apps.orders.models import NeedGroup, NeedType


class ChatDictionaryTests(TestCase):
    def _labels(self, nhom):
        with connection.cursor() as cur:
            cur.execute("SELECT ma, nhan FROM ai.tu_dien_ma WHERE nhom_ma = %s", [nhom])
            return dict(cur.fetchall())

    def test_static_groups_seeded_by_migration(self):
        for nhom, items in CHAT_DICTIONARY.items():
            seeded = self._labels(nhom)
            for ma, nhan in items:
                self.assertEqual(seeded.get(ma), nhan, f"{nhom}.{ma} chưa seed")

    def test_platform_codes_cover_measured_source_values(self):
        # 6 platform đo được trên DB crawler ngày 04/09/2026
        seeded = self._labels("nen_tang_chat")
        for code in ("facebook", "zalo", "personal_zalo", "tiktok_shop",
                     "shopee_official", "youtube"):
            self.assertIn(code, seeded)

    def test_refresh_picks_up_need_type_added_after_migration(self):
        group = NeedGroup.objects.create(code="grp_moi", name_vi="Nhóm mới", sort_order=900)
        NeedType.objects.create(
            code="nhu_cau_moi", group=group, name_vi="Nhu cầu mới",
            sort_order=910, is_active=True,
        )
        with connection.cursor() as cur:
            refresh_need_dictionary(cur)
        self.assertEqual(self._labels("nhu_cau").get("nhu_cau_moi"), "Nhu cầu mới")
        self.assertEqual(self._labels("nhom_nhu_cau").get("grp_moi"), "Nhóm mới")

    def test_inactive_need_type_seeded_with_deprecation_note(self):
        group = NeedGroup.objects.create(code="grp_cu", name_vi="Nhóm cũ", sort_order=901)
        NeedType.objects.create(
            code="nhu_cau_cu", group=group, name_vi="Nhu cầu cũ",
            sort_order=911, is_active=False,
        )
        with connection.cursor() as cur:
            refresh_need_dictionary(cur)
            cur.execute(
                "SELECT mo_ta FROM ai.tu_dien_ma WHERE nhom_ma = 'nhu_cau' AND ma = 'nhu_cau_cu'"
            )
            (mo_ta,) = cur.fetchone()
        # mã ngừng dùng VẪN phải có nhãn (nhãn cũ trên mart không được thành mồ côi),
        # nhưng phải nói rõ là đã ngừng — nếu không người đọc tưởng vẫn dùng được.
        self.assertIn("ngừng dùng", (mo_ta or ""))
