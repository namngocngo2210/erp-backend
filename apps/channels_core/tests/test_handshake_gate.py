"""Trần đồng thời cho phần chạm DB của bắt tay WebSocket (sự cố 2026-07-23)."""

import asyncio

from django.test import SimpleTestCase, override_settings

from apps.channels_core.handshake import handshake_db_gate, reset_handshake_gate


class HandshakeDbGateTests(SimpleTestCase):
    def setUp(self):
        reset_handshake_gate()

    def tearDown(self):
        reset_handshake_gate()

    async def _run_handshakes(self, count: int) -> tuple[int, int]:
        """Chạy `count` bắt tay đồng thời, trả (đỉnh đồng thời, số ca hoàn tất)."""
        in_flight = 0
        peak = 0
        done = 0

        async def handshake() -> None:
            nonlocal in_flight, peak, done
            async with handshake_db_gate():
                in_flight += 1
                peak = max(peak, in_flight)
                # Nhường event loop để các task khác có cơ hội chen vào gate.
                await asyncio.sleep(0)
                in_flight -= 1
            done += 1

        await asyncio.gather(*(handshake() for _ in range(count)))
        return peak, done

    @override_settings(WS_HANDSHAKE_DB_CONCURRENCY=4)
    def test_96_tab_reconnect_cung_luc_khong_vuot_tran(self):
        """Đúng kịch bản sự cố: ~96 client rớt cùng lúc rồi bắt tay đồng thời."""
        peak, done = asyncio.run(self._run_handshakes(96))

        self.assertLessEqual(peak, 4, "gate phải chặn số truy vấn DB đồng thời")
        # Vẫn phải song song thật, không phải tuần tự hoá về 1.
        self.assertGreaterEqual(peak, 2)
        # Và không ai bị bỏ đói: mọi client đều bắt tay xong.
        self.assertEqual(done, 96)

    @override_settings(WS_HANDSHAKE_DB_CONCURRENCY=0)
    def test_gia_tri_vo_nghia_roi_ve_mac_dinh(self):
        """Cấu hình sai (0/âm) không được biến gate thành khoá chết."""
        peak, done = asyncio.run(self._run_handshakes(20))

        self.assertGreaterEqual(peak, 2)
        self.assertEqual(done, 20)
