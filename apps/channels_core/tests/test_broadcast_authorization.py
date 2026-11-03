"""Phân quyền event broadcast phải quyết định từ payload, không chạm DB.

Sự cố 2026-07-29: ``CRMConsumer._is_authorized`` tra DB cho mỗi event, mà consumer
chạy trên MỌI client trong group ``crm_updates``. Một event sinh ra N query đồng thời
(N = số tab đang mở) và với ``CONN_MAX_AGE=0`` thì mỗi query là một connection mới →
cạn ``max_connections=100``.

Xem docs/ws-broadcast-db-fanout-2026-07-29.md.
"""

import asyncio

from django.test import SimpleTestCase, override_settings

from apps.channels_core.consumers import CRMConsumer
from apps.channels_core.handshake import reset_handshake_gate


class _Consumer(CRMConsumer):
    """Consumer trần giữ đúng state mà ``_is_authorized`` đọc.

    Hai hàm chạm DB được thay bằng bản đếm số lần gọi — đó là thứ bài test này đo.
    """

    def __init__(
        self,
        *,
        scope_kind,
        user_id=None,
        allowed_channel_codes=None,
        owner_id=None,
        db_channel_code=None,
    ):
        self.scope_kind = scope_kind
        self.user_id = user_id
        self.allowed_channel_codes = allowed_channel_codes
        self._owner_id = owner_id
        self._db_channel_code = db_channel_code
        self.db_calls = 0

    async def _order_belongs_to_user(self, order_id, user_id):
        self.db_calls += 1
        return self._owner_id == user_id

    async def _resolve_order_channel_code(self, order_id):
        self.db_calls += 1
        return self._db_channel_code


def _payload(**overrides) -> dict:
    payload = {
        "type": "order.updated",
        "order_id": 549426,
        "channel_code": "FB",
        "staff_id": 7,
        "source": "orders.service.update_order_inline",
    }
    payload.update(overrides)
    return payload


class BroadcastAuthorizationTests(SimpleTestCase):
    def setUp(self):
        reset_handshake_gate()

    def tearDown(self):
        reset_handshake_gate()

    # ── Đường nhanh: payload tự chứa → không chạm DB ────────────────────────

    def test_scope_own_dung_chu_don_tu_payload(self):
        consumer = _Consumer(scope_kind="own", user_id=7)

        allowed = asyncio.run(consumer._is_authorized(_payload()))

        self.assertTrue(allowed)
        self.assertEqual(consumer.db_calls, 0)

    def test_scope_own_tu_choi_don_cua_nguoi_khac_khong_cham_db(self):
        consumer = _Consumer(scope_kind="own", user_id=99)

        allowed = asyncio.run(consumer._is_authorized(_payload(staff_id=7)))

        self.assertFalse(allowed)
        self.assertEqual(consumer.db_calls, 0)

    def test_scope_own_don_chua_co_chu_khong_lot_cho_ai(self):
        """staff_id=0 là "chưa giao cho ai" — không được khớp với user_id nào."""
        consumer = _Consumer(scope_kind="own", user_id=7)

        allowed = asyncio.run(consumer._is_authorized(_payload(staff_id=0)))

        self.assertFalse(allowed)

    def test_scope_channels_loc_theo_payload(self):
        consumer = _Consumer(scope_kind="channels", allowed_channel_codes={"FB", "TIKTOK"})

        allowed = asyncio.run(consumer._is_authorized(_payload(channel_code="FB")))

        self.assertTrue(allowed)
        self.assertEqual(consumer.db_calls, 0)

    def test_scope_channels_tu_choi_kenh_ngoai_pham_vi(self):
        consumer = _Consumer(scope_kind="channels", allowed_channel_codes={"FB"})

        allowed = asyncio.run(consumer._is_authorized(_payload(channel_code="ZALO")))

        self.assertFalse(allowed)
        self.assertEqual(consumer.db_calls, 0)

    def test_scope_all_khong_cham_db(self):
        consumer = _Consumer(scope_kind="all")

        allowed = asyncio.run(consumer._is_authorized(_payload()))

        self.assertTrue(allowed)
        self.assertEqual(consumer.db_calls, 0)

    # ── Hồi quy chính: fan-out không còn tỉ lệ với số client ────────────────

    def test_100_client_nhan_cung_event_khong_sinh_query_nao(self):
        """Đúng hình dạng sự cố: một event tới toàn bộ tab đang mở.

        Trước bản vá đây là 100 query đồng thời → 100 connection Postgres.
        """
        consumers = [
            _Consumer(scope_kind="own", user_id=index) for index in range(100)
        ]
        payload = _payload(staff_id=7)

        async def run_all():
            return await asyncio.gather(
                *(consumer._is_authorized(payload) for consumer in consumers)
            )

        results = asyncio.run(run_all())

        self.assertEqual(sum(1 for allowed in results if allowed), 1)
        self.assertEqual(sum(consumer.db_calls for consumer in consumers), 0)

    # ── Fallback: payload thiếu field thì vẫn đúng, nhưng bị gate chặn ──────

    def test_thieu_staff_id_van_phan_quyen_dung_qua_fallback(self):
        consumer = _Consumer(scope_kind="own", user_id=7, owner_id=7)
        payload = _payload()
        payload.pop("staff_id")

        allowed = asyncio.run(consumer._is_authorized(payload))

        self.assertTrue(allowed)
        self.assertEqual(consumer.db_calls, 1, "phải fallback đúng một lần")

    def test_thieu_channel_code_van_phan_quyen_dung_qua_fallback(self):
        consumer = _Consumer(
            scope_kind="channels", allowed_channel_codes={"FB"}, db_channel_code="FB"
        )
        payload = _payload()
        payload.pop("channel_code")

        allowed = asyncio.run(consumer._is_authorized(payload))

        self.assertTrue(allowed)
        self.assertEqual(consumer.db_calls, 1)

    @override_settings(WS_BROADCAST_DB_CONCURRENCY=4)
    def test_fallback_bi_gate_chan_du_co_bao_nhieu_client(self):
        """Call site sót field không được kéo connection tỉ lệ theo số client."""
        in_flight = 0
        peak = 0

        class _Slow(_Consumer):
            async def _order_belongs_to_user(self, order_id, user_id):
                nonlocal in_flight, peak
                in_flight += 1
                peak = max(peak, in_flight)
                await asyncio.sleep(0)
                in_flight -= 1
                return True

        consumers = [_Slow(scope_kind="own", user_id=1) for _ in range(96)]
        payload = _payload()
        payload.pop("staff_id")

        async def run_all():
            return await asyncio.gather(
                *(consumer._is_authorized(payload) for consumer in consumers)
            )

        results = asyncio.run(run_all())

        self.assertEqual(len(results), 96)
        self.assertLessEqual(peak, 4, "gate broadcast phải chặn truy vấn đồng thời")
        self.assertGreaterEqual(peak, 2, "vẫn phải song song, không tuần tự hoá về 1")
