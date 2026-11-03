"""API feed conversion Google Ads Web: cửa token, tập đơn, map trường, keyset."""

from datetime import datetime
from itertools import count
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.catalog.models import Product
from apps.channels_core.models import Channel
from apps.conversion_feed.domain.lead import TRACKING_FIELDS
from apps.customers.models import Customer
from apps.orders.models import Order, OrderAssessment, OrderItem

URL = "/api/conversion-feed/google-ads-web"
TOKEN = "conversion-feed-test-token"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_seq = count(1)


def _client() -> APIClient:
    c = APIClient()
    c.credentials(HTTP_X_API_TOKEN=TOKEN)
    return c


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(code="gg-ads-web-test", name="Google Ads Web")
        cls.other_channel = Channel.objects.create(code="other-test", name="Khác")

    def seed(self, *, sales_status="closed", channel=None, created=datetime(2026, 9, 10, 10, 0, tzinfo=TZ), **kw) -> Order:
        n = next(_seq)
        phone = f"09{n:08d}"
        customer = Customer.objects.create(full_name="Khách", phone_raw=phone, phone_normalized=phone)
        kw.setdefault("name", "Nguyễn Văn An")
        kw.setdefault("phone", phone)
        order = Order.objects.create(channel=channel or self.channel, customer=customer, **kw)
        Order.objects.filter(id=order.id).update(created_at=created)
        if sales_status is not None:
            OrderAssessment.objects.create(order=order, sales_status_code=sales_status)
        return order

    def get(self, **query):
        with override_settings(CONVERSION_FEED_GOOGLE_ADS_WEB_CHANNEL_ID=self.channel.id):
            return _client().get(URL, {"from": "2026-09-01", "to": "2026-09-30", **query})


@override_settings(ORDERS_INGEST_API_TOKEN=TOKEN)
class ConversionFeedAuthTests(TestCase):
    def test_missing_or_wrong_token_is_401(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)
        self.assertEqual(APIClient().get(URL, HTTP_X_API_TOKEN="sai").status_code, 401)
        self.assertEqual(APIClient().get(URL, HTTP_X_API_TOKEN="tôken").status_code, 401)

    def test_bearer_token_passes(self):
        self.assertEqual(APIClient().get(URL, HTTP_AUTHORIZATION=f"Bearer {TOKEN}").status_code, 200)

    @override_settings(ORDERS_INGEST_API_TOKEN="", IS_PRODUCTION=True)
    def test_fails_closed_in_production_without_token(self):
        self.assertEqual(APIClient().get(URL).status_code, 503)

    def test_post_is_not_allowed(self):
        self.assertEqual(_client().post(URL).status_code, 405)


@override_settings(ORDERS_INGEST_API_TOKEN=TOKEN)
class ConversionFeedDataTests(_Base):
    def test_maps_all_contract_fields(self):
        order = self.seed(
            province="Hà Nội", district="Cầu Giấy", total_after_discount=12_490_000,
            sku="FALLBACK", order_source="landing",
        )
        product_a = Product.objects.create(name="Ghế", sku="CF-MSC06")
        product_b = Product.objects.create(name="Gối", sku="CF-GOI01")
        OrderItem.objects.create(order=order, product=product_a, sku="CF-MSC06")
        OrderItem.objects.create(order=order, product=product_b, sku="CF-GOI01")

        resp = self.get()
        self.assertEqual(resp.status_code, 200, resp.content)
        [item] = resp.json()["items"]
        self.assertEqual(item["time"], "2026-09-10T10:00:00+07:00")
        self.assertEqual((item["first_name"], item["last_name"]), ("Văn An", "Nguyễn"))
        self.assertEqual(item["phone"], order.phone_normalized)
        self.assertEqual((item["province"], item["district"], item["zip"]), ("Hà Nội", "Cầu Giấy", "10000"))
        self.assertEqual(item["sku"], "CF-MSC06;CF-GOI01")
        self.assertEqual((item["value"], item["currency"]), (12_490_000, "VND"))
        self.assertEqual((item["status"], item["status_code"]), ("Đã chốt", "closed"))
        self.assertEqual(item["source"], "googleads")
        self.assertEqual(item["event_id"], str(order.id))
        self.assertEqual(item["order_id"], str(order.id))
        for field in TRACKING_FIELDS:
            self.assertEqual(item[field], "", field)

    def test_sku_falls_back_and_province_level2_used(self):
        self.seed(sku="SKU-ONLY", province="", province_level2="Thành phố Hồ Chí Minh", order_source="hotline")
        [item] = self.get().json()["items"]
        self.assertEqual((item["sku"], item["province"], item["zip"]), ("SKU-ONLY", "Hồ Chí Minh", "70000"))
        self.assertEqual(item["source"], "hotline")

    def test_only_closed_statuses_of_the_channel_in_window(self):
        keep = {
            self.seed(sales_status="closed").id,
            self.seed(sales_status="closed_express").id,
            self.seed(sales_status="closed_scheduled_date").id,
            self.seed(sales_status="created_order").id,
        }
        self.seed(sales_status="not_buying")
        self.seed(sales_status=None)
        self.seed(channel=self.other_channel)
        self.seed(created=datetime(2026, 8, 31, 23, 59, tzinfo=TZ))
        deleted = self.seed()
        Order.all_objects.filter(id=deleted.id).update(deleted_at=datetime(2026, 9, 11, tzinfo=TZ))
        self.seed(order_source="foxia")

        ids = {int(i["order_id"]) for i in self.get().json()["items"]}
        self.assertEqual(ids, keep)

    def test_keyset_pagination_walks_every_row_once(self):
        expected = [self.seed().id for _ in range(5)]
        seen, cursor = [], None
        while True:
            query = {"limit": "2", **({"cursor": str(cursor)} if cursor else {})}
            body = self.get(**query).json()
            seen += [int(i["order_id"]) for i in body["items"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, sorted(expected))

    def test_bad_params_are_400(self):
        self.assertEqual(self.get(cursor="abc").status_code, 400)
        self.assertEqual(self.get(limit="0").status_code, 400)
        self.assertEqual(self.get(to="2026-08-01").status_code, 400)

    def test_single_query_regardless_of_row_count(self):
        for _ in range(3):
            self.seed()
        # SHOW + set statement_timeout + SELECT + reset — không phụ thuộc số dòng (không N+1).
        with self.assertNumQueries(4):
            self.get()
