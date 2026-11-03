"""Backfill fb_campaign_id / fb_ad_id cho đơn cũ từ url_page. Idempotent, mặc định dry-run."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order

_URL = "https://www.delax.click/x?utm_id=120249877608640636&utm_content=120249877608630636"


class BackfillFbAttributionTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="fb-bf-attr", name="FB BF")
        self.customer = Customer.objects.create(full_name="KH", phone_raw="0900000011", phone_normalized="0900000011")

    def _order(self, **kw):
        return Order.objects.create(
            channel=self.channel, customer=self.customer, name="X", phone="0900000011", order_source="landing", **kw
        )

    def _run(self, *args):
        out = StringIO()
        call_command("backfill_fb_attribution", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing_but_counts(self):
        order = self._order(url_page=_URL)
        out = self._run()
        order.refresh_from_db()
        self.assertEqual(order.fb_campaign_id, "")
        self.assertIn("DRY-RUN", out)
        self.assertIn("1", out)

    def test_apply_fills_both_ids(self):
        order = self._order(url_page=_URL)
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.fb_campaign_id, "120249877608640636")
        self.assertEqual(order.fb_ad_id, "120249877608630636")

    def test_apply_skips_orders_already_filled_and_without_ids(self):
        filled = self._order(url_page=_URL, fb_campaign_id="1", fb_ad_id="2")
        no_ids = self._order(url_page="https://delax.vn/msc06/?fbclid=abc")
        empty = self._order(url_page="")
        self._run("--apply")
        for o in (filled, no_ids, empty):
            o.refresh_from_db()
        self.assertEqual((filled.fb_campaign_id, filled.fb_ad_id), ("1", "2"))
        self.assertEqual((no_ids.fb_campaign_id, no_ids.fb_ad_id), ("", ""))
        self.assertEqual((empty.fb_campaign_id, empty.fb_ad_id), ("", ""))

    def test_apply_fills_ad_id_when_only_campaign_present(self):
        # Đơn ghi campaign nhưng chưa có ad (ví dụ backfill lần trước thiếu cột) → vẫn quét.
        order = self._order(url_page=_URL, fb_campaign_id="120249877608640636")
        self._run("--apply")
        order.refresh_from_db()
        self.assertEqual(order.fb_ad_id, "120249877608630636")
