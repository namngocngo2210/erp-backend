"""Ba bộ lọc yes/no/rỗng dạng Exists() trên các list endpoint đơn hàng, cấu trúc
mirror nhau: has_recall_code, has_return_slip, has_call_recording — mỗi bộ một
class bên dưới (gộp từ test_recall_code_filter.py, test_return_slip_filter.py,
test_call_recording_filter.py)."""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.callcenter.models import CallRecord
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order, OrderRecall, OrderRecallEntry
from apps.users.tests.helpers import make_admin


class RecallCodeFilterApiTests(TestCase):
    """Bộ lọc has_recall_code (yes/no/rỗng) — Exists sang
    order_recall_entries.recall_code (Task 1 tách khỏi order_recalls).

    yes = có ít nhất một nhóm (OrderRecallEntry) với recall_code khác rỗng; no =
    phần bù (không có nhóm nào HOẶC mọi nhóm đều mã rỗng). Parse ở cả 4 list
    endpoint dùng chung OrdersFilterPanel.

    3 case dữ liệu: có nhóm với mã / có nhóm nhưng mã rỗng / không có nhóm nào.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-recall-code-filter")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(
            code="recall-ch", name="Recall Ch", owner="Recall Ch"
        )
        self.customer = Customer.objects.create(
            full_name="Khach Thu Hoi",
            phone_raw="0909000030",
            phone_normalized="0909000030",
        )
        # Nguồn "landing" để hiện trên /api/orders và /api/orders/landing.
        self.order_with_recall = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Co Ma Thu Hoi",
            phone="0909000030",
            order_source="landing",
        )
        # recall_code giờ nằm trên OrderRecallEntry (1-N, Task 1).
        OrderRecallEntry.objects.create(order=self.order_with_recall, recall_code="TH-001")
        self.order_empty_recall = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Recall Ma Rong",
            phone="0909000031",
            order_source="landing",
        )
        OrderRecallEntry.objects.create(order=self.order_empty_recall, recall_code="")
        self.order_no_recall = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Khong Recall",
            phone="0909000032",
            order_source="landing",
        )
        # Đơn KOC riêng cho endpoint /api/orders/koc (landing loại trừ koc).
        self.koc_with_recall = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="KOC Co Ma Thu Hoi",
            phone="0909000033",
            order_source="koc",
        )
        OrderRecallEntry.objects.create(order=self.koc_with_recall, recall_code="TH-KOC-1")
        self.koc_no_recall = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="KOC Khong Recall",
            phone="0909000034",
            order_source="koc",
        )

    def _ids(self, endpoint, body):
        response = self.client.post(
            endpoint, {"limit": "100", "offset": "0", **body}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {item["id"] for item in response.json()["results"]}

    def test_orders_list_filters_by_has_recall_code(self):
        self.assertEqual(
            self._ids("/api/orders", {"has_recall_code": "yes"}),
            {self.order_with_recall.id, self.koc_with_recall.id},
        )
        self.assertEqual(
            self._ids("/api/orders", {"has_recall_code": "no"}),
            {self.order_empty_recall.id, self.order_no_recall.id, self.koc_no_recall.id},
        )
        all_ids = self._ids("/api/orders", {})
        self.assertIn(self.order_with_recall.id, all_ids)
        self.assertIn(self.order_empty_recall.id, all_ids)
        self.assertIn(self.order_no_recall.id, all_ids)

    def test_landing_list_filters_by_has_recall_code(self):
        self.assertEqual(
            self._ids("/api/orders/landing", {"has_recall_code": "yes"}),
            {self.order_with_recall.id},
        )
        self.assertEqual(
            self._ids("/api/orders/landing", {"has_recall_code": "no"}),
            {self.order_empty_recall.id, self.order_no_recall.id},
        )

    def test_koc_list_filters_by_has_recall_code(self):
        self.assertEqual(
            self._ids("/api/orders/koc", {"has_recall_code": "yes"}),
            {self.koc_with_recall.id},
        )
        self.assertEqual(
            self._ids("/api/orders/koc", {"has_recall_code": "no"}),
            {self.koc_no_recall.id},
        )


class ReturnSlipFilterApiTests(TestCase):
    """Bộ lọc has_return_slip (yes/no/rỗng) — Exists sang order_recalls.return_slip_code.

    yes = có bản ghi recall với return_slip_code khác rỗng; no = phần bù (không có
    recall HOẶC recall slip rỗng). Parse ở cả 4 list endpoint dùng chung OrdersFilterPanel.

    3 case dữ liệu: recall có phiếu / recall phiếu rỗng / không có recall.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-return-slip-filter")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(code="slip-ch", name="Slip Ch", owner="Slip Ch")
        self.customer = Customer.objects.create(
            full_name="Khach Phieu Tra",
            phone_raw="0909000020",
            phone_normalized="0909000020",
        )
        # Nguồn "landing" để hiện trên /api/orders và /api/orders/landing.
        self.order_with_slip = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Co Phieu Tra",
            phone="0909000020",
            order_source="landing",
        )
        OrderRecall.objects.create(order=self.order_with_slip, return_slip_code=["PT-001"])
        self.order_empty_slip = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Recall Phieu Rong",
            phone="0909000021",
            order_source="landing",
        )
        OrderRecall.objects.create(order=self.order_empty_slip, return_slip_code=[])
        self.order_no_recall = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Khong Recall",
            phone="0909000022",
            order_source="landing",
        )
        # Đơn KOC riêng cho endpoint /api/orders/koc (landing loại trừ koc).
        self.koc_with_slip = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="KOC Co Phieu Tra",
            phone="0909000023",
            order_source="koc",
        )
        OrderRecall.objects.create(order=self.koc_with_slip, return_slip_code=["PT-KOC-1"])
        self.koc_no_recall = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="KOC Khong Recall",
            phone="0909000024",
            order_source="koc",
        )

    def _ids(self, endpoint, body):
        response = self.client.post(
            endpoint, {"limit": "100", "offset": "0", **body}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {item["id"] for item in response.json()["results"]}

    def test_orders_list_filters_by_has_return_slip(self):
        self.assertEqual(
            self._ids("/api/orders", {"has_return_slip": "yes"}),
            {self.order_with_slip.id, self.koc_with_slip.id},
        )
        self.assertEqual(
            self._ids("/api/orders", {"has_return_slip": "no"}),
            {self.order_empty_slip.id, self.order_no_recall.id, self.koc_no_recall.id},
        )
        all_ids = self._ids("/api/orders", {})
        self.assertIn(self.order_with_slip.id, all_ids)
        self.assertIn(self.order_empty_slip.id, all_ids)
        self.assertIn(self.order_no_recall.id, all_ids)

    def test_landing_list_filters_by_has_return_slip(self):
        self.assertEqual(
            self._ids("/api/orders/landing", {"has_return_slip": "yes"}),
            {self.order_with_slip.id},
        )
        self.assertEqual(
            self._ids("/api/orders/landing", {"has_return_slip": "no"}),
            {self.order_empty_slip.id, self.order_no_recall.id},
        )

    def test_koc_list_filters_by_has_return_slip(self):
        self.assertEqual(
            self._ids("/api/orders/koc", {"has_return_slip": "yes"}), {self.koc_with_slip.id}
        )
        self.assertEqual(
            self._ids("/api/orders/koc", {"has_return_slip": "no"}), {self.koc_no_recall.id}
        )


class CallRecordingFilterApiTests(TestCase):
    """Bộ lọc has_call_recording (yes/no/rỗng) — Exists sang callcenter_call_records.

    yes = có ít nhất 1 cuộc gọi với recording_url khác rỗng; no = phần bù (không có
    cuộc gọi HOẶC toàn cuộc gọi nhỡ không file ghi âm). Mirror RecallCodeFilterApiTests
    ở đầu file — parse ở cả các list endpoint dùng chung OrdersFilterPanel.

    3 case dữ liệu: cuộc gọi có ghi âm / cuộc gọi nhỡ (url rỗng) / không có cuộc gọi.
    """

    def setUp(self):
        self.client = APIClient()
        self.admin = make_admin("admin-call-recording-filter")
        self.client.force_authenticate(user=self.admin)
        self.channel = Channel.objects.create(
            code="callrec-ch", name="CallRec Ch", owner="CallRec Ch"
        )
        self.customer = Customer.objects.create(
            full_name="Khach Goi Dien",
            phone_raw="0909000040",
            phone_normalized="0909000040",
        )
        self.order_with_recording = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Co Ghi Am",
            phone="0909000040",
            order_source="landing",
        )
        CallRecord.objects.create(
            call_id="rec-1",
            order=self.order_with_recording,
            caller="0909000040",
            recording_url="https://rec.zenify.vn/rec-1.mp3",
        )
        self.order_missed_call = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Goi Nho",
            phone="0909000041",
            order_source="landing",
        )
        CallRecord.objects.create(
            call_id="rec-2", order=self.order_missed_call, caller="0909000041", recording_url=""
        )
        self.order_no_call = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Order Khong Cuoc Goi",
            phone="0909000042",
            order_source="landing",
        )

    def _ids(self, endpoint, body):
        response = self.client.post(
            endpoint, {"limit": "100", "offset": "0", **body}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {item["id"] for item in response.json()["results"]}

    def test_orders_list_filters_by_has_call_recording(self):
        self.assertEqual(
            self._ids("/api/orders", {"has_call_recording": "yes"}),
            {self.order_with_recording.id},
        )
        self.assertEqual(
            self._ids("/api/orders", {"has_call_recording": "no"}),
            {self.order_missed_call.id, self.order_no_call.id},
        )
        all_ids = self._ids("/api/orders", {})
        self.assertIn(self.order_with_recording.id, all_ids)
        self.assertIn(self.order_missed_call.id, all_ids)
        self.assertIn(self.order_no_call.id, all_ids)

    def test_landing_list_filters_by_has_call_recording(self):
        self.assertEqual(
            self._ids("/api/orders/landing", {"has_call_recording": "yes"}),
            {self.order_with_recording.id},
        )
        self.assertEqual(
            self._ids("/api/orders/landing", {"has_call_recording": "no"}),
            {self.order_missed_call.id, self.order_no_call.id},
        )
