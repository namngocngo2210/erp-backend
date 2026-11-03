"""Tests bảng nối ChannelAdAccount thay ArrayField fb/google_ads_accounts (audit 2026-07 DB #6).

Contract API PHẢI giữ nguyên: request/response vẫn là list[int] y hệt ArrayField cũ,
kể cả thứ tự phần tử và schema OpenAPI (đối chiếu snapshot copy từ schema.yaml cũ).
"""
import importlib

from django.test import SimpleTestCase, TestCase

from apps.channels_core.models import Channel, ChannelAdAccount
from apps.channels_core.repositories import ChannelRepository
from apps.channels_core.serializers import (
    ChannelCreateSerializer,
    ChannelSerializer,
    ChannelUpdateSerializer,
)

_MIGRATION = importlib.import_module(
    "apps.channels_core.migrations.0021_channel_ad_accounts"
)

# Snapshot copy nguyên văn từ backend/schema.yaml TRƯỚC khi đổi sang bảng nối —
# regen schema không được diff phần này.
OLD_SCHEMA_FB = {
    "type": "array",
    "items": {"type": "integer", "maximum": 2147483647, "minimum": -2147483648},
    "description": "ID các FacebookAdAccount được map vào kênh này (dùng cho báo cáo Ads).",
}
OLD_SCHEMA_GOOGLE = {
    "type": "array",
    "items": {"type": "integer", "maximum": 2147483647, "minimum": -2147483648},
    "description": "ID các GoogleAdsAccount được map vào kênh này (dùng cho báo cáo Ads).",
}


class ChannelAdAccountRepositoryTests(TestCase):
    def test_create_channel_persists_mapping_rows_in_order(self):
        channel = ChannelRepository.create_channel(
            code="c1",
            name="Kênh 1",
            owner="",
            landingpage_source=[],
            is_active=True,
            fb_ads_accounts=[5, 3, 8],
            google_ads_accounts=[7],
        )
        self.assertEqual(channel.fb_ads_accounts, [5, 3, 8])
        self.assertEqual(channel.google_ads_accounts, [7])
        self.assertEqual(
            ChannelAdAccount.objects.filter(channel=channel).count(), 4
        )

    def test_update_channel_replaces_only_provided_provider(self):
        channel = ChannelRepository.create_channel(
            code="c2",
            name="Kênh 2",
            owner="",
            landingpage_source=[],
            is_active=True,
            fb_ads_accounts=[1, 2],
            google_ads_accounts=[9],
        )
        ChannelRepository.update_channel(channel, fb_ads_accounts=[2])
        channel.refresh_from_db()
        self.assertEqual(channel.fb_ads_accounts, [2])
        # google không gửi (None) → giữ nguyên, semantics như ArrayField cũ.
        self.assertEqual(channel.google_ads_accounts, [9])

    def test_update_channel_empty_list_clears_mapping(self):
        channel = ChannelRepository.create_channel(
            code="c3",
            name="Kênh 3",
            owner="",
            landingpage_source=[],
            is_active=True,
            fb_ads_accounts=[1],
            google_ads_accounts=[2],
        )
        ChannelRepository.update_channel(channel, fb_ads_accounts=[], google_ads_accounts=[])
        self.assertEqual(channel.fb_ads_accounts, [])
        self.assertEqual(channel.google_ads_accounts, [])

    def test_replace_ad_accounts_dedups_defensively(self):
        channel = Channel.objects.create(code="c4", name="Kênh 4")
        ChannelRepository.replace_ad_accounts(
            channel, ChannelAdAccount.Provider.FACEBOOK, [4, 4, 1]
        )
        self.assertEqual(channel.fb_ads_accounts, [4, 1])

    def test_same_account_id_allowed_on_both_providers(self):
        # 2 provider là 2 không gian id khác nhau — không được đụng nhau.
        channel = Channel.objects.create(code="c5", name="Kênh 5")
        ChannelRepository.replace_ad_accounts(channel, ChannelAdAccount.Provider.FACEBOOK, [10])
        ChannelRepository.replace_ad_accounts(channel, ChannelAdAccount.Provider.GOOGLE, [10])
        self.assertEqual(channel.fb_ads_accounts, [10])
        self.assertEqual(channel.google_ads_accounts, [10])

    def test_property_uses_prefetch_cache(self):
        channel = Channel.objects.create(code="c6", name="Kênh 6")
        ChannelRepository.replace_ad_accounts(channel, ChannelAdAccount.Provider.FACEBOOK, [1, 2])
        prefetched = Channel.objects.prefetch_related("ad_account_links").get(id=channel.id)
        with self.assertNumQueries(0):
            self.assertEqual(prefetched.fb_ads_accounts, [1, 2])
            self.assertEqual(prefetched.google_ads_accounts, [])


class ChannelSerializerContractTests(TestCase):
    """Round-trip write/read: payload cũ vào → response y hệt contract ArrayField."""

    OLD_PAYLOAD = {
        "code": "contract",
        "name": "Kênh contract",
        "fb_ads_accounts": [501, 302, 803],
        "google_ads_accounts": [12],
    }

    def _create_via_serializers(self) -> dict:
        serializer = ChannelCreateSerializer(data=self.OLD_PAYLOAD)
        serializer.is_valid(raise_exception=True)
        channel = ChannelRepository.create_channel(**serializer.validated_data)
        return ChannelSerializer(channel).data

    def test_create_round_trip_returns_identical_lists(self):
        data = self._create_via_serializers()
        self.assertEqual(data["fb_ads_accounts"], [501, 302, 803])
        self.assertEqual(data["google_ads_accounts"], [12])
        self.assertTrue(all(isinstance(x, int) for x in data["fb_ads_accounts"]))

    def test_update_round_trip(self):
        self._create_via_serializers()
        channel = Channel.objects.get(code="contract")
        serializer = ChannelUpdateSerializer(
            data={"fb_ads_accounts": [99, 501]}, partial=True
        )
        serializer.is_valid(raise_exception=True)
        updated = ChannelRepository.update_channel(channel, **serializer.validated_data)
        data = ChannelSerializer(updated).data
        self.assertEqual(data["fb_ads_accounts"], [99, 501])
        self.assertEqual(data["google_ads_accounts"], [12])

    def test_normalization_unchanged_dedup_keeps_first_occurrence(self):
        serializer = ChannelUpdateSerializer(
            data={"fb_ads_accounts": [7, 7, 3]}, partial=True
        )
        serializer.is_valid(raise_exception=True)
        self.assertEqual(serializer.validated_data["fb_ads_accounts"], [7, 3])

    def test_response_keys_unchanged(self):
        data = self._create_via_serializers()
        self.assertEqual(
            set(data.keys()),
            {
                "id",
                "code",
                "name",
                "owner",
                "landingpage_source",
                "fb_ads_accounts",
                "google_ads_accounts",
                "is_active",
                "created_at",
                "department_ids",
                "department_names",
                "assigned_user_count",
                "assigned_users",
                "kiotviet_mappings",
                "channel_type",
                "price_book",
            },
        )


class ChannelOpenApiSchemaSnapshotTests(TestCase):
    """Schema OpenAPI của 2 field phải bằng snapshot lấy từ schema.yaml cũ (không diff khi regen)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from drf_spectacular.generators import SchemaGenerator

        cls.schema = SchemaGenerator().get_schema(request=None, public=True)

    def test_channel_component_fields_unchanged(self):
        props = self.schema["components"]["schemas"]["Channel"]["properties"]
        self.assertEqual(props["fb_ads_accounts"], OLD_SCHEMA_FB)
        self.assertEqual(props["google_ads_accounts"], OLD_SCHEMA_GOOGLE)

    def test_fields_still_not_required(self):
        required = self.schema["components"]["schemas"]["Channel"].get("required", [])
        self.assertNotIn("fb_ads_accounts", required)
        self.assertNotIn("google_ads_accounts", required)


class DataMigrationCleanIdsTests(SimpleTestCase):
    """Logic lọc id mồ côi của data migration 0021 (fix gốc bug "ads phồng")."""

    def test_orphans_skipped_and_counted(self):
        kept, orphans = _MIGRATION._clean_ids([1, 999, 2, 888], {1, 2})
        self.assertEqual(kept, [1, 2])
        self.assertEqual(orphans, 2)

    def test_dedup_preserves_order(self):
        kept, orphans = _MIGRATION._clean_ids([3, 1, 3, 1, 2], {1, 2, 3})
        self.assertEqual(kept, [3, 1, 2])
        self.assertEqual(orphans, 0)

    def test_non_int_values_ignored(self):
        kept, orphans = _MIGRATION._clean_ids(["5", None, "x", 6], {5, 6})
        self.assertEqual(kept, [5, 6])
        self.assertEqual(orphans, 0)

    def test_empty_and_none_input(self):
        self.assertEqual(_MIGRATION._clean_ids([], {1}), ([], 0))
        self.assertEqual(_MIGRATION._clean_ids(None, {1}), ([], 0))
