"""Test map cặp kho đồng bộ new→old (old_sync_branch_map)."""

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command

from apps.kiotviet.infrastructure.services.branch_sync_map import (
    branch_map_lookup,
    normalize_branch_map,
    resolve_old_sync_target_branch_id,
)
from apps.kiotviet.infrastructure.services.reconcile import iter_reconcile_pairs
from apps.kiotviet.models import KiotVietChannel


def _pair(source: str, target: str) -> dict:
    return {"source_branch_id": source, "target_branch_id": target}


class TestNormalizeBranchMap:
    def test_non_list_returns_empty(self):
        assert normalize_branch_map(None) == []
        assert normalize_branch_map("abc") == []
        assert normalize_branch_map({"source_branch_id": "1"}) == []

    def test_strips_and_drops_incomplete_entries(self):
        raw = [
            {"source_branch_id": " 111 ", "target_branch_id": " 999 "},
            {"source_branch_id": "222", "target_branch_id": ""},
            {"source_branch_id": "", "target_branch_id": "888"},
            "not-a-dict",
        ]
        assert normalize_branch_map(raw) == [_pair("111", "999")]

    def test_duplicate_source_keeps_first(self):
        raw = [_pair("111", "999"), _pair("111", "888"), _pair("222", "777")]
        assert normalize_branch_map(raw) == [_pair("111", "999"), _pair("222", "777")]


class TestResolveTargetBranch:
    def _channels(self, branch_map=None, old_sync_branch_id="", old_branch_id="415000"):
        new_channel = SimpleNamespace(
            old_sync_branch_map=branch_map or [],
            old_sync_branch_id=old_sync_branch_id,
        )
        old_channel = SimpleNamespace(branch_id=old_branch_id)
        return new_channel, old_channel

    def test_map_hit_wins(self):
        new_channel, old_channel = self._channels(
            branch_map=[_pair("111", "901"), _pair("222", "902")],
            old_sync_branch_id="909",
        )
        assert resolve_old_sync_target_branch_id(new_channel, old_channel, "222") == "902"

    def test_map_miss_falls_back_to_legacy_field(self):
        new_channel, old_channel = self._channels(
            branch_map=[_pair("111", "901")],
            old_sync_branch_id="909",
        )
        assert resolve_old_sync_target_branch_id(new_channel, old_channel, "333") == "909"

    def test_no_map_no_legacy_falls_back_to_old_branch_id(self):
        new_channel, old_channel = self._channels()
        assert resolve_old_sync_target_branch_id(new_channel, old_channel, "111") == "415000"

    def test_int_source_matches_string_map(self):
        new_channel, old_channel = self._channels(branch_map=[_pair("111", "901")])
        assert resolve_old_sync_target_branch_id(new_channel, old_channel, 111) == "901"

    def test_lookup_empty_source_returns_empty(self):
        new_channel, _ = self._channels(branch_map=[_pair("111", "901")])
        assert branch_map_lookup(new_channel, None) == ""
        assert branch_map_lookup(new_channel, "") == ""


@pytest.mark.django_db
class TestIterReconcilePairs:
    def _old_channel(self, **overrides) -> KiotVietChannel:
        defaults = dict(
            name="KV Old",
            branch_id="415000",
            branch_ids=["415000", "415001"],
            client_id="old-client",
            client_secret="old-secret",
            retailer="old-retailer",
            old=True,
        )
        defaults.update(overrides)
        return KiotVietChannel.objects.create(**defaults)

    def _new_channel(self, old_channel: KiotVietChannel, **overrides) -> KiotVietChannel:
        defaults = dict(
            name="KV New",
            branch_id="231000",
            branch_ids=["231000", "231001"],
            client_id="new-client",
            client_secret="new-secret",
            retailer="new-retailer",
            old=False,
            paired_old_channel=old_channel,
        )
        defaults.update(overrides)
        return KiotVietChannel.objects.create(**defaults)

    def test_map_yields_one_pair_per_entry(self):
        old_channel = self._old_channel()
        new_channel = self._new_channel(
            old_channel,
            old_sync_branch_id="415000",
            old_sync_branch_map=[_pair("231000", "415000"), _pair("231001", "415001")],
        )
        pairs = list(iter_reconcile_pairs())
        assert len(pairs) == 2
        assert all(not pair["skip"] for pair in pairs)
        assert [(p["source_branch_id"], p["target_branch_id"]) for p in pairs] == [
            ("231000", "415000"),
            ("231001", "415001"),
        ]
        assert pairs[0]["source_kv_id"] == new_channel.id
        assert pairs[0]["target_kv_id"] == old_channel.id
        # Label phân biệt theo kho để log/monitoring không lẫn 2 cặp cùng kênh.
        assert pairs[0]["pair"] != pairs[1]["pair"]

    def test_empty_map_keeps_legacy_single_pair(self):
        old_channel = self._old_channel()
        self._new_channel(old_channel, old_sync_branch_id="415001", old_sync_branch_map=[])
        pairs = list(iter_reconcile_pairs())
        assert len(pairs) == 1
        assert pairs[0]["skip"] is False
        assert pairs[0]["source_branch_id"] == "231000"
        assert pairs[0]["target_branch_id"] == "415001"

    def test_empty_map_no_legacy_uses_old_branch_id(self):
        old_channel = self._old_channel()
        self._new_channel(old_channel, old_sync_branch_id="", old_sync_branch_map=[])
        pairs = list(iter_reconcile_pairs())
        assert len(pairs) == 1
        assert pairs[0]["target_branch_id"] == "415000"

    def test_malformed_map_entries_ignored(self):
        old_channel = self._old_channel()
        self._new_channel(
            old_channel,
            old_sync_branch_id="415000",
            old_sync_branch_map=[{"source_branch_id": "", "target_branch_id": "415001"}],
        )
        pairs = list(iter_reconcile_pairs())
        # Map toàn entry hỏng coi như rỗng -> legacy single pair.
        assert len(pairs) == 1
        assert pairs[0]["target_branch_id"] == "415000"

    def test_cli_source_branch_filter_runs_single_pair(self):
        old_channel = self._old_channel()
        self._new_channel(
            old_channel,
            old_sync_branch_id="415000",
            old_sync_branch_map=[_pair("231000", "415000"), _pair("231001", "415001")],
        )
        with patch(
            "apps.kiotviet.management.commands.reconcile_channels.reconcile_one_pair",
            return_value={"modes": {}, "failed_modes": []},
        ) as mock_run:
            call_command(
                "reconcile_channels",
                "--date", "2026-07-15",
                "--source-branch-id", "231001",
                stdout=StringIO(),
            )
        assert mock_run.call_count == 1
        kwargs = mock_run.call_args.kwargs
        assert kwargs["source_branch_id"] == "231001"
        assert kwargs["target_branch_id"] == "415001"
