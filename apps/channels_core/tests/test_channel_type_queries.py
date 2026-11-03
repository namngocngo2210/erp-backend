"""`ChannelTypeSerializer.channel_count` đọc annotate của repository — không 1 COUNT/loại.

Audit 2026-09-23 perf §2.8 đo 12 loại → 13 query khi serialize thẳng
`ChannelType.objects.all()`; đường view đi `ChannelRepository.list_channel_types`
(annotate `channel_count_annotated`) nên là 1 query bất kể số loại. Test chốt
đường đó và giữ fallback đúng cho instance rời.
"""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.channels_core.models import Channel, ChannelType
from apps.channels_core.repositories import ChannelRepository
from apps.channels_core.transport.serializers.channel_serializers_impl import ChannelTypeSerializer


class ChannelTypeQueryCountTests(TestCase):
    def _measure(self) -> tuple[int, list]:
        with CaptureQueriesContext(connection) as ctx:
            data = ChannelTypeSerializer(
                ChannelRepository.list_channel_types(include_inactive=True), many=True
            ).data
        return len(ctx.captured_queries), data

    def test_query_count_constant_with_more_types(self):
        for i in range(5):
            ChannelType.objects.create(code=f"ct-q-{i}", name=f"Loại {i}", sort_order=100 + i)
        small, small_data = self._measure()
        for i in range(5, 15):
            ChannelType.objects.create(code=f"ct-q-{i}", name=f"Loại {i}", sort_order=100 + i)
        large, large_data = self._measure()
        self.assertGreater(len(large_data), len(small_data))
        self.assertEqual(large, small)
        self.assertEqual(large, 1)

    def test_count_excludes_soft_deleted_and_fallback_matches(self):
        from django.utils import timezone

        ctype = ChannelType.objects.create(code="ct-q-x", name="X", sort_order=200)
        Channel.objects.create(code="ct-q-a", name="A", channel_type=ctype)
        Channel.objects.create(code="ct-q-b", name="B", channel_type=ctype, deleted_at=timezone.now())
        listed = {row["code"]: row for row in self._measure()[1]}
        self.assertEqual(listed["ct-q-x"]["channel_count"], 1)
        self.assertEqual(ChannelTypeSerializer(ctype).data["channel_count"], 1)
