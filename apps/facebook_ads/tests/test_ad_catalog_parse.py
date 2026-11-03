"""Suy page_id từ payload /ads. Ba nguồn, thử theo thứ tự ưu tiên."""

from django.test import SimpleTestCase

from apps.facebook_ads.ad_catalog import parse_ad_payload, resolve_page_id


class ResolvePageIdTests(SimpleTestCase):
    def test_from_effective_object_story_id(self):
        ad = {"creative": {"effective_object_story_id": "111_999"}}
        self.assertEqual(resolve_page_id(ad), "111")

    def test_falls_back_to_object_story_spec(self):
        ad = {"creative": {"object_story_spec": {"page_id": "222"}}}
        self.assertEqual(resolve_page_id(ad), "222")

    def test_falls_back_to_promoted_object(self):
        """Campaign tin nhắn/lead: page nằm ở adset chứ không ở creative."""
        ad = {"adset": {"promoted_object": {"page_id": "333"}}}
        self.assertEqual(resolve_page_id(ad), "333")

    def test_prefers_story_id_over_others(self):
        ad = {
            "creative": {
                "effective_object_story_id": "111_999",
                "object_story_spec": {"page_id": "222"},
            },
            "adset": {"promoted_object": {"page_id": "333"}},
        }
        self.assertEqual(resolve_page_id(ad), "111")

    def test_returns_empty_when_nothing_resolves(self):
        self.assertEqual(resolve_page_id({}), "")
        self.assertEqual(resolve_page_id({"creative": {}}), "")

    def test_ignores_non_numeric_values(self):
        """Graph API thỉnh thoảng trả chuỗi lạ — không được nhận làm page id."""
        ad = {"creative": {"effective_object_story_id": "abc_999"}}
        self.assertEqual(resolve_page_id(ad), "")

    def test_falls_through_from_invalid_story_id_to_object_story_spec(self):
        """Story id hỏng KHÔNG được chặn nguồn sau — nếu không, ad vẫn còn page mà bị coi là không rõ."""
        ad = {
            "creative": {
                "effective_object_story_id": "abc_999",
                "object_story_spec": {"page_id": "222"},
            },
        }
        self.assertEqual(resolve_page_id(ad), "222")

    def test_prefers_object_story_spec_over_promoted_object(self):
        """Creative sát với quảng cáo hơn adset, nên thắng khi cả hai cùng có page."""
        ad = {
            "creative": {"object_story_spec": {"page_id": "222"}},
            "adset": {"promoted_object": {"page_id": "333"}},
        }
        self.assertEqual(resolve_page_id(ad), "222")


class ParseAdPayloadTests(SimpleTestCase):
    def test_extracts_all_fields(self):
        ad = {
            "id": "120210",
            "name": "Ad A",
            "campaign": {"id": "c1", "name": "Camp A"},
            "adset": {"id": "s1", "name": "Set A"},
            "creative": {"effective_object_story_id": "111_999"},
        }
        self.assertEqual(
            parse_ad_payload(ad),
            {
                "fb_ad_id": "120210",
                "name": "Ad A",
                "campaign_id": "c1",
                "campaign_name": "Camp A",
                "adset_id": "s1",
                "adset_name": "Set A",
                "fb_page_id": "111",
            },
        )

    def test_non_dict_nested_fields_do_not_raise(self):
        """Graph API là schema-less: field khai là object vẫn có thể về dạng chuỗi/
        list/số. Trước fix, `.get()` trên chuỗi ném AttributeError và giết cả lượt
        refresh catalog của app (list comprehension không có guard) — kéo theo ad
        thiếu catalog bị lazy-fetch với page = NULL, chi phí rơi vào "Không rõ page".
        Suy biến thành "không rõ page" cho ĐÚNG ad đó là hành vi đúng.
        """
        ad = {
            "id": "1",
            "campaign": "not-a-dict",
            "adset": ["nor", "this"],
            "creative": 42,
        }
        self.assertEqual(resolve_page_id(ad), "")
        self.assertEqual(
            parse_ad_payload(ad),
            {
                "fb_ad_id": "1",
                "name": "",
                "campaign_id": "",
                "campaign_name": "",
                "adset_id": "",
                "adset_name": "",
                "fb_page_id": "",
            },
        )

    def test_non_dict_payload_degrades_instead_of_raising(self):
        self.assertEqual(resolve_page_id("hoàn toàn không phải dict"), "")
        self.assertEqual(parse_ad_payload(None)["fb_ad_id"], "")

    def test_non_dict_object_story_spec_falls_through_to_promoted_object(self):
        """Nguồn 2 hỏng không được chặn nguồn 3 — ad vẫn còn page thật ở adset."""
        ad = {
            "creative": {"object_story_spec": "wrong"},
            "adset": {"promoted_object": {"page_id": "333"}},
        }
        self.assertEqual(resolve_page_id(ad), "333")

    def test_missing_optional_fields_become_empty_strings(self):
        self.assertEqual(
            parse_ad_payload({"id": "1"}),
            {
                "fb_ad_id": "1",
                "name": "",
                "campaign_id": "",
                "campaign_name": "",
                "adset_id": "",
                "adset_name": "",
                "fb_page_id": "",
            },
        )
