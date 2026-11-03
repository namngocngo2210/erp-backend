"""Test serializer create/update kênh KiotViet với old_sync_branch_map (cặp kho đồng bộ)."""

import pytest

from apps.channels_core.transport.serializers.channel_serializers_impl import (
    KiotVietChannelCreateSerializer,
    KiotVietChannelUpdateSerializer,
)
from apps.kiotviet.models import KiotVietChannel


def _pair(source: str, target: str) -> dict:
    return {"source_branch_id": source, "target_branch_id": target}


@pytest.fixture
def old_channel(db) -> KiotVietChannel:
    return KiotVietChannel.objects.create(
        name="KV Old",
        branch_id="415000",
        branch_ids=["415000", "415001"],
        client_id="old-client",
        client_secret="old-secret",
        retailer="old-retailer",
        old=True,
    )


def _create_payload(old_channel: KiotVietChannel, **overrides) -> dict:
    payload = {
        "name": "KV New",
        "branch_ids": ["231000", "231001"],
        "client_id": "new-client",
        "client_secret": "new-secret",
        "retailer": "new-retailer",
        "paired_old_channel_id": old_channel.id,
        "old_sync_branch_map": [
            _pair("231000", "415000"),
            _pair("231001", "415001"),
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestCreateSerializerBranchMap:
    def test_valid_map_passes_and_derives_fallback(self, old_channel):
        serializer = KiotVietChannelCreateSerializer(data=_create_payload(old_channel))
        assert serializer.is_valid(), serializer.errors
        data = serializer.validated_data
        assert data["old_sync_branch_map"] == [
            _pair("231000", "415000"),
            _pair("231001", "415001"),
        ]
        # old_sync_branch_id fallback = target của kho chính (branch_id = branch_ids[0]).
        assert data["old_sync_branch_id"] == "415000"

    def test_explicit_old_sync_branch_id_kept(self, old_channel):
        payload = _create_payload(old_channel, old_sync_branch_id="415001")
        serializer = KiotVietChannelCreateSerializer(data=payload)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["old_sync_branch_id"] == "415001"

    def test_duplicate_source_rejected(self, old_channel):
        payload = _create_payload(
            old_channel,
            old_sync_branch_map=[_pair("231000", "415000"), _pair("231000", "415001")],
        )
        serializer = KiotVietChannelCreateSerializer(data=payload)
        assert not serializer.is_valid()
        assert "old_sync_branch_map" in serializer.errors

    def test_source_not_in_branch_ids_rejected(self, old_channel):
        payload = _create_payload(old_channel, old_sync_branch_map=[_pair("999999", "415000")])
        serializer = KiotVietChannelCreateSerializer(data=payload)
        assert not serializer.is_valid()
        assert "old_sync_branch_map" in serializer.errors

    def test_target_not_in_old_channel_rejected(self, old_channel):
        payload = _create_payload(old_channel, old_sync_branch_map=[_pair("231000", "888888")])
        serializer = KiotVietChannelCreateSerializer(data=payload)
        assert not serializer.is_valid()
        assert "old_sync_branch_map" in serializer.errors

    def test_paired_without_map_and_without_legacy_rejected(self, old_channel):
        payload = _create_payload(old_channel, old_sync_branch_map=[])
        serializer = KiotVietChannelCreateSerializer(data=payload)
        assert not serializer.is_valid()
        assert "old_sync_branch_map" in serializer.errors

    def test_legacy_only_still_accepted(self, old_channel):
        # API client cũ chỉ gửi old_sync_branch_id — giữ tương thích ngược.
        payload = _create_payload(
            old_channel, old_sync_branch_map=[], old_sync_branch_id="415000"
        )
        serializer = KiotVietChannelCreateSerializer(data=payload)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["old_sync_branch_map"] == []
        assert serializer.validated_data["old_sync_branch_id"] == "415000"

    def test_old_channel_clears_map(self, old_channel):
        payload = _create_payload(old_channel, old=True, paired_old_channel_id=None)
        serializer = KiotVietChannelCreateSerializer(data=payload)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["old_sync_branch_map"] == []
        assert serializer.validated_data["old_sync_branch_id"] == ""

    def test_no_paired_channel_clears_map(self, old_channel):
        payload = _create_payload(old_channel, paired_old_channel_id=None)
        serializer = KiotVietChannelCreateSerializer(data=payload)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["old_sync_branch_map"] == []


@pytest.mark.django_db
class TestUpdateSerializerBranchMap:
    @pytest.fixture
    def new_channel(self, old_channel) -> KiotVietChannel:
        return KiotVietChannel.objects.create(
            name="KV New",
            branch_id="231000",
            branch_ids=["231000", "231001"],
            client_id="new-client",
            client_secret="new-secret",
            retailer="new-retailer",
            old=False,
            paired_old_channel=old_channel,
            old_sync_branch_id="415000",
            old_sync_branch_map=[_pair("231000", "415000")],
        )

    def _validate(self, channel: KiotVietChannel, data: dict):
        serializer = KiotVietChannelUpdateSerializer(
            data=data, partial=True, context={"instance": channel}
        )
        return serializer

    def test_patch_map_only(self, new_channel):
        serializer = self._validate(
            new_channel,
            {"old_sync_branch_map": [_pair("231000", "415000"), _pair("231001", "415001")]},
        )
        assert serializer.is_valid(), serializer.errors
        assert len(serializer.validated_data["old_sync_branch_map"]) == 2

    def test_patch_map_source_outside_instance_branches_rejected(self, new_channel):
        serializer = self._validate(
            new_channel, {"old_sync_branch_map": [_pair("777777", "415000")]}
        )
        assert not serializer.is_valid()
        assert "old_sync_branch_map" in serializer.errors

    def test_clear_map_and_legacy_rejected(self, new_channel):
        serializer = self._validate(
            new_channel, {"old_sync_branch_map": [], "old_sync_branch_id": ""}
        )
        assert not serializer.is_valid()
        assert "old_sync_branch_map" in serializer.errors

    def test_clear_map_keeps_instance_legacy_fallback(self, new_channel):
        serializer = self._validate(new_channel, {"old_sync_branch_map": []})
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["old_sync_branch_map"] == []
        assert serializer.validated_data["old_sync_branch_id"] == "415000"

    def test_set_old_true_clears_map(self, new_channel):
        serializer = self._validate(
            new_channel, {"old": True, "paired_old_channel_id": None}
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["old_sync_branch_map"] == []
        assert serializer.validated_data["old_sync_branch_id"] == ""

    def test_unrelated_patch_does_not_touch_map(self, new_channel):
        serializer = self._validate(new_channel, {"name": "Renamed"})
        assert serializer.is_valid(), serializer.errors
        assert "old_sync_branch_map" not in serializer.validated_data

    def test_shrink_branch_ids_invalidates_map_source(self, new_channel):
        # Bỏ kho 231001 khỏi branch_ids trong khi map vẫn trỏ kho đó -> lỗi.
        serializer = self._validate(
            new_channel,
            {
                "branch_ids": ["231000"],
                "old_sync_branch_map": [_pair("231001", "415001")],
            },
        )
        assert not serializer.is_valid()
        assert "old_sync_branch_map" in serializer.errors

    def test_shrink_branch_ids_alone_rechecks_instance_map(self, old_channel):
        # PATCH chỉ branch_ids (KHÔNG gửi lại map): bỏ kho 231001 đang nằm trong map
        # instance -> phải re-validate map instance và raise, không để map mồ côi vẫn
        # sync kho đã gỡ (audit 2026-07-15).
        channel = KiotVietChannel.objects.create(
            name="KV New Multi",
            branch_id="231000",
            branch_ids=["231000", "231001"],
            client_id="c2",
            client_secret="s2",
            retailer="r2",
            old=False,
            paired_old_channel=old_channel,
            old_sync_branch_id="415000",
            old_sync_branch_map=[_pair("231000", "415000"), _pair("231001", "415001")],
        )
        serializer = self._validate(channel, {"branch_ids": ["231000"]})
        assert not serializer.is_valid()
        assert "old_sync_branch_map" in serializer.errors

    def test_shrink_branch_ids_alone_passes_when_map_unaffected(self, new_channel):
        # new_channel map chỉ có {231000->415000}; bỏ 231001 (không nằm trong map) ->
        # pass im lặng, map giữ nguyên.
        serializer = self._validate(new_channel, {"branch_ids": ["231000"]})
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["old_sync_branch_map"] == [_pair("231000", "415000")]
