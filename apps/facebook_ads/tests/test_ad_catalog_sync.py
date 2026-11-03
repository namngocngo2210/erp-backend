"""Đồng bộ catalog ad → page: upsert, idempotent, và không mất ad khi thiếu page."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from apps.facebook_ads.infrastructure import ad_catalog_sync
from apps.facebook_ads.models import (
    FacebookAd,
    FacebookAdAccount,
    FacebookApp,
    FacebookPage,
)
from apps.facebook_ads.services import FacebookGraphError, FacebookTokenError


def _ad(ad_id: str, page_id: str = "111", name: str = "Ad") -> dict:
    payload = {"id": ad_id, "name": name, "campaign": {"id": "c1", "name": "Camp"}}
    if page_id:
        payload["creative"] = {"effective_object_story_id": f"{page_id}_999"}
    return payload


class SyncAdCatalogTests(TestCase):
    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="123", app_secret="s", access_token="t",
        )
        self.account = FacebookAdAccount.objects.create(
            app=self.app, name="Acc", account_id="999", fb_id="act_999",
        )

    def test_creates_ads_and_pages(self):
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1"), _ad("2")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={"111": "Page A"}):
            result = ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        self.assertEqual(result["ads_upserted"], 2)
        self.assertEqual(FacebookPage.objects.get(fb_page_id="111").name, "Page A")
        self.assertEqual(FacebookAd.objects.get(fb_ad_id="1").page.fb_page_id, "111")

    def test_is_idempotent(self):
        for _ in range(2):
            with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1")]), \
                 patch.object(ad_catalog_sync, "_fetch_page_names", return_value={"111": "Page A"}):
                ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        self.assertEqual(FacebookAd.objects.count(), 1)
        self.assertEqual(FacebookPage.objects.count(), 1)

    def test_keeps_ad_when_page_unresolved(self):
        """Không suy ra page thì vẫn phải lưu ad — chi tiêu cần chỗ bám."""
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1", page_id="")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={}):
            result = ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        self.assertEqual(result["unresolved_pages"], 1)
        self.assertIsNone(FacebookAd.objects.get(fb_ad_id="1").page_id)

    def test_updates_name_on_rename(self):
        """Đổi tên trên Ads Manager thì lượt sync sau phải ghi đè."""
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1", name="Cũ")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={"111": "Page A"}):
            ad_catalog_sync.sync_ad_catalog_for_app(self.app)
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1", name="Mới")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={}):
            ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        self.assertEqual(FacebookAd.objects.get(fb_ad_id="1").name, "Mới")

    def test_skips_page_name_fetch_when_recently_synced(self):
        """Tên page hiếm đổi — không gọi lại API mỗi giờ."""
        FacebookPage.objects.create(fb_page_id="111", name="Page A", last_synced_at=None)
        FacebookPage.objects.filter(fb_page_id="111").update(
            last_synced_at=ad_catalog_sync.timezone.now(),
        )
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names") as fetch_names:
            ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        fetch_names.assert_not_called()

    def test_refreshes_page_name_after_window_expires(self):
        """Bump last_synced_at mỗi lượt sync sẽ khiến tên page không bao giờ được lấy lại."""
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={"111": "Tên cũ"}):
            ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        # Đẩy mốc lùi quá cửa sổ refresh
        stale = ad_catalog_sync.timezone.now() - timedelta(
            hours=ad_catalog_sync.PAGE_NAME_REFRESH_HOURS + 1,
        )
        FacebookPage.objects.filter(fb_page_id="111").update(last_synced_at=stale)

        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names",
                          return_value={"111": "Tên mới"}) as fetch_names:
            ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        fetch_names.assert_called_once()
        self.assertEqual(FacebookPage.objects.get(fb_page_id="111").name, "Tên mới")

    def test_retries_page_name_next_run_when_fetch_failed(self):
        """Page mới, batch lấy tên thất bại (page rỗng trong kết quả) → last_synced_at
        phải None để lượt sync kế tiếp coi là stale và thử lại, không phải chờ 24h."""
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={}):
            ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        page = FacebookPage.objects.get(fb_page_id="111")
        self.assertIsNone(page.last_synced_at)

        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1")]), \
             patch.object(ad_catalog_sync, "_fetch_page_names",
                          return_value={"111": "Tên đúng"}) as fetch_names:
            ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        fetch_names.assert_called_once_with(self.app, ["111"])
        self.assertEqual(FacebookPage.objects.get(fb_page_id="111").name, "Tên đúng")


class AdCatalogPerAccountGuardTests(TestCase):
    """1 TKQC lỗi không được làm hỏng catalog của các TKQC còn lại.

    Sibling `ad_spend_sync.sync_ad_spend_for_app` đã có guard này. Thiếu ở đây thì
    một non-200 của `_fetch_ads` ở TKQC thứ 3/50 làm mất catalog của 47 TKQC còn
    lại, MỖI GIỜ — và hệ quả là tiền: ad thiếu catalog sẽ bị lazy-fetch với
    page = NULL nên chi phí rơi vào "Không rõ page".
    """

    def setUp(self):
        self.app = FacebookApp.objects.create(
            name="App", app_id="123", app_secret="s", access_token="t",
        )
        self.bad = FacebookAdAccount.objects.create(
            app=self.app, name="Bad", account_id="1", fb_id="act_1",
        )
        self.good = FacebookAdAccount.objects.create(
            app=self.app, name="Good", account_id="2", fb_id="act_2",
        )

    def _fetch_ads_failing_first(self, exc):
        def _side_effect(account):
            if account.id == self.bad.id:
                raise exc
            return [_ad("GOOD-1", page_id="222")]

        return _side_effect

    def test_one_account_failure_does_not_block_others(self):
        with patch.object(
            ad_catalog_sync, "_fetch_ads",
            side_effect=self._fetch_ads_failing_first(FacebookGraphError("HTTP 500")),
        ), patch.object(ad_catalog_sync, "_fetch_page_names", return_value={"222": "Page B"}):
            with self.assertLogs(
                "apps.facebook_ads.infrastructure.ad_catalog_sync", level="WARNING",
            ):
                result = ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        self.assertEqual(result["failed_accounts"], 1)
        self.assertEqual(result["ads_upserted"], 1)
        self.assertTrue(FacebookAd.objects.filter(fb_ad_id="GOOD-1").exists())

    def test_token_error_still_escalates(self):
        """Token hỏng là sự cố cấp APP: nuốt thành "lỗi lẻ 1 TKQC" thì ngắt mạch
        token không bao giờ bật và không ai được gọi đi cấp token mới."""
        with patch.object(
            ad_catalog_sync, "_fetch_ads",
            side_effect=self._fetch_ads_failing_first(FacebookTokenError("code 190")),
        ), patch.object(ad_catalog_sync, "_fetch_page_names", return_value={}):
            with self.assertRaises(FacebookTokenError):
                ad_catalog_sync.sync_ad_catalog_for_app(self.app)

    def test_malformed_ad_payload_degrades_to_no_page(self):
        """Graph API trả field object dưới dạng chuỗi/list → trước đây AttributeError
        giết cả lượt refresh của app. Giờ chỉ mất page của đúng ad đó."""
        malformed = [
            {"id": "M1", "name": "Ad", "creative": "not-a-dict", "adset": ["also-wrong"]},
            {"id": "M2", "creative": {"object_story_spec": "wrong"}, "campaign": 12},
            "hoàn toàn không phải dict",
        ]
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=malformed), \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={}):
            result = ad_catalog_sync.sync_ad_catalog_for_app(self.app)

        self.assertEqual(result["failed_accounts"], 0)
        self.assertIsNone(FacebookAd.objects.get(fb_ad_id="M1").page_id)
        self.assertIsNone(FacebookAd.objects.get(fb_ad_id="M2").page_id)
        # Dòng không phải dict không có id → không tạo ad rác.
        self.assertEqual(FacebookAd.objects.filter(fb_ad_id="").count(), 0)


class FetchAdsPageLimitTests(SimpleTestCase):
    """Graph API chê trang nặng ("reduce the amount of data") → hạ limit, thử lại
    đúng trang đó thay vì fail cả TKQC (sự cố 2026-09-23)."""

    TOO_MUCH = {
        "error": {
            "code": 1,
            "message": "Please reduce the amount of data you're asking for, then retry your request",
        }
    }

    def setUp(self):
        self.account = SimpleNamespace(id=73, fb_id="act_73", app=SimpleNamespace(access_token="t"))

    @staticmethod
    def _resp(status, body):
        return Mock(status_code=status, json=Mock(return_value=body), text=str(body))

    def _page(self, ids, after=None):
        paging = {"cursors": {"after": after}, "next": "https://x"} if after else {}
        return self._resp(200, {"data": [{"id": i} for i in ids], "paging": paging})

    def test_halves_limit_and_retries_same_page(self):
        responses = [
            self._resp(400, self.TOO_MUCH),
            self._resp(400, self.TOO_MUCH),
            self._page(["1", "2"], after="c1"),
            self._page(["3"]),
        ]
        with patch.object(ad_catalog_sync._client, "get", side_effect=responses) as get:
            ads = ad_catalog_sync._fetch_ads(self.account)

        self.assertEqual([a["id"] for a in ads], ["1", "2", "3"])
        calls = [c.kwargs["params"] for c in get.call_args_list]
        self.assertEqual([p["limit"] for p in calls], [200, 100, 50, 50])
        self.assertNotIn("after", calls[2])
        self.assertEqual(calls[3]["after"], "c1")

    def test_gives_up_at_floor(self):
        with patch.object(
            ad_catalog_sync._client, "get", return_value=self._resp(400, self.TOO_MUCH)
        ) as get, self.assertRaises(FacebookGraphError):
            ad_catalog_sync._fetch_ads(self.account)

        limits = [c.kwargs["params"]["limit"] for c in get.call_args_list]
        self.assertEqual(limits[-1], ad_catalog_sync.AD_PAGE_LIMIT_MIN)
        self.assertEqual(limits, [200, 100, 50, 25])  # 25 vẫn lỗi → raise

    def test_other_errors_are_not_retried(self):
        err = self._resp(500, {"error": {"code": 2, "message": "Service temporarily unavailable"}})
        with patch.object(ad_catalog_sync._client, "get", return_value=err) as get, \
                self.assertRaises(FacebookGraphError):
            ad_catalog_sync._fetch_ads(self.account)
        self.assertEqual(get.call_count, 1)


class SyncAdCatalogSharedAccountTests(TestCase):
    """Cùng một TKQC gắn vào nhiều app (prod 2026-09-23: 71 TKQC × 4 app) → mỗi
    lượt chỉ kéo mỗi fb_id một lần, nếu không task không bao giờ xong trong 600s."""

    def setUp(self):
        self.apps = [
            FacebookApp.objects.create(
                name=f"App{i}", app_id=str(i), app_secret="s", access_token=f"t{i}",
            )
            for i in range(2)
        ]
        self.accounts = [
            FacebookAdAccount.objects.create(
                app=app, name="Acc", account_id="999", fb_id="act_999",
            )
            for app in self.apps
        ]

    def test_skips_account_already_synced_via_other_app(self):
        synced: set[str] = set()
        with patch.object(ad_catalog_sync, "_fetch_ads", return_value=[_ad("1")]) as fetch, \
             patch.object(ad_catalog_sync, "_fetch_page_names", return_value={"111": "Page A"}):
            first = ad_catalog_sync.sync_ad_catalog_for_app(self.apps[0], synced)
            second = ad_catalog_sync.sync_ad_catalog_for_app(self.apps[1], synced)

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual((first["accounts"], second["accounts"]), (1, 0))
        self.assertEqual(synced, {"act_999"})
        self.assertEqual(FacebookAd.objects.get(fb_ad_id="1").ad_account_id, self.accounts[0].pk)

    def test_account_failed_on_one_app_is_retried_on_next(self):
        """Token app trước không có quyền TKQC thì app sau phải được thử — chỉ ghi
        nhận fb_id khi kéo THÀNH CÔNG."""
        synced: set[str] = set()
        with patch.object(
            ad_catalog_sync, "_fetch_ads",
            side_effect=[FacebookGraphError("HTTP 403"), [_ad("1")]],
        ) as fetch, patch.object(ad_catalog_sync, "_fetch_page_names", return_value={}):
            with self.assertLogs(
                "apps.facebook_ads.infrastructure.ad_catalog_sync", level="WARNING",
            ):
                first = ad_catalog_sync.sync_ad_catalog_for_app(self.apps[0], synced)
            second = ad_catalog_sync.sync_ad_catalog_for_app(self.apps[1], synced)

        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(first["failed_accounts"], 1)
        self.assertEqual(second["ads_upserted"], 1)
        self.assertEqual(FacebookAd.objects.get(fb_ad_id="1").ad_account_id, self.accounts[1].pk)
