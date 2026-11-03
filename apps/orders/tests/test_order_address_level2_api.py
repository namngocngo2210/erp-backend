"""Tạo/sửa đơn lưu thêm địa chỉ 2 cấp; Khu vực/Phường xã Kiot tự điền theo 2 cấp."""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderStatus
from apps.orders.application.services.order_service_impl import OrderService
from apps.users.tests.helpers import make_admin

_RAW = "Số 5 ngõ 12 phố Chùa Láng, Láng Thượng, Đống Đa, Hà Nội"


class OrderAddressLevel2ApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-addr-level2")
        self.channel = Channel.objects.create(code="lp-addr2", name="Landing Addr2")
        self.client.force_authenticate(user=self.admin)

    def test_create_order_stores_level2_and_kiotviet_names_in_new_format(self):
        response = self.client.post(
            "/api/orders/create",
            {
                "channel_code": self.channel.code,
                "name": "Khach Addr2",
                "phone": "0909 123 456",
                "raw_address": _RAW,
                "product": "SKU01 x1",
                "status": OrderStatus.NEW,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)

        order = Order.objects.get(channel=self.channel)
        # 3 cấp giữ nguyên
        self.assertIn("Đống Đa", order.district)
        self.assertIn("Láng Thượng", order.ward)
        # 2 cấp lưu thêm
        self.assertEqual(order.province_level2, "Thành phố Hà Nội")
        self.assertEqual(order.ward_level2, "Phường Láng")
        self.assertEqual(order.province_code_level2, "01")
        self.assertTrue(order.address_level2.endswith("Phường Láng, Thành phố Hà Nội"))
        # Khu vực/Phường xã Kiot theo cấu trúc mới → hoá đơn Kiot ra 2 cấp
        self.assertEqual(order.kiotviet_location, "Thành phố Hà Nội")
        self.assertEqual(order.kiotviet_ward, "Phường Láng")

        detail = self.client.get(f"/api/orders/{order.id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["ward_level2"], "Phường Láng")
        self.assertEqual(detail.data["address_level2"], order.address_level2)

    def test_patch_keeps_explicit_kiotviet_choice(self):
        """Sale chọn tay Khu vực/Phường xã cùng lúc sửa địa chỉ thô → không bị 2 cấp ghi đè."""
        order = self._order("0909111334")
        response = self.client.patch(
            f"/api/orders/{order.id}",
            {
                "raw_address": _RAW,
                "kiotviet_location": "Hà Nội - Quận Đống Đa",
                "kiotviet_ward": "Phường Láng Thượng",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.kiotviet_location, "Hà Nội - Quận Đống Đa")
        self.assertEqual(order.kiotviet_ward, "Phường Láng Thượng")
        # cột 2 cấp vẫn tính lại theo địa chỉ thô mới
        self.assertEqual(order.ward_level2, "Phường Láng")

    def _order(self, phone: str) -> Order:
        customer = Customer.objects.create(
            full_name="Khach Patch", phone_raw=phone, phone_normalized=phone
        )
        return Order.objects.create(
            channel=self.channel,
            customer=customer,
            name="Khach Patch",
            phone=phone,
            status=OrderStatus.NEW,
        )

    def test_patch_raw_address_reresolves_level2(self):
        order = self._order("0909111333")

        response = self.client.patch(
            f"/api/orders/{order.id}",
            {"raw_address": "Thôn 3, xã Ea Kly, huyện Krông Pắc, Đắk Lắk"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

        order.refresh_from_db()
        self.assertEqual(order.province_level2, "Tỉnh Đắk Lắk")
        self.assertEqual(order.ward_level2, "Xã Ea Kly")
        self.assertEqual(order.kiotviet_location, "Tỉnh Đắk Lắk")
        self.assertEqual(order.kiotviet_ward, "Xã Ea Kly")
        self.assertEqual(response.data["ward_level2"], "Xã Ea Kly")

    def test_parse_address_endpoint_returns_level2_and_new_kiotviet_names(self):
        response = self.client.post("/api/orders/parse-address", {"raw_address": _RAW}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["district"], "Quận Đống Đa")
        self.assertEqual(response.data["ward_level2"], "Phường Láng")
        self.assertEqual(response.data["province_level2"], "Thành phố Hà Nội")
        self.assertEqual(response.data["kiotviet_location"], "Thành phố Hà Nội")
        self.assertEqual(response.data["kiotviet_ward"], "Phường Láng")

    def test_landing_ingest_stores_level2_and_new_kiotviet_names(self):
        """Đơn đổ về từ API ingest (landing/Lark/Pancake) đi qua create_order_from_landing."""
        order = OrderService.create_order_from_landing(
            {
                "channel_id": self.channel.id,
                "phone": "0909 123 458",
                "name": "Khach Ingest",
                "raw_address": "Thôn 3, xã Ea Kly, huyện Krông Pắc, Đắk Lắk",
                "product": "SKU01 x1",
            }
        )

        self.assertEqual(order.province_level2, "Tỉnh Đắk Lắk")
        self.assertEqual(order.ward_level2, "Xã Ea Kly")
        self.assertEqual(order.kiotviet_location, "Tỉnh Đắk Lắk")
        self.assertEqual(order.kiotviet_ward, "Xã Ea Kly")

    def test_patch_address_level2_reresolves_ward_province_and_kiotviet(self):
        """Sửa tay Địa chỉ 2 cấp → tỉnh/phường mới + Khu vực/Phường xã Kiot đổi theo."""
        order = self._order("0909111335")
        order.kiotviet_location = "Hà Nội - Quận Đống Đa"
        order.kiotviet_ward = "Phường Láng Thượng"
        order.save(update_fields=["kiotviet_location", "kiotviet_ward"])

        response = self.client.patch(
            f"/api/orders/{order.id}",
            {"address_level2": "Thôn 3, xã Ea Kly, Tỉnh Đắk Lắk"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

        order.refresh_from_db()
        self.assertEqual(order.address_level2, "Thôn 3, xã Ea Kly, Tỉnh Đắk Lắk")
        self.assertEqual(order.province_level2, "Tỉnh Đắk Lắk")
        self.assertEqual(order.ward_level2, "Xã Ea Kly")
        self.assertEqual(order.kiotviet_location, "Tỉnh Đắk Lắk")
        self.assertEqual(order.kiotviet_ward, "Xã Ea Kly")
        self.assertEqual(response.data["ward_level2"], "Xã Ea Kly")

    def test_patch_address_level2_unmatched_keeps_old_kiotviet(self):
        """Gõ sai không tra được Kiot → giữ Khu vực/Phường xã cũ, không xoá trống."""
        order = self._order("0909111336")
        order.kiotviet_location = "Hà Nội - Quận Đống Đa"
        order.kiotviet_ward = "Phường Láng Thượng"
        order.save(update_fields=["kiotviet_location", "kiotviet_ward"])

        response = self.client.patch(
            f"/api/orders/{order.id}", {"address_level2": "abc xyz"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)

        order.refresh_from_db()
        self.assertEqual(order.address_level2, "abc xyz")
        self.assertEqual(order.ward_level2, "")
        self.assertEqual(order.kiotviet_location, "Hà Nội - Quận Đống Đa")
        self.assertEqual(order.kiotviet_ward, "Phường Láng Thượng")

    def test_patch_address_level2_respects_explicit_kiotviet(self):
        order = self._order("0909111337")
        response = self.client.patch(
            f"/api/orders/{order.id}",
            {
                "address_level2": "Thôn 3, xã Ea Kly, Tỉnh Đắk Lắk",
                "kiotviet_location": "Đắk Lắk - Huyện Krông Pắc",
                "kiotviet_ward": "Xã Ea Kly",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.kiotviet_location, "Đắk Lắk - Huyện Krông Pắc")
        self.assertEqual(order.ward_level2, "Xã Ea Kly")

    def test_patch_address_level2_wins_over_raw_address_in_same_payload(self):
        """Gửi cả hai: 2 cấp lấy theo ô sale sửa tay, 3 cấp vẫn suy từ địa chỉ thô."""
        order = self._order("0909111338")
        response = self.client.patch(
            f"/api/orders/{order.id}",
            {"raw_address": _RAW, "address_level2": "Thôn 3, xã Ea Kly, Tỉnh Đắk Lắk"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertIn("Đống Đa", order.address)
        self.assertEqual(order.address_level2, "Thôn 3, xã Ea Kly, Tỉnh Đắk Lắk")
        self.assertEqual(order.ward_level2, "Xã Ea Kly")
        self.assertEqual(order.kiotviet_location, "Tỉnh Đắk Lắk")

    def test_parse_address_level2_endpoint(self):
        response = self.client.post(
            "/api/orders/parse-address-level2",
            {"address_level2": "Thôn 3, xã Ea Kly, Tỉnh Đắk Lắk"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["address_level2"], "Thôn 3, xã Ea Kly, Tỉnh Đắk Lắk")
        self.assertEqual(response.data["ward_level2"], "Xã Ea Kly")
        self.assertEqual(response.data["province_level2"], "Tỉnh Đắk Lắk")
        self.assertEqual(response.data["kiotviet_location"], "Tỉnh Đắk Lắk")
        self.assertEqual(response.data["kiotviet_ward"], "Xã Ea Kly")
