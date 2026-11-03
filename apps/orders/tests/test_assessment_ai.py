"""Test hợp đồng dữ liệu AI extract + luật merge vào OrderAssessment.

Gộp từ test_assessment_ai_fill.py và test_dynamic_ai_extract.py (tái cấu trúc
2026-08-05): phần sau phủ schema/normalize/fill cho field động (AssessmentFieldDef)
và open_insights — các class Dynamic* ở cuối file.
"""

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.ai_extract_contract import (
    AI_EVIDENCE_KEYS,
    OPEN_INSIGHTS_KEY,
    ai_evidence_keys,
    build_extract_response_schema,
    build_prompt_catalog_text,
    normalize_extracted,
)
from apps.orders.application.services.assessment_ai_fill_service import (
    OPEN_INSIGHT_MAX_ITEMS,
    apply_ai_insights,
)
from apps.orders.application.services.order_service_helpers import OrderServiceHelpers
from apps.orders.crm_catalog import EVENT_TYPE_LABELS
from apps.orders.dynamic_assessment import REGISTRY_CACHE_KEY
from apps.orders.models import (
    AssessmentFieldDef,
    FieldKind,
    NeedGroup,
    NeedType,
    Order,
    OrderAssessment,
    OrderJourneyEvent,
    OrderNeed,
)
from apps.users.tests.helpers import make_admin


def make_order(code: str) -> Order:
    """Order.channel và Order.customer đều là FK NOT NULL (PROTECT) —
    thiếu 1 trong 2 là IntegrityError."""
    channel = Channel.objects.create(code=code, name=f"Landing {code}")
    customer = Customer.objects.create(
        full_name="Khách A", phone_raw="0912345678", phone_normalized="0912345678"
    )
    return Order.objects.create(
        channel=channel, customer=customer, name="Khách A", phone="0912345678", status=0
    )


class ExtractContractTests(SimpleTestCase):
    def test_schema_enum_comes_from_catalog(self):
        schema = build_extract_response_schema()
        props = schema["properties"]
        self.assertIn("severe", props["pain_level_code"]["enum"])
        self.assertIn("sit_a_lot", props["lifestyle_habit_codes"]["items"]["enum"])
        self.assertIn("neck_shoulder_nape", props["need_type_codes"]["items"]["enum"])
        # Enum evidence = AI_EVIDENCE_KEYS tĩnh + field động ai_enabled=True (migration
        # 0113 seed sẵn 3 field text AI) — chỉ assert tập tĩnh là subset, không assert
        # đúng bằng để không phụ thuộc field động nào đang tồn tại trong DB.
        self.assertLessEqual(
            set(AI_EVIDENCE_KEYS),
            set(props["evidence"]["items"]["properties"]["field"]["enum"]),
        )

    def test_normalize_drops_codes_outside_catalog(self):
        clean, rejected = normalize_extracted(
            {
                "pain_level_code": "khong_co_ma_nay",
                "pain_duration_code": "over_1y",
                "lifestyle_habit_codes": ["sit_a_lot", "bay_len_troi", "sit_a_lot"],
                "need_type_codes": ["knee_pain", "khong_ton_tai"],
                "evidence": [{"field": "pain_duration_code", "quote": "đau hơn một năm rồi"}],
            }
        )
        self.assertNotIn("pain_level_code", clean)
        self.assertEqual(clean["pain_duration_code"], "over_1y")
        self.assertEqual(clean["lifestyle_habit_codes"], ["sit_a_lot"])
        self.assertEqual(clean["need_type_codes"], ["knee_pain"])
        self.assertEqual(clean["evidence"], {"pain_duration_code": "đau hơn một năm rồi"})
        self.assertIn("pain_level_code", rejected)
        self.assertIn("bay_len_troi", rejected["lifestyle_habit_codes"])

    def test_normalize_rejects_age_text_outside_catalog(self):
        # age_text từng là text tự do cắt theo độ dài; nay là mã nhóm tuổi nên
        # luật là whitelist, không phải max_length.
        clean, rejected = normalize_extracted(
            {"age_text": "x" * 100, "used_massage_before": False, "evidence": []}
        )
        self.assertNotIn("age_text", clean)
        self.assertEqual(rejected["age_text"], "x" * 100)
        self.assertIs(clean["used_massage_before"], False)

    def test_normalize_drops_wrong_types(self):
        clean, rejected = normalize_extracted(
            {"age_text": 45, "used_massage_before": "có", "lifestyle_habit_codes": "sit_a_lot",
             "evidence": []}
        )
        self.assertNotIn("age_text", clean)
        self.assertNotIn("used_massage_before", clean)
        self.assertNotIn("lifestyle_habit_codes", clean)
        self.assertEqual(set(rejected), {"age_text", "used_massage_before", "lifestyle_habit_codes"})


class AiFilledFieldsColumnTests(TestCase):
    def setUp(self):
        self.order = make_order("lp-ai-col")

    def test_event_type_has_label(self):
        self.assertEqual(EVENT_TYPE_LABELS["assessment_ai_filled"], "AI điền từ cuộc gọi")

    def test_column_defaults_to_empty_dict(self):
        assessment = OrderAssessment.objects.create(order=self.order)
        assessment.refresh_from_db()
        self.assertEqual(assessment.ai_filled_fields, {})

    def test_sale_edit_clears_ai_key_for_that_field(self):
        assessment = OrderAssessment.objects.create(
            order=self.order,
            pain_level_code="light",
            age_text="35_44",
            ai_filled_fields={
                "pain_level_code": {"call_record_id": 1, "quote": "hơi mỏi", "at": "2026-07-24T09:00:00+07:00"},
                "age_text": {"call_record_id": 1, "quote": "em 40", "at": "2026-07-24T09:00:00+07:00"},
            },
        )
        OrderServiceHelpers._upsert_order_assessment(
            self.order, {"pain_level_code": "severe"}, None
        )
        assessment.refresh_from_db()
        self.assertNotIn("pain_level_code", assessment.ai_filled_fields)
        self.assertIn("age_text", assessment.ai_filled_fields)

    def test_serializer_exposes_ai_filled_fields_read_only(self):
        from apps.orders.transport.serializers.order_serializers_impl.product_item_crm_serializers import (
            OrderCRMSerializer,
        )

        marker = {"age_text": {"call_record_id": 3, "quote": "em 40", "at": "2026-07-24T09:00:00+07:00"}}
        assessment = OrderAssessment.objects.create(order=self.order, ai_filled_fields=marker)
        data = OrderCRMSerializer(assessment).data
        self.assertEqual(data["ai_filled_fields"], marker)
        self.assertTrue(OrderCRMSerializer().fields["ai_filled_fields"].read_only)


def _clean(**overrides):
    payload = {
        "age_text": "45_54",
        "pain_level_code": "severe",
        "lifestyle_habit_codes": ["sit_a_lot"],
        "need_type_codes": ["knee_pain"],
        "evidence": {
            "age_text": "em 45 tuổi",
            "pain_level_code": "đau nặng lắm",
            "lifestyle_habit_codes": "ngồi văn phòng cả ngày",
            "needs": "đầu gối đau",
        },
    }
    payload.update(overrides)
    return payload


class ApplyAiInsightsTests(TestCase):
    def setUp(self):
        # Dọn registry cache field động để không phụ thuộc thứ tự chạy với
        # test khác (bài học event_bus: cache còn lại từ test trước gây flake).
        cache.delete(REGISTRY_CACHE_KEY)
        self.addCleanup(cache.delete, REGISTRY_CACHE_KEY)
        self.order = make_order("lp-ai-apply")  # helper ở Task 3, cùng file
        # "pain_relief"/"knee_pain" đã có sẵn từ data migration catalog (0026,
        # 0043) — get_or_create để không đụng unique constraint khi chạy full suite.
        group, _ = NeedGroup.objects.get_or_create(
            code="pain_relief", defaults={"name_vi": "Giảm đau nhức", "sort_order": 10}
        )
        NeedType.objects.get_or_create(
            code="knee_pain", defaults={"group": group, "name_vi": "Đau đầu gối", "sort_order": 80}
        )

    def test_fills_empty_fields_and_creates_needs(self):
        filled = apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.age_text, "45_54")
        self.assertEqual(assessment.pain_level_code, "severe")
        self.assertEqual(assessment.lifestyle_habit_codes, ["sit_a_lot"])
        self.assertEqual(
            list(OrderNeed.objects.filter(order_assessment=assessment).values_list("need_type__code", flat=True)),
            ["knee_pain"],
        )
        self.assertEqual(filled, ["age_text", "lifestyle_habit_codes", "needs", "pain_level_code"])
        self.assertEqual(assessment.ai_filled_fields["pain_level_code"]["call_record_id"], 7)
        self.assertEqual(assessment.ai_filled_fields["pain_level_code"]["quote"], "đau nặng lắm")

    def test_never_overwrites_values_entered_by_sale(self):
        OrderAssessment.objects.create(order=self.order, pain_level_code="light", age_text="")
        apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.pain_level_code, "light")
        self.assertEqual(assessment.age_text, "45_54")
        self.assertNotIn("pain_level_code", assessment.ai_filled_fields)

    def test_does_not_union_non_empty_arrays(self):
        OrderAssessment.objects.create(order=self.order, lifestyle_habit_codes=["sports"])
        apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.lifestyle_habit_codes, ["sports"])

    def test_does_not_add_needs_when_assessment_already_has_some(self):
        assessment = OrderAssessment.objects.create(order=self.order)
        # "calf_pain" cũng đã có sẵn từ catalog seed, không tạo trùng.
        other, _ = NeedType.objects.get_or_create(
            code="calf_pain",
            defaults={
                "group": NeedGroup.objects.get(code="pain_relief"),
                "name_vi": "Đau bắp chân",
                "sort_order": 90,
            },
        )
        OrderNeed.objects.create(order_assessment=assessment, need_type=other)
        apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        self.assertEqual(
            list(OrderNeed.objects.filter(order_assessment=assessment).values_list("need_type__code", flat=True)),
            ["calf_pain"],
        )

    def test_skips_fields_without_evidence(self):
        clean = _clean(evidence={"age_text": "em 45 tuổi"})
        apply_ai_insights(order=self.order, clean=clean, call_record_id=7)
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.age_text, "45_54")
        self.assertIsNone(assessment.pain_level_code)
        self.assertEqual(assessment.lifestyle_habit_codes, [])

    def test_no_field_filled_creates_no_event(self):
        OrderAssessment.objects.create(
            order=self.order, age_text="25_34", pain_level_code="light",
            lifestyle_habit_codes=["sports"],
        )
        OrderNeed.objects.create(
            order_assessment=OrderAssessment.objects.get(order=self.order),
            need_type=NeedType.objects.get(code="knee_pain"),
        )
        filled = apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        self.assertEqual(filled, [])
        self.assertFalse(
            OrderJourneyEvent.objects.filter(order=self.order, event_type="assessment_ai_filled").exists()
        )

    def test_writes_exactly_one_journey_event_with_actor_none(self):
        apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        events = OrderJourneyEvent.objects.filter(order=self.order, event_type="assessment_ai_filled")
        self.assertEqual(events.count(), 1)
        event = events.first()
        self.assertIsNone(event.actor)
        self.assertEqual(event.payload_json["call_record_id"], 7)
        self.assertEqual(event.payload_json["fields"]["pain_level_code"]["value"], "severe")

    def test_preserves_updated_by(self):
        sale = make_admin()
        OrderAssessment.objects.create(order=self.order, updated_by=sale)
        apply_ai_insights(order=self.order, clean=_clean(), call_record_id=7)
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.updated_by_id, sale.id)

    def test_fills_false_boolean_when_field_is_none(self):
        # False là giá trị THẬT (chưa từng dùng máy massage), không phải "trống" —
        # _is_empty coi None mới là trống, chứ không phải falsy nói chung. Nếu
        # ai đó đơn giản hoá _is_empty thành `not value`, False sẽ bị coi là
        # trống và test này phải fail để bắt lại.
        OrderAssessment.objects.create(order=self.order)
        clean = _clean(
            used_massage_before=False,
            evidence={**_clean()["evidence"], "used_massage_before": "chưa dùng máy massage bao giờ"},
        )
        filled = apply_ai_insights(order=self.order, clean=clean, call_record_id=7)
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertIs(assessment.used_massage_before, False)
        self.assertIn("used_massage_before", filled)
        self.assertEqual(
            assessment.ai_filled_fields["used_massage_before"]["quote"],
            "chưa dùng máy massage bao giờ",
        )

    def test_does_not_overwrite_existing_false_boolean(self):
        # assessment đã có used_massage_before=False do sale nhập tay — đây là
        # câu trả lời thật, không phải ô trống. AI trả về True (đối lập) không
        # được đè lên. Nếu _is_empty bị thay bằng `not value`, False cũ sẽ bị
        # coi là "trống" và bị AI ghi đè, test này phải fail để bắt lại.
        OrderAssessment.objects.create(order=self.order, used_massage_before=False)
        clean = _clean(
            used_massage_before=True,
            evidence={**_clean()["evidence"], "used_massage_before": "có dùng máy massage rồi"},
        )
        apply_ai_insights(order=self.order, clean=clean, call_record_id=7)
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertIs(assessment.used_massage_before, False)
        self.assertNotIn("used_massage_before", assessment.ai_filled_fields)


class DynamicContractTests(TestCase):
    def setUp(self):
        cache.delete(REGISTRY_CACHE_KEY)
        self.addCleanup(cache.delete, REGISTRY_CACHE_KEY)
        # Migration 0113 seed "family_context" vĩnh viễn trong DB test; xoá để tránh
        # trùng code với fixture "family_context" tạo bên dưới.
        AssessmentFieldDef.objects.all().delete()
        AssessmentFieldDef.objects.create(
            code="housing_type", label="Loại nhà ở", kind=FieldKind.SINGLE_CHOICE,
            options=[{"code": "apartment", "label": "Chung cư"}, {"code": "house", "label": "Nhà đất"}],
            ai_enabled=True,
        )
        AssessmentFieldDef.objects.create(
            code="family_context", label="Hoàn cảnh gia đình", kind=FieldKind.TEXT,
            ai_enabled=True, ai_hint="ai dùng máy, ai quyết định chi tiền",
        )
        AssessmentFieldDef.objects.create(
            code="manual_only", label="Chỉ sale nhập", kind=FieldKind.TEXT, ai_enabled=False,
        )
        AssessmentFieldDef.objects.create(
            code="purchase_channels", label="Kênh mua hàng", kind=FieldKind.MULTI_CHOICE,
            options=[
                {"code": "facebook", "label": "Facebook"},
                {"code": "zalo", "label": "Zalo"},
                {"code": "tiktok", "label": "TikTok"},
            ],
            ai_enabled=True,
        )
        AssessmentFieldDef.objects.create(
            code="budget_million", label="Ngân sách (triệu)", kind=FieldKind.NUMBER, ai_enabled=True,
        )
        AssessmentFieldDef.objects.create(
            code="has_elderly", label="Có người lớn tuổi trong nhà", kind=FieldKind.BOOL, ai_enabled=True,
        )

    def test_schema_includes_dynamic_defs_and_open_insights(self):
        props = build_extract_response_schema()["properties"]
        self.assertEqual(props["housing_type"]["enum"], ["apartment", "house"])
        self.assertEqual(props["family_context"]["type"], "string")
        self.assertNotIn("manual_only", props)
        item_props = props[OPEN_INSIGHTS_KEY]["items"]["properties"]
        self.assertEqual(set(item_props), {"topic", "content", "quote"})

    def test_schema_maps_multi_choice_number_bool_kinds(self):
        props = build_extract_response_schema()["properties"]
        self.assertEqual(
            props["purchase_channels"],
            {"type": "array", "items": {"type": "string", "enum": ["facebook", "tiktok", "zalo"]}},
        )
        self.assertEqual(props["budget_million"], {"type": "number", "nullable": True})
        self.assertEqual(props["has_elderly"], {"type": "boolean", "nullable": True})

    def test_evidence_enum_covers_dynamic_codes(self):
        enum = build_extract_response_schema()["properties"]["evidence"]["items"]["properties"]["field"]["enum"]
        self.assertIn("housing_type", enum)
        self.assertIn("family_context", enum)
        self.assertEqual(sorted(ai_evidence_keys()), enum)

    def test_prompt_lists_dynamic_fields_with_hint(self):
        text = build_prompt_catalog_text()
        self.assertIn("housing_type", text)
        self.assertIn("ai dùng máy, ai quyết định chi tiền", text)
        self.assertIn(OPEN_INSIGHTS_KEY, text)

    def test_normalize_keeps_valid_dynamic_and_rejects_unknown_code(self):
        clean, rejected = normalize_extracted(
            {
                "housing_type": "apartment",
                "family_context": "  sống cùng con trai  ",
                "manual_only": "AI không được điền",
                "evidence": [{"field": "housing_type", "quote": "nhà tôi chung cư"}],
            }
        )
        self.assertEqual(clean["housing_type"], "apartment")
        self.assertEqual(clean["family_context"], "sống cùng con trai")
        self.assertNotIn("manual_only", clean)
        self.assertEqual(clean["evidence"], {"housing_type": "nhà tôi chung cư"})
        clean2, rejected2 = normalize_extracted({"housing_type": "tent"})
        self.assertNotIn("housing_type", clean2)
        self.assertEqual(rejected2["housing_type"], "tent")

    def test_normalize_open_insights_requires_quote_and_caps_topic(self):
        clean, _ = normalize_extracted(
            {
                OPEN_INSIGHTS_KEY: [
                    {"topic": "Bảo hành", "content": "khách hỏi bảo hành 5 năm", "quote": "bảo hành mấy năm"},
                    {"topic": "Thiếu quote", "content": "bị loại", "quote": ""},
                    {"topic": "t" * 200, "content": "topic bị cắt", "quote": "q"},
                    "not-a-dict",
                ]
            }
        )
        items = clean[OPEN_INSIGHTS_KEY]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["topic"], "Bảo hành")
        self.assertEqual(len(items[1]["topic"]), 100)

    def test_normalize_multi_choice_dedupes_keeps_order_and_rejects_unknown(self):
        clean, rejected = normalize_extracted(
            {"purchase_channels": ["zalo", "facebook", "instagram", "facebook"]}
        )
        self.assertEqual(clean["purchase_channels"], ["zalo", "facebook"])
        self.assertEqual(rejected["purchase_channels"], ["instagram"])

    def test_normalize_number_rejects_bool_and_numeric_string(self):
        clean, rejected = normalize_extracted({"budget_million": 25})
        self.assertEqual(clean["budget_million"], 25)
        self.assertNotIn("budget_million", rejected)

        clean_bool, rejected_bool = normalize_extracted({"budget_million": True})
        self.assertNotIn("budget_million", clean_bool)
        self.assertEqual(rejected_bool["budget_million"], True)

        clean_str, rejected_str = normalize_extracted({"budget_million": "25"})
        self.assertNotIn("budget_million", clean_str)
        self.assertEqual(rejected_str["budget_million"], "25")

    def test_normalize_bool_rejects_truthy_non_bool(self):
        clean, rejected = normalize_extracted({"has_elderly": True})
        self.assertIs(clean["has_elderly"], True)
        self.assertNotIn("has_elderly", rejected)

        clean_str, rejected_str = normalize_extracted({"has_elderly": "yes"})
        self.assertNotIn("has_elderly", clean_str)
        self.assertEqual(rejected_str["has_elderly"], "yes")

        clean_int, rejected_int = normalize_extracted({"has_elderly": 1})
        self.assertNotIn("has_elderly", clean_int)
        self.assertEqual(rejected_int["has_elderly"], 1)


class DynamicAiFillTests(TestCase):
    def setUp(self):
        cache.delete(REGISTRY_CACHE_KEY)
        self.addCleanup(cache.delete, REGISTRY_CACHE_KEY)
        # Migration 0113 seed "family_context" vĩnh viễn trong DB test; xoá để tránh
        # trùng code với fixture "family_context" tạo bên dưới.
        AssessmentFieldDef.objects.all().delete()
        channel = Channel.objects.create(code="lp-ai-dyn-fill", name="AI Dynamic Fill Channel")
        customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000003", phone_normalized="0900000003"
        )
        self.order = Order.objects.create(
            channel=channel, customer=customer, phone="0900000003", name="KH"
        )
        AssessmentFieldDef.objects.create(
            code="family_context", label="Hoàn cảnh gia đình", kind=FieldKind.TEXT, ai_enabled=True,
        )

    def test_fills_empty_dynamic_field_with_evidence(self):
        filled = apply_ai_insights(
            order=self.order,
            clean={"family_context": "sống cùng con trai", "evidence": {"family_context": "tôi ở với con trai"}},
            call_record_id=7,
        )
        self.assertEqual(filled, ["family_context"])
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(assessment.extra_fields["family_context"], "sống cùng con trai")
        self.assertEqual(assessment.ai_filled_fields["family_context"]["call_record_id"], 7)

    def test_does_not_overwrite_existing_dynamic_value(self):
        OrderAssessment.objects.create(order=self.order, extra_fields={"family_context": "sale đã ghi"})
        filled = apply_ai_insights(
            order=self.order,
            clean={"family_context": "AI ghi khác", "evidence": {"family_context": "q"}},
            call_record_id=7,
        )
        self.assertEqual(filled, [])
        self.assertEqual(
            OrderAssessment.objects.get(order=self.order).extra_fields["family_context"], "sale đã ghi"
        )

    def test_no_evidence_no_write(self):
        filled = apply_ai_insights(
            order=self.order, clean={"family_context": "không căn cứ", "evidence": {}}, call_record_id=7
        )
        self.assertEqual(filled, [])

    def test_open_insights_appended_and_deduped(self):
        clean = {
            "evidence": {},
            "open_insights": [
                {"topic": "Bảo hành", "content": "khách quan tâm bảo hành", "quote": "bảo hành mấy năm"},
            ],
        }
        self.assertEqual(apply_ai_insights(order=self.order, clean=clean, call_record_id=7), ["open_insights"])
        # Gọi lại cùng nội dung (cuộc gọi khác) → dedupe, không append thêm.
        self.assertEqual(apply_ai_insights(order=self.order, clean=clean, call_record_id=8), [])
        assessment = OrderAssessment.objects.get(order=self.order)
        self.assertEqual(len(assessment.ai_open_insights), 1)
        item = assessment.ai_open_insights[0]
        self.assertEqual(item["call_record_id"], 7)
        self.assertEqual(item["topic"], "Bảo hành")

    def test_open_insights_capped(self):
        OrderAssessment.objects.create(
            order=self.order,
            ai_open_insights=[
                {"topic": f"t{i}", "content": f"c{i}", "quote": "q", "call_record_id": 1, "at": "x"}
                for i in range(OPEN_INSIGHT_MAX_ITEMS)
            ],
        )
        filled = apply_ai_insights(
            order=self.order,
            clean={"evidence": {}, "open_insights": [{"topic": "mới", "content": "bị bỏ", "quote": "q"}]},
            call_record_id=9,
        )
        self.assertEqual(filled, [])
        self.assertEqual(
            len(OrderAssessment.objects.get(order=self.order).ai_open_insights), OPEN_INSIGHT_MAX_ITEMS
        )
