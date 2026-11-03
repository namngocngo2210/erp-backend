from django.test import TestCase

from apps.ai_mart.domain import chat_extract


class ChatExtractContractTests(TestCase):
    def test_invented_codes_go_to_rejected_not_clean(self):
        # too_expensive: mã THẬT trong crm_catalog.NOT_CLOSED_REASON_OPTIONS
        # (price_high không tồn tại — xem apps/orders/crm_catalog.py).
        clean, rejected = chat_extract.normalize_chat_extracted({
            "tom_tat": "khách chê đắt",
            "nhu_cau": ["neck_shoulder_nape", "mã_tự_chế"],
            "ly_do_khong_mua": ["too_expensive", "lý_do_bịa"],
            "giai_doan": "giai_doan_bia",
            "muc_do_quan_tam": "cao",
        })
        self.assertEqual(clean["nhu_cau"], ["neck_shoulder_nape"])
        self.assertEqual(rejected["nhu_cau"], ["mã_tự_chế"])
        self.assertEqual(clean["ly_do_khong_mua"], ["too_expensive"])
        self.assertEqual(rejected["ly_do_khong_mua"], ["lý_do_bịa"])
        self.assertNotIn("giai_doan", clean)
        self.assertEqual(rejected["giai_doan"], "giai_doan_bia")
        self.assertEqual(clean["muc_do_quan_tam"], "cao")

    def test_phone_is_normalized_before_storing(self):
        clean, _ = chat_extract.normalize_chat_extracted({"sdt_khach": "+84 912 345 678"})
        self.assertEqual(clean["sdt_khach"], "0912345678")

    def test_unparseable_phone_is_dropped_not_stored_raw(self):
        # Nối đơn join thẳng customers.phone_normalized — rác lọt vào là join sai.
        clean, rejected = chat_extract.normalize_chat_extracted({"sdt_khach": "gọi em nhé"})
        self.assertNotIn("sdt_khach", clean)
        self.assertEqual(rejected["sdt_khach"], "gọi em nhé")

    def test_product_names_are_trimmed_and_capped(self):
        clean, _ = chat_extract.normalize_chat_extracted(
            {"san_pham_quan_tam": ["  Ghế X10  ", "", "Ghế X10", "A" * 300]}
        )
        products = clean["san_pham_quan_tam"]
        self.assertEqual(products[0], "Ghế X10")
        self.assertNotIn("", products)
        # "  Ghế X10  " và "Ghế X10" trim về CÙNG một chuỗi — khử trùng lặp trên
        # giá trị ĐÃ TRIM (giá trị này đi thẳng vào cột TEXT[] của mart), nên chỉ
        # còn 2 mục: "Ghế X10" và bản "A"*300 đã cắt còn 200 ký tự.
        self.assertEqual(len(products), 2)
        self.assertEqual(len(set(products)), len(products))        # không còn trùng lặp
        self.assertLessEqual(len(products[-1]), 200)

    def test_evidence_field_must_be_a_known_field_and_list_is_capped(self):
        evidence = [{"field": "nhu_cau", "quote": f"câu {i}"} for i in range(30)]
        evidence.append({"field": "mã_field_bịa", "quote": "khách nói gì đó"})
        clean, _ = chat_extract.normalize_chat_extracted({"evidence": evidence})
        self.assertTrue(all(item["field"] == "nhu_cau" for item in clean["evidence"]))
        self.assertLessEqual(len(clean["evidence"]), chat_extract.MAX_EVIDENCE)

    def test_schema_enums_come_from_the_dictionary(self):
        schema = chat_extract.build_chat_response_schema()
        self.assertIn("hoi_gia", schema["properties"]["giai_doan"]["enum"])
        self.assertIn(
            "neck_shoulder_nape", schema["properties"]["nhu_cau"]["items"]["enum"]
        )

    def test_prompt_forbids_guessing_and_lists_codes(self):
        prompt = chat_extract.build_chat_prompt("Khách: đau vai\nPage: dạ")
        self.assertIn("TUYỆT ĐỐI không suy đoán", prompt)
        self.assertIn("neck_shoulder_nape", prompt)
        self.assertIn("Khách: đau vai", prompt)
