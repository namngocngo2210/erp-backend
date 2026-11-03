"""Task catalog: bỏ qua app token hỏng, không để lỗi một app chặn app còn lại."""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from apps.facebook_ads import tasks
from apps.facebook_ads.models import FacebookApp
from apps.facebook_ads.services import FacebookGraphError


class SyncAllAdCatalogTests(TestCase):
    def setUp(self):
        self.ok_app = FacebookApp.objects.create(
            name="OK", app_id="1", app_secret="s", access_token="t",
        )
        self.dead_app = FacebookApp.objects.create(
            name="Dead", app_id="2", app_secret="s", access_token="t",
            token_status=FacebookApp.TOKEN_INVALID,
        )

    def test_skips_app_with_invalid_token(self):
        """Token chết thì gọi API bao nhiêu lần cũng vô nghĩa — ngắt mạch."""
        with patch.object(tasks, "sync_ad_catalog_for_app", return_value={
            "accounts": 1, "ads_upserted": 3, "pages_upserted": 1, "unresolved_pages": 0,
        }) as sync:
            summary = tasks._sync_all_ad_catalog()

        self.assertEqual(summary["skipped_apps"], 1)
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(sync.call_args[0][0].pk, self.ok_app.pk)

    def test_one_app_failing_does_not_stop_others(self):
        other = FacebookApp.objects.create(
            name="Other", app_id="3", app_secret="s", access_token="t",
        )
        calls = []

        def _side_effect(app, *_args):
            calls.append(app.pk)
            if app.pk == self.ok_app.pk:
                raise FacebookGraphError("rate limit")
            return {"accounts": 1, "ads_upserted": 1, "pages_upserted": 1, "unresolved_pages": 0}

        with patch.object(tasks, "sync_ad_catalog_for_app", side_effect=_side_effect):
            summary = tasks._sync_all_ad_catalog()

        self.assertIn(other.pk, calls)
        self.assertEqual(len(summary["errors"]), 1)

    def test_successful_sync_marks_token_ok(self):
        """Sync thành công phải tự đóng lại mạch ngắt — nếu không, app từng gặp lỗi
        tạm thời rồi phục hồi vẫn kẹt ở trạng thái cũ mãi vì không ai xoá cờ đó."""
        self.assertEqual(self.ok_app.token_status, FacebookApp.TOKEN_UNKNOWN)
        with patch.object(tasks, "sync_ad_catalog_for_app", return_value={
            "accounts": 1, "ads_upserted": 1, "pages_upserted": 1, "unresolved_pages": 0,
        }):
            tasks._sync_all_ad_catalog()

        self.ok_app.refresh_from_db()
        self.assertEqual(self.ok_app.token_status, FacebookApp.TOKEN_OK)

    def test_skips_invalid_token_with_throttled_log(self):
        """Bỏ qua app token hỏng phải để lại dấu vết quan sát được (throttled 1
        lần/giờ) — nếu im lặng hoàn toàn thì không ai biết catalog app này đã
        ngừng đồng bộ cho tới khi có người tình cờ phát hiện."""
        # Khoá tách theo từng task: dùng chung một khoá thì task nào chạy trước
        # trong giờ đó chiếm mất cache.add, hai task còn lại im lặng hoàn toàn.
        cache_key = tasks._breaker_log_key("ad_catalog", self.dead_app.pk)
        cache.delete(cache_key)

        with patch.object(tasks, "sync_ad_catalog_for_app", return_value={
            "accounts": 1, "ads_upserted": 1, "pages_upserted": 1, "unresolved_pages": 0,
        }):
            tasks._sync_all_ad_catalog()

        self.assertIsNotNone(cache.get(cache_key))

    def test_passes_one_shared_synced_set_across_apps(self):
        """Tập fb_id đã kéo phải dùng CHUNG giữa các app trong một lượt — mỗi app
        một tập riêng thì TKQC gắn vào nhiều app lại bị kéo lặp."""
        FacebookApp.objects.create(name="Third", app_id="3", app_secret="s", access_token="t")
        seen = []

        def _side_effect(app, synced_fb_ids):
            seen.append(synced_fb_ids)
            synced_fb_ids.add(f"act_{app.pk}")
            return {"accounts": 1, "ads_upserted": 0, "pages_upserted": 0, "unresolved_pages": 0}

        with patch.object(tasks, "sync_ad_catalog_for_app", side_effect=_side_effect):
            tasks._sync_all_ad_catalog()

        self.assertEqual(len(seen), 2)
        self.assertIs(seen[0], seen[1])
