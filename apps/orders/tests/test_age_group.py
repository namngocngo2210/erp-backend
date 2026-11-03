"""Nhóm tuổi (age_text) — validation, hợp đồng AI, parser backfill và catalog.

Gộp từ test_age_group_validation.py, test_age_group_backfill.py,
test_age_group_catalog.py.
"""

from django.test import SimpleTestCase, TestCase

from apps.orders.age_group_backfill import (
    AGE_GROUP_NOT_DISCLOSED,
    group_for_age,
    parse_age_group,
)
from apps.orders.ai_extract_contract import (
    build_extract_response_schema,
    build_prompt_catalog_text,
    normalize_extracted,
)
from apps.orders.assessment_fields import (
    ASSESSMENT_FIELDS_BY_NAME,
    AssessmentValidationError,
    coerce_and_validate,
)
from apps.orders.crm_catalog import (
    AGE_GROUP_LABELS,
    AGE_GROUP_OPTIONS,
    CRM_CODE_LABELS,
    build_crm_catalog_payload,
)


class AgeGroupValidationTests(SimpleTestCase):
    def test_is_a_code_field(self):
        self.assertEqual(ASSESSMENT_FIELDS_BY_NAME["age_text"].kind, "code")

    def test_accepts_valid_code(self):
        self.assertEqual(coerce_and_validate("age_text", "45_54"), "45_54")

    def test_rejects_free_text(self):
        for value in ("45", "45-60", "ngoài 40"):
            with self.assertRaises(AssessmentValidationError, msg=value):
                coerce_and_validate("age_text", value)

    def test_null_becomes_empty_string(self):
        """Cột NOT NULL default '' nên null phải hoá rỗng, không phải None."""
        self.assertEqual(coerce_and_validate("age_text", None), "")

    def test_empty_string_allowed(self):
        self.assertEqual(coerce_and_validate("age_text", ""), "")


class AiContractAgeGroupTests(TestCase):
    def test_accepts_whitelisted_code(self):
        clean, rejected = normalize_extracted({"age_text": "45_54", "evidence": []})
        self.assertEqual(clean["age_text"], "45_54")
        self.assertNotIn("age_text", rejected)

    def test_rejects_free_text_from_llm(self):
        clean, rejected = normalize_extracted({"age_text": "45 tuổi", "evidence": []})
        self.assertNotIn("age_text", clean)
        self.assertEqual(rejected["age_text"], "45 tuổi")

    def test_schema_exposes_enum(self):
        prop = build_extract_response_schema()["properties"]["age_text"]
        self.assertIn("65_plus", prop["enum"])
        self.assertEqual(prop["type"], "string")

    def test_schema_enum_hides_not_disclosed(self):
        """Sale chọn được "Không xác định"; AI thì không — xem AI_AGE_GROUP_LABELS.

        Enum còn lại phải đúng bằng catalog trừ đúng mã đó, để thêm bracket
        tuổi mới trong crm_catalog vẫn tự chảy sang prompt.
        """
        prop = build_extract_response_schema()["properties"]["age_text"]
        self.assertNotIn(AGE_GROUP_NOT_DISCLOSED, prop["enum"])
        self.assertEqual(set(prop["enum"]), set(AGE_GROUP_LABELS) - {AGE_GROUP_NOT_DISCLOSED})

    def test_normalize_rejects_not_disclosed(self):
        # Schema chỉ là gợi ý — model vẫn trả mã ngoài enum được, nên chốt chặn
        # thật nằm ở normalize.
        clean, rejected = normalize_extracted(
            {"age_text": AGE_GROUP_NOT_DISCLOSED, "evidence": []}
        )
        self.assertNotIn("age_text", clean)
        self.assertEqual(rejected["age_text"], AGE_GROUP_NOT_DISCLOSED)

    def test_prompt_tells_model_age_text_is_a_bracket(self):
        # Tên field đọc như text tự do; thiếu chú thích này model trả "45 tuổi".
        line = next(
            row
            for row in build_prompt_catalog_text().splitlines()
            if row.startswith("- age_text")
        )
        self.assertIn("nhóm tuổi", line)
        self.assertIn("45_54 = 45-54", line)
        self.assertNotIn(AGE_GROUP_NOT_DISCLOSED, line)


class ParseAgeGroupTests(SimpleTestCase):
    def test_empty_stays_empty(self):
        """Rỗng = sale chưa hỏi, khác hẳn 'đã hỏi nhưng khách không nói'."""
        for raw in ("", "   ", None):
            with self.subTest(raw=repr(raw)):
                self.assertEqual(parse_age_group(raw), "")

    def test_plain_number(self):
        cases = {"70": "65_plus", "60": "55_64", "50": "45_54",
                 "40": "35_44", "30": "25_34", "20": "18_24"}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_age_group(raw), expected)

    def test_t_suffix_is_stripped(self):
        """'t' = 'tuổi'. 305/2725 dòng prod dùng dạng này."""
        self.assertEqual(parse_age_group("70t"), "65_plus")
        self.assertEqual(parse_age_group("45T"), "45_54")

    def test_boundaries(self):
        cases = {"18": "18_24", "24": "18_24", "25": "25_34", "34": "25_34", "35": "35_44",
                 "44": "35_44", "45": "45_54", "54": "45_54", "55": "55_64",
                 "64": "55_64", "65": "65_plus", "100": "65_plus"}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_age_group(raw), expected)

    def test_out_of_range_is_not_disclosed(self):
        for raw in ("9", "17", "101", "150"):
            with self.subTest(raw=raw):
                self.assertEqual(parse_age_group(raw), "not_disclosed")

    def test_text_without_digits_is_not_disclosed(self):
        for raw in ("ko chia sẻ", "trung niên", "già", "n/a", "-"):
            with self.subTest(raw=raw):
                self.assertEqual(parse_age_group(raw), "not_disclosed")

    def test_range_uses_midpoint(self):
        cases = {"35-40": "35_44", "38-45": "35_44", "55-60": "55_64",
                 "49-55": "45_54", "60-70": "65_plus", "35-45t": "35_44"}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_age_group(raw), expected)

    def test_decade_shorthand(self):
        """'7x' = bảy mươi mấy → midpoint 74. '6x' → 64."""
        self.assertEqual(parse_age_group("7x"), "65_plus")
        self.assertEqual(parse_age_group("6x"), "55_64")

    def test_fuzzy_prefix_uses_first_number(self):
        cases = {"ngoài 40": "35_44", "ngoài  40": "35_44",
                 "ngoài 50": "45_54", "gần 70": "65_plus"}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_age_group(raw), expected)

    def test_birth_year(self):
        """Mốc cố định 2026 để kết quả tái lập được."""
        self.assertEqual(parse_age_group("1980"), "45_54")
        self.assertEqual(parse_age_group("1960"), "65_plus")

    def test_group_for_age_direct(self):
        self.assertEqual(group_for_age(47), "45_54")
        self.assertEqual(group_for_age(5), "not_disclosed")

    def test_all_catalog_codes_are_fixed_points(self):
        """Load-bearing: migration idempotency + re-import of already-coded CSV."""
        for code in AGE_GROUP_LABELS:
            self.assertEqual(parse_age_group(code), code, code)


class AgeGroupCatalogTests(SimpleTestCase):
    # build_crm_catalog_payload() từ Task 6 đọc AssessmentFieldDef (dynamic_fields) qua
    # get_active_defs() — cần khai báo databases để SimpleTestCase cho phép query.
    databases = {"default"}

    def test_seven_options_in_order(self):
        self.assertEqual(
            [o.code for o in AGE_GROUP_OPTIONS],
            ["18_24", "25_34", "35_44", "45_54", "55_64", "65_plus", "not_disclosed"],
        )

    def test_labels(self):
        self.assertEqual(AGE_GROUP_LABELS["65_plus"], "65+")
        self.assertEqual(AGE_GROUP_LABELS["not_disclosed"], "Không xác định")

    def test_parser_code_matches_catalog(self):
        """Parser và catalog phải nói cùng một ngôn ngữ."""
        self.assertIn(AGE_GROUP_NOT_DISCLOSED, AGE_GROUP_LABELS)

    def test_never_uses_unknown_string(self):
        """'unknown' đã bị report_fields.UNKNOWN_CODE chiếm cho nghĩa 'chưa điền'."""
        self.assertNotIn("unknown", AGE_GROUP_LABELS)

    def test_registered_for_label_lookup(self):
        self.assertIs(CRM_CODE_LABELS["age_text"], AGE_GROUP_LABELS)

    def test_exposed_in_catalog_payload(self):
        payload = build_crm_catalog_payload()
        self.assertEqual(
            payload["age_group_options"][0], {"code": "18_24", "label": "18-24"},
        )
        self.assertEqual(len(payload["age_group_options"]), 7)
