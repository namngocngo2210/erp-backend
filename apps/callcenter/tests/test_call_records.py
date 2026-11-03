"""Test pipeline cuộc gọi Zenify: sync/dedupe, match đơn, transcribe, API RBAC.

Mock toàn bộ HTTP (Zenify + tải audio + Gemini) — không gọi mạng thật.
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.callcenter.models import CallRecord, TranscribeStatus
from apps.callcenter.sync_service import (
    match_unmatched_records,
    pick_order_for_call,
    rematch_records_of_deleted_orders,
    sync_call_histories,
)
from apps.callcenter.transcribe import TranscribeError, transcribe_pending_records
from apps.channels_core.models import Channel
from apps.customers.models import Customer
from apps.orders.models import Order
from apps.users.models import DataScope, User
from apps.users.tests.helpers import make_admin, make_user_with_sections

PHONE = "0912345678"


# BOM LỊCH đã nổ ~2026-08-18 (lộ khi chạy suite 2026-08-20, đỏ cả trên master
# cf02bd4b): fixture cũ ghi CỨNG startTime "2026-07-18" trong khi match đơn giới
# hạn ±ZENIFY_CALL_MATCH_WINDOW_DAYS (30 ngày) quanh created_at của đơn (= now)
# — quá mốc đó mọi test khớp đơn tự đỏ. Dựng mốc TƯƠNG ĐỐI theo now, format đúng
# kiểu Zenify (giờ ZENIFY_TZ) — cùng lớp lỗi đã ghi ở memory calendar-bomb-tests.
def _zenify_time(dt) -> str:
    from apps.callcenter.sync_service import ZENIFY_TZ

    return dt.astimezone(ZENIFY_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _zenify_item(call_id="c1", caller=PHONE, url="https://rec.zenify.vn/c1.mp3", acd="65", **extra):
    start = timezone.now() - timedelta(days=1)
    item = {
        "callId": call_id,
        "callType": "1",
        "agentId": "a1",
        "username": "Agent A",
        "caller": caller,
        "called": "02471000000",
        "url": url,
        "startTime": _zenify_time(start),
        "endTime": _zenify_time(start + timedelta(seconds=65)),
        "acdTime": acd,
    }
    item.update(extra)
    return item


def make_order(channel, customer, *, phone=PHONE, source=None, created_at=None, staff=None):
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        staff=staff,
        name="KH",
        phone=phone,
        status=0,
        order_source=source,
    )
    if created_at is not None:
        Order.objects.filter(pk=order.pk).update(created_at=created_at)
        order.refresh_from_db(fields=["created_at"])
    return order


def _reset_quota_counters():
    """Xoá đích danh counter quota của phút/ngày hiện tại — KHÔNG cache.clear()
    (FLUSHDB đè Redis thật, xem docs/audit-followups-2026-07-06.md)."""
    now = timezone.now()
    cache.delete(f"gemini:transcribe:rpm:{now.strftime('%Y%m%d%H%M')}")
    cache.delete(f"gemini:transcribe:rpd:{now.strftime('%Y%m%d')}")
    cache.delete("gemini:transcribe:cooldown")


class SyncCallHistoriesTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="hotline", name="Hotline")
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw=PHONE, phone_normalized=PHONE
        )

    def _sync(self, items):
        with patch("apps.callcenter.sync_service.fetch_call_histories", return_value=items):
            return sync_call_histories()

    def test_creates_record_and_matches_order(self):
        order = make_order(self.channel, self.customer)
        summary = self._sync([_zenify_item()])
        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["matched"], 1)
        record = CallRecord.objects.get(call_id="c1")
        self.assertEqual(record.caller_normalized, PHONE)
        self.assertEqual(record.order_id, order.id)
        self.assertEqual(record.transcribe_status, TranscribeStatus.PENDING)
        self.assertIsNotNone(record.start_time)

    def test_dedupes_by_call_id_on_overlapping_windows(self):
        make_order(self.channel, self.customer)
        self._sync([_zenify_item()])
        summary = self._sync([_zenify_item()])
        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["existing"], 1)
        self.assertEqual(CallRecord.objects.count(), 1)

    def test_missed_call_marked_skipped(self):
        summary = self._sync([_zenify_item(call_id="c2", url="", acd="0")])
        self.assertEqual(summary["created"], 1)
        record = CallRecord.objects.get(call_id="c2")
        self.assertEqual(record.transcribe_status, TranscribeStatus.SKIPPED)

    def test_late_recording_url_reopens_skipped_record(self):
        self._sync([_zenify_item(call_id="c3", url="", acd="0")])
        self._sync([_zenify_item(call_id="c3", url="https://rec.zenify.vn/c3.mp3", acd="40")])
        record = CallRecord.objects.get(call_id="c3")
        self.assertEqual(record.transcribe_status, TranscribeStatus.PENDING)
        self.assertEqual(record.recording_url, "https://rec.zenify.vn/c3.mp3")

    def test_marketplace_orders_excluded_from_match(self):
        make_order(self.channel, self.customer, source="shopee")
        make_order(self.channel, self.customer, source="tiktok")
        summary = self._sync([_zenify_item()])
        record = CallRecord.objects.get(call_id="c1")
        self.assertIsNone(record.order_id)
        self.assertEqual(record.match_attempts, 1)
        self.assertEqual(summary["matched"], 0)

    def test_call_before_order_creation_matches_on_next_round(self):
        """Cuộc gọi tư vấn tới trước khi lên đơn: lượt đầu không khớp, đơn tạo
        xong thì vòng match sau khớp được."""
        self._sync([_zenify_item()])
        self.assertIsNone(CallRecord.objects.get(call_id="c1").order_id)
        order = make_order(self.channel, self.customer)
        # Backoff giãn dần (audit 2026-07-18): lượt 2 chỉ chạy khi record đủ tuổi —
        # lùi created_at 30' để mô phỏng vòng match sau.
        CallRecord.objects.filter(call_id="c1").update(
            created_at=timezone.now() - timedelta(minutes=30)
        )
        summary = match_unmatched_records()
        self.assertEqual(summary["matched"], 1)
        self.assertEqual(CallRecord.objects.get(call_id="c1").order_id, order.id)

    def test_match_stops_after_max_attempts(self):
        self._sync([_zenify_item()])
        # attempts=6 theo lịch backoff cần record đủ ~1 ngày tuổi mới tới lượt cuối.
        CallRecord.objects.filter(call_id="c1").update(
            match_attempts=6, created_at=timezone.now() - timedelta(days=3)
        )
        summary = match_unmatched_records()
        self.assertEqual(summary["exhausted"], 1)
        self.assertEqual(CallRecord.objects.get(call_id="c1").match_attempts, 7)
        # Đủ 7 lần: các vòng sau không quét nữa dù đơn đã xuất hiện.
        make_order(self.channel, self.customer)
        summary = match_unmatched_records()
        self.assertEqual(summary["scanned"], 0)
        self.assertIsNone(CallRecord.objects.get(call_id="c1").order_id)


class RematchDeletedOrderTests(TestCase):
    """Đơn xoá mềm rồi tạo lại cùng SĐT: ghi âm phải đi theo đơn mới."""

    def setUp(self):
        self.channel = Channel.objects.create(code="hotline", name="Hotline")
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw=PHONE, phone_normalized=PHONE
        )

    def _sync(self, items):
        with patch("apps.callcenter.sync_service.fetch_call_histories", return_value=items):
            return sync_call_histories()

    def _soft_delete(self, order):
        Order.objects.filter(pk=order.pk).update(deleted_at=timezone.now())

    def test_moves_record_to_recreated_order(self):
        old = make_order(self.channel, self.customer)
        self._sync([_zenify_item()])
        self.assertEqual(CallRecord.objects.get(call_id="c1").order_id, old.id)

        self._soft_delete(old)
        new = make_order(self.channel, self.customer)
        summary = rematch_records_of_deleted_orders()

        self.assertEqual(summary, {"scanned": 1, "moved": 1})
        record = CallRecord.objects.get(call_id="c1")
        self.assertEqual(record.order_id, new.id)
        self.assertIsNotNone(record.matched_at)

    def test_keeps_record_on_deleted_order_when_no_replacement(self):
        """Không có đơn thay thế thì để yên (không gỡ về NULL để khỏi đốt lượt
        backoff); tick sau đơn mới xuất hiện vẫn chuyển được."""
        old = make_order(self.channel, self.customer)
        self._sync([_zenify_item()])
        self._soft_delete(old)

        summary = rematch_records_of_deleted_orders()
        self.assertEqual(summary, {"scanned": 1, "moved": 0})
        self.assertEqual(CallRecord.objects.get(call_id="c1").order_id, old.id)

        new = make_order(self.channel, self.customer)
        summary = rematch_records_of_deleted_orders()
        self.assertEqual(summary["moved"], 1)
        self.assertEqual(CallRecord.objects.get(call_id="c1").order_id, new.id)

    def test_ignores_marketplace_and_out_of_window_replacements(self):
        old = make_order(self.channel, self.customer)
        self._sync([_zenify_item()])
        self._soft_delete(old)
        make_order(self.channel, self.customer, source="shopee")
        make_order(
            self.channel, self.customer, created_at=timezone.now() - timedelta(days=60)
        )

        summary = rematch_records_of_deleted_orders()
        self.assertEqual(summary["moved"], 0)
        self.assertEqual(CallRecord.objects.get(call_id="c1").order_id, old.id)

    def test_dry_run_reports_without_writing(self):
        old = make_order(self.channel, self.customer)
        self._sync([_zenify_item()])
        self._soft_delete(old)
        new = make_order(self.channel, self.customer)

        summary = rematch_records_of_deleted_orders(dry_run=True)
        self.assertEqual(summary["moved"], 1)
        self.assertEqual(summary["moves"], [(CallRecord.objects.get(call_id="c1").id, old.id, new.id)])
        self.assertEqual(CallRecord.objects.get(call_id="c1").order_id, old.id)

    def test_sync_tick_runs_rematch(self):
        old = make_order(self.channel, self.customer)
        self._sync([_zenify_item()])
        self._soft_delete(old)
        new = make_order(self.channel, self.customer)

        summary = self._sync([_zenify_item()])
        self.assertEqual(summary["rematched_deleted"], 1)
        self.assertEqual(CallRecord.objects.get(call_id="c1").order_id, new.id)


class PickOrderForCallTests(SimpleTestCase):
    def test_prefers_nearest_created_at_within_window(self):
        now = timezone.now()
        candidates = [
            (1, now - timedelta(days=10)),
            (2, now - timedelta(days=1)),
            (3, now + timedelta(days=2)),
        ]
        self.assertEqual(pick_order_for_call(candidates, now), 2)

    def test_rejects_orders_outside_window(self):
        now = timezone.now()
        candidates = [(1, now - timedelta(days=45))]
        self.assertIsNone(pick_order_for_call(candidates, now))


@override_settings(GEMINI_API_KEY="test-key", GEMINI_TRANSCRIBE_BATCH=8)
class TranscribePendingTests(TestCase):
    def setUp(self):
        _reset_quota_counters()

    def _record(self, call_id="c1", status=TranscribeStatus.PENDING, attempts=0):
        return CallRecord.objects.create(
            call_id=call_id,
            caller=PHONE,
            caller_normalized=PHONE,
            recording_url=f"https://rec.zenify.vn/{call_id}.mp3",
            acd_time=60,
            transcribe_status=status,
            transcribe_attempts=attempts,
        )

    def test_transcribes_pending_record(self):
        record = self._record()
        with (
            patch("apps.callcenter.transcribe.download_recording", return_value=b"audio"),
            patch("apps.callcenter.transcribe.transcribe_audio", return_value="Nhân viên: alo"),
        ):
            summary = transcribe_pending_records()
        record.refresh_from_db()
        self.assertEqual(summary["done"], 1)
        self.assertEqual(record.transcribe_status, TranscribeStatus.DONE)
        self.assertEqual(record.transcript, "Nhân viên: alo")

    def test_error_increments_attempts_and_requeues(self):
        record = self._record()
        with patch(
            "apps.callcenter.transcribe.download_recording",
            side_effect=TranscribeError("HTTP 500"),
        ):
            summary = transcribe_pending_records()
        record.refresh_from_db()
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(record.transcribe_status, TranscribeStatus.PENDING)
        self.assertEqual(record.transcribe_attempts, 1)
        self.assertIn("HTTP 500", record.transcribe_error)

    @override_settings(GEMINI_MAX_ATTEMPTS=2)
    def test_exhausted_attempts_marks_failed(self):
        record = self._record(attempts=1)
        with patch(
            "apps.callcenter.transcribe.download_recording",
            side_effect=TranscribeError("HTTP 500"),
        ):
            transcribe_pending_records()
        record.refresh_from_db()
        self.assertEqual(record.transcribe_status, TranscribeStatus.FAILED)

    @override_settings(GEMINI_MAX_RPD=1)
    def test_quota_stops_batch_and_requeues_rest(self):
        self._record("c1")
        self._record("c2")
        with (
            patch("apps.callcenter.transcribe.download_recording", return_value=b"audio"),
            patch("apps.callcenter.transcribe.transcribe_audio", return_value="ok"),
        ):
            summary = transcribe_pending_records()
        self.assertEqual(summary["done"], 1)
        self.assertTrue(summary["quota_stopped"])
        statuses = set(CallRecord.objects.values_list("transcribe_status", flat=True))
        self.assertEqual(statuses, {TranscribeStatus.DONE, TranscribeStatus.PENDING})

    @override_settings(GEMINI_API_KEY="")
    def test_noop_without_api_key(self):
        self._record()
        summary = transcribe_pending_records()
        self.assertEqual(summary["claimed"], 0)


class OrderCallRecordsApiTests(TestCase):
    def setUp(self):
        self.channel = Channel.objects.create(code="hotline", name="Hotline")
        self.customer = Customer.objects.create(
            full_name="KH", phone_raw=PHONE, phone_normalized=PHONE
        )
        self.staff = User.objects.create_user(username="sale-call-api", password="x")
        self.order = make_order(self.channel, self.customer, staff=self.staff)
        self.record = CallRecord.objects.create(
            call_id="c1",
            caller=PHONE,
            caller_normalized=PHONE,
            recording_url="https://rec.zenify.vn/c1.mp3",
            acd_time=60,
            order=self.order,
            transcribe_status=TranscribeStatus.DONE,
            transcript="Nhân viên: alo",
        )

    def _url(self, order_id=None):
        return f"/api/callcenter/orders/{order_id or self.order.id}/call-records"

    def test_admin_sees_records(self):
        client = APIClient()
        client.force_authenticate(make_admin("admin-call-api"))
        resp = client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["transcript"], "Nhân viên: alo")

    def test_requires_orders_section(self):
        client = APIClient()
        client.force_authenticate(make_user_with_sections("kho-call-api", ["inventory"]))
        self.assertEqual(client.get(self._url()).status_code, 403)

    def test_own_scope_hides_other_staff_order(self):
        viewer = make_user_with_sections("own-call-api", ["orders"], scope=DataScope.OWN)
        client = APIClient()
        client.force_authenticate(viewer)
        # Đơn của staff khác → 404 (không lộ tồn tại).
        self.assertEqual(client.get(self._url()).status_code, 404)
        # Đơn do chính user phụ trách → thấy.
        own_order = make_order(self.channel, self.customer, staff=viewer)
        CallRecord.objects.create(call_id="c2", order=own_order, caller=PHONE)
        self.assertEqual(client.get(self._url(own_order.id)).status_code, 200)

    def test_unknown_order_404(self):
        client = APIClient()
        client.force_authenticate(make_admin("admin-call-api-2"))
        self.assertEqual(client.get(self._url(999999)).status_code, 404)


@override_settings(GEMINI_API_KEY="test-key", GEMINI_TRANSCRIBE_BATCH=8)
class TranscribeHardeningTests(TestCase):
    """Audit 2026-07-18: transient không đốt attempt, quota tiêu SAU download,
    ưu tiên mới-trước, command requeue FAILED, backoff match không đốt lượt."""

    def setUp(self):
        _reset_quota_counters()

    def _record(self, call_id="c1", **kwargs):
        defaults = dict(
            call_id=call_id,
            caller=PHONE,
            caller_normalized=PHONE,
            recording_url=f"https://rec.zenify.vn/{call_id}.mp3",
            acd_time=60,
            transcribe_status=TranscribeStatus.PENDING,
        )
        defaults.update(kwargs)
        return CallRecord.objects.create(**defaults)

    def test_transient_error_does_not_burn_attempt(self):
        record = self._record()
        with patch(
            "apps.callcenter.transcribe.download_recording",
            side_effect=TranscribeError("Gemini lỗi: 503 UNAVAILABLE", transient=True),
        ):
            summary = transcribe_pending_records()
        record.refresh_from_db()
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(record.transcribe_attempts, 0, "lỗi transient không được đốt lượt")
        self.assertEqual(record.transcribe_status, TranscribeStatus.PENDING)

    def test_dead_url_does_not_consume_quota(self):
        """URL chết (404) phải fail TRƯỚC khi tiêu quota — không đốt RPD vô ích."""
        self._record("c1")
        good = self._record("c2")
        calls = {"n": 0}

        def fake_download(url):
            calls["n"] += 1
            if "c1" in url:
                raise TranscribeError("Tải file ghi âm thất bại: HTTP 404")
            return b"audio"

        with (
            override_settings(GEMINI_MAX_RPD=1),
            patch("apps.callcenter.transcribe.download_recording", side_effect=fake_download),
            patch("apps.callcenter.transcribe.transcribe_audio", return_value="ok"),
        ):
            summary = transcribe_pending_records()
        good.refresh_from_db()
        # RPD=1 nhưng c1 chết trước khi tiêu quota → c2 vẫn được transcribe.
        self.assertEqual(summary["done"], 1)
        self.assertEqual(good.transcribe_status, TranscribeStatus.DONE)

    def test_requeue_failed_transcripts_command(self):
        from django.core.management import call_command

        self._record(
            "c9",
            transcribe_status=TranscribeStatus.FAILED,
            transcribe_attempts=3,
            transcribe_error="Gemini lỗi: 503",
        )
        call_command("requeue_failed_transcripts")
        record = CallRecord.objects.get(call_id="c9")
        self.assertEqual(record.transcribe_status, TranscribeStatus.PENDING)
        self.assertEqual(record.transcribe_attempts, 0)

    def test_match_backoff_skips_young_record_without_burning_attempt(self):
        record = CallRecord.objects.create(
            call_id="cb1",
            caller=PHONE,
            caller_normalized=PHONE,
            recording_url="",
            acd_time=60,
            match_attempts=1,  # lượt 2 cần record ≥15' tuổi
        )
        summary = match_unmatched_records()
        record.refresh_from_db()
        self.assertEqual(summary["scanned"], 0, "chưa tới lượt theo backoff → không quét")
        self.assertEqual(record.match_attempts, 1, "không được đốt lượt khi skip")


@override_settings(GEMINI_API_KEY="test-key", GEMINI_TRANSCRIBE_BATCH=8, GEMINI_MAX_RPD=300)
class TranscribeQuotaCooldownTests(TestCase):
    """Sự cố prod 2026-07-22: quota thật của Google thấp hơn trần local (300) rất
    nhiều. Counter local incr TRƯỚC khi gọi Gemini nên mỗi request ăn 429 vẫn đốt
    1 slot → counter tự chạm trần và khoá sạch phần còn lại của ngày (23/280 file
    xong). Tick sau vẫn claim batch + tải 1 file rồi vứt, 288 lần/ngày.

    429 RESOURCE_EXHAUSTED là tín hiệu đáng tin duy nhất về quota thật → hoàn lại
    slot local rồi nghỉ trọn một chu kỳ cooldown, thay vì thử lại mỗi 5 phút.
    """

    def setUp(self):
        _reset_quota_counters()

    def _record(self, call_id="c1"):
        return CallRecord.objects.create(
            call_id=call_id,
            caller=PHONE,
            caller_normalized=PHONE,
            recording_url=f"https://rec.zenify.vn/{call_id}.mp3",
            acd_time=60,
            transcribe_status=TranscribeStatus.PENDING,
        )

    @staticmethod
    def _quota_error():
        return TranscribeError(
            "Gemini lỗi: 429 RESOURCE_EXHAUSTED. You exceeded your current quota",
            transient=True,
            quota_exhausted=True,
        )

    def test_gemini_quota_error_refunds_local_counter(self):
        self._record()
        with (
            patch("apps.callcenter.transcribe.download_recording", return_value=b"audio"),
            patch("apps.callcenter.transcribe.transcribe_audio", side_effect=self._quota_error()),
        ):
            transcribe_pending_records()
        day_key = f"gemini:transcribe:rpd:{timezone.now().strftime('%Y%m%d')}"
        self.assertEqual(
            cache.get(day_key), 0, "request bị Google từ chối không được đốt quota local"
        )

    def test_gemini_quota_error_stops_tick_and_requeues_batch(self):
        self._record("c1")
        self._record("c2")
        downloads = []
        with (
            patch(
                "apps.callcenter.transcribe.download_recording",
                side_effect=lambda url: downloads.append(url) or b"audio",
            ),
            patch("apps.callcenter.transcribe.transcribe_audio", side_effect=self._quota_error()),
        ):
            summary = transcribe_pending_records()
        self.assertTrue(summary["quota_stopped"])
        self.assertEqual(summary["done"], 0)
        self.assertEqual(len(downloads), 1, "chạm quota Google thì dừng ngay, không tải tiếp")
        for record in CallRecord.objects.all():
            self.assertEqual(record.transcribe_status, TranscribeStatus.PENDING)
            self.assertEqual(record.transcribe_attempts, 0, "quota không phải lỗi của record")

    def test_cooldown_skips_following_tick_without_claiming(self):
        self._record()
        with (
            patch("apps.callcenter.transcribe.download_recording", return_value=b"audio"),
            patch("apps.callcenter.transcribe.transcribe_audio", side_effect=self._quota_error()),
        ):
            transcribe_pending_records()

        downloads = []
        with (
            patch(
                "apps.callcenter.transcribe.download_recording",
                side_effect=lambda url: downloads.append(url) or b"audio",
            ),
            patch("apps.callcenter.transcribe.transcribe_audio", return_value="ok"),
        ):
            summary = transcribe_pending_records()
        self.assertEqual(summary["claimed"], 0, "đang cooldown thì không claim record nào")
        self.assertTrue(summary["quota_stopped"])
        self.assertEqual(downloads, [], "đang cooldown thì không tải file nào")

    def test_refund_targets_the_bucket_that_was_actually_consumed(self):
        """Timeout Gemini là 120s → request dễ vắt qua ranh giới phút. Refund phải
        hoàn đúng bucket đã trừ, không trừ oan counter của phút kế tiếp."""
        from apps.callcenter.transcribe import _consume_quota, _refund_quota

        started = timezone.now()
        with patch("apps.callcenter.transcribe.timezone.now", return_value=started):
            consumed = _consume_quota()
        with patch(
            "apps.callcenter.transcribe.timezone.now",
            return_value=started + timedelta(minutes=1),
        ):
            _refund_quota(consumed)

        minute_key = f"gemini:transcribe:rpm:{started.strftime('%Y%m%d%H%M')}"
        self.addCleanup(cache.delete, minute_key)
        self.assertEqual(cache.get(minute_key), 0, "phải hoàn vào bucket phút đã tiêu")

    def test_non_quota_error_keeps_pipeline_running(self):
        self._record()
        with (
            patch("apps.callcenter.transcribe.download_recording", return_value=b"audio"),
            patch(
                "apps.callcenter.transcribe.transcribe_audio",
                side_effect=TranscribeError("Gemini lỗi: 500 INTERNAL", transient=True),
            ),
        ):
            transcribe_pending_records()

        with (
            patch("apps.callcenter.transcribe.download_recording", return_value=b"audio"),
            patch("apps.callcenter.transcribe.transcribe_audio", return_value="ok"),
        ):
            summary = transcribe_pending_records()
        self.assertEqual(summary["done"], 1, "lỗi 5xx không phải quota → không được bật cooldown")
