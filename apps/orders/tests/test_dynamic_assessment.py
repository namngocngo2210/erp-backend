"""Field đánh giá động (AssessmentFieldDef): registry + upsert qua service/serializer.

Gộp từ test_dynamic_assessment_registry.py và test_dynamic_assessment_upsert.py
(tái cấu trúc 2026-08-05) — docstring gốc giữ nguyên ở từng class.
"""
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.application.services.order_service_helpers import OrderServiceHelpers
from apps.orders.assessment_fields import AssessmentValidationError
from apps.orders.crm_catalog import build_crm_catalog_payload
from apps.orders.dynamic_assessment import (
    REGISTRY_CACHE_KEY,
    coerce_and_validate_dynamic,
    get_active_defs,
    get_active_defs_by_code,
    get_ai_enabled_defs,
)
from apps.orders.models import AssessmentFieldDef, FieldKind, Order, OrderAssessment
from apps.orders.transport.serializers.order_serializers_impl.order_core_serializers import (
    OrderCRMInputSerializer,
)


def make_def(**kwargs) -> AssessmentFieldDef:
    defaults = {
        "code": "family_context",
        "label": "Hoàn cảnh gia đình",
        "kind": FieldKind.TEXT,
        "options": [],
    }
    defaults.update(kwargs)
    return AssessmentFieldDef(**defaults)


class AssessmentFieldDefCleanTests(TestCase):
    def setUp(self):
        # Migration 0113 seed 3 field text AI (bao gồm "family_context" — code mặc định
        # của make_def()) tồn tại vĩnh viễn trong DB test; xoá trong transaction của từng
        # test để tránh IntegrityError trùng code, không ảnh hưởng test khác.
        AssessmentFieldDef.objects.all().delete()

    def test_valid_text_def_passes(self):
        make_def().full_clean()  # không raise

    def test_reserved_code_rejected(self):
        for code in (
            "sales_status_code",
            "needs",
            "extra_fields",
            "ai_filled_fields",
            "ai_open_insights",
            "need_type_codes",
            "evidence",
            "open_insights",
        ):
            with self.assertRaises(ValidationError, msg=code):
                make_def(code=code).full_clean()

    def test_choice_kind_requires_options(self):
        with self.assertRaises(ValidationError):
            make_def(code="housing_type", kind=FieldKind.SINGLE_CHOICE, options=[]).full_clean()

    def test_choice_options_must_have_code_and_label(self):
        with self.assertRaises(ValidationError):
            make_def(
                code="housing_type", kind=FieldKind.SINGLE_CHOICE, options=[{"code": "apartment"}]
            ).full_clean()

    def test_choice_option_codes_must_be_unique(self):
        with self.assertRaises(ValidationError):
            make_def(
                code="housing_type",
                kind=FieldKind.SINGLE_CHOICE,
                options=[
                    {"code": "apartment", "label": "Chung cư"},
                    {"code": "apartment", "label": "Trùng"},
                ],
            ).full_clean()

    def test_non_choice_kind_must_not_have_options(self):
        with self.assertRaises(ValidationError):
            make_def(options=[{"code": "x", "label": "X"}]).full_clean()

    def test_kind_change_blocked_when_data_exists(self):
        from apps.channels_core.models import Channel
        from apps.customers.models import Customer
        from apps.orders.models import Order, OrderAssessment

        spec = make_def()
        spec.full_clean()
        spec.save()
        channel = Channel.objects.create(code="lp-dyn-field", name="Dynamic Field Channel")
        customer = Customer.objects.create(full_name="KH", phone_raw="0900000001", phone_normalized="0900000001")
        order = Order.objects.create(
            channel=channel, customer=customer, phone="0900000001", name="KH"
        )
        OrderAssessment.objects.create(order=order, extra_fields={"family_context": "sống cùng con"})
        spec.kind = FieldKind.NUMBER
        with self.assertRaises(ValidationError):
            spec.full_clean()

    def test_kind_change_allowed_without_data(self):
        spec = make_def()
        spec.full_clean()
        spec.save()
        spec.kind = FieldKind.NUMBER
        spec.full_clean()  # không raise


class DynamicRegistryReadTests(TestCase):
    def setUp(self):
        cache.delete(REGISTRY_CACHE_KEY)
        self.addCleanup(cache.delete, REGISTRY_CACHE_KEY)
        # Xoá seed migration 0113 để list active defs chỉ gồm field do test này tạo.
        AssessmentFieldDef.objects.all().delete()

    def _seed(self):
        AssessmentFieldDef.objects.create(
            code="housing_type", label="Loại nhà ở", kind=FieldKind.SINGLE_CHOICE,
            options=[{"code": "apartment", "label": "Chung cư"}, {"code": "house", "label": "Nhà đất"}],
            ai_enabled=True, sort_order=2,
        )
        AssessmentFieldDef.objects.create(
            code="family_context", label="Hoàn cảnh gia đình", kind=FieldKind.TEXT, sort_order=1,
        )
        AssessmentFieldDef.objects.create(
            code="old_field", label="Đã tắt", kind=FieldKind.TEXT, is_active=False,
        )

    def test_active_defs_sorted_and_exclude_inactive(self):
        self._seed()
        self.assertEqual([d.code for d in get_active_defs()], ["family_context", "housing_type"])

    def test_ai_enabled_subset(self):
        self._seed()
        self.assertEqual([d.code for d in get_ai_enabled_defs()], ["housing_type"])

    def test_cache_invalidated_on_save(self):
        self._seed()
        get_active_defs()  # nạp cache
        AssessmentFieldDef.objects.create(code="budget", label="Ngân sách", kind=FieldKind.NUMBER)
        self.assertIn("budget", get_active_defs_by_code())

    def test_cache_get_failure_falls_back_to_db(self):
        # Spec §6: registry đọc cache lỗi (vd Redis chết) → query thẳng DB thay
        # vì raise 500 lên save assessment/catalog/extract.
        self._seed()
        with patch("apps.orders.dynamic_assessment.cache.get", side_effect=Exception("redis down")):
            defs = get_active_defs()
        self.assertEqual([d.code for d in defs], ["family_context", "housing_type"])


class CoerceDynamicTests(TestCase):
    def _choice(self, kind=FieldKind.SINGLE_CHOICE):
        spec = AssessmentFieldDef.objects.create(
            code="housing_type", label="Loại nhà ở", kind=kind,
            options=[{"code": "apartment", "label": "Chung cư"}, {"code": "house", "label": "Nhà đất"}],
        )
        return get_active_defs_by_code()[spec.code]

    def setUp(self):
        cache.delete(REGISTRY_CACHE_KEY)
        self.addCleanup(cache.delete, REGISTRY_CACHE_KEY)

    def test_single_choice_accepts_listed_code(self):
        self.assertEqual(coerce_and_validate_dynamic(self._choice(), "apartment"), "apartment")

    def test_single_choice_rejects_unknown_code(self):
        with self.assertRaises(AssessmentValidationError):
            coerce_and_validate_dynamic(self._choice(), "tent")

    def test_multi_choice_dedupes_and_validates(self):
        spec = self._choice(kind=FieldKind.MULTI_CHOICE)
        self.assertEqual(
            coerce_and_validate_dynamic(spec, ["house", "apartment", "house"]), ["house", "apartment"]
        )
        with self.assertRaises(AssessmentValidationError):
            coerce_and_validate_dynamic(spec, ["house", "tent"])
        with self.assertRaises(AssessmentValidationError):
            coerce_and_validate_dynamic(spec, "house")  # phải là list

    def test_text_length_capped(self):
        spec_row = AssessmentFieldDef.objects.create(code="note_x", label="X", kind=FieldKind.TEXT)
        spec = get_active_defs_by_code()[spec_row.code]
        self.assertEqual(coerce_and_validate_dynamic(spec, " ok "), " ok ")
        with self.assertRaises(AssessmentValidationError):
            coerce_and_validate_dynamic(spec, "a" * 4001)
        with self.assertRaises(AssessmentValidationError):
            coerce_and_validate_dynamic(spec, 123)

    def test_number_rejects_bool_and_strings(self):
        spec_row = AssessmentFieldDef.objects.create(code="budget", label="Ngân sách", kind=FieldKind.NUMBER)
        spec = get_active_defs_by_code()[spec_row.code]
        self.assertEqual(coerce_and_validate_dynamic(spec, 25), 25)
        self.assertEqual(coerce_and_validate_dynamic(spec, 25.5), 25.5)
        for bad in (True, "25"):
            with self.assertRaises(AssessmentValidationError):
                coerce_and_validate_dynamic(spec, bad)

    def test_bool_kind(self):
        spec_row = AssessmentFieldDef.objects.create(code="has_elderly", label="Có người già", kind=FieldKind.BOOL)
        spec = get_active_defs_by_code()[spec_row.code]
        self.assertIs(coerce_and_validate_dynamic(spec, True), True)
        with self.assertRaises(AssessmentValidationError):
            coerce_and_validate_dynamic(spec, "yes")

    def test_none_passes_through(self):
        spec_row = AssessmentFieldDef.objects.create(code="note_y", label="Y", kind=FieldKind.TEXT)
        spec = get_active_defs_by_code()[spec_row.code]
        self.assertIsNone(coerce_and_validate_dynamic(spec, None))


class DynamicCatalogPayloadTests(TestCase):
    def setUp(self):
        cache.delete(REGISTRY_CACHE_KEY)
        self.addCleanup(cache.delete, REGISTRY_CACHE_KEY)
        # Seed migration 0113 tồn tại vĩnh viễn trong DB test — xoá để assert exact-list
        # dưới đây chỉ phản ánh field do chính test này tạo.
        AssessmentFieldDef.objects.all().delete()

    def test_payload_contains_active_dynamic_fields(self):
        AssessmentFieldDef.objects.create(
            code="housing_type", label="Loại nhà ở", kind=FieldKind.SINGLE_CHOICE,
            options=[{"code": "apartment", "label": "Chung cư"}], sort_order=5,
        )
        AssessmentFieldDef.objects.create(code="off", label="Tắt", kind=FieldKind.TEXT, is_active=False)
        payload = build_crm_catalog_payload()
        self.assertEqual(
            payload["dynamic_fields"],
            [{
                "code": "housing_type", "label": "Loại nhà ở", "kind": "single_choice",
                "options": [{"code": "apartment", "label": "Chung cư"}], "sort_order": 5,
            }],
        )


class SeedDynamicDefsTests(TestCase):
    def test_seed_migration_created_ai_text_fields(self):
        codes = set(
            AssessmentFieldDef.objects.filter(ai_enabled=True, kind=FieldKind.TEXT).values_list(
                "code", flat=True
            )
        )
        self.assertLessEqual({"call_summary", "competitors_mentioned", "family_context"}, codes)


class CrmSerializerDynamicTests(TestCase):
    def test_serializer_exposes_extra_fields_and_open_insights(self):
        from apps.channels_core.models import Channel
        from apps.customers.models import Customer
        from apps.orders.transport.serializers.order_serializers_impl.product_item_crm_serializers import (
            OrderCRMSerializer,
        )

        channel = Channel.objects.create(code="lp-dyn-serializer", name="Dynamic Serializer Channel")
        customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000004", phone_normalized="0900000004"
        )
        order = Order.objects.create(
            channel=channel, customer=customer, phone="0900000004", name="KH"
        )
        assessment = OrderAssessment.objects.create(
            order=order,
            extra_fields={"family_context": "sống cùng con"},
            ai_open_insights=[{"topic": "t", "content": "c", "quote": "q", "call_record_id": 1, "at": "x"}],
        )
        data = OrderCRMSerializer(assessment, context={"journey_events": []}).data
        self.assertEqual(data["extra_fields"], {"family_context": "sống cùng con"})
        self.assertEqual(len(data["ai_open_insights"]), 1)


class DynamicUpsertTests(TestCase):
    def setUp(self):
        cache.delete(REGISTRY_CACHE_KEY)
        self.addCleanup(cache.delete, REGISTRY_CACHE_KEY)
        # Migration 0113 seed "family_context" (và 2 field khác) vĩnh viễn trong DB test;
        # xoá trong transaction của test này để tránh trùng code khi tạo fixture bên dưới.
        AssessmentFieldDef.objects.all().delete()
        channel = Channel.objects.create(code="lp-dyn-upsert", name="Dynamic Upsert Channel")
        customer = Customer.objects.create(
            full_name="KH", phone_raw="0900000002", phone_normalized="0900000002"
        )
        self.order = Order.objects.create(
            channel=channel, customer=customer, phone="0900000002", name="KH"
        )
        AssessmentFieldDef.objects.create(
            code="housing_type", label="Loại nhà ở", kind=FieldKind.SINGLE_CHOICE,
            options=[{"code": "apartment", "label": "Chung cư"}, {"code": "house", "label": "Nhà đất"}],
        )
        AssessmentFieldDef.objects.create(
            code="family_context", label="Hoàn cảnh gia đình", kind=FieldKind.TEXT,
        )
        AssessmentFieldDef.objects.create(
            code="inactive_field", label="Đã tắt", kind=FieldKind.TEXT, is_active=False,
        )

    def _upsert(self, payload):
        assessment, changed = OrderServiceHelpers._upsert_order_assessment(self.order, payload, None)
        assessment.refresh_from_db()
        return assessment, changed

    def test_writes_dynamic_value_into_extra_fields(self):
        assessment, changed = self._upsert({"housing_type": "apartment", "internal_note": "ghi chú"})
        self.assertEqual(assessment.extra_fields, {"housing_type": "apartment"})
        self.assertEqual(assessment.internal_note, "ghi chú")
        self.assertIn("extra_fields", changed)

    def test_unknown_key_rejected(self):
        with self.assertRaises(AssessmentValidationError):
            self._upsert({"not_a_field": "x"})

    def test_serializer_echo_keys_ignored(self):
        """Editor inline gửi `crm: {...row.crm, field: value}` — spread nguyên object
        OrderCRMSerializer trả về, kèm các key meta chỉ-đọc. Bản strict đầu (7a2608a2)
        reject chúng → MỌI inline edit 400 (sự cố chiều 2026-08-03). Key echo phải
        được lờ đi: không raise, không ghi vào extra_fields, không tính là đổi."""
        assessment, changed = self._upsert(
            {
                "housing_type": "apartment",
                # Trích từ payload thật của sự cố (PATCH /api/orders/550625): meta
                # + toàn bộ họ `*_label` do metaclass serializer tự sinh + events.
                "ai_filled_fields": {"internal_note": {"call_record_id": 1}},
                "extra_fields": {"housing_type": "house"},
                "ai_open_insights": [{"label": "x"}],
                "events": [],
                "updated_by_name": "Ai đó",
                "next_reminder_at": "2026-08-04T09:00:00Z",
                "created_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-08-03T00:00:00Z",
                "sales_status_label": "Đã lên đơn",
                "age_text_label": "45-54",
                "potential_level_label": None,
                "bomb_reason_group_label": None,
            }
        )
        self.assertEqual(assessment.extra_fields, {"housing_type": "apartment"})
        self.assertNotIn("ai_filled_fields", changed)
        self.assertNotIn("created_at", changed)

    def test_label_key_without_real_base_field_still_rejected(self):
        """Quy tắc lờ `*_label` chỉ áp cho twin của field thật — label mồ côi vẫn
        là key lạ để giữ nguyên chủ đích strict của registry."""
        with self.assertRaises(AssessmentValidationError):
            self._upsert({"khong_ton_tai_label": "x"})

    def test_inactive_def_rejected(self):
        with self.assertRaises(AssessmentValidationError):
            self._upsert({"inactive_field": "x"})

    def test_invalid_code_rejected(self):
        with self.assertRaises(AssessmentValidationError):
            self._upsert({"housing_type": "tent"})

    def test_empty_value_removes_key(self):
        self._upsert({"housing_type": "apartment"})
        assessment, _ = self._upsert({"housing_type": None})
        self.assertEqual(assessment.extra_fields, {})

    def test_manual_edit_clears_ai_provenance_for_dynamic_code(self):
        assessment, _ = self._upsert({"family_context": "AI ghi"})
        OrderAssessment.objects.filter(pk=assessment.pk).update(
            ai_filled_fields={"family_context": {"call_record_id": 1, "quote": "q", "at": "t"}}
        )
        assessment, _ = self._upsert({"family_context": "sale sửa lại"})
        self.assertNotIn("family_context", assessment.ai_filled_fields)
        self.assertEqual(assessment.extra_fields["family_context"], "sale sửa lại")

    def test_no_dynamic_change_no_extra_fields_in_changed(self):
        self._upsert({"housing_type": "apartment"})
        _, changed = self._upsert({"housing_type": "apartment"})
        self.assertNotIn("extra_fields", changed)


class CrmInputSerializerDynamicTests(SimpleTestCase):
    """Xác nhận key động sống sót qua OrderCRMInputSerializer trước khi tới
    _apply_dynamic_assessment_payload — thiếu bước này thì Task 3 không thể
    kích hoạt từ API thật (DRF Serializer chỉ giữ field khai báo trong
    validated_data mặc định)."""

    def test_dynamic_key_passes_through(self):
        serializer = OrderCRMInputSerializer(
            data={"internal_note": "x", "family_context": "sống cùng con"}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data.get("family_context"), "sống cùng con")

    def test_declared_fields_still_validated(self):
        serializer = OrderCRMInputSerializer(data={"sales_status_code": "not_a_code"})
        self.assertFalse(serializer.is_valid())
