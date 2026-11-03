import json
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from apps.channels_core.models import Channel, ChannelKiotvietMapping
from apps.customers.models import Customer
from apps.kiotviet.models import KiotVietChannel
from apps.orders.models import Order, OrderItem
from apps.catalog.models import Product


class _MockResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        if payload is None:
            self.content = b""
            self.text = ""
        else:
            self.text = json.dumps(payload, ensure_ascii=False)
            self.content = self.text.encode("utf-8")

    def json(self):
        return self._payload


class ImportKiotVietInvoicesCommandTests(TestCase):
    def setUp(self):
        self.channel = KiotVietChannel.objects.create(
            name="KV Import",
            branch_id="231174",
            branch_ids=["231174", "231175"],
            client_id="kv-client-id",
            client_secret="kv-client-secret",
            retailer="kv-retailer",
            old=False,
        )
        self.new_sale_channel = Channel.objects.create(
            code="sales-new",
            name="Sales New",
            owner="kiotviet",
        )
        self.old_sale_channel = Channel.objects.create(
            code="sales-old",
            name="Sales Old",
            owner="kiotviet",
        )
        # Mapping: new_sale_channel <-> self.channel (KV new) = "101"
        ChannelKiotvietMapping.objects.create(
            channel=self.new_sale_channel,
            kiotviet_channel=self.channel,
            external_sale_channel_id="101",
        )

    @staticmethod
    def _run_command(channel_id, branch_ids, dry_run=False, stdout=None):
        args = [
            "import_kiotviet_invoices",
            "--channel-id",
            str(channel_id),
            "--branch-ids",
            branch_ids,
        ]
        if dry_run:
            args.append("--dry-run")
        call_command(*args, stdout=stdout or StringIO())

    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_pagination_uses_page_size_100_and_current_item_offset(self, mock_request):
        page_1 = {
            "data": [
                {"id": idx + 1, "code": f"INV-{idx + 1}", "invoiceDelivery": {"address": "A"}}
                for idx in range(100)
            ]
        }
        page_2 = {
            "data": [
                {"id": idx + 101, "code": f"INV-{idx + 101}", "invoiceDelivery": {"address": "A"}}
                for idx in range(100)
            ]
        }
        page_3 = {"data": []}

        mock_request.side_effect = [_MockResponse(page_1), _MockResponse(page_2), _MockResponse(page_3)]

        self._run_command(self.channel.id, "231174,231175", dry_run=True)

        self.assertEqual(mock_request.call_count, 3)
        expected_offsets = [0, 100, 200]
        for index, expected_offset in enumerate(expected_offsets):
            params = mock_request.call_args_list[index].kwargs["params"]
            self.assertEqual(params["pageSize"], 100)
            self.assertEqual(params["currentItem"], expected_offset)
            self.assertEqual(params["branchIds"], "231174,231175")

    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_dry_run_does_not_write_database(self, mock_request):
        payload = {
            "data": [
                {
                    "id": 1,
                    "code": "INV-DRY-1",
                    "customerId": 1001,
                    "customerCode": "C-1001",
                    "customerName": "Dry User",
                    "customerPhone": "0909000111",
                    "invoiceDelivery": {
                        "address": "1 Test St",
                        "wardName": "Phuong 1",
                        "locationName": "Ho Chi Minh",
                    },
                    "invoiceDetails": [
                        {"productCode": "SKU-DRY", "productName": "Dry Product", "price": 10000, "quantity": 1}
                    ],
                }
            ]
        }
        mock_request.return_value = _MockResponse(payload)

        self._run_command(self.channel.id, "231174", dry_run=True)

        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(Customer.objects.count(), 0)
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(OrderItem.objects.count(), 0)

    @patch("apps.kiotviet.infrastructure.services.import_invoices_service.map_to_catalog")
    @patch("apps.kiotviet.infrastructure.services.import_invoices_service.parse_legacy_level3")
    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_address_mapping_persists_text_and_codes(self, mock_request, mock_parse, mock_map):
        mock_parse.return_value = {
            "address": "12 Nguyen Trai",
            "ward": "Phuong Ben Thanh",
            "district": "Quan 1",
            "province": "Ho Chi Minh",
            "ward_code": "W_PARSE",
            "district_code": "D_PARSE",
            "province_code": "P_PARSE",
        }
        mock_map.return_value = {
            "ward": "Phuong Ben Thanh",
            "district": "Quan 1",
            "province": "Ho Chi Minh",
            "ward_code": "W001",
            "district_code": "D001",
            "province_code": "P079",
        }
        mock_request.return_value = _MockResponse(
            {
                "data": [
                    {
                        "id": 2,
                        "code": "INV-ADDR-1",
                        "customerId": 2002,
                        "customerCode": "C-2002",
                        "customerName": "Address User",
                        "customerPhone": "0909000222",
                        "soldById": 10,
                        "branchId": "231174",
                        "createdDate": "2026-01-02T10:00:00+07:00",
                        "modifiedDate": "2026-01-02T10:30:00+07:00",
                        "invoiceDelivery": {
                            "price": 15000,
                            "deliveryCode": "GHN123",
                            "status": 1,
                            "address": "12 Nguyen Trai",
                            "wardName": "Phuong Ben Thanh",
                            "locationName": "Ho Chi Minh",
                            "weight": 0.5,
                            "length": 10,
                            "width": 20,
                            "height": 30,
                        },
                        "invoiceDetails": [
                            {"productCode": "SKU-ADDR", "productName": "Address Product", "price": 120000, "quantity": 2}
                        ],
                    }
                ]
            }
        )

        self._run_command(self.channel.id, "231174", dry_run=False)

        order = Order.objects.get(invoice_code="INV-ADDR-1")
        self.assertEqual(order.address, "12 Nguyen Trai")
        self.assertEqual(order.ward, "Phuong Ben Thanh")
        self.assertEqual(order.district, "Quan 1")
        self.assertEqual(order.province, "Ho Chi Minh")
        self.assertEqual(order.ward_code, "W001")
        self.assertEqual(order.district_code, "D001")
        self.assertEqual(order.province_code, "P079")
        self.assertEqual(order.dimensions, "10x20x30")

    @patch("apps.kiotviet.infrastructure.services.import_invoices_service.map_to_catalog")
    @patch("apps.kiotviet.infrastructure.services.import_invoices_service.parse_legacy_level3")
    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_address_mapping_fallback_when_location_or_ward_missing(self, mock_request, mock_parse, mock_map):
        mock_parse.return_value = {
            "address": "99 Fallback St",
            "ward": "",
            "district": "",
            "province": "",
            "ward_code": "",
            "district_code": "",
            "province_code": "",
        }
        mock_map.return_value = {}
        mock_request.return_value = _MockResponse(
            {
                "data": [
                    {
                        "id": 3,
                        "code": "INV-ADDR-2",
                        "customerId": 2003,
                        "customerCode": "C-2003",
                        "customerName": "Fallback User",
                        "invoiceDelivery": {
                            "address": "99 Fallback St",
                            "locationName": "Can Tho",
                        },
                        "invoiceDetails": [
                            {"productCode": "SKU-FB", "productName": "Fallback Product", "price": 99000, "quantity": 1}
                        ],
                    }
                ]
            }
        )

        self._run_command(self.channel.id, "231174", dry_run=False)

        order = Order.objects.get(invoice_code="INV-ADDR-2")
        self.assertEqual(order.address, "99 Fallback St")
        self.assertEqual(order.ward, "")
        self.assertEqual(order.province, "Can Tho")
        self.assertEqual(order.province_code, "")

    @patch("apps.kiotviet.infrastructure.services.import_invoices_service.map_to_catalog", return_value={})
    @patch(
        "apps.kiotviet.infrastructure.services.import_invoices_service.parse_legacy_level3",
        return_value={
            "address": "",
            "ward": "",
            "district": "",
            "province": "",
            "ward_code": "",
            "district_code": "",
            "province_code": "",
        },
    )
    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_sold_by_mapping_uses_new_or_old_kiot_id_by_channel_mode(self, mock_request, *_):
        from apps.users.models import UserKiotvietMapping
        UserModel = get_user_model()
        user_new = UserModel.objects.create_user(
            username="user-new",
            password="pass",
        )
        user_old = UserModel.objects.create_user(
            username="user-old",
            password="pass",
        )
        old_channel = KiotVietChannel.objects.create(
            name="KV Old",
            branch_id="231175",
            branch_ids=["231175"],
            client_id="old-client",
            client_secret="old-secret",
            retailer="kv-old",
            old=True,
        )
        UserKiotvietMapping.objects.create(
            user=user_new,
            kiotviet_channel=self.channel,
            external_kiot_user_id="111",
        )
        UserKiotvietMapping.objects.create(
            user=user_old,
            kiotviet_channel=old_channel,
            external_kiot_user_id="222",
        )

        mock_request.return_value = _MockResponse(
            {
                "data": [
                    {
                        "id": 4,
                        "code": "INV-STAFF-NEW",
                        "customerId": 3001,
                        "customerCode": "C-3001",
                        "customerName": "Staff New",
                        "soldById": 111,
                        "invoiceDelivery": {"address": "A"},
                        "invoiceDetails": [
                            {"productCode": "SKU-STAFF-1", "productName": "P1", "price": 1000, "quantity": 1}
                        ],
                    }
                ]
            }
        )
        self._run_command(self.channel.id, "231174", dry_run=False)
        order_new = Order.objects.get(invoice_code="INV-STAFF-NEW")
        self.assertEqual(order_new.staff_id, user_new.id)

        mock_request.return_value = _MockResponse(
            {
                "data": [
                    {
                        "id": 5,
                        "code": "INV-STAFF-OLD",
                        "customerId": 3002,
                        "customerCode": "C-3002",
                        "customerName": "Staff Old",
                        "soldById": 222,
                        "invoiceDelivery": {"address": "B"},
                        "invoiceDetails": [
                            {"productCode": "SKU-STAFF-2", "productName": "P2", "price": 2000, "quantity": 1}
                        ],
                    }
                ]
            }
        )
        self._run_command(old_channel.id, "231175", dry_run=False)
        order_old = Order.objects.get(invoice_code="INV-STAFF-OLD")
        self.assertEqual(order_old.staff_id, user_old.id)

    @patch("apps.kiotviet.infrastructure.services.import_invoices_service.map_to_catalog", return_value={})
    @patch(
        "apps.kiotviet.infrastructure.services.import_invoices_service.parse_legacy_level3",
        return_value={
            "address": "",
            "ward": "",
            "district": "",
            "province": "",
            "ward_code": "",
            "district_code": "",
            "province_code": "",
        },
    )
    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_product_customer_auto_create_and_idempotent_rerun(self, mock_request, *_):
        invoice_v1 = {
            "id": 6,
            "code": "INV-IDEMP-1",
            "customerId": 4001,
            "customerCode": "C-4001",
            "customerName": "Idempotent User",
            "customerPhone": "0909000666",
            "invoiceDelivery": {
                "address": "6 Rerun St",
                "price": 10000,
                "status": 1,
            },
            "invoiceDetails": [
                {"productCode": "SKU-IDEMP", "productName": "Product V1", "price": 100000, "quantity": 1}
            ],
        }

        invoice_v2 = {
            "id": 6,
            "code": "INV-IDEMP-1",
            "customerId": 4001,
            "customerCode": "C-4001",
            "customerName": "Idempotent User",
            "customerPhone": "0909000666",
            "total": 240000,
            "invoiceDelivery": {
                "address": "6 Rerun St",
                "price": 20000,
                "status": 2,
            },
            "invoiceDetails": [
                {"productCode": "SKU-IDEMP", "productName": "Product V2", "price": 120000, "quantity": 2}
            ],
        }

        mock_request.return_value = _MockResponse({"data": [invoice_v1]})
        self._run_command(self.channel.id, "231174", dry_run=False)

        self.assertEqual(Order.objects.filter(invoice_code="INV-IDEMP-1").count(), 1)
        self.assertEqual(Customer.objects.filter(code="C-4001").count(), 1)
        self.assertEqual(Product.objects.filter(sku="SKU-IDEMP").count(), 1)

        mock_request.return_value = _MockResponse({"data": [invoice_v2]})
        self._run_command(self.channel.id, "231174", dry_run=False)

        order = Order.objects.get(invoice_code="INV-IDEMP-1")
        self.assertEqual(Order.objects.filter(invoice_code="INV-IDEMP-1").count(), 1)
        self.assertEqual(order.shipping_fee, 20000)
        self.assertEqual(order.status, 2)
        self.assertEqual(order.quantity, 2)
        # total_amount lấy từ field "total" của invoice, KHÔNG tính từ invoice details.
        self.assertEqual(order.total_amount, 240000)

        product = Product.objects.get(sku="SKU-IDEMP")
        # Rerun chỉ cập nhật tên sản phẩm; giá giữ nguyên giá lúc tạo (price chỉ là default khi tạo mới).
        self.assertEqual(product.name, "Product V2")
        self.assertEqual(product.price, 100000)
        self.assertEqual(OrderItem.objects.filter(order=order).count(), 1)

    @patch("apps.kiotviet.infrastructure.services.import_invoices_service.map_to_catalog", return_value={})
    @patch(
        "apps.kiotviet.infrastructure.services.import_invoices_service.parse_legacy_level3",
        return_value={
            "address": "",
            "ward": "",
            "district": "",
            "province": "",
            "ward_code": "",
            "district_code": "",
            "province_code": "",
        },
    )
    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_sale_channel_id_maps_by_new_or_old_channel_mode(self, mock_request, *_):
        old_channel = KiotVietChannel.objects.create(
            name="KV Old For Sale Channel",
            branch_id="231175",
            branch_ids=["231175"],
            client_id="old-client-sale-channel",
            client_secret="old-secret-sale-channel",
            retailer="kv-old-sale-channel",
            old=True,
        )
        ChannelKiotvietMapping.objects.create(
            channel=self.old_sale_channel,
            kiotviet_channel=old_channel,
            external_sale_channel_id="202",
        )

        mock_request.return_value = _MockResponse(
            {
                "data": [
                    {
                        "id": 7,
                        "code": "INV-SALE-NEW",
                        "customerId": 7001,
                        "customerCode": "C-7001",
                        "customerName": "Sale New",
                        "saleChannelId": 101,
                        "invoiceDelivery": {"address": "New Addr"},
                        "invoiceDetails": [
                            {"productCode": "SKU-SALE-NEW", "productName": "Sale New Product", "price": 1000, "quantity": 1}
                        ],
                    }
                ]
            }
        )
        self._run_command(self.channel.id, "231174", dry_run=False)
        order_new = Order.objects.get(invoice_code="INV-SALE-NEW")
        self.assertEqual(order_new.channel_id, self.new_sale_channel.id)

        mock_request.return_value = _MockResponse(
            {
                "data": [
                    {
                        "id": 8,
                        "code": "INV-SALE-OLD",
                        "customerId": 8001,
                        "customerCode": "C-8001",
                        "customerName": "Sale Old",
                        "saleChannelId": 202,
                        "invoiceDelivery": {"address": "Old Addr"},
                        "invoiceDetails": [
                            {"productCode": "SKU-SALE-OLD", "productName": "Sale Old Product", "price": 2000, "quantity": 1}
                        ],
                    }
                ]
            }
        )
        self._run_command(old_channel.id, "231175", dry_run=False)
        order_old = Order.objects.get(invoice_code="INV-SALE-OLD")
        self.assertEqual(order_old.channel_id, self.old_sale_channel.id)

    @patch("apps.kiotviet.infrastructure.services.import_invoices_service.map_to_catalog", return_value={})
    @patch(
        "apps.kiotviet.infrastructure.services.import_invoices_service.parse_legacy_level3",
        return_value={
            "address": "",
            "ward": "",
            "district": "",
            "province": "",
            "ward_code": "",
            "district_code": "",
            "province_code": "",
        },
    )
    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_old_channel_updates_existing_order_by_shipping_code(self, mock_request, *_):
        old_channel = KiotVietChannel.objects.create(
            name="KV Old Update Shipping",
            branch_id="231175",
            branch_ids=["231175"],
            client_id="old-client-update",
            client_secret="old-secret-update",
            retailer="kv-old-update",
            old=True,
        )
        customer = Customer.objects.create(full_name="Existing User", phone_raw="", phone_normalized="")
        existing_order = Order.objects.create(
            channel=self.new_sale_channel,
            customer=customer,
            shipping_code="DELIVERY-OLD-001",
            invoice_code="",
            invoice_id=None,
            old_invoice_code="",
            old_id=None,
            external_order_id="existing-order",
        )

        mock_request.return_value = _MockResponse(
            {
                "data": [
                    {
                        "id": 9001,
                        "code": "INV-OLD-SHIP-001",
                        "customerId": 9001,
                        "customerCode": "C-9001",
                        "customerName": "Old Shipping User",
                        "invoiceDelivery": {
                            "deliveryCode": "DELIVERY-OLD-001",
                            "address": "Old Addr",
                            "status": 2,
                        },
                        "invoiceDetails": [
                            {"productCode": "SKU-OLD-SHIP", "productName": "Old Ship Product", "price": 1000, "quantity": 1}
                        ],
                    }
                ]
            }
        )

        self._run_command(old_channel.id, "231175", dry_run=False)

        self.assertEqual(Order.objects.count(), 1)
        existing_order.refresh_from_db()
        self.assertEqual(existing_order.invoice_code, "")
        self.assertIsNone(existing_order.invoice_id)
        self.assertEqual(existing_order.old_id, 9001)
        self.assertEqual(existing_order.old_invoice_code, "INV-OLD-SHIP-001")
        self.assertEqual(existing_order.shipping_code, "DELIVERY-OLD-001")

    @patch("apps.kiotviet.infrastructure.services.import_invoices_service.map_to_catalog", return_value={})
    @patch(
        "apps.kiotviet.infrastructure.services.import_invoices_service.parse_legacy_level3",
        return_value={
            "address": "",
            "ward": "",
            "district": "",
            "province": "",
            "ward_code": "",
            "district_code": "",
            "province_code": "",
        },
    )
    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_old_channel_creates_new_order_and_sets_old_fields_when_shipping_not_found(self, mock_request, *_):
        old_channel = KiotVietChannel.objects.create(
            name="KV Old Create Shipping",
            branch_id="231176",
            branch_ids=["231176"],
            client_id="old-client-create",
            client_secret="old-secret-create",
            retailer="kv-old-create",
            old=True,
        )

        mock_request.return_value = _MockResponse(
            {
                "data": [
                    {
                        "id": 9002,
                        "code": "INV-OLD-SHIP-002",
                        "customerId": 9002,
                        "customerCode": "C-9002",
                        "customerName": "Old Shipping New User",
                        "invoiceDelivery": {
                            "deliveryCode": "DELIVERY-OLD-002",
                            "address": "New Addr",
                            "status": 1,
                        },
                        "invoiceDetails": [
                            {"productCode": "SKU-OLD-SHIP-2", "productName": "Old Ship Product 2", "price": 2000, "quantity": 1}
                        ],
                    }
                ]
            }
        )

        self._run_command(old_channel.id, "231176", dry_run=False)

        order = Order.objects.get(shipping_code="DELIVERY-OLD-002")
        self.assertEqual(order.invoice_code, "INV-OLD-SHIP-002")
        self.assertEqual(order.invoice_id, 9002)
        self.assertEqual(order.old_invoice_code, "INV-OLD-SHIP-002")
        self.assertEqual(order.old_id, 9002)

    @patch("apps.kiotviet.infrastructure.services.import_invoices_service.map_to_catalog", return_value={})
    @patch(
        "apps.kiotviet.infrastructure.services.import_invoices_service.parse_legacy_level3",
        return_value={
            "address": "",
            "ward": "",
            "district": "",
            "province": "",
            "ward_code": "",
            "district_code": "",
            "province_code": "",
        },
    )
    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_masked_phone_does_not_fail_and_creates_customer_without_normalized(self, mock_request, *_):
        # TikTok Shop che SĐT người mua: "(+84)037*****54" → normalize_phone trả "".
        # Import phải tạo được order/customer thay vì raise "Invalid phone payload".
        mock_request.return_value = _MockResponse(
            {
                "data": [
                    {
                        "id": 9100,
                        "code": "INV-MASKED-PHONE",
                        "customerCode": "KHTTS7496161100889950466.1",
                        "customerName": "t*** n***ễn q** t***",
                        "invoiceDelivery": {
                            "address": "Masked Addr",
                            "contactNumber": "(+84)037*****54",
                            "status": 1,
                        },
                        "invoiceDetails": [
                            {"productCode": "SKU-MASKED", "productName": "Masked Product", "price": 1000, "quantity": 1}
                        ],
                    }
                ]
            }
        )

        self._run_command(self.channel.id, "231174", dry_run=False)

        order = Order.objects.get(invoice_code="INV-MASKED-PHONE")
        self.assertEqual(order.phone, "(+84)037*****54")
        customer = order.customer
        self.assertIsNotNone(customer)
        self.assertEqual(customer.phone_raw, "(+84)037*****54")
        self.assertEqual(customer.phone_normalized, "")
        self.assertEqual(customer.code, "KHTTS7496161100889950466.1")

    @patch("apps.kiotviet.infrastructure.services.import_invoices_service.map_to_catalog", return_value={})
    @patch(
        "apps.kiotviet.infrastructure.services.import_invoices_service.parse_legacy_level3",
        return_value={
            "address": "", "ward": "", "district": "", "province": "",
            "ward_code": "", "district_code": "", "province_code": "",
        },
    )
    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_create_only_skips_existing_and_creates_new(self, mock_request, *_):
        existing_invoice = {
            "id": 5001, "code": "INV-CO-EXIST", "customerId": 5001,
            "customerCode": "C-5001", "customerName": "Existing CO",
            "invoiceDelivery": {"address": "A", "status": 1},
            "invoiceDetails": [{"productCode": "SKU-CO-1", "productName": "P1", "price": 1000, "quantity": 1}],
        }
        # First run (normal) creates it.
        mock_request.return_value = _MockResponse({"data": [existing_invoice]})
        self._run_command(self.channel.id, "231174", dry_run=False)
        self.assertEqual(Order.objects.filter(invoice_code="INV-CO-EXIST").count(), 1)

        # Second run with --create-only: existing skipped, new one created.
        new_invoice = {
            "id": 5002, "code": "INV-CO-NEW", "customerId": 5002,
            "customerCode": "C-5002", "customerName": "New CO",
            "invoiceDelivery": {"address": "B", "status": 1},
            "invoiceDetails": [{"productCode": "SKU-CO-2", "productName": "P2", "price": 2000, "quantity": 1}],
        }
        mock_request.return_value = _MockResponse({"data": [existing_invoice, new_invoice]})
        out = StringIO()
        call_command(
            "import_kiotviet_invoices",
            "--channel-id", str(self.channel.id),
            "--branch-ids", "231174",
            "--create-only",
            stdout=out,
        )
        self.assertEqual(Order.objects.filter(invoice_code="INV-CO-EXIST").count(), 1)
        self.assertEqual(Order.objects.filter(invoice_code="INV-CO-NEW").count(), 1)
        summary = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["skipped"], 1)

    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_cancelled_invoice_without_delivery_is_created_with_cancelled_status(self, mock_request):
        # Đơn huỷ (status=2) chưa có trong DB → vẫn được import, invoice_status=2,
        # không có invoiceDelivery.status → Order.status = CANCELLED (6).
        mock_request.return_value = _MockResponse(
            {
                "data": [
                    {
                        "id": 7001,
                        "code": "INV-CANCELLED-1",
                        "status": 2,
                        "customerId": 7001,
                        "customerCode": "C-7001",
                        "customerName": "Cancelled User",
                        "customerPhone": "0909000777",
                        "invoiceDetails": [
                            {"productCode": "SKU-CX", "productName": "PX", "price": 1000, "quantity": 1}
                        ],
                    }
                ]
            }
        )

        self._run_command(self.channel.id, "231174", dry_run=False)

        order = Order.objects.get(invoice_code="INV-CANCELLED-1")
        self.assertEqual(order.invoice_status, 2)
        self.assertEqual(order.status, 6)  # OrderStatus.CANCELLED

    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_cancelled_invoice_with_delivery_status_keeps_delivery_status(self, mock_request):
        # Đơn huỷ nhưng đã có vận đơn → Order.status giữ theo invoiceDelivery.status.
        mock_request.return_value = _MockResponse(
            {
                "data": [
                    {
                        "id": 7002,
                        "code": "INV-CANCELLED-2",
                        "status": 2,
                        "customerId": 7002,
                        "customerCode": "C-7002",
                        "customerName": "Cancelled Delivery User",
                        "customerPhone": "0909000778",
                        "invoiceDelivery": {"address": "A", "status": 1, "deliveryCode": "SHIP-CX-2"},
                        "invoiceDetails": [
                            {"productCode": "SKU-CY", "productName": "PY", "price": 2000, "quantity": 1}
                        ],
                    }
                ]
            }
        )

        self._run_command(self.channel.id, "231174", dry_run=False)

        order = Order.objects.get(invoice_code="INV-CANCELLED-2")
        self.assertEqual(order.invoice_status, 2)
        self.assertEqual(order.status, 1)

    @patch("apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request")
    def test_create_only_imports_missing_cancelled_invoice(self, mock_request):
        # --create-only (cron) cũng phải tạo được đơn huỷ chưa có trong DB.
        mock_request.return_value = _MockResponse(
            {
                "data": [
                    {
                        "id": 7003,
                        "code": "INV-CANCELLED-3",
                        "status": 2,
                        "customerId": 7003,
                        "customerCode": "C-7003",
                        "customerName": "Cancelled CO User",
                        "customerPhone": "0909000779",
                        "invoiceDetails": [
                            {"productCode": "SKU-CZ", "productName": "PZ", "price": 3000, "quantity": 1}
                        ],
                    }
                ]
            }
        )

        out = StringIO()
        call_command(
            "import_kiotviet_invoices",
            "--channel-id", str(self.channel.id),
            "--branch-ids", "231174",
            "--create-only",
            stdout=out,
        )

        order = Order.objects.get(invoice_code="INV-CANCELLED-3")
        self.assertEqual(order.invoice_status, 2)
        summary = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(summary["created"], 1)


class CheckChannelMatchScopeTests(TestCase):
    """--check-channel-match: phạm vi sửa và cách xử lý sale channel không map được.

    Kênh fallback ``kiotviet-*`` là nơi đơn rơi vào khi KV trả saleChannelId lạ.
    Backfill về kênh thật chỉ an toàn khi (a) không đụng đơn ngoài phạm vi và
    (b) không dồn đơn của shop chưa khai báo vào kênh gom (audit 2026-08-23).
    """

    def setUp(self):
        self.kv = KiotVietChannel.objects.create(
            name="Old Kiot",
            branch_id="30965",
            branch_ids=["30965"],
            client_id="kv-client-id",
            client_secret="kv-client-secret",
            retailer="jiashi",
            old=False,
        )
        self.fallback_channel = Channel.objects.create(
            code=f"kiotviet-{self.kv.id}", name="KiotViet Old Kiot", owner="kiotviet"
        )
        self.shop_channel = Channel.objects.create(code="shop-a", name="Shop A", owner="Shop A")
        self.misc_channel = Channel.objects.create(code="khac", name="Khác", owner="Khác")
        ChannelKiotvietMapping.objects.create(
            channel=self.shop_channel, kiotviet_channel=self.kv, external_sale_channel_id="777"
        )
        self.customer = Customer.objects.create(
            full_name="Check Match User", phone_raw="", phone_normalized=""
        )

    def _make_order(self, *, invoice_code, invoice_id, channel):
        return Order.objects.create(
            channel=channel,
            customer=self.customer,
            kiotviet_channel=self.kv,
            invoice_code=invoice_code,
            invoice_id=invoice_id,
            external_order_id=invoice_code,
        )

    @staticmethod
    def _invoice(*, invoice_id, code, sale_channel_id=None):
        payload = {"id": invoice_id, "code": code}
        if sale_channel_id is not None:
            payload["saleChannelId"] = sale_channel_id
        return payload

    def _run(self, invoices, *, extra_args=(), stdout=None):
        out = stdout or StringIO()
        with patch(
            "apps.kiotviet.infrastructure.services._import_service_handlers_mixin._kiotviet_request"
        ) as mock_request:
            mock_request.side_effect = [_MockResponse({"data": invoices}), _MockResponse({"data": []})]
            call_command(
                "import_kiotviet_invoices",
                "--channel-id", str(self.kv.id),
                "--branch-ids", "30965",
                "--check-channel-match",
                *extra_args,
                stdout=out,
            )
        return out

    @staticmethod
    def _summary(out):
        for line in reversed(out.getvalue().strip().splitlines()):
            stripped = line.strip()
            if stripped.startswith("{"):
                return json.loads(stripped)
        raise AssertionError("Không tìm thấy summary JSON trong output")

    def test_only_current_channel_ids_leaves_orders_outside_scope_untouched(self):
        in_scope = self._make_order(
            invoice_code="INV-1", invoice_id=1, channel=self.fallback_channel
        )
        out_of_scope = self._make_order(
            invoice_code="INV-2", invoice_id=2, channel=self.misc_channel
        )

        out = self._run(
            [
                self._invoice(invoice_id=1, code="INV-1", sale_channel_id=777),
                self._invoice(invoice_id=2, code="INV-2", sale_channel_id=777),
            ],
            extra_args=("--only-current-channel-ids", str(self.fallback_channel.id)),
        )

        in_scope.refresh_from_db()
        out_of_scope.refresh_from_db()
        self.assertEqual(in_scope.channel_id, self.shop_channel.id)
        self.assertEqual(out_of_scope.channel_id, self.misc_channel.id)
        summary = self._summary(out)
        self.assertEqual(summary["fixed"], 1)
        self.assertEqual(summary["out_of_scope"], 1)

    def test_unmapped_sale_channel_is_reported_not_moved_to_fallback(self):
        order = self._make_order(invoice_code="INV-3", invoice_id=3, channel=self.fallback_channel)

        out = self._run(
            [self._invoice(invoice_id=3, code="INV-3", sale_channel_id=999)],
            extra_args=("--empty-sale-channel-to", str(self.misc_channel.id)),
        )

        order.refresh_from_db()
        self.assertEqual(order.channel_id, self.fallback_channel.id)
        summary = self._summary(out)
        self.assertEqual(summary["unmapped_sale_channel"], 1)
        self.assertEqual(summary["empty_sale_channel"], 0)
        self.assertEqual(summary["fixed"], 0)
        self.assertIn('"sale_channel_id": "999"', out.getvalue())

    def test_empty_sale_channel_goes_to_configured_channel(self):
        order = self._make_order(invoice_code="INV-4", invoice_id=4, channel=self.fallback_channel)

        out = self._run(
            [self._invoice(invoice_id=4, code="INV-4")],
            extra_args=("--empty-sale-channel-to", str(self.misc_channel.id)),
        )

        order.refresh_from_db()
        self.assertEqual(order.channel_id, self.misc_channel.id)
        summary = self._summary(out)
        self.assertEqual(summary["empty_sale_channel"], 1)
        self.assertEqual(summary["fixed"], 1)

    def test_empty_sale_channel_untouched_when_no_target_configured(self):
        order = self._make_order(invoice_code="INV-5", invoice_id=5, channel=self.fallback_channel)

        out = self._run([self._invoice(invoice_id=5, code="INV-5")])

        order.refresh_from_db()
        self.assertEqual(order.channel_id, self.fallback_channel.id)
        summary = self._summary(out)
        self.assertEqual(summary["empty_sale_channel"], 1)
        self.assertEqual(summary["fixed"], 0)

    def test_dry_run_reports_without_writing(self):
        order = self._make_order(invoice_code="INV-6", invoice_id=6, channel=self.fallback_channel)

        out = self._run(
            [self._invoice(invoice_id=6, code="INV-6", sale_channel_id=777)],
            extra_args=("--only-current-channel-ids", str(self.fallback_channel.id), "--dry-run"),
        )

        order.refresh_from_db()
        self.assertEqual(order.channel_id, self.fallback_channel.id)
        self.assertEqual(self._summary(out)["fixed"], 1)


class ExtractItemRowsDiscountTests(SimpleTestCase):
    """Dòng hoá đơn KV mang `discount` (giảm cho MỖI sản phẩm) → OrderItem.discount."""

    def test_reads_per_unit_discount_from_invoice_detail(self):
        from decimal import Decimal

        from apps.kiotviet.infrastructure.services.import_invoices_service import ImportInvoicesService

        rows = ImportInvoicesService._extract_item_rows(
            {
                "invoiceDetails": [
                    {"productCode": "SKU-D", "productName": "SP", "price": 100000, "quantity": 2, "discount": 5000, "subTotal": 190000},
                    {"productCode": "SKU-N", "productName": "SP2", "price": 50000, "quantity": 1},
                ]
            }
        )
        self.assertEqual(rows[0]["discount"], Decimal("5000"))
        self.assertEqual(rows[0]["line_total"], Decimal("190000"))
        self.assertEqual(rows[1]["discount"], Decimal("0"))
