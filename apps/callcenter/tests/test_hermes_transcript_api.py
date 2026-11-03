"""API máy-tới-máy cấp transcript cuộc gọi cho agent phân tích ngoài (Hermes).

Endpoint là ``AllowAny`` + token tĩnh nên bộ test này gác đúng hai thứ dễ hỏng
nhất: cửa xác thực (thiếu/sai/chưa cấu hình) và phân trang keyset (agent kéo
toàn bộ danh sách — lặp hoặc sót một trang là hỏng dữ liệu phân tích, im lặng).
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.callcenter.models import CallRecord, TranscribeStatus
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order

URL = "/api/callcenter/hermes/calls"
TOKEN = "hermes-test-token"
PHONE = "0912345678"


def make_call(call_id, *, started=None, acd=180, transcript="Nhân viên: alo", status=None, **extra):
    started = started or (timezone.now() - timedelta(days=1))
    return CallRecord.objects.create(
        call_id=call_id,
        call_type=extra.pop("call_type", "1"),
        agent_id=extra.pop("agent_id", "6863"),
        caller=extra.pop("caller", PHONE),
        caller_normalized=PHONE,
        recording_url=f"https://rec.zenify.vn/{call_id}.mp3",
        start_time=started,
        end_time=started + timedelta(seconds=acd),
        acd_time=acd,
        transcript=transcript,
        transcribe_status=(
            status
            if status is not None
            else (TranscribeStatus.DONE if transcript else TranscribeStatus.PENDING)
        ),
        **extra,
    )


@override_settings(HERMES_API_TOKEN=TOKEN)
class HermesAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_missing_token_is_rejected(self):
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_wrong_token_is_rejected(self):
        resp = self.client.get(URL, HTTP_X_API_TOKEN="sai-token")
        self.assertEqual(resp.status_code, 401)

    def test_non_ascii_token_is_rejected_not_500(self):
        """`compare_digest` trên str đòi ASCII thuần — token có dấu từng nổ
        TypeError (500) thay vì 401; so bytes với bytes để đóng cửa êm."""
        resp = self.client.get(URL, HTTP_X_API_TOKEN="token-có-dấu")
        self.assertEqual(resp.status_code, 401)

    def test_accepts_bearer_header(self):
        resp = self.client.get(URL, HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
        self.assertEqual(resp.status_code, 200)

    def test_jwt_of_a_crm_user_is_not_enough(self):
        """Token của nhân viên CRM KHÔNG mở được cửa này: đây là kênh riêng cho
        máy, thu hồi bằng cách đổi env chứ không dính vào phân quyền người dùng."""
        from apps.users.tests.helpers import make_admin

        client = APIClient()
        client.force_authenticate(make_admin("admin-hermes"))
        self.assertEqual(client.get(URL).status_code, 401)


class HermesUnconfiguredTests(TestCase):
    @override_settings(HERMES_API_TOKEN="", IS_PRODUCTION=True)
    def test_production_without_token_fails_closed(self):
        """Thiếu env trên prod = endpoint đóng (503), KHÔNG phải mở toang."""
        resp = APIClient().get(URL)
        self.assertEqual(resp.status_code, 503)


@override_settings(HERMES_API_TOKEN=TOKEN)
class HermesPayloadTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        channel = Channel.objects.create(code="hotline", name="Hotline")
        customer = Customer.objects.create(
            full_name="KH", phone_raw=PHONE, phone_normalized=PHONE
        )
        self.order = Order.objects.create(
            channel=channel, customer=customer, name="KH", phone=PHONE, status=0
        )
        self.record = make_call("c1", order=self.order)

    def _get(self, **params):
        return self.client.get(URL, params, HTTP_X_API_TOKEN=TOKEN)

    def test_returns_transcript_and_call_metadata(self):
        item = self._get().json()["items"][0]
        self.assertEqual(item["call_id"], "c1")
        self.assertEqual(item["transcript"], "Nhân viên: alo")
        self.assertEqual(item["duration_seconds"], 180)
        self.assertEqual(item["direction"], "outbound")
        self.assertEqual(item["agent_id"], "6863")
        self.assertEqual(item["order_id"], self.order.id)

    def test_inbound_call_direction(self):
        make_call("c2", call_type="0")
        directions = {i["call_id"]: i["direction"] for i in self._get().json()["items"]}
        self.assertEqual(directions["c2"], "inbound")

    def test_customer_phone_is_masked_by_default(self):
        """Transcript đã là PII; không đẩy kèm cả danh bạ khách ra hệ thống ngoài."""
        item = self._get().json()["items"][0]
        self.assertEqual(item["customer_phone"], "*******678")

    def test_include_phone_returns_raw_number(self):
        item = self._get(include_phone="1").json()["items"][0]
        self.assertEqual(item["customer_phone"], PHONE)

    def test_call_without_order_returns_null_order_id(self):
        make_call("c3")
        items = {i["call_id"]: i for i in self._get().json()["items"]}
        self.assertIsNone(items["c3"]["order_id"])


@override_settings(HERMES_API_TOKEN=TOKEN)
class HermesListingTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _get(self, **params):
        return self.client.get(URL, params, HTTP_X_API_TOKEN=TOKEN)

    def test_only_transcribed_calls_by_default(self):
        make_call("done")
        make_call("pending", transcript="", status=TranscribeStatus.PENDING)
        make_call("skipped", transcript="", status=TranscribeStatus.SKIPPED)
        ids = [i["call_id"] for i in self._get().json()["items"]]
        self.assertEqual(ids, ["done"])

    def test_only_transcribed_zero_includes_untranscribed(self):
        make_call("done")
        make_call("pending", transcript="", status=TranscribeStatus.PENDING)
        ids = sorted(i["call_id"] for i in self._get(only_transcribed="0").json()["items"])
        self.assertEqual(ids, ["done", "pending"])

    def test_defaults_to_last_seven_days(self):
        make_call("recent")
        make_call("old", started=timezone.now() - timedelta(days=30))
        ids = [i["call_id"] for i in self._get().json()["items"]]
        self.assertEqual(ids, ["recent"])

    def test_from_and_to_filter_by_call_start(self):
        now = timezone.now()
        make_call("d10", started=now - timedelta(days=10))
        make_call("d3", started=now - timedelta(days=3))
        params = {
            "from": (now - timedelta(days=12)).date().isoformat(),
            "to": (now - timedelta(days=8)).date().isoformat(),
        }
        ids = [i["call_id"] for i in self._get(**params).json()["items"]]
        self.assertEqual(ids, ["d10"])

    def test_to_bound_covers_the_whole_day(self):
        """``to=2026-08-20`` phải lấy cả cuộc gọi lúc 23h ngày đó, không cắt ở 00:00."""
        now = timezone.now()
        late = (now - timedelta(days=2)).replace(hour=23, minute=30)
        make_call("late", started=late)
        local_day = timezone.localtime(late).date().isoformat()
        params = {"from": local_day, "to": local_day}
        ids = [i["call_id"] for i in self._get(**params).json()["items"]]
        self.assertEqual(ids, ["late"])

    def test_min_duration_filters_short_calls(self):
        make_call("short", acd=30)
        make_call("long", acd=200)
        ids = [i["call_id"] for i in self._get(min_duration="120").json()["items"]]
        self.assertEqual(ids, ["long"])

    def test_invalid_date_returns_400(self):
        self.assertEqual(self._get(**{"from": "hôm qua"}).status_code, 400)

    def test_invalid_limit_returns_400(self):
        self.assertEqual(self._get(limit="abc").status_code, 400)


@override_settings(HERMES_API_TOKEN=TOKEN)
class HermesPaginationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        for index in range(5):
            make_call(f"c{index}")

    def _get(self, **params):
        return self.client.get(URL, params, HTTP_X_API_TOKEN=TOKEN)

    def test_cursor_walks_every_record_exactly_once(self):
        seen = []
        cursor = None
        for _ in range(10):  # trần vòng lặp: cursor đứng yên là bug, không phải treo
            params = {"limit": "2"}
            if cursor is not None:
                params["cursor"] = str(cursor)
            body = self._get(**params).json()
            seen += [item["call_id"] for item in body["items"]]
            cursor = body["next_cursor"]
            if not body["has_more"]:
                break
        self.assertEqual(sorted(seen), [f"c{i}" for i in range(5)])
        self.assertEqual(len(seen), len(set(seen)))

    def test_last_page_reports_no_more(self):
        body = self._get(limit="100").json()
        self.assertFalse(body["has_more"])
        self.assertIsNone(body["next_cursor"])

    def test_limit_is_capped(self):
        """Transcript nặng vài KB/cuộc — agent xin 5000 dòng vẫn chỉ được trần cứng."""
        body = self._get(limit="5000").json()
        self.assertEqual(body["limit"], 200)
