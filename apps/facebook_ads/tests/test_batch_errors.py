"""Lỗi từ Graph Batch API: phân loại token vs tạm thời + sub-error không bị nuốt.

Gộp từ test_token_error_classification.py + test_batch_sub_errors.py
(tái cấu trúc 2026-08-05). Docstring gốc của từng file giữ ở docstring class.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase

from apps.facebook_ads import services
from apps.facebook_ads.models import FacebookAdAccount, FacebookApp
from apps.facebook_ads.tests._fakes import _FakeResponse


class BatchErrorClassificationTests(TestCase):
    """Lỗi xác thực (OAuth) phải phân biệt được với lỗi tạm thời.

    Trước đây mọi lỗi Graph API đều là `FacebookGraphError` với chỉ nguyên phần
    ``message`` — task không thể biết nên retry (429/5xx) hay ngắt mạch + gọi người
    (token chết). Xem docs/facebook-token-lifecycle-2026-07-27.md §3.2.
    """

    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="123", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="1", fb_id="act_1",
            status=FacebookAdAccount.STATUS_ACTIVE,
        )

    def test_expired_session_raises_token_error(self):
        response = _FakeResponse(
            400,
            {
                "error": {
                    "message": "Error validating access token: Session has expired on Saturday.",
                    "type": "OAuthException",
                    "code": 190,
                    "error_subcode": 463,
                }
            },
        )

        with patch.object(services._client, "post", return_value=response):
            with self.assertRaises(services.FacebookTokenError) as ctx:
                services.fetch_insights_batch(
                    self.app, [self.account], level="account", date_preset="today",
                )

        self.assertIn("Session has expired", str(ctx.exception))

    def test_non_auth_error_stays_generic_graph_error(self):
        response = _FakeResponse(
            400,
            {"error": {"message": "Please reduce the amount of data", "type": "OAuthException", "code": 1}},
        )

        with patch.object(services._client, "post", return_value=response):
            with self.assertRaises(services.FacebookGraphError) as ctx:
                services.fetch_insights_batch(
                    self.app, [self.account], level="account", date_preset="today",
                )

        # code 1 = lỗi tạm thời phía FB, KHÔNG phải token hỏng → không được ngắt mạch.
        self.assertNotIsInstance(ctx.exception, services.FacebookTokenError)

    def test_oauth_sub_error_escalates_to_token_error(self):
        # Batch trả HTTP 200 nhưng sub-request lỗi 190: token cấp app hỏng chứ không
        # phải riêng account đó — phải leo thang thay vì ghi nhận "1 account lỗi".
        response = _FakeResponse(
            200,
            [
                {
                    "code": 401,
                    "body": json.dumps(
                        {
                            "error": {
                                "message": "Error validating access token: Session has expired",
                                "type": "OAuthException",
                                "code": 190,
                            }
                        }
                    ),
                }
            ],
        )

        with patch.object(services._client, "post", return_value=response):
            with self.assertRaises(services.FacebookTokenError):
                services.fetch_insights_batch(
                    self.app, [self.account], level="account", date_preset="today",
                )

    def test_account_level_sub_error_still_reported_as_failed_account(self):
        # Lỗi không phải OAuth (vd account bị vô hiệu hoá) giữ hành vi cũ: ghi vào
        # sentinel _errors, không ném exception, không ngắt mạch cả app.
        response = _FakeResponse(
            200,
            [
                {
                    "code": 400,
                    "body": json.dumps(
                        {"error": {"message": "Ad account is disabled", "code": 100}}
                    ),
                }
            ],
        )

        with patch.object(services._client, "post", return_value=response):
            with self.assertLogs("apps.facebook_ads.services", level="WARNING"):
                results = services.fetch_insights_batch(
                    self.app, [self.account], level="account", date_preset="today",
                )

        self.assertEqual(results[self.account.id], [])
        self.assertIn(self.account.id, results["_errors"])

    def test_ad_account_listing_classifies_token_error(self):
        response = _FakeResponse(
            400,
            {"error": {"message": "Invalid OAuth access token", "type": "OAuthException", "code": 190}},
        )

        with patch.object(services._client, "get", return_value=response):
            with self.assertRaises(services.FacebookTokenError):
                services.sync_ad_accounts_for_app(self.app)


class FetchInsightsBatchSubErrorTests(TestCase):
    """Batch API sub-error (vd token hết hạn cho 1 ad account) không được nuốt im lặng.

    Bug sửa: `results.setdefault("_errors", {})` đứng riêng 1 dòng nên KHÔNG gán được
    `err_msg` vào dict trả về — lỗi sub-account biến mất, log/Sentry không thấy gì,
    cron tưởng chi tiêu = 0 (task 2.7).
    """

    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="123", app_secret="s", access_token="t",
        )
        self.ok_account = FacebookAdAccount.objects.create(
            app=self.app, name="OK", account_id="1", fb_id="act_1",
            status=FacebookAdAccount.STATUS_ACTIVE,
        )
        self.expired_account = FacebookAdAccount.objects.create(
            app=self.app, name="Expired token", account_id="2", fb_id="act_2",
            status=FacebookAdAccount.STATUS_ACTIVE,
        )

    def _batch_response(self):
        return _FakeResponse(
            200,
            [
                {
                    "code": 200,
                    "body": json.dumps({"data": [{"spend": "10", "date_start": "2026-07-01"}]}),
                },
                {
                    "code": 400,
                    "body": json.dumps(
                        {"error": {"message": "Error validating access token: Session has expired"}}
                    ),
                },
            ],
        )

    def test_sub_error_recorded_in_errors_and_logged(self):
        with patch.object(services._client, "post", return_value=self._batch_response()):
            with self.assertLogs("apps.facebook_ads.services", level="WARNING") as logs:
                results = services.fetch_insights_batch(
                    self.app, [self.ok_account, self.expired_account],
                    level="account", date_preset="today", time_increment="1",
                )

        # Account lỗi: rows rỗng (không giả 0 chi tiêu là "đã đồng bộ") NHƯNG lỗi
        # phải nằm trong sentinel "_errors", không biến mất.
        assert results[self.ok_account.id] == [{"spend": "10", "date_start": "2026-07-01"}]
        assert results[self.expired_account.id] == []
        assert self.expired_account.id in results["_errors"]
        assert "Session has expired" in results["_errors"][self.expired_account.id]
        assert any("insights batch failed" in message for message in logs.output)

    def test_sync_daily_spend_logs_and_counts_failed_accounts(self):
        with patch.object(services._client, "post", return_value=self._batch_response()):
            with self.assertLogs("apps.facebook_ads.services", level="WARNING"):
                summary = services.sync_daily_spend_for_app(self.app, date_preset="today")

        assert summary["failed_accounts"] == 1

    def test_paginates_sub_request_until_no_next(self):
        """limit=500 chưa từng tràn ở level=account (7 dòng/account), nhưng level=ad
        với time_increment=1 dễ vượt — bỏ qua `paging.next` là cắt cụt data mà FB
        không hề báo lỗi (mất tiền âm thầm).
        """
        first_page_body = {
            "data": [{"spend": "1", "date_start": "2026-07-01"}],
            "paging": {"next": "https://graph.facebook.com/v19.0/act_1/insights?after=CURSOR"},
        }
        second_page_body = {"data": [{"spend": "2", "date_start": "2026-07-02"}]}
        batch_response = _FakeResponse(
            200, [{"code": 200, "body": json.dumps(first_page_body)}],
        )
        next_page_response = _FakeResponse(200, second_page_body)

        with patch.object(services._client, "post", return_value=batch_response), \
             patch.object(services._client, "get", return_value=next_page_response) as mock_get:
            results = services.fetch_insights_batch(
                self.app, [self.ok_account],
                level="ad", date_preset="today", time_increment="1",
            )

        assert results[self.ok_account.id] == [
            {"spend": "1", "date_start": "2026-07-01"},
            {"spend": "2", "date_start": "2026-07-02"},
        ]
        mock_get.assert_called_once()

    def _paginated_batch(self, next_url: str):
        first_page_body = {
            "data": [{"spend": "1", "date_start": "2026-07-01"}],
            "paging": {"next": next_url},
        }
        return (
            _FakeResponse(200, [{"code": 200, "body": json.dumps(first_page_body)}]),
            _FakeResponse(200, {"data": [{"spend": "2", "date_start": "2026-07-02"}]}),
        )

    def _follow_url(self, next_url: str) -> str:
        batch_response, next_page_response = self._paginated_batch(next_url)
        with patch.object(services._client, "post", return_value=batch_response), \
             patch.object(services._client, "get", return_value=next_page_response) as mock_get:
            services.fetch_insights_batch(
                self.app, [self.ok_account],
                level="ad", date_preset="today", time_increment="1",
            )
        mock_get.assert_called_once()
        return mock_get.call_args[0][0]

    def test_pagination_request_is_authenticated_when_next_url_has_no_token(self):
        """`paging.next` lấy từ BODY của một sub-request Batch — sub-request KHÔNG
        mang token riêng (token cấp một lần ở envelope). Nếu URL FB sinh ra không
        kèm token thì GET tiếp theo trả error 190 → xếp vào FacebookTokenError →
        `mark_token_invalid(app)` → ngắt mạch chặn cả grain account lẫn báo cáo Lark.
        Một token KHOẺ bị khai tử toàn công ty chỉ vì một URL phân trang.

        Mọi test khác đều mock `_client.get` nên CI không phân biệt được hai thế
        giới — test này ghim đúng thứ đi ra dây: URL phải có access_token.
        """
        url = self._follow_url("https://graph.facebook.com/v19.0/act_1/insights?after=CURSOR")

        self.assertIn("access_token=t", url)
        self.assertIn("after=CURSOR", url)

    def test_pagination_does_not_duplicate_existing_token(self):
        """URL đã có token thì giữ nguyên — hai tham số `access_token` là request hỏng."""
        original = (
            "https://graph.facebook.com/v19.0/act_1/insights?access_token=t&after=CURSOR"
        )
        url = self._follow_url(original)

        self.assertEqual(url, original)
        self.assertEqual(url.count("access_token="), 1)

    def test_pagination_preserves_cursor_untouched(self):
        """Cursor của FB là base64 có thể chứa `+`/`=`; parse rồi encode lại query sẽ
        biến `+` thành khoảng trắng và làm hỏng cursor — phải nối chuỗi, không roundtrip.
        """
        cursor = "MjM4NDI5NTM0OTc4Nzg1MTM=+abc/def"
        url = self._follow_url(
            f"https://graph.facebook.com/v19.0/act_1/insights?after={cursor}"
        )

        self.assertIn(f"after={cursor}", url)

    def test_pagination_bound_stops_looping_and_logs(self):
        """`paging.next` bất thường (vd lặp cùng 1 cursor) phải bị chặn, không treo mãi
        — nhưng phải LOG rõ, vì cắt data âm thầm chính là bug đang sửa.
        """
        looping_url = "https://graph.facebook.com/v19.0/act_1/insights?after=SAME"
        first_page_body = {
            "data": [{"spend": "1", "date_start": "2026-07-01"}],
            "paging": {"next": looping_url},
        }
        batch_response = _FakeResponse(
            200, [{"code": 200, "body": json.dumps(first_page_body)}],
        )
        looping_page_response = _FakeResponse(
            200, {"data": [{"spend": "1", "date_start": "x"}], "paging": {"next": looping_url}},
        )

        with patch.object(services._client, "post", return_value=batch_response), \
             patch.object(services._client, "get", return_value=looping_page_response):
            with self.assertLogs("apps.facebook_ads.services", level="WARNING") as logs:
                results = services.fetch_insights_batch(
                    self.app, [self.ok_account],
                    level="ad", date_preset="today", time_increment="1",
                )

        # Không treo vô hạn: số dòng bị chặn ở MAX_INSIGHTS_PAGES nhưng vẫn > 1 (đã
        # cố lấy nhiều trang trước khi dừng), và có log cảnh báo chạm giới hạn.
        assert 1 < len(results[self.ok_account.id]) <= services.MAX_INSIGHTS_PAGES
        assert any("pagination" in message.lower() for message in logs.output)
