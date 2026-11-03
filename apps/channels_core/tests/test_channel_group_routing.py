"""Route WS theo kênh — group join theo scope + khử trùng lặp giai đoạn chuyển tiếp.

Audit performance 2026-08-19 (WS fanout O(clients)): mọi event có kênh từng đi
group chung ``crm_updates`` tới MỌI client rồi mới lọc ở ``_is_authorized`` — sale
kênh A nhận cả traffic kênh B/C/D. Lộ trình hai giai đoạn (apps/channels_core/
groups.py): consumer join group theo kênh ngay từ giờ, publisher bắn kép
(kênh + chung, cùng ``dedup_id``) để consumer CŨ không mất event khi web/worker
restart lệch nhịp; consumer MỚI khử bản trùng.

Khuôn test: consumer trần + ``asyncio.run`` / ``async_to_sync`` (không
WebsocketCommunicator — xem apps/exports/tests/test_ws.py). Không đánh dấu
``django_db``: đường connect/crm_event KHÔNG được chạm DB ngoài ``_load_scope``
(đã stub) — pytest-django tự chặn nếu vi phạm.
"""

import asyncio
from unittest.mock import AsyncMock

from django.test import SimpleTestCase

from apps.channels_core.consumers import _DEDUP_WINDOW, CRMConsumer
from apps.channels_core.groups import CRM_BROAD_GROUP, CRM_GROUP, order_channel_group


class _FakeUser:
    id = 55
    is_authenticated = True


def _connected_consumer(scope_result):
    """Consumer đã stub đủ để chạy connect() không chạm DB."""
    consumer = CRMConsumer()
    consumer.scope = {"user": _FakeUser()}
    consumer.channel_layer = AsyncMock()
    consumer.channel_name = "specific.channel.abcd"
    consumer.accept = AsyncMock()
    consumer._load_scope = AsyncMock(return_value=scope_result)
    asyncio.run(consumer.connect())
    return consumer


def _joined_groups(consumer):
    return [call.args[0] for call in consumer.channel_layer.group_add.await_args_list]


class ConnectGroupRoutingTests(SimpleTestCase):
    def test_scope_channels_join_group_tung_kenh_va_group_chung(self):
        """Group chung vẫn phải join: event không-theo-kênh (restore.completed,
        bridge, payload thiếu channel_code) và publisher CŨ đều đi qua đó."""
        consumer = _connected_consumer(("channels", {"FB", "ZALO"}, {}, True))

        groups = _joined_groups(consumer)
        self.assertIn(CRM_GROUP, groups)
        self.assertIn(order_channel_group("FB"), groups)
        self.assertIn(order_channel_group("ZALO"), groups)
        self.assertNotIn(CRM_BROAD_GROUP, groups, "scope channels không join broad")
        consumer.accept.assert_awaited_once()

    def test_scope_all_join_broad_khong_join_group_kenh(self):
        """Scope all/own phải thấy MỌI kênh → không liệt kê group kênh (kênh tạo
        sau lúc connect sẽ lọt lưới) mà join CRM_BROAD_GROUP — join NGAY từ giai
        đoạn 1 để giai đoạn 2 chỉ cần đổi publisher, không cần đổi consumer."""
        consumer = _connected_consumer(("all", None, {}, True))

        groups = _joined_groups(consumer)
        self.assertIn(CRM_GROUP, groups)
        self.assertIn(CRM_BROAD_GROUP, groups)
        self.assertFalse(
            [g for g in groups if g.startswith("crm_updates.ch.")],
            "scope all không join group kênh nào",
        )

    def test_scope_own_join_broad(self):
        """"Own" lọc theo staff_id — kênh không biểu diễn được "của tôi"."""
        consumer = _connected_consumer(("own", None, {}, True))

        self.assertIn(CRM_BROAD_GROUP, _joined_groups(consumer))

    def test_scope_channels_rong_chi_join_group_chung(self):
        """Set kênh rỗng = không thấy gì: không join group kênh nào, group chung
        vẫn join (event không-theo-kênh) và _is_authorized chặn phần còn lại."""
        consumer = _connected_consumer(("channels", set(), {}, True))

        groups = _joined_groups(consumer)
        self.assertNotIn(CRM_BROAD_GROUP, groups)
        self.assertFalse([g for g in groups if g.startswith("crm_updates.ch.")])

    def test_disconnect_discard_du_moi_group_da_join(self):
        consumer = _connected_consumer(("channels", {"FB"}, {}, True))

        asyncio.run(consumer.disconnect(1000))

        discarded = [
            call.args[0]
            for call in consumer.channel_layer.group_discard.await_args_list
        ]
        self.assertCountEqual(discarded, _joined_groups(consumer))

    def test_disconnect_truoc_khi_join_khong_vo(self):
        """Client rớt giữa handshake: chưa có _crm_scope_groups/_export_group."""
        consumer = CRMConsumer()
        consumer.channel_layer = AsyncMock()
        consumer.channel_name = "specific.channel.abcd"

        asyncio.run(consumer.disconnect(1000))

        consumer.channel_layer.group_discard.assert_awaited_once_with(
            CRM_GROUP, "specific.channel.abcd"
        )


def _event(dedup_id=None, **overrides):
    payload = {
        "type": "order.updated",
        "order_id": 549426,
        "channel_code": "FB",
        "staff_id": 7,
        "source": "orders.service.update_order_inline",
    }
    payload.update(overrides)
    event = {"type": "crm.event", "payload": payload}
    if dedup_id is not None:
        event["dedup_id"] = dedup_id
    return event


def _receiving_consumer():
    """Consumer trần scope all — _is_authorized cho qua mà không chạm DB."""
    consumer = CRMConsumer()
    consumer.scope_kind = "all"
    consumer.send_json = AsyncMock()
    return consumer


class CrmEventDedupTests(SimpleTestCase):
    """Bắn kép giai đoạn 1 không được thành hai lần render trên client."""

    def test_cung_dedup_id_chi_gui_mot_lan(self):
        consumer = _receiving_consumer()

        asyncio.run(consumer.crm_event(_event(dedup_id="aa")))
        asyncio.run(consumer.crm_event(_event(dedup_id="aa")))

        consumer.send_json.assert_awaited_once()

    def test_dedup_id_khac_nhau_gui_du_hai(self):
        consumer = _receiving_consumer()

        asyncio.run(consumer.crm_event(_event(dedup_id="aa")))
        asyncio.run(consumer.crm_event(_event(dedup_id="bb", order_id=549427)))

        self.assertEqual(consumer.send_json.await_count, 2)

    def test_khong_co_dedup_id_khong_khu_gi(self):
        """Publisher cũ / pubsub bridge / restore không gắn dedup_id — mỗi event
        chỉ có MỘT bản, khử theo nội dung sẽ nuốt oan hai event thật giống nhau
        (hai lần bấm lưu liên tiếp cùng field)."""
        consumer = _receiving_consumer()

        asyncio.run(consumer.crm_event(_event()))
        asyncio.run(consumer.crm_event(_event()))

        self.assertEqual(consumer.send_json.await_count, 2)

    def test_cua_so_dedup_tu_dao_thai(self):
        """Set không được phình vô hạn theo tuổi connection (tab mở suốt ca)."""
        consumer = _receiving_consumer()

        for index in range(_DEDUP_WINDOW + 5):
            self.assertTrue(consumer._first_delivery(f"id-{index}"))

        self.assertLessEqual(len(consumer._dedup_seen), _DEDUP_WINDOW)
        # Id đã bị đào thải khỏi cửa sổ thì được coi là mới — chấp nhận được:
        # bản trùng luôn tới ngay sau bản đầu, không bao giờ cách 256 event.
        self.assertTrue(consumer._first_delivery("id-0"))
        # Id còn trong cửa sổ vẫn bị khử.
        self.assertFalse(consumer._first_delivery(f"id-{_DEDUP_WINDOW + 4}"))

    def test_dedup_khong_lam_hong_phan_quyen(self):
        """Bản đầu tiên vẫn đi qua _is_authorized như cũ — group kênh chỉ thu hẹp
        fanout, không thay thế phân quyền (defense in depth)."""
        consumer = _receiving_consumer()
        consumer.scope_kind = "channels"
        consumer.allowed_channel_codes = {"ZALO"}

        asyncio.run(consumer.crm_event(_event(dedup_id="aa", channel_code="FB")))

        consumer.send_json.assert_not_awaited()


class OrderChannelGroupNameTests(SimpleTestCase):
    def test_code_ascii_giu_nguyen(self):
        self.assertEqual(order_channel_group("SALE_FB"), "crm_updates.ch.SALE_FB")

    def test_code_co_ky_tu_ngoai_pham_vi_duoc_sanitize(self):
        """Channel.code là CharField tự do; group name Channels chỉ nhận
        [a-zA-Z0-9._-]. Trùng group sau sanitize là vô hại: _is_authorized vẫn so
        channel_code GỐC trong payload."""
        group = order_channel_group("Kênh chính")
        self.assertEqual(group, "crm_updates.ch.K_nh_ch_nh")

    def test_code_dai_bi_cat(self):
        group = order_channel_group("x" * 200)
        self.assertLessEqual(len(group), 99)
