"""Test in hóa đơn / phiếu giao hàng: record-scope, throttle, quyền ghi phiếu giao, COD."""

import uuid

from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps as django_apps
from django.core.cache.backends.locmem import LocMemCache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels_core.models import Channel, UserChannel
from apps.common.business_constants import MARKETPLACE_ORDER_SOURCE_CODES
from apps.customers.models import Customer
from apps.orders.invoice import _resolve_slip_cod
from apps.orders.models import Order
from apps.orders.transport.views.order_views_impl.order_export_view import (
    OrderPrintRateThrottle,
)
from apps.orders.tests.helpers import (
    make_scoped_user,
    make_scoped_user_by_codes,
    mock_kiotviet,
)
from apps.users.models import (
    DataScope,
    Department,
    User,
    UserDepartment,
)
from apps.users.rbac import bump_rbac_cache_version
from apps.users.rbac_catalog import sync_permissions

INVOICE_URL = "/api/orders/{}/invoice"
SLIP_URL = "/api/orders/{}/delivery-slip"
BULK_INVOICE_URL = "/api/orders/bulk-invoices"
BULK_SLIP_URL = "/api/orders/bulk-delivery-slips"


# ─────────────────────────────────────────────────────────────────────────────
# Record-scope + trần số đơn cho 4 endpoint in hóa đơn / phiếu giao hàng.
#
# Trước fix, 4 endpoint (`/<id>/invoice`, `/<id>/delivery-slip`, `/bulk-invoices`,
# `/bulk-delivery-slips`) chỉ guard bằng `require_section(*ORDER_SECTIONS)` — tức chỉ
# kiểm tra user CÓ quyền `{section}:view`, không kiểm tra user có được xem CHÍNH bản
# ghi đó. Bất kỳ nhân viên mở được trang Đơn hàng đều tải được hóa đơn (tên, số điện
# thoại, địa chỉ khách) của toàn công ty chỉ bằng cách đổi `order_id`/`ids`
# (audit 2026-07-26 N1).
#
# Test cũng khóa hai chiều: siết scope KHÔNG được làm user hợp lệ (scope all /
# department / section khác orders) mất quyền in.
# ─────────────────────────────────────────────────────────────────────────────


class OrderPrintScopeTests(TestCase):
    def setUp(self):
        # KHÔNG cache.clear() — RedisCache.clear() FLUSHDB cả redis dùng chung.
        sync_permissions()
        self.client = APIClient()
        # get_model thay vì import apps.kiotviet.models: orders KHÔNG được import
        # kiotviet theo contract import-linter (tầng dưới → tầng trên).
        kiotviet_channel_model = django_apps.get_model("kiotviet", "KiotVietChannel")
        self.kv = kiotviet_channel_model.objects.create(name="KV Print", retailer="desi-print")

        self.ch_a = Channel.objects.create(code="print-a", name="Print A")
        self.ch_b = Channel.objects.create(code="print-b", name="Print B")
        self.customer = Customer.objects.create(
            full_name="Khach Print",
            phone_raw="0909555666",
            phone_normalized="0909555666",
        )

        self.owner = make_scoped_user("print-owner", "orders", scope=DataScope.OWN)
        self.teammate = make_scoped_user("print-teammate", "orders", scope=DataScope.OWN)

        self.own_order = self._order("INV-OWN-1", 1001, self.ch_a, self.owner)
        self.other_order = self._order("INV-OTHER-2", 1002, self.ch_a, self.teammate)
        self.order_b = self._order("INV-CHB-3", 1003, self.ch_b, self.teammate)

    def _order(self, invoice_code: str, invoice_id: int, channel: Channel, staff: User) -> Order:
        return Order.objects.create(
            channel=channel,
            kiotviet_channel=self.kv,
            customer=self.customer,
            staff=staff,
            name=f"Don {invoice_code}",
            phone="0909555666",
            total_amount="100000.00",
            invoice_code=invoice_code,
            invoice_id=invoice_id,
        )

    def _get(self, user, url: str):
        self.client.force_authenticate(user=user)
        return self.client.get(url)

    def _post(self, user, url: str, ids):
        self.client.force_authenticate(user=user)
        return self.client.post(url, {"ids": ids}, format="json")

    # ── bulk ────────────────────────────────────────────────────────────────
    def test_bulk_invoices_excludes_orders_outside_own_scope(self):
        with mock_kiotviet() as printed:
            resp = self._post(
                self.owner, BULK_INVOICE_URL, [self.own_order.id, self.other_order.id]
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn(self.own_order.invoice_code, body)
        self.assertNotIn(self.other_order.invoice_code, body)
        self.assertEqual(printed["invoice_codes"], [self.own_order.invoice_code])

    def test_bulk_delivery_slips_excludes_orders_outside_own_scope(self):
        with mock_kiotviet() as printed:
            resp = self._post(self.owner, BULK_SLIP_URL, [self.own_order.id, self.other_order.id])
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn(self.own_order.invoice_code, body)
        self.assertNotIn(self.other_order.invoice_code, body)
        self.assertEqual(printed["invoice_ids"], [self.own_order.invoice_id])

    def test_bulk_endpoints_reject_too_many_ids(self):
        # Phải khoá cả NỘI DUNG: 400 còn phát sinh từ invoice.py ("Không có hóa đơn
        # nào." — text/plain) khi queryset rỗng. Chạy trong cả suite apps/orders,
        # sequence orders.id đã vượt xa 201 (TestCase rollback không reset sequence)
        # nên id 1..201 không khớp đơn nào → chỉ assert status_code là tautology:
        # xoá sạch check trần mà test vẫn xanh. Nhánh trần trả DRF JSON nên phân biệt được.
        for url in (BULK_INVOICE_URL, BULK_SLIP_URL):
            with self.subTest(url=url), mock_kiotviet():
                resp = self._post(self.owner, url, list(range(1, 202)))
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()["detail"], "Chỉ in được tối đa 200 đơn mỗi lần.")

    def test_bulk_invoices_rejects_non_integer_ids(self):
        # `{"ids": ["abc"]}` trước đây rơi vào filter(id__in=...) → ValueError ngoài
        # try/except → 500. Lỗi của client phải là 400 và message đọc được.
        with mock_kiotviet() as printed:
            resp = self._post(self.owner, BULK_INVOICE_URL, ["abc"])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "Danh sách đơn không hợp lệ.")
        self.assertEqual(printed["invoice_codes"], [])

    def test_bulk_invoices_rejects_id_beyond_bigint(self):
        # Order.id là int8: số vượt miền bigint phải bị chặn ở transport (400) chứ
        # không để driver DB raise → 500.
        with mock_kiotviet() as printed:
            resp = self._post(self.owner, BULK_INVOICE_URL, [9_223_372_036_854_775_808])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "Danh sách đơn không hợp lệ.")
        self.assertEqual(printed["invoice_codes"], [])

    def test_bulk_invoices_accepts_exactly_max_ids(self):
        # Trần là 200 → đúng 200 id vẫn phải in được (off-by-one guard).
        ids = [self.own_order.id, *range(900_000, 900_199)]
        self.assertEqual(len(ids), 200)
        with mock_kiotviet() as printed:
            resp = self._post(self.owner, BULK_INVOICE_URL, ids)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(printed["invoice_codes"], [self.own_order.invoice_code])

    def test_bulk_invoices_all_scope_prints_every_selected_order(self):
        # Siết scope không được cắt quyền của user scope=all.
        boss = make_scoped_user("print-boss", "orders", scope=DataScope.ALL)
        with mock_kiotviet() as printed:
            resp = self._post(boss, BULK_INVOICE_URL, [self.own_order.id, self.order_b.id])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            set(printed["invoice_codes"]),
            {self.own_order.invoice_code, self.order_b.invoice_code},
        )

    def test_bulk_invoices_channel_scope_keeps_assigned_channel(self):
        user = make_scoped_user("print-channel", "orders", scope=DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.ch_a, is_active=True)
        bump_rbac_cache_version()
        with mock_kiotviet() as printed:
            resp = self._post(user, BULK_INVOICE_URL, [self.other_order.id, self.order_b.id])
        self.assertEqual(resp.status_code, 200)
        # Đơn kênh A của đồng nghiệp: vẫn in được (scope channel, không phải own).
        self.assertEqual(printed["invoice_codes"], [self.other_order.invoice_code])

    def test_bulk_invoices_without_ids_still_rejected(self):
        resp = self._post(self.owner, BULK_INVOICE_URL, [])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "Chưa chọn đơn hàng.")

    # ── scope rỗng: channel/department chưa được gán kênh nào ────────────────
    #
    # Nhánh nguy hiểm nhất của _scoped_print_queryset. Với user scope
    # CHANNEL/DEPARTMENT chưa có kênh, scope_filters KHÔNG chứa cả `owner_user_id`
    # lẫn `channel` → nếu bỏ nhánh `scope_empty` thì không filter gì nữa và toàn bộ
    # đơn công ty rò lại, trong khi mọi test scope khác vẫn xanh (chúng đều có
    # owner_user_id hoặc channel để lọc). 2 test dưới là thứ duy nhất khoá nhánh này.
    def test_channel_scope_without_any_channel_denies_single_print(self):
        user = make_scoped_user("print-channel-none", "orders", scope=DataScope.CHANNEL)
        # Cố tình KHÔNG tạo UserChannel nào.
        with mock_kiotviet() as printed:
            resp = self._get(user, INVOICE_URL.format(self.own_order.id))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(printed["invoice_codes"], [])

    def test_channel_scope_without_any_channel_prints_nothing_in_bulk(self):
        user = make_scoped_user("print-channel-none-bulk", "orders", scope=DataScope.CHANNEL)
        with mock_kiotviet() as printed:
            resp = self._post(
                user, BULK_INVOICE_URL, [self.own_order.id, self.other_order.id, self.order_b.id]
            )
        # Không đơn nào được gửi sang KiotViet → invoice.py trả 400 text/plain.
        self.assertEqual(printed["invoice_codes"], [])
        self.assertEqual(resp.status_code, 400)

    # ── single ──────────────────────────────────────────────────────────────
    def test_single_invoice_denies_order_of_unassigned_channel(self):
        user = make_scoped_user("print-channel-a", "orders", scope=DataScope.CHANNEL)
        UserChannel.objects.create(user=user, channel=self.ch_a, is_active=True)
        bump_rbac_cache_version()
        with mock_kiotviet() as printed:
            resp = self._get(user, INVOICE_URL.format(self.order_b.id))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(printed["invoice_codes"], [])

    def test_single_invoice_denies_order_outside_own_scope(self):
        with mock_kiotviet() as printed:
            resp = self._get(self.owner, INVOICE_URL.format(self.other_order.id))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(printed["invoice_codes"], [])

    def test_single_delivery_slip_denies_order_outside_own_scope(self):
        with mock_kiotviet() as printed:
            resp = self._get(self.owner, SLIP_URL.format(self.other_order.id))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(printed["invoice_ids"], [])
        self.other_order.refresh_from_db()
        # Không được đánh dấu "đã in" cho đơn bị chặn.
        self.assertFalse(self.other_order.is_delivery_invoice_printed)

    def test_single_invoice_allows_own_order(self):
        with mock_kiotviet() as printed:
            resp = self._get(self.owner, INVOICE_URL.format(self.own_order.id))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(printed["invoice_codes"], [self.own_order.invoice_code])

    def test_single_delivery_slip_allows_own_order(self):
        with mock_kiotviet() as printed:
            resp = self._get(self.owner, SLIP_URL.format(self.own_order.id))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(printed["invoice_ids"], [self.own_order.invoice_id])

    def test_single_invoice_missing_order_returns_404(self):
        resp = self._get(self.owner, INVOICE_URL.format(9_999_999))
        self.assertEqual(resp.status_code, 404)

    def test_single_invoice_deleted_order_returns_404(self):
        self.own_order.deleted_at = timezone.now()
        self.own_order.save(update_fields=["deleted_at"])
        resp = self._get(self.owner, INVOICE_URL.format(self.own_order.id))
        self.assertEqual(resp.status_code, 404)


class OrderPrintSectionScopeTests(TestCase):
    """Hai ca dễ đi sai hướng khi siết scope cho endpoint in dùng chung.

    - scope `department`: read-scope mở theo kênh của PHÒNG BAN, không qua
      UserChannel → không được gate bằng edit-permission (can_edit_channel) nếu
      không muốn chặn oan user hợp lệ. Phải in được.
    - user chỉ được cấp section con (koc/landing/outside): scope resolve theo
      section "orders" nên rơi về OWN → chỉ in được đơn của mình. Đây là đánh đổi
      CÓ CHỦ Ý (fail-safe): nếu lấy scope của section con áp cho endpoint dùng
      chung thì user KOC sẽ in được cả đơn thường — chính lỗ hổng đang bịt.
    """

    def setUp(self):
        sync_permissions()
        self.client = APIClient()
        kiotviet_channel_model = django_apps.get_model("kiotviet", "KiotVietChannel")
        self.kv = kiotviet_channel_model.objects.create(name="KV Print 2", retailer="desi-print2")
        self.channel = Channel.objects.create(code="print-dept", name="Print Dept")
        self.customer = Customer.objects.create(
            full_name="Khach Dept",
            phone_raw="0909777888",
            phone_normalized="0909777888",
        )
        self.staff = User.objects.create_user(username="print-dept-staff", password="x")
        self.order = Order.objects.create(
            channel=self.channel,
            kiotviet_channel=self.kv,
            customer=self.customer,
            staff=self.staff,
            name="Don Dept",
            phone="0909777888",
            total_amount="1.00",
            invoice_code="INV-DEPT-1",
            invoice_id=2001,
        )

    def test_department_scope_can_print_department_channel_order(self):
        dept = Department.objects.create(name="Phong In An")
        self.channel.departments.add(dept)
        user = make_scoped_user("print-dept-user", "orders", scope=DataScope.DEPARTMENT)
        UserDepartment.objects.create(user=user, department=dept)
        bump_rbac_cache_version()

        self.client.force_authenticate(user=user)
        with mock_kiotviet() as printed:
            resp = self.client.get(INVOICE_URL.format(self.order.id))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(printed["invoice_codes"], [self.order.invoice_code])

    def test_koc_only_all_scope_cannot_print_order_of_other_staff(self):
        # Role chỉ cấp koc-orders scope=all: KHÔNG được in đơn thường của người khác.
        # Trang KOC khóa cứng order_source="koc" nên "all" ở đây = mọi đơn KOC, không
        # phải mọi đơn — endpoint in dùng chung không phân biệt được, nên fail-safe.
        user = make_scoped_user("print-koc-all", "koc-orders", scope=DataScope.ALL)
        self.client.force_authenticate(user=user)
        with mock_kiotviet() as printed:
            resp = self.client.get(INVOICE_URL.format(self.order.id))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(printed["invoice_codes"], [])

    def test_koc_only_all_scope_can_print_own_order(self):
        # Đánh đổi trên chỉ giới hạn ở đơn của NGƯỜI KHÁC — đơn của chính mình vẫn in được.
        user = make_scoped_user("print-koc-own", "koc-orders", scope=DataScope.ALL)
        self.order.staff = user
        self.order.save(update_fields=["staff"])
        self.client.force_authenticate(user=user)
        with mock_kiotviet() as printed:
            resp = self.client.get(INVOICE_URL.format(self.order.id))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(printed["invoice_codes"], [self.order.invoice_code])


@contextmanager
def _throttle_at(rate: str):
    """Ép rate throttle + cache riêng (LocMem) cho khối lệnh bên trong.

    Ghi đè qua `THROTTLE_RATES` (bảng scope→rate mà `get_rate()` tra cứu) thay vì gán
    thẳng `rate`: giữ nguyên đường đi thật của DRF nên test vẫn gãy nếu ai đó đổi
    `scope` của throttle mà quên thêm entry tương ứng vào settings.
    """
    # Tên cache duy nhất mỗi lần gọi: LocMemCache cùng tên dùng chung dict → hai
    # khối `_throttle_at` liên tiếp (vd subTest theo endpoint) sẽ ăn counter của nhau.
    isolated = LocMemCache(f"print-throttle-{rate}-{uuid.uuid4().hex}", {})
    with (
        patch.object(OrderPrintRateThrottle, "cache", isolated),
        patch.object(
            OrderPrintRateThrottle,
            "THROTTLE_RATES",
            {**OrderPrintRateThrottle.THROTTLE_RATES, OrderPrintRateThrottle.scope: rate},
        ),
    ):
        yield


class OrderPrintThrottleTests(TestCase):
    """Throttle cho 4 endpoint in ấn.

    Mỗi request in có thể sinh tới `PRINT_MAX_IDS` (=200) round-trip HTTP TUẦN TỰ sang
    KiotViet. Không có trần tần suất thì một tab bị kẹt vòng lặp (hoặc một script) đủ để
    giữ hết worker gunicorn và đốt rate-limit của KiotViet cho cả công ty — trần 200 id
    mỗi request chỉ chặn được chiều RỘNG, không chặn chiều SÂU (audit 2026-07-26 N4).

    Cô lập state throttle: DRF đếm request trong `django.core.cache`, mà test dùng Redis
    THẬT dùng chung. Patch `cache` của class throttle sang một LocMemCache mới cho từng
    test — KHÔNG BAO GIỜ `cache.clear()` (RedisCache.clear() = FLUSHDB cả redis dev).
    Patch luôn `rate` để test không phụ thuộc con số cấu hình ở settings.
    """

    def setUp(self):
        sync_permissions()
        self.client = APIClient()
        kiotviet_channel_model = django_apps.get_model("kiotviet", "KiotVietChannel")
        self.kv = kiotviet_channel_model.objects.create(
            name="KV Throttle", retailer="desi-throttle"
        )
        self.channel = Channel.objects.create(code="thr-a", name="Throttle A")
        self.customer = Customer.objects.create(
            full_name="Khach Throttle",
            phone_raw="0909333444",
            phone_normalized="0909333444",
        )
        self.user = make_scoped_user("throttle-user", "orders", scope=DataScope.ALL)
        self.other_user = make_scoped_user("throttle-user-2", "orders", scope=DataScope.ALL)
        self.order = Order.objects.create(
            channel=self.channel,
            kiotviet_channel=self.kv,
            customer=self.customer,
            staff=self.user,
            name="Don Throttle",
            phone="0909333444",
            total_amount="100000.00",
            invoice_code="INV-THR-1",
            invoice_id=4001,
        )

    def _get(self, user, url: str):
        self.client.force_authenticate(user=user)
        return self.client.get(url)

    def _post(self, user, url: str, ids):
        self.client.force_authenticate(user=user)
        return self.client.post(url, {"ids": ids}, format="json")

    def test_single_invoice_returns_429_over_rate(self):
        with _throttle_at("2/min"), mock_kiotviet():
            first = self._get(self.user, INVOICE_URL.format(self.order.id))
            second = self._get(self.user, INVOICE_URL.format(self.order.id))
            third = self._get(self.user, INVOICE_URL.format(self.order.id))
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(third.status_code, 429, third.content)

    def test_single_delivery_slip_returns_429_over_rate(self):
        with _throttle_at("1/min"), mock_kiotviet():
            first = self._get(self.user, SLIP_URL.format(self.order.id))
            second = self._get(self.user, SLIP_URL.format(self.order.id))
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 429, second.content)

    def test_bulk_endpoints_return_429_over_rate(self):
        for url in (BULK_INVOICE_URL, BULK_SLIP_URL):
            with self.subTest(url=url), _throttle_at("1/min"), mock_kiotviet():
                first = self._post(self.user, url, [self.order.id])
                second = self._post(self.user, url, [self.order.id])
                self.assertEqual(first.status_code, 200, first.content)
                self.assertEqual(second.status_code, 429, second.content)

    def test_all_four_print_endpoints_share_one_budget(self):
        """Cùng một scope throttle: đổi endpoint không reset hạn mức.

        Nếu mỗi endpoint có bucket riêng thì kẻ lạm dụng chỉ cần xoay vòng 4 URL để
        nhân 4 lần tần suất — trong khi chi phí thật (round-trip KiotViet) là như nhau.
        """
        with _throttle_at("2/min"), mock_kiotviet():
            first = self._get(self.user, INVOICE_URL.format(self.order.id))
            second = self._post(self.user, BULK_INVOICE_URL, [self.order.id])
            third = self._get(self.user, SLIP_URL.format(self.order.id))
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(third.status_code, 429, third.content)

    def test_under_rate_stays_normal(self):
        with _throttle_at("10/min"), mock_kiotviet():
            for _ in range(5):
                resp = self._get(self.user, INVOICE_URL.format(self.order.id))
                self.assertEqual(resp.status_code, 200, resp.content)

    def test_budget_is_per_user_not_shared_across_staff(self):
        """Kho in chung một đường mạng NAT → key phải theo USER, không theo IP.

        Nếu key theo IP, cả kho dùng chung một bucket và người thứ hai bị 429 oan
        ngay khi người thứ nhất in xong lô của mình.
        """
        with _throttle_at("1/min"), mock_kiotviet():
            first = self._get(self.user, INVOICE_URL.format(self.order.id))
            blocked = self._get(self.user, INVOICE_URL.format(self.order.id))
            other = self._get(self.other_user, INVOICE_URL.format(self.order.id))
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(blocked.status_code, 429, blocked.content)
        self.assertEqual(other.status_code, 200, other.content)

    def test_configured_rate_is_active_by_default(self):
        """Scope `order_print` phải có mặt trong DEFAULT_THROTTLE_RATES.

        Không có entry thì `get_rate()` raise ImproperlyConfigured lúc chạy thật —
        lỗi chỉ lộ ra trên production, vì mọi test khác đều patch `rate`.
        """
        throttle = OrderPrintRateThrottle()
        self.assertTrue(throttle.rate)
        self.assertGreater(throttle.num_requests, 0)
        self.assertGreater(throttle.duration, 0)


class DeliverySlipWritePermissionTests(TestCase):
    """In phiếu giao GHI dữ liệu → phải yêu cầu quyền GHI, không phải `{section}:view`.

    `delivery_slip_response` / `bulk_delivery_slip_response` set
    `Order.is_delivery_invoice_printed = True` (invoice.py). Trước fix, 2 endpoint gọi
    chúng chỉ guard `require_section(*ORDER_SECTIONS)` = `{section}:view`, nên một role
    CHỈ ĐỌC (audit viên, thực tập sinh) gọi `POST /api/orders/bulk-delivery-slips` với
    200 id là lật cờ "đã in" của 200 đơn. Kho lọc "chưa in" sẽ BỎ SÓT đúng 200 đơn đó —
    mutate sau quyền đọc, hậu quả vận hành thật (audit 2026-07-26 N3).

    Phương án chọn: (a) yêu cầu quyền ghi cho ĐƯỜNG IN PHIẾU GIAO, giữ cờ gắn liền với
    hành vi in (atomic). Xem docstring `_DELIVERY_SLIP_WRITE_CODES` ở order_export_view
    để biết vì sao KHÔNG tách cờ ra một hành động riêng.

    In HÓA ĐƠN (`/invoice`, `/bulk-invoices`) KHÔNG ghi gì → giữ nguyên `{section}:view`.
    Test khoá cả hai vế để lần sau không ai siết nhầm luôn đường in hóa đơn.

    User tạo bằng `make_scoped_user_by_codes` (cấp lẻ từng permission code, không
    phải cả section): trục đang test là ACTION (view vs edit).
    """

    def setUp(self):
        sync_permissions()
        self.client = APIClient()
        kiotviet_channel_model = django_apps.get_model("kiotviet", "KiotVietChannel")
        self.kv = kiotviet_channel_model.objects.create(
            name="KV Slip Perm", retailer="desi-slip-perm"
        )
        self.channel = Channel.objects.create(code="slip-perm", name="Slip Perm")
        self.customer = Customer.objects.create(
            full_name="Khach Slip",
            phone_raw="0909111222",
            phone_normalized="0909111222",
        )
        self.staff = User.objects.create_user(username="slip-perm-staff", password="x")
        self.order = self._order("INV-SLIP-1", 3001)
        self.order2 = self._order("INV-SLIP-2", 3002)

        # Chỉ đọc: có `orders:view`, KHÔNG có bất kỳ `{section}:edit` nào.
        self.viewer = make_scoped_user_by_codes("slip-viewer", "orders:view", scope=DataScope.ALL)
        # Vận hành: có cả view lẫn edit.
        self.editor = make_scoped_user_by_codes(
            "slip-editor", "orders:view", "orders:edit", scope=DataScope.ALL
        )

    def _order(self, invoice_code: str, invoice_id: int) -> Order:
        return Order.objects.create(
            channel=self.channel,
            kiotviet_channel=self.kv,
            customer=self.customer,
            staff=self.staff,
            name=f"Don {invoice_code}",
            phone="0909111222",
            total_amount="100000.00",
            invoice_code=invoice_code,
            invoice_id=invoice_id,
        )

    def _get(self, user, url: str):
        self.client.force_authenticate(user=user)
        return self.client.get(url)

    def _post(self, user, url: str, ids):
        self.client.force_authenticate(user=user)
        return self.client.post(url, {"ids": ids}, format="json")

    # ── chiều CHẶN: chỉ có quyền đọc ────────────────────────────────────────
    def test_view_only_user_cannot_print_single_delivery_slip(self):
        with mock_kiotviet() as printed:
            resp = self._get(self.viewer, SLIP_URL.format(self.order.id))
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertEqual(printed["invoice_ids"], [])
        self.order.refresh_from_db()
        self.assertFalse(self.order.is_delivery_invoice_printed)

    def test_view_only_user_cannot_print_bulk_delivery_slips(self):
        with mock_kiotviet() as printed:
            resp = self._post(self.viewer, BULK_SLIP_URL, [self.order.id, self.order2.id])
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertEqual(printed["invoice_ids"], [])
        for order in (self.order, self.order2):
            order.refresh_from_db()
            self.assertFalse(order.is_delivery_invoice_printed)

    # ── chiều CHO PHÉP: in hóa đơn KHÔNG ghi gì, giữ quyền đọc ──────────────
    def test_view_only_user_still_prints_single_invoice(self):
        with mock_kiotviet() as printed:
            resp = self._get(self.viewer, INVOICE_URL.format(self.order.id))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(printed["invoice_codes"], [self.order.invoice_code])

    def test_view_only_user_still_prints_bulk_invoices(self):
        with mock_kiotviet() as printed:
            resp = self._post(self.viewer, BULK_INVOICE_URL, [self.order.id])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(printed["invoice_codes"], [self.order.invoice_code])

    # ── chiều CHO PHÉP: có quyền ghi → in được và cờ vẫn được đặt ───────────
    def test_editor_prints_single_delivery_slip_and_marks_printed(self):
        with mock_kiotviet() as printed:
            resp = self._get(self.editor, SLIP_URL.format(self.order.id))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(printed["invoice_ids"], [self.order.invoice_id])
        self.order.refresh_from_db()
        self.assertTrue(self.order.is_delivery_invoice_printed)

    def test_editor_prints_bulk_delivery_slips_and_marks_printed(self):
        with mock_kiotviet() as printed:
            resp = self._post(self.editor, BULK_SLIP_URL, [self.order.id, self.order2.id])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(
            set(printed["invoice_ids"]), {self.order.invoice_id, self.order2.invoice_id}
        )
        for order in (self.order, self.order2):
            order.refresh_from_db()
            self.assertTrue(order.is_delivery_invoice_printed)

    def test_edit_on_any_order_section_is_enough(self):
        """Endpoint dùng chung 4 trang đơn → `koc-orders:edit` cũng mở được đường in.

        Gate phải là HỢP các `{section}:edit`, khớp đúng khuôn của `require_section`
        (vào được endpoint = có BẤT KỲ section nào). Chốt cứng "orders:edit" sẽ chặn
        oan role chỉ vận hành trang KOC/Landing/Outside.
        """
        user = make_scoped_user_by_codes(
            "slip-koc-editor", "koc-orders:view", "koc-orders:edit", scope=DataScope.ALL
        )
        self.order.staff = user
        self.order.save(update_fields=["staff"])
        with mock_kiotviet() as printed:
            resp = self._get(user, SLIP_URL.format(self.order.id))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(printed["invoice_ids"], [self.order.invoice_id])


class DeliverySlipCodTests(TestCase):
    """COD in trên phiếu giao hàng.

    Phiếu giao lấy COD từ ``Debt`` của hoá đơn KiotViet. Đơn KOC "có trả lại" cố ý để
    công nợ trên KiotViet nên ``Debt`` = full tiền hàng — nếu in thẳng thì shipper sẽ
    đi thu tiền của KOC. Đơn KOC phải luôn hiện COD = 0.
    """

    def setUp(self):
        self.koc_channel = Channel.objects.create(code="koc", name="Hang mau KOC")
        self.sale_channel = Channel.objects.create(code="lp-slip", name="Landing")
        self.invoice_data = {"Debt": "250000"}

    def _order(self, channel: Channel, order_type: str) -> Order:
        return Order(channel=channel, order_type=order_type)

    def test_koc_slip_cod_by_order_type(self):
        """Đơn KOC có/không thu hồi → COD 0; đơn KOC thường vẫn lấy nợ KiotViet."""
        for order_type, expected in (
            ("has_return", Decimal("0")),
            ("no_return", Decimal("0")),
            ("normal", Decimal("250000")),
        ):
            with self.subTest(order_type=order_type):
                cod = _resolve_slip_cod(self.invoice_data, self._order(self.koc_channel, order_type))
                self.assertEqual(cod, expected)

    def test_non_koc_channel_keeps_kiotviet_debt(self):
        cod = _resolve_slip_cod(self.invoice_data, self._order(self.sale_channel, "has_return"))
        self.assertEqual(cod, Decimal("250000"))

    def test_missing_order_falls_back_to_kiotviet_debt(self):
        self.assertEqual(_resolve_slip_cod(self.invoice_data, None), Decimal("250000"))

    def test_cod_need_payment_used_when_debt_absent(self):
        cod = _resolve_slip_cod({"CodNeedPayment": "99000"}, self._order(self.sale_channel, "normal"))
        self.assertEqual(cod, Decimal("99000"))


# ─────────────────────────────────────────────────────────────────────────────
# Che SĐT khách trên HOÁ ĐƠN in (audit 2026-09-23 M3).
#
# Trước fix, `invoice.py` lấy `customerPhone` nguyên vẹn từ KiotViet nên sale scope
# ALL không có `customer-phone:view` vẫn đọc được số thật của đơn người khác qua
# `GET /<id>/invoice` hoặc `bulk-invoices` — đường vòng qua toàn bộ luật che ở REST
# (docs/customer-phone-masking.md). Cùng luật: có quyền HOẶC đơn của mình HOẶC
# đơn sàn TMĐT → số thật; còn lại 091****678.
# ─────────────────────────────────────────────────────────────────────────────

_INVOICE_PHONE = "0912345678"
_INVOICE_PHONE_MASKED = "091****678"


@contextmanager
def _mock_kiotviet_invoice_with_phone():
    def fake_invoice(invoice_code, order, channel):
        return {"code": invoice_code, "customerPhone": _INVOICE_PHONE}

    with patch("apps.orders.invoice.get_invoice_by_code", side_effect=fake_invoice):
        yield


class InvoicePhoneMaskingTests(TestCase):
    def setUp(self):
        sync_permissions()
        self.client = APIClient()
        kiotviet_channel_model = django_apps.get_model("kiotviet", "KiotVietChannel")
        self.kv = kiotviet_channel_model.objects.create(name="KV Mask", retailer="desi-mask")
        self.channel = Channel.objects.create(code="mask-a", name="Mask A")
        self.customer = Customer.objects.create(
            full_name="Khach Mask", phone_raw=_INVOICE_PHONE, phone_normalized=_INVOICE_PHONE
        )
        # Sale scope ALL, KHÔNG có customer-phone:view.
        self.sale = make_scoped_user_by_codes("mask-sale", "orders:view", scope=DataScope.ALL)
        # Quản lý: có customer-phone:view.
        self.manager = make_scoped_user_by_codes(
            "mask-manager", "orders:view", "customer-phone:view", scope=DataScope.ALL
        )
        self.other = make_scoped_user_by_codes("mask-other", "orders:view", scope=DataScope.ALL)
        self.own_order = self._order("INV-MASK-OWN", 2001, self.sale)
        self.other_order = self._order("INV-MASK-OTHER", 2002, self.other)
        marketplace_source = sorted(MARKETPLACE_ORDER_SOURCE_CODES)[0]
        self.marketplace_order = self._order(
            "INV-MASK-SHOP", 2003, self.other, order_source=marketplace_source
        )

    def _order(self, invoice_code: str, invoice_id: int, staff: User, **extra) -> Order:
        return Order.objects.create(
            channel=self.channel,
            kiotviet_channel=self.kv,
            customer=self.customer,
            staff=staff,
            name=f"Don {invoice_code}",
            phone=_INVOICE_PHONE,
            total_amount="100000.00",
            invoice_code=invoice_code,
            invoice_id=invoice_id,
            **extra,
        )

    def _invoice_html(self, user, order) -> str:
        self.client.force_authenticate(user=user)
        with _mock_kiotviet_invoice_with_phone():
            resp = self.client.get(INVOICE_URL.format(order.id))
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.content.decode("utf-8")

    def _bulk_html(self, user, orders) -> str:
        self.client.force_authenticate(user=user)
        with _mock_kiotviet_invoice_with_phone():
            resp = self.client.post(
                BULK_INVOICE_URL, {"ids": [o.id for o in orders]}, format="json"
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.content.decode("utf-8")

    def test_sale_without_permission_sees_masked_phone_on_others_invoice(self):
        html = self._invoice_html(self.sale, self.other_order)
        self.assertIn(_INVOICE_PHONE_MASKED, html)
        self.assertNotIn(_INVOICE_PHONE, html)

    def test_sale_sees_full_phone_on_own_invoice(self):
        html = self._invoice_html(self.sale, self.own_order)
        self.assertIn(_INVOICE_PHONE, html)
        self.assertNotIn(_INVOICE_PHONE_MASKED, html)

    def test_manager_with_permission_sees_full_phone_on_others_invoice(self):
        html = self._invoice_html(self.manager, self.other_order)
        self.assertIn(_INVOICE_PHONE, html)

    def test_marketplace_order_keeps_phone_unmasked(self):
        """Đơn sàn TMĐT miễn che — số của sàn, không phải tệp khách của sale."""
        html = self._invoice_html(self.sale, self.marketplace_order)
        self.assertIn(_INVOICE_PHONE, html)

    def test_bulk_invoices_mask_per_order(self):
        html = self._bulk_html(self.sale, [self.own_order, self.other_order])
        # Đơn của mình: số thật; đơn người khác: che — cả hai trong cùng một file.
        self.assertIn(_INVOICE_PHONE, html)
        self.assertIn(_INVOICE_PHONE_MASKED, html)
        self.assertEqual(html.count(_INVOICE_PHONE), 1)
        self.assertEqual(html.count(_INVOICE_PHONE_MASKED), 1)

    def test_builder_without_visibility_fails_closed(self):
        """Quên truyền order/visibility ở call site mới → che, không rò."""
        from apps.orders.invoice import build_invoice_html_from_kiotviet

        html = build_invoice_html_from_kiotviet({"code": "X", "customerPhone": _INVOICE_PHONE})
        self.assertIn(_INVOICE_PHONE_MASKED, html)
        self.assertNotIn(_INVOICE_PHONE, html)
