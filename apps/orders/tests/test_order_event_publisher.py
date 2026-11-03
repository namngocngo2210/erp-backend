"""Làm giàu payload tại điểm phát — sự cố 2026-07-29.

Payload phải mang đủ ``staff_id`` + ``channel_code`` khi rời khỏi publisher, để
``CRMConsumer`` phân quyền mà không tra DB cho từng client.
"""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.channels_core.groups import CRM_GROUP, order_channel_group
from apps.orders.application.services.order_event_publisher import (
    _broad_copy_group,
    build_order_created_row,
    enrich_order_event,
    load_event_meta,
    publish_order_event,
)
from apps.orders.contracts import OrderEventContract


class _FakeQuerySet:
    def __init__(self, row):
        self._row = row
        self.calls = 0

    def filter(self, **_kwargs):
        return self

    def values_list(self, *_fields):
        return self

    def first(self):
        self.calls += 1
        return self._row


def _patch_order(row):
    """Thay Order.objects bằng queryset giả trả về `row`."""
    fake = _FakeQuerySet(row)
    return patch("apps.orders.models.Order.objects", fake), fake


class EnrichOrderEventTests(SimpleTestCase):
    def test_payload_du_field_khong_cham_db(self):
        payload = {"order_id": 1, "staff_id": 7, "channel_code": "FB"}
        patcher, fake = _patch_order((9, "ZALO"))

        with patcher:
            result = enrich_order_event(payload)

        self.assertEqual(fake.calls, 0, "đủ field thì không được query")
        self.assertEqual(result["staff_id"], 7)
        self.assertEqual(result["channel_code"], "FB")

    def test_bo_sung_ca_hai_field_bang_dung_mot_query(self):
        patcher, fake = _patch_order((7, "FB"))

        with patcher:
            result = enrich_order_event({"order_id": 549426})

        self.assertEqual(fake.calls, 1)
        self.assertEqual(result["staff_id"], 7)
        self.assertEqual(result["channel_code"], "FB")

    def test_don_chua_co_chu_duoc_danh_dau_bang_sentinel_0(self):
        """Không có sentinel thì mọi đơn chưa giao sale sẽ fallback mỗi lần phát."""
        patcher, fake = _patch_order((None, "FB"))

        with patcher:
            result = enrich_order_event({"order_id": 1})

        self.assertEqual(result["staff_id"], 0)
        self.assertEqual(fake.calls, 1)

    def test_khong_tra_duoc_don_thi_tra_nguyen_trang(self):
        patcher, _fake = _patch_order(None)
        payload = {"order_id": 999999}

        with patcher:
            result = enrich_order_event(payload)

        self.assertEqual(result, payload)

    def test_loi_db_khong_lam_hong_luong_phat_event(self):
        payload = {"order_id": 1}

        with patch("apps.orders.models.Order.objects") as objects:
            objects.filter.side_effect = RuntimeError("db sập")
            result = enrich_order_event(payload)

        self.assertEqual(result, payload)

    def test_order_id_khong_hop_le_thi_bo_qua(self):
        patcher, fake = _patch_order((7, "FB"))

        with patcher:
            result = enrich_order_event({"order_id": None})

        self.assertEqual(fake.calls, 0)
        self.assertEqual(result, {"order_id": None})


class LoadEventMetaTests(SimpleTestCase):
    """Luồng bulk phải nạp meta cả lô bằng một query, không phải mỗi đơn một query."""

    def test_lo_rong_khong_cham_db(self):
        with patch("apps.orders.models.Order.objects") as objects:
            result = load_event_meta([])

        self.assertEqual(result, {})
        objects.filter.assert_not_called()

    def test_gom_theo_order_id(self):
        rows = [
            {"id": 1, "staff_id": 7, "channel__code": "FB"},
            {"id": 2, "staff_id": None, "channel__code": "ZALO"},
        ]

        with patch("apps.orders.models.Order.objects") as objects:
            objects.filter.return_value.values.return_value = rows
            result = load_event_meta([1, 2])

        self.assertEqual(result[1]["staff_id"], 7)
        self.assertEqual(result[2]["channel__code"], "ZALO")
        # Đúng MỘT lần chạm DB cho cả lô.
        objects.filter.assert_called_once()


class StaffIdContractTests(SimpleTestCase):
    """staff_id phải sống sót qua validate → to_payload (consumer validate lần hai)."""

    def test_staff_id_giu_nguyen_qua_contract(self):
        payload = OrderEventContract.build_order_updated(
            order_id=549426, channel_code="FB", staff_id=7
        )

        self.assertEqual(payload["staff_id"], 7)

        revalidated = OrderEventContract.validate_payload(payload).to_payload()
        self.assertEqual(revalidated["staff_id"], 7)

    def test_sentinel_0_khong_bi_loc_mat(self):
        """0 phải đi qua được cả hai vòng validate, nếu không sentinel vô nghĩa."""
        payload = OrderEventContract.build_order_updated(
            order_id=1, channel_code="FB", staff_id=0
        )

        self.assertEqual(payload["staff_id"], 0)

        revalidated = OrderEventContract.validate_payload(payload).to_payload()
        self.assertEqual(revalidated["staff_id"], 0)

    def test_khong_truyen_staff_id_thi_vang_mat_khoi_payload(self):
        payload = OrderEventContract.build_order_updated(order_id=1, channel_code="FB")

        self.assertNotIn("staff_id", payload)


class RowContractTests(SimpleTestCase):
    """`row` (bản list của đơn) phải sống sót qua 2 vòng validate; row hỏng bị BỎ
    (không giết event) — client mất row thì rơi về đường fetch cũ."""

    def test_row_hop_le_giu_nguyen_qua_hai_vong_validate(self):
        row = {"id": 5, "phone": "0900000001", "status": 0}
        payload = OrderEventContract.build_order_created(
            order_id=5, channel_code="FB", row=row
        )
        self.assertEqual(payload["row"], row)

        revalidated = OrderEventContract.validate_payload(payload).to_payload()
        self.assertEqual(revalidated["row"], row)

    def test_row_id_lech_order_id_bi_bo_event_van_song(self):
        payload = OrderEventContract.build_order_created(
            order_id=5, channel_code="FB", row={"id": 6}
        )
        self.assertNotIn("row", payload)
        self.assertEqual(payload["order_id"], 5)

    def test_row_khong_phai_dict_bi_bo(self):
        payload = OrderEventContract.validate_payload(
            {"type": "order.created", "order_id": 5, "channel_code": "FB", "row": "xxx"}
        ).to_payload()
        self.assertNotIn("row", payload)

    def test_khong_truyen_row_thi_vang_mat_khoi_payload(self):
        payload = OrderEventContract.build_order_created(order_id=5, channel_code="FB")
        self.assertNotIn("row", payload)


class _RecordingLayer:
    """Channel layer giả ghi lại mọi group_send — đủ cho việc khoá routing."""

    def __init__(self):
        self.sent = []

    async def group_send(self, group, message):
        self.sent.append((group, message))


class PublishRoutingTests(SimpleTestCase):
    """Route theo kênh, giai đoạn 1 (an toàn deploy web/worker lệch phiên bản).

    Event CÓ kênh phải đi HAI group — group kênh (đích cuối) + bản sao diện rộng
    (consumer cũ chỉ ở group chung, consumer scope all/own) — cùng ``dedup_id``
    để consumer mới khử bản trùng. Xem apps/channels_core/groups.py.
    """

    def _publish(self, payload, order_row=None):
        layer = _RecordingLayer()
        patcher, _ = _patch_order(order_row)
        with patcher, patch(
            "apps.orders.application.services.order_event_publisher.get_channel_layer",
            return_value=layer,
        ):
            publish_order_event(payload)
        return layer.sent

    def test_event_co_kenh_ban_hai_group_cung_dedup_id(self):
        sent = self._publish(
            {"type": "order.updated", "order_id": 5, "channel_code": "FB", "staff_id": 7}
        )

        self.assertEqual(
            [group for group, _message in sent],
            [order_channel_group("FB"), _broad_copy_group()],
        )
        first, second = (message for _group, message in sent)
        self.assertEqual(first["type"], "crm.event")
        self.assertTrue(first["dedup_id"])
        # Cùng dedup_id + cùng payload — consumer chỉ render bản tới trước.
        self.assertEqual(first["dedup_id"], second["dedup_id"])
        self.assertEqual(first["payload"], second["payload"])
        # dedup_id ở TẦNG MESSAGE, không lọt vào payload gửi xuống FE.
        self.assertNotIn("dedup_id", first["payload"])

    def test_phase_mac_dinh_ban_sao_dien_rong_la_group_chung(self):
        """Khoá quyết định giai đoạn 1 (mặc định): còn consumer cũ (chỉ join group
        chung) mà chuyển broad-copy sang CRM_BROAD_GROUP là client đó MẤT event.
        Giai đoạn 2 giờ là ENV (WS_CHANNEL_FANOUT_PHASE=2 + restart worker), chỉ
        bật sau khi mọi web đã chạy consumer mới."""
        self.assertEqual(_broad_copy_group(), CRM_GROUP)

    def test_phase_2_ban_sao_dien_rong_vao_broad_group(self):
        from django.test import override_settings

        from apps.channels_core.groups import CRM_BROAD_GROUP

        with override_settings(WS_CHANNEL_FANOUT_PHASE=2):
            self.assertEqual(_broad_copy_group(), CRM_BROAD_GROUP)
        # Trở về mặc định sau override — không rò trạng thái sang test khác.
        self.assertEqual(_broad_copy_group(), CRM_GROUP)

    def test_hai_lan_phat_khac_dedup_id(self):
        payload = {"type": "order.updated", "order_id": 5, "channel_code": "FB", "staff_id": 7}

        first = self._publish(dict(payload))
        second = self._publish(dict(payload))

        self.assertNotEqual(first[0][1]["dedup_id"], second[0][1]["dedup_id"])

    def test_event_khong_kenh_mot_ban_vao_group_chung_khong_dedup_id(self):
        """Không xác định được kênh (enrich không tra được đơn) → group chung như
        cũ, KHÔNG dedup_id: chỉ có một bản, gắn dedup_id chỉ tốn cửa sổ khử trùng
        của consumer."""
        sent = self._publish(
            {"type": "order.updated", "order_id": 5, "staff_id": 7}, order_row=None
        )

        self.assertEqual(len(sent), 1)
        group, message = sent[0]
        self.assertEqual(group, CRM_GROUP)
        self.assertNotIn("dedup_id", message)


class BuildOrderCreatedRowTests(TestCase):
    """Row broadcast là bản OrderListSerializer: đủ field bảng cần, KHÔNG can_edit
    (per-user — FE tự suy), lỗi serialize trả None chứ không raise."""

    def test_row_dang_list_va_khong_can_edit(self):
        from apps.channels_core.models import Channel
        from apps.customers.models import Customer
        from apps.orders.models import Order
        from apps.orders.serializers import LIST_EXCLUDED_ORDER_FIELDS

        channel = Channel.objects.create(code="row-ch", name="Row Ch", owner="Row")
        customer = Customer.objects.create(
            full_name="KH Row", phone_raw="0900000050", phone_normalized="0900000050"
        )
        order = Order.objects.create(
            channel=channel,
            customer=customer,
            name="Order Row",
            phone="0900000051",
            status=0,
            order_source="landing",
        )
        row = build_order_created_row(order)
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], order.id)
        self.assertNotIn("can_edit", row)
        for field in ("phone", "items", "crm", "created_at", "channel_code", "status"):
            self.assertIn(field, row, field)
        for field in LIST_EXCLUDED_ORDER_FIELDS:
            self.assertNotIn(field, row, field)

    def test_row_don_co_crm_dong_goi_duoc_vao_payload(self):
        """Đơn CÓ assessment: `row["crm"]` là ReturnDict của DRF, không phải dict thường.

        `to_payload()` từng dựng lại mọi mapping bằng chính type của nó, mà
        `ReturnDict.__init__` bắt buộc kwarg `serializer` → KeyError: 'serializer'
        (500 ở POST /api/orders/transfer-landing, prod 2026-08-11). Test cũ tạo đơn
        KHÔNG assessment nên `crm=None` và bug lọt lưới — đơn Landing chuyển sang
        Đơn hàng thì luôn đã qua CRM.
        """
        from apps.channels_core.models import Channel
        from apps.customers.models import Customer
        from apps.orders.models import Order, OrderAssessment

        channel = Channel.objects.create(code="row-crm-ch", name="Row CRM Ch", owner="Row")
        customer = Customer.objects.create(
            full_name="KH Row CRM", phone_raw="0900000060", phone_normalized="0900000060"
        )
        order = Order.objects.create(
            channel=channel,
            customer=customer,
            name="Order Row CRM",
            phone="0900000061",
            status=0,
            order_source="landing",
        )
        OrderAssessment.objects.create(order=order, sales_status_code="closed")
        order.refresh_from_db()

        row = build_order_created_row(order)
        self.assertIsNotNone(row["crm"])

        for payload in (
            OrderEventContract.build_order_created(
                order_id=order.id, channel_code=channel.code, row=row
            ),
            OrderEventContract.build_order_updated(
                order_id=order.id,
                channel_code=channel.code,
                row=row,
                source="orders.service.transfer_landing_orders",
            ),
        ):
            self.assertEqual(payload["row"]["id"], order.id)
            self.assertEqual(payload["row"]["crm"]["sales_status_code"], "closed")

    def test_loi_serialize_tra_none(self):
        self.assertIsNone(build_order_created_row(object()))


class OrderUpdatedRowContractTests(SimpleTestCase):
    """`order.updated` cũng mang được `row` — dành riêng cho chuyển Landing → Đơn hàng.

    Đây là đường REST-fanout cuối cùng còn sót của trang Đơn hàng (đo 2026-08-11,
    §6.5 docs/cache-latency-optimization-2026-08-11.md): tab đang mở section
    `orders` KHÔNG có dòng nào để vá tại chỗ nên buộc phải đi lấy, mà chuyển đơn
    là thao tác hàng loạt nên mọi tab cùng bắn POST list một lúc.
    """

    def test_row_song_qua_hai_vong_validate(self):
        row = {"id": 9, "phone": "0900000009", "show_orders": True}
        payload = OrderEventContract.build_order_updated(
            order_id=9, channel_code="FB", row=row,
            source="orders.service.transfer_landing_orders",
        )
        self.assertEqual(payload["row"], row)
        self.assertEqual(
            OrderEventContract.validate_payload(payload).to_payload()["row"], row
        )

    def test_row_hong_bi_bo_event_van_song(self):
        payload = OrderEventContract.build_order_updated(
            order_id=9, channel_code="FB", row={"id": 10},
        )
        self.assertNotIn("row", payload)
        self.assertEqual(payload["order_id"], 9)

    def test_updated_khong_row_van_nhu_cu(self):
        """Phần lớn order.updated KHÔNG kèm row (FE vá tại chỗ) — không được đổi."""
        payload = OrderEventContract.build_order_updated(order_id=9, channel_code="FB")
        self.assertNotIn("row", payload)
