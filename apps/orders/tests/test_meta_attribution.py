"""Bóc campaign id / ad id Facebook từ URL landing (tham số động Meta trong UTM)."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from apps.channels_core.models import Channel
from apps.orders.infrastructure.tasks.order_tasks_impl.ingest_job_tasks import process_landing_ingest
from apps.orders.meta_attribution import parse_meta_attribution
from apps.orders.models import Order
from apps.orders.application.services.order_service_impl import OrderService
from apps.orders.tests.helpers import ingest_headers
from apps.users.tests.helpers import make_admin

# URL thật (rút gọn fbclid) — mẫu A: utm_campaign = tên, utm_id = campaign.id,
# utm_content = ad.id, utm_term = ad.id.
_URL_NAMED = (
    "https://www.delax.id.vn/massagechanMSC06?utm_source=Facebook_Instream_Video"
    "&utm_medium=Desiglobal_MayMassageChan_XYZ_LDP_MSC06_160926_Adsplus+5.2+-+06+-+bc"
    "&utm_campaign=Desiglobal_MayMassageChan_XYZ_LDP_MSC06_160926_Adsplus+5.2+-+06+-+bc"
    "&utm_content=120252055274600365&fbclid=IwcGRvZgFmZGlk_aem_YFnJ4NedSPZy3anD3KnKxQ"
    "&utm_id=120252055274580365&utm_term=120252055274600365"
)
# Mẫu B: utm_campaign = utm_id = campaign.id, utm_content = ad.id, utm_term = adset.id.
_URL_NUMERIC = (
    "https://www.delax.click/demmassagenhatban?fbclid=IwcGRvZgVmZGlk&utm_medium=paid"
    "&utm_source=fb&utm_id=120249877608640636&utm_content=120249877608630636"
    "&utm_term=120249877608650636&utm_campaign=120249877608640636"
)


def test_named_campaign_url_uses_utm_id_and_utm_content():
    assert parse_meta_attribution(_URL_NAMED) == ("120252055274580365", "120252055274600365")


def test_numeric_campaign_url():
    assert parse_meta_attribution(_URL_NUMERIC) == ("120249877608640636", "120249877608630636")


def test_url_without_utm_gives_empty_strings():
    assert parse_meta_attribution("https://delax.vn/msc06/?fbclid=abc") == ("", "")
    assert parse_meta_attribution("") == ("", "")
    assert parse_meta_attribution(None) == ("", "")


def test_non_numeric_ids_are_ignored():
    # Landing tự đặt utm_id/utm_content bằng chữ → không phải id Meta, bỏ.
    url = "https://delax.vn/x?utm_id=summer-sale&utm_content=video1"
    assert parse_meta_attribution(url) == ("", "")


def test_only_ad_id_present():
    url = "https://delax.vn/x?utm_content=120252055274600365"
    assert parse_meta_attribution(url) == ("", "120252055274600365")


def test_garbage_url_does_not_raise():
    assert parse_meta_attribution("not a url at all") == ("", "")


def test_duplicate_param_takes_first_value():
    url = "https://delax.vn/x?utm_id=120249877608640636&utm_id=999"
    assert parse_meta_attribution(url) == ("120249877608640636", "")


# ── Ghi vào đơn lúc tạo ────────────────────────────────────────────────────────


class MetaAttributionOnCreateTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="fb-attr", name="FB Attr", owner="FB Attr")

    def test_landing_ingest_fills_campaign_and_ad_id_from_link(self):
        payload = {
            "channel_id": "FB Attr",
            "name": "Nguyen Van A",
            "phone": "0909123456",
            "address": "1 Nguyen Trai, Q1",
            "product_id": "BNC02",
            "link": _URL_NAMED,
        }

        def _run_task(_task_name, **kwargs):
            process_landing_ingest(payload=kwargs["payload"], trace_id=kwargs["trace_id"])
            return SimpleNamespace(id="test-job")

        with patch("apps.orders.application.services.order_ingest_service.dispatch", side_effect=_run_task):
            resp = APIClient().post("/api/orders/ingest", data=payload, format="json", **ingest_headers())
        self.assertEqual(resp.status_code, 200, resp.data)

        order = Order.objects.latest("id")
        self.assertEqual(order.fb_campaign_id, "120252055274580365")
        self.assertEqual(order.fb_ad_id, "120252055274600365")

    def test_manual_create_fills_from_url_page(self):
        order = OrderService.create_order_manual(
            {
                "channel": self.channel,
                "name": "Tay",
                "phone": "0909123457",
                "raw_address": "1 Nguyen Trai, Q1",
                "url_page": _URL_NUMERIC,
                "order_source": "landing",
            },
            make_admin(),
        )
        self.assertEqual(order.fb_campaign_id, "120249877608640636")
        self.assertEqual(order.fb_ad_id, "120249877608630636")

    def test_create_without_url_leaves_ids_empty(self):
        order = OrderService.create_order_manual(
            {"channel": self.channel, "name": "Tay", "phone": "0909123458", "raw_address": "x"},
            make_admin(),
        )
        self.assertEqual((order.fb_campaign_id, order.fb_ad_id), ("", ""))
