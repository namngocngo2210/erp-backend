"""Record-scope cho các endpoint GHI hàng loạt của trang Đơn hàng.

Trước fix, 8 truy vấn ở `order_action_view.py` / `order_crud_view.py`
(`bulk-delete`, `transfer-landing`, `restore-cancelled`, `cancel-kiotviet`,
`kiotviet-actions` × 2 nhánh, `kship-options`, `kship-delivery`) nhận `ids` từ client
rồi `filter(id__in=ids)` thẳng, chỉ gate bằng `require_section(...)` +
`OrderService.can_edit_channel`. Nhưng `UserService.can_edit_order` trả `True` VÔ ĐIỀU
KIỆN cho scope OWN/ALL — docstring nói "chặn theo owner ở tầng view" nhưng tầng view
KHÔNG hề chặn. Kết quả: một sale scope OWN xoá / huỷ hóa đơn / đẩy KiotViet được đơn
của bất kỳ ai, chỉ bằng cách đổi `ids` (audit 2026-07-26 N1, phía GHI).

Mọi test đo TRẠNG THÁI DB sau khi gọi (`deleted_at`, `show_orders`,
`OrderKiotVietJob`, tham số `dispatch`), không chỉ status code: các endpoint này trả
200/202 cả khi queryset rỗng, nên assert riêng status code là tautology — xoá sạch
phần lọc scope mà test vẫn xanh.

Test khoá cả hai chiều: siết scope KHÔNG được làm user hợp lệ (scope all / channel
đúng kênh) mất quyền xoá / chuyển / huỷ / đẩy đơn.
"""

from unittest.mock import patch

from django.apps import apps as django_apps
from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderKiotVietJob
from apps.orders.tests.helpers import make_scoped_user
from apps.users.models import DataScope, Permission, Role, RolePermission, User, UserRoleAssignment
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import sync_permissions

BULK_DELETE_URL = "/api/orders/bulk-delete"
TRANSFER_URL = "/api/orders/transfer-landing"
RESTORE_URL = "/api/orders/restore-cancelled"
CANCEL_KV_URL = "/api/orders/cancel-kiotviet"
KV_ACTION_URL = "/api/orders/kiotviet-actions"
KSHIP_OPTIONS_URL = "/api/orders/kship-options"
KSHIP_DELIVERY_URL = "/api/orders/kship-delivery"

# Patch tại NƠI ĐỊNH NGHĨA (OrderServiceCommands) để phủ cả `OrderService.push_event`
# (kế thừa) lẫn lời gọi trực tiếp trong transfer_landing_orders — nếu không, channel
# layer thật (Redis) bị gọi trong test.
PUSH_EVENT_PATH = (
    "apps.orders.application.services.order_service_commands.OrderServiceCommands.push_event"
)
# restore-cancelled vẫn dispatch thẳng từ view; còn cancel-kiotviet / kiotviet-actions /
# kship-delivery đẩy job qua OrderKiotVietJobService (audit 2026-09-23 T3/P2-1) nên
# patch tại service.
ACTION_DISPATCH_PATH = "apps.orders.transport.views.order_views_impl.order_action_view.dispatch"
KV_JOB_DISPATCH_PATH = "apps.orders.application.services.order_kiotviet_job_service.dispatch"
KSHIP_OPTIONS_PATH = (
    "apps.orders.transport.views.order_views_impl.order_crud_view.get_kship_providers_and_services"
)


class _BulkActionScopeBase(TestCase):
    """Dựng 2 kênh + 3 user (own / teammate / boss) + 3 đơn dùng chung cho mọi endpoint."""

    sections: tuple[str, ...] = ("orders",)

    @classmethod
    def setUpTestData(cls):
        # Dữ liệu bất biến dựng MỘT lần cho cả class (Django rollback từng test về
        # savepoint của class). KHÔNG cache.clear() — FLUSHDB redis dùng chung của máy dev.
        sync_permissions()
        # get_model thay vì import apps.kiotviet.models: orders KHÔNG được import
        # kiotviet theo contract import-linter (và không thêm ignore_imports mới).
        kiotviet_channel_model = django_apps.get_model("kiotviet", "KiotVietChannel")
        cls.kv = kiotviet_channel_model.objects.create(
            name="KV Bulk",
            retailer="desi-bulk",
            username="kv-user",
            password="kv-pass",
        )
        cls.ch_a = Channel.objects.create(code="bulk-a", name="Bulk A")
        cls.ch_b = Channel.objects.create(code="bulk-b", name="Bulk B")
        cls.customer = Customer.objects.create(
            full_name="Khach Bulk",
            phone_raw="0909111222",
            phone_normalized="0909111222",
        )

        cls.owner = make_scoped_user("bulk-owner", *cls.sections, scope=DataScope.OWN)
        cls.teammate = make_scoped_user("bulk-teammate", *cls.sections, scope=DataScope.OWN)
        cls.boss = make_scoped_user("bulk-boss", *cls.sections, scope=DataScope.ALL)

        cls.own_order = cls._order("HD-OWN-1", 5001, cls.ch_a, cls.owner)
        cls.other_order = cls._order("HD-OTHER-2", 5002, cls.ch_a, cls.teammate)
        cls.order_b = cls._order("HD-CHB-3", 5003, cls.ch_b, cls.teammate)

    def setUp(self):
        self.client = APIClient()

    @classmethod
    def _order(cls, invoice_code, invoice_id, channel, staff, **extra) -> Order:
        defaults = {
            "channel": channel,
            "kiotviet_channel": cls.kv,
            "customer": cls.customer,
            "staff": staff,
            "name": f"Don {invoice_code}",
            "phone": "0909111222",
            "total_amount": "100000.00",
            "invoice_code": invoice_code,
            "invoice_id": invoice_id,
        }
        defaults.update(extra)
        return Order.objects.create(**defaults)

    def _post(self, user, url: str, payload: dict):
        self.client.force_authenticate(user=user)
        return self.client.post(url, payload, format="json")

    def _channel_scope_user(self, username: str, channel: Channel | None = None) -> User:
        """User scope CHANNEL; `channel=None` = bị giới hạn nhưng CHƯA được gán kênh nào.

        Nhánh `channel=None` là nhánh rò rỉ dễ bỏ sót nhất: scope_filters không chứa
        cả `owner_user_id` lẫn `channel`, nên nếu quên `should_return_empty` thì
        queryset không bị lọc gì → tác động lên TOÀN BỘ đơn công ty.
        """
        user = make_scoped_user(username, *self.sections, scope=DataScope.CHANNEL)
        if channel is not None:
            UserChannel.objects.create(user=user, channel=channel, is_active=True)
        bump_rbac_cache_version()
        return user


class BulkDeleteOrdersScopeTests(_BulkActionScopeBase):
    def test_own_scope_cannot_soft_delete_order_of_other_staff(self):
        resp = self._post(self.owner, BULK_DELETE_URL, {"ids": [self.other_order.id]})
        self.other_order.refresh_from_db()
        self.assertIsNone(self.other_order.deleted_at)
        self.assertEqual(resp.json().get("deleted_count"), 0)

    def test_own_scope_deletes_only_own_order_in_mixed_selection(self):
        resp = self._post(
            self.owner, BULK_DELETE_URL, {"ids": [self.own_order.id, self.other_order.id]}
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.own_order.refresh_from_db()
        self.other_order.refresh_from_db()
        self.assertIsNotNone(self.own_order.deleted_at)
        self.assertIsNone(self.other_order.deleted_at)
        self.assertEqual(resp.json()["deleted_count"], 1)

    def test_all_scope_still_deletes_any_order(self):
        resp = self._post(
            self.boss, BULK_DELETE_URL, {"ids": [self.other_order.id, self.order_b.id]}
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.other_order.refresh_from_db()
        self.order_b.refresh_from_db()
        self.assertIsNotNone(self.other_order.deleted_at)
        self.assertIsNotNone(self.order_b.deleted_at)
        self.assertEqual(resp.json()["deleted_count"], 2)

    def test_channel_scope_still_deletes_order_of_assigned_channel(self):
        user = self._channel_scope_user("bulk-del-channel", self.ch_a)
        resp = self._post(user, BULK_DELETE_URL, {"ids": [self.other_order.id]})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.other_order.refresh_from_db()
        self.assertIsNotNone(self.other_order.deleted_at)

    def test_channel_scope_without_any_channel_deletes_nothing(self):
        user = self._channel_scope_user("bulk-del-nochannel")
        self._post(
            user,
            BULK_DELETE_URL,
            {"ids": [self.own_order.id, self.other_order.id, self.order_b.id]},
        )
        for order in (self.own_order, self.other_order, self.order_b):
            order.refresh_from_db()
            self.assertIsNone(order.deleted_at, f"order {order.id} bị xoá oan")


class TransferLandingOrdersScopeTests(_BulkActionScopeBase):
    sections = ("orders", "landing-orders")

    def setUp(self):
        super().setUp()
        # `order_source="landing"` là BẮT BUỘC để fixture khớp thực tế: trang Landing
        # (list_landing_orders) khóa `order_source_present=true` + loại koc/sàn TMĐT, nên
        # đơn KHÔNG có nguồn không bao giờ hiện trên trang đó và không thể được chọn để
        # chuyển. Từ khi record-scope dùng `order_record_scope_q`, điều kiện nhận dạng
        # trang Landing được áp cho cả endpoint chuyển đơn — xem
        # `test_order_without_source_is_not_transferable`.
        self.own_landing = self._order(
            "HD-LAND-OWN", 6001, self.ch_a, self.owner, show_orders=False, order_source="landing"
        )
        self.other_landing = self._order(
            "HD-LAND-OTHER",
            6002,
            self.ch_a,
            self.teammate,
            show_orders=False,
            order_source="landing",
        )
        self.own_landing_no_kv = self._order(
            "HD-LAND-NOKV",
            6003,
            self.ch_a,
            self.owner,
            show_orders=False,
            kiotviet_channel=None,
            order_source="landing",
        )
        self.own_no_source = self._order(
            "HD-NOSRC", 6004, self.ch_a, self.owner, show_orders=False
        )

    def test_order_without_source_is_not_transferable(self):
        """Đơn KHÔNG có nguồn không thuộc trang Landing → endpoint Landing không chạm tới.

        Khoá điều kiện nhận dạng của `order_record_scope_q`: `transfer_landing_orders`
        guard bằng `require_section("landing-orders")` nên record-scope = (đơn thuộc trang
        Landing) AND (scope của user ở section đó). Dùng user scope ALL để loại hẳn yếu tố
        scope — nếu vẫn chuyển được thì phần "điều kiện nhận dạng" đã bị bỏ.
        """
        boss_landing = make_scoped_user("bulk-boss-landing", *self.sections, scope=DataScope.ALL)
        with patch(PUSH_EVENT_PATH):
            resp = self._post(boss_landing, TRANSFER_URL, {"ids": [self.own_no_source.id]})
        self.assertEqual(resp.status_code, 404, resp.content)
        self.own_no_source.refresh_from_db()
        self.assertFalse(self.own_no_source.show_orders)

    def test_own_scope_cannot_transfer_order_of_other_staff(self):
        with patch(PUSH_EVENT_PATH) as push_event:
            self._post(self.owner, TRANSFER_URL, {"ids": [self.other_landing.id]})
        self.other_landing.refresh_from_db()
        self.assertFalse(self.other_landing.show_orders)
        push_event.assert_not_called()

    def test_own_scope_transfers_own_order(self):
        with patch(PUSH_EVENT_PATH):
            resp = self._post(self.owner, TRANSFER_URL, {"ids": [self.own_landing.id]})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.own_landing.refresh_from_db()
        self.assertTrue(self.own_landing.show_orders)
        self.assertEqual(resp.json()["transferred_count"], 1)

    def test_channel_scope_transfers_order_of_assigned_channel(self):
        user = self._channel_scope_user("transfer-channel", self.ch_a)
        with patch(PUSH_EVENT_PATH):
            resp = self._post(user, TRANSFER_URL, {"ids": [self.other_landing.id]})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.other_landing.refresh_from_db()
        self.assertTrue(self.other_landing.show_orders)

    def test_channel_scope_without_any_channel_transfers_nothing(self):
        user = self._channel_scope_user("transfer-nochannel")
        with patch(PUSH_EVENT_PATH):
            self._post(
                user, TRANSFER_URL, {"ids": [self.own_landing.id, self.other_landing.id]}
            )
        for order in (self.own_landing, self.other_landing):
            order.refresh_from_db()
            self.assertFalse(order.show_orders, f"order {order.id} bị chuyển oan")

    def test_invalid_orders_payload_has_no_customer_pii(self):
        """`invalid_orders` chỉ được trả `id`.

        Trước fix, payload 400 trả `name` + `phone` của khách — tức chỉ cần quyền
        `landing-orders:view` là dò được PII của đơn bất kỳ qua vòng lặp `ids`.
        """
        with patch(PUSH_EVENT_PATH):
            resp = self._post(self.owner, TRANSFER_URL, {"ids": [self.own_landing_no_kv.id]})
        self.assertEqual(resp.status_code, 400, resp.content)
        body = resp.json()
        self.assertEqual(body["invalid_orders"], [{"id": self.own_landing_no_kv.id}])
        self.assertNotIn("0909111222", resp.content.decode())


class RestoreCancelledOrdersScopeTests(_BulkActionScopeBase):
    def test_own_scope_cannot_restore_order_of_other_staff(self):
        with patch(ACTION_DISPATCH_PATH) as dispatch_mock:
            self._post(self.owner, RESTORE_URL, {"ids": [self.other_order.id]})
        dispatch_mock.assert_not_called()

    def test_own_scope_restores_only_own_order_in_mixed_selection(self):
        with patch(ACTION_DISPATCH_PATH) as dispatch_mock:
            resp = self._post(
                self.owner, RESTORE_URL, {"ids": [self.own_order.id, self.other_order.id]}
            )
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(resp.json()["queued_ids"], [self.own_order.id])
        self.assertIn(self.other_order.id, resp.json()["skipped_ids"])
        self.assertEqual(dispatch_mock.call_args.kwargs["order_ids"], [self.own_order.id])

    def test_all_scope_restores_any_order(self):
        with patch(ACTION_DISPATCH_PATH) as dispatch_mock:
            resp = self._post(self.boss, RESTORE_URL, {"ids": [self.other_order.id]})
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(dispatch_mock.call_args.kwargs["order_ids"], [self.other_order.id])

    def test_channel_scope_without_any_channel_restores_nothing(self):
        user = self._channel_scope_user("restore-nochannel")
        with patch(ACTION_DISPATCH_PATH) as dispatch_mock:
            self._post(user, RESTORE_URL, {"ids": [self.own_order.id, self.other_order.id]})
        dispatch_mock.assert_not_called()


class CancelKiotVietOrdersScopeTests(_BulkActionScopeBase):
    def _cancel_jobs(self, order: Order) -> int:
        return OrderKiotVietJob.objects.filter(
            order=order, action=OrderKiotVietJob.ACTION_CANCEL_INVOICE
        ).count()

    def test_own_scope_cannot_cancel_invoice_of_other_staff(self):
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            self._post(self.owner, CANCEL_KV_URL, {"ids": [self.other_order.id]})
        self.assertEqual(self._cancel_jobs(self.other_order), 0)

    def test_own_scope_cancels_only_own_order_in_mixed_selection(self):
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            resp = self._post(
                self.owner, CANCEL_KV_URL, {"ids": [self.own_order.id, self.other_order.id]}
            )
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(self._cancel_jobs(self.own_order), 1)
        self.assertEqual(self._cancel_jobs(self.other_order), 0)
        self.assertEqual(resp.json()["queued_count"], 1)

    def test_all_scope_cancels_any_order(self):
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            resp = self._post(self.boss, CANCEL_KV_URL, {"ids": [self.order_b.id]})
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(self._cancel_jobs(self.order_b), 1)

    def test_channel_scope_without_any_channel_cancels_nothing(self):
        user = self._channel_scope_user("cancel-nochannel")
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            self._post(
                user,
                CANCEL_KV_URL,
                {"ids": [self.own_order.id, self.other_order.id, self.order_b.id]},
            )
        for order in (self.own_order, self.other_order, self.order_b):
            self.assertEqual(self._cancel_jobs(order), 0, f"order {order.id} bị huỷ oan")


class RunKiotVietActionScopeTests(_BulkActionScopeBase):
    def _push_jobs(self, order: Order) -> int:
        return OrderKiotVietJob.objects.filter(
            order=order, action=OrderKiotVietJob.ACTION_PUSH
        ).count()

    def test_execute_push_selected_skips_order_of_other_staff(self):
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            resp = self._post(
                self.owner,
                KV_ACTION_URL,
                {"action": "push_selected", "step": "execute", "ids": [self.other_order.id]},
            )
        self.assertEqual(self._push_jobs(self.other_order), 0)
        self.assertEqual(resp.json()["target_count"], 0)

    def test_execute_push_selected_pushes_own_order(self):
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            resp = self._post(
                self.owner,
                KV_ACTION_URL,
                {
                    "action": "push_selected",
                    "step": "execute",
                    "ids": [self.own_order.id, self.other_order.id],
                },
            )
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(self._push_jobs(self.own_order), 1)
        self.assertEqual(self._push_jobs(self.other_order), 0)

    def test_execute_all_scope_pushes_any_order(self):
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            resp = self._post(
                self.boss,
                KV_ACTION_URL,
                {"action": "push_selected", "step": "execute", "ids": [self.order_b.id]},
            )
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(self._push_jobs(self.order_b), 1)

    def test_execute_push_unsent_without_ids_only_touches_own_orders(self):
        """Nhánh chạy theo FILTER (`order_code=""`), không theo ids.

        Đây là nhánh nguy hiểm nhất: user scope OWN bấm "đẩy tất cả đơn chưa gửi"
        trước fix sẽ đẩy đơn chưa gửi của TOÀN công ty lên KiotViet.
        """
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            self._post(self.owner, KV_ACTION_URL, {"action": "push_unsent", "step": "execute"})
        self.assertEqual(self._push_jobs(self.own_order), 1)
        self.assertEqual(self._push_jobs(self.other_order), 0)
        self.assertEqual(self._push_jobs(self.order_b), 0)

    def test_precheck_push_selected_excludes_order_of_other_staff(self):
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            resp = self._post(
                self.owner,
                KV_ACTION_URL,
                {
                    "action": "push_selected",
                    "step": "precheck",
                    "ids": [self.own_order.id, self.other_order.id],
                },
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["target_count"], 1)
        # Đơn ngoài scope không được xuất hiện ở bất kỳ nhánh nào của precheck payload.
        self.assertNotIn(self.other_order.id, body["valid_order_ids"])
        self.assertNotIn(
            self.other_order.id, [row["order_id"] for row in body["invalid_orders"]]
        )

    def test_channel_scope_without_any_channel_pushes_nothing(self):
        user = self._channel_scope_user("kv-action-nochannel")
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            self._post(
                user,
                KV_ACTION_URL,
                {
                    "action": "push_selected",
                    "step": "execute",
                    "ids": [self.own_order.id, self.other_order.id, self.order_b.id],
                },
            )
        for order in (self.own_order, self.other_order, self.order_b):
            self.assertEqual(self._push_jobs(order), 0, f"order {order.id} bị đẩy oan")


class KshipScopeTests(_BulkActionScopeBase):
    def _kship_jobs(self, order: Order) -> int:
        return OrderKiotVietJob.objects.filter(
            order=order, action=OrderKiotVietJob.ACTION_CREATE_KSHIP_DELIVERY
        ).count()

    def test_kship_options_denies_order_of_other_staff(self):
        with patch(KSHIP_OPTIONS_PATH) as options_mock:
            resp = self._post(self.owner, KSHIP_OPTIONS_URL, {"ids": [self.other_order.id]})
        self.assertEqual(resp.status_code, 404, resp.content)
        options_mock.assert_not_called()

    def test_kship_options_allows_own_order(self):
        with patch(KSHIP_OPTIONS_PATH, return_value={"providers": [], "services": []}):
            resp = self._post(self.owner, KSHIP_OPTIONS_URL, {"ids": [self.own_order.id]})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["channel_id"], self.kv.id)

    def test_kship_delivery_does_not_queue_order_of_other_staff(self):
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            resp = self._post(
                self.owner,
                KSHIP_DELIVERY_URL,
                {"ids": [self.other_order.id], "provider_code": "BEST", "service_code": "STD"},
            )
        self.assertEqual(resp.status_code, 404, resp.content)
        self.assertEqual(self._kship_jobs(self.other_order), 0)

    def test_kship_delivery_queues_only_own_order_in_mixed_selection(self):
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            resp = self._post(
                self.owner,
                KSHIP_DELIVERY_URL,
                {
                    "ids": [self.own_order.id, self.other_order.id],
                    "provider_code": "BEST",
                    "service_code": "STD",
                },
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._kship_jobs(self.own_order), 1)
        self.assertEqual(self._kship_jobs(self.other_order), 0)

    def test_kship_delivery_all_scope_queues_any_order(self):
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            resp = self._post(
                self.boss,
                KSHIP_DELIVERY_URL,
                {"ids": [self.order_b.id], "provider_code": "BEST", "service_code": "STD"},
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._kship_jobs(self.order_b), 1)

    def test_channel_scope_without_any_channel_queues_no_kship_job(self):
        user = self._channel_scope_user("kship-nochannel")
        with patch(KV_JOB_DISPATCH_PATH), patch(PUSH_EVENT_PATH):
            self._post(
                user,
                KSHIP_DELIVERY_URL,
                {
                    "ids": [self.own_order.id, self.other_order.id, self.order_b.id],
                    "provider_code": "BEST",
                    "service_code": "STD",
                },
            )
        for order in (self.own_order, self.other_order, self.order_b):
            self.assertEqual(self._kship_jobs(order), 0, f"order {order.id} bị tạo job oan")


class TransferLandingOrdersRowPayloadTests(_BulkActionScopeBase):
    # KHÔNG kế thừa TransferLandingOrdersScopeTests: unittest chạy lại toàn bộ test
    # của lớp cha trong lớp con, nhân đôi 8 test scope chẳng để làm gì.
    sections = ("orders", "landing-orders")
    """Event chuyển đơn phải MANG SẴN row — đường REST-fanout cuối cùng của trang Đơn hàng.

    Đo trên prod 2026-08-11 (§6.5 docs/cache-latency-optimization-2026-08-11.md):
    tab đang mở section `orders` không có dòng nào để vá tại chỗ nên phải đi lấy,
    mà chuyển đơn là thao tác hàng loạt → coalescer của FE gom ≥2 id → MỌI tab bắn
    một `POST /api/orders` đầy đủ (~460ms server). Mang sẵn row thì con số đó về 0.
    """

    def setUp(self):
        super().setUp()
        self.own_landing = self._order(
            "HD-ROW-OWN", 6101, self.ch_a, self.owner, show_orders=False, order_source="landing"
        )

    def _transfer(self, ids):
        with patch(PUSH_EVENT_PATH) as push_event:
            resp = self._post(self.boss, TRANSFER_URL, {"ids": ids})
        self.assertEqual(resp.status_code, 200, resp.content)
        return [call.args[0] for call in push_event.call_args_list]

    def test_event_mang_row_cua_dung_don(self):
        payloads = self._transfer([self.own_landing.id])

        self.assertEqual(len(payloads), 1)
        row = payloads[0].get("row")
        self.assertIsNotNone(row, "event chuyển đơn phải kèm row, nếu không tab phải đi fetch")
        self.assertEqual(row["id"], self.own_landing.id)

    def test_row_mang_show_orders_true(self):
        """`show_orders` được set bằng queryset `.update()` nên instance trong bộ nhớ
        vẫn là giá trị CŨ. Quên đồng bộ lại thì row phát đi mang `show_orders=false`
        và FE lọc nó ra ngay sau khi upsert — đơn "chuyển xong mà không thấy đâu"."""
        payloads = self._transfer([self.own_landing.id])

        self.assertTrue(payloads[0]["row"]["show_orders"])

    def test_lo_qua_tran_thi_bo_row(self):
        """Lô rất lớn: serialize N row ở điểm phát đắt hơn một lần refetch của mỗi tab
        (coalescer gom cả lô thành MỘT request), nên server chủ động trả row=None."""
        module = "apps.orders.transport.views.order_views_impl.order_action_view"
        with patch(f"{module}._TRANSFER_ROW_PAYLOAD_MAX", 0):
            payloads = self._transfer([self.own_landing.id])

        self.assertNotIn("row", payloads[0])
