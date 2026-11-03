"""API máy-tới-máy cấp hội thoại chat cho agent phân tích ngoài (Hermes).

Endpoint là ``AllowAny`` + token tĩnh nên bộ test gác hai thứ dễ hỏng nhất, y
như bộ test transcript cuộc gọi: cửa xác thực (thiếu/sai/chưa cấu hình) và
phân trang keyset (lặp hoặc sót một trang là hỏng dữ liệu phân tích, im lặng).
Dữ liệu chèn thẳng vào ``ai.hoi_thoai`` / ``ai.tin_nhan`` — endpoint đọc mart,
không đụng ETL, nên không cần dựng nguồn ``ai_stg.chat_*``.
"""

import itertools
from datetime import timedelta

from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

LIST_URL = "/api/ai/hermes/conversations"
TOKEN = "hermes-test-token"

_ids = itertools.count(1)


def _insert(table: str, **values) -> int:
    cols = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    with connection.cursor() as cur:
        cur.execute(f"INSERT INTO ai.{table} ({cols}) VALUES ({marks})", list(values.values()))
    return next(iter(values.values()))


def mk_conversation(*, ended=None, **kw) -> int:
    ended = ended or (timezone.now() - timedelta(days=1))
    values = {
        "hoi_thoai_id": next(_ids),
        "pancake_hoi_thoai_id": f"pc-{next(_ids)}",
        "page_ten": "Delax Fanpage",
        "nen_tang_text": "Facebook",
        "loai_kenh_text": "Tin nhắn",
        "bat_dau": ended - timedelta(hours=1),
        "bat_dau_ngay": (ended - timedelta(hours=1)).date(),
        "ket_thuc": ended,
        "ket_thuc_ngay": ended.date(),
        "so_tin": 3,
        "so_tin_khach": 2,
        "so_tin_page": 1,
        "co_khach_tra_loi": True,
        "do_bot_xu_ly": False,
        "nhan_tag": ["Đã tư vấn"],
        "khach_ten": "Chị Lan",
        "sdt_che": "0912***678",
        "noi_dung": "Khách: ghế này bao nhiêu\nPage: dạ 20 triệu ạ",
        "tom_tat_ai": "Khách hỏi giá ghế",
        "nhu_cau_ai_text": "Hỏi giá",
        "giai_doan_text": "Hỏi giá",
        "trang_thai_gan_nhan_text": "Đã gán nhãn",
        "dong_bo_luc": timezone.now(),
    }
    values.update(kw)
    return _insert("hoi_thoai", **values)


def mk_message(conv_id: int, *, sent=None, direction="Khách gửi", content="xin chào", **kw) -> int:
    values = {
        "tin_nhan_id": next(_ids),
        "hoi_thoai_id": conv_id,
        "page_ten": "Delax Fanpage",
        "gui_luc": sent or timezone.now(),
        "chieu_text": direction,
        "loai_tin_text": "Văn bản",
        "noi_dung": content,
        "co_dinh_kem": False,
    }
    values.update(kw)
    return _insert("tin_nhan", **values)


def messages_url(conv_id: int) -> str:
    return f"{LIST_URL}/{conv_id}/messages"


class HermesChatTokenGateSmokeTests(TestCase):
    """Smoke "đi qua cửa" cho bản sao token-gate của ai_mart.

    Bộ đầy đủ (thiếu/sai token, Bearer, JWT CRM không đủ, fail-closed trên prod)
    nằm ở `apps/callcenter/tests/test_hermes_transcript_api.py::HermesAuthTests`
    — helper gốc mà `_token_gate` ở đây chép lại. Ở đây chỉ chứng minh cả 2
    endpoint đều đi qua cùng cửa: chặn khi thiếu, mở khi đúng, đóng khi prod thiếu env.
    """

    def test_both_endpoints_go_through_the_token_gate(self):
        for url in (LIST_URL, messages_url(1)):
            with self.subTest(url=url):
                with override_settings(HERMES_API_TOKEN=TOKEN):
                    self.assertEqual(APIClient().get(url).status_code, 401)
                    # Qua cửa = không còn 401/503; messages của hội thoại chưa có → 404 nghiệp vụ.
                    passed = APIClient().get(url, HTTP_AUTHORIZATION=f"Bearer {TOKEN}").status_code
                    self.assertNotIn(passed, (401, 503))
                with override_settings(HERMES_API_TOKEN="", IS_PRODUCTION=True):
                    self.assertEqual(APIClient().get(url).status_code, 503)


@override_settings(HERMES_API_TOKEN=TOKEN)
class HermesConversationListTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def get(self, **params):
        return self.client.get(LIST_URL, params, HTTP_X_API_TOKEN=TOKEN)

    def test_item_shape(self):
        conv = mk_conversation(don_id=77, ma_don="DH077", khach_id=5)
        body = self.get().json()
        self.assertEqual(body["limit"], 50)
        self.assertFalse(body["has_more"])
        self.assertIsNone(body["next_cursor"])
        (item,) = body["items"]
        self.assertEqual(item["id"], conv)
        self.assertEqual(item["platform"], "Facebook")
        self.assertEqual(item["channel_type"], "Tin nhắn")
        self.assertEqual(item["page"], "Delax Fanpage")
        self.assertEqual(item["customer_name"], "Chị Lan")
        self.assertEqual(item["customer_phone_masked"], "0912***678")
        self.assertEqual(item["message_count"], 3)
        self.assertEqual(item["customer_message_count"], 2)
        self.assertTrue(item["customer_replied"])
        self.assertFalse(item["bot_handled"])
        self.assertEqual(item["tags"], ["Đã tư vấn"])
        self.assertEqual(item["order_id"], 77)
        self.assertEqual(item["order_code"], "DH077")
        self.assertEqual(item["customer_id"], 5)
        self.assertEqual(item["ai"]["summary"], "Khách hỏi giá ghế")
        self.assertEqual(item["ai"]["stage"], "Hỏi giá")
        self.assertEqual(item["ai"]["label_status"], "Đã gán nhãn")
        self.assertEqual(item["cursor"], conv)
        self.assertIn("ended_at", item)
        self.assertIn("synced_at", item)
        # Nội dung gộp nặng — mặc định KHÔNG trả, phải xin bằng include_content.
        self.assertNotIn("content", item)

    def test_include_content(self):
        mk_conversation()
        (item,) = self.get(include_content="1").json()["items"]
        self.assertTrue(item["content"].startswith("Khách: ghế này"))

    def test_default_window_is_seven_days_by_last_message(self):
        old = mk_conversation(ended=timezone.now() - timedelta(days=9))
        fresh = mk_conversation()
        ids = [i["id"] for i in self.get().json()["items"]]
        self.assertEqual(ids, [fresh])
        ids = [i["id"] for i in self.get(**{"from": "2000-01-01"}).json()["items"]]
        self.assertEqual(ids, [old, fresh])

    def test_to_date_expands_to_end_of_day(self):
        now = timezone.localtime()
        conv = mk_conversation(ended=now.replace(hour=23, minute=30))
        ids = [i["id"] for i in self.get(to=now.date().isoformat()).json()["items"]]
        self.assertEqual(ids, [conv])

    def test_bad_date_is_400(self):
        resp = self.get(**{"from": "hôm-qua"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("from", resp.json()["detail"])

    def test_platform_and_channel_type_filters(self):
        fb_inbox = mk_conversation()
        mk_conversation(loai_kenh_text="Bình luận")
        zalo = mk_conversation(nen_tang_text="Zalo OA")
        ids = [
            i["id"]
            for i in self.get(platform="facebook", channel_type="Tin nhắn").json()["items"]
        ]
        self.assertEqual(ids, [fb_inbox])
        ids = [i["id"] for i in self.get(platform="Zalo OA").json()["items"]]
        self.assertEqual(ids, [zalo])

    def test_page_filter(self):
        mk_conversation()
        other = mk_conversation(page_ten="Delax Massage")
        ids = [i["id"] for i in self.get(page="Delax Massage").json()["items"]]
        self.assertEqual(ids, [other])

    def test_bot_excluded_by_default(self):
        human = mk_conversation()
        bot = mk_conversation(do_bot_xu_ly=True)
        # NULL cờ bot đọc như false — hội thoại chưa gắn thẻ vẫn là của người.
        untagged = mk_conversation(do_bot_xu_ly=None)
        ids = [i["id"] for i in self.get().json()["items"]]
        self.assertEqual(ids, [human, untagged])
        ids = [i["id"] for i in self.get(exclude_bot="0").json()["items"]]
        self.assertEqual(ids, [human, bot, untagged])

    def test_only_replied_and_min_customer_messages(self):
        talkative = mk_conversation(so_tin_khach=5)
        silent = mk_conversation(co_khach_tra_loi=False, so_tin_khach=0)
        ids = [i["id"] for i in self.get(only_replied="1").json()["items"]]
        self.assertEqual(ids, [talkative])
        ids = [i["id"] for i in self.get(min_customer_messages="3").json()["items"]]
        self.assertEqual(ids, [talkative])
        self.assertEqual(len(self.get().json()["items"]), 2, (talkative, silent))

    def test_keyset_pagination_covers_every_row_once(self):
        ids = [mk_conversation() for _ in range(5)]
        seen, cursor = [], None
        for _ in range(10):
            params = {"limit": "2"}
            if cursor:
                params["cursor"] = cursor
            body = self.get(**params).json()
            seen.extend(i["id"] for i in body["items"])
            if not body["has_more"]:
                self.assertIsNone(body["next_cursor"])
                break
            cursor = body["next_cursor"]
        self.assertEqual(seen, ids)

    def test_limit_is_clamped(self):
        self.assertEqual(self.get(limit="9999").json()["limit"], 200)
        self.assertEqual(self.get(limit="0").json()["limit"], 1)
        self.assertEqual(self.get(limit="abc").status_code, 400)


@override_settings(HERMES_API_TOKEN=TOKEN)
class HermesConversationMessagesTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def get(self, conv_id, **params):
        return self.client.get(messages_url(conv_id), params, HTTP_X_API_TOKEN=TOKEN)

    def test_unknown_conversation_is_404(self):
        self.assertEqual(self.get(999_999).status_code, 404)

    def test_messages_in_send_order_with_shape(self):
        conv = mk_conversation()
        base = timezone.now() - timedelta(hours=2)
        # Chèn lệch thứ tự id để chắc là sắp theo thời gian gửi, không theo id.
        later = mk_message(
            conv, sent=base + timedelta(minutes=5), direction="Page gửi", content="dạ 20 triệu"
        )
        first = mk_message(conv, sent=base, content="ghế này bao nhiêu")
        mk_message(next(_ids) + 1000, sent=base, content="hội thoại khác")
        body = self.get(conv).json()
        self.assertEqual([m["id"] for m in body["items"]], [first, later])
        self.assertEqual(body["conversation_id"], conv)
        msg = body["items"][0]
        self.assertEqual(msg["direction"], "Khách gửi")
        self.assertEqual(msg["content"], "ghế này bao nhiêu")
        self.assertEqual(msg["type"], "Văn bản")
        self.assertFalse(msg["has_attachment"])
        self.assertIn("sent_at", msg)
        self.assertFalse(body["has_more"])

    def test_keyset_pagination_follows_send_order(self):
        conv = mk_conversation()
        base = timezone.now() - timedelta(hours=2)
        # id giảm dần theo thời gian: keyset theo id trần sẽ trả sai thứ tự.
        expected = [
            mk_message(conv, sent=base + timedelta(minutes=m), content=f"tin {m}")
            for m in (4, 3, 2, 1, 0)
        ][::-1]
        seen, cursor = [], None
        for _ in range(10):
            params = {"limit": "2"}
            if cursor:
                params["cursor"] = cursor
            body = self.get(conv, **params).json()
            seen.extend(m["id"] for m in body["items"])
            if not body["has_more"]:
                break
            cursor = body["next_cursor"]
        self.assertEqual(seen, expected)

    def test_unknown_cursor_is_400(self):
        conv = mk_conversation()
        mk_message(conv)
        resp = self.get(conv, cursor="424242")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cursor", resp.json()["detail"])

    def test_limit_is_clamped(self):
        conv = mk_conversation()
        self.assertEqual(self.get(conv, limit="9999").json()["limit"], 500)
        self.assertEqual(self.get(conv).json()["limit"], 200)
