"""Single-flight cho cache enriched rows của Danh sách contact (audit 2026-09-23 perf §3.1).

Cache locmem riêng (mirror ContactRowsCacheTests) — không đụng Redis thật.
- Miss + giành được lock → tính 1 lần, ghi cache; request kế đọc cache.
- Miss + KHÔNG giành được lock (tab khác đang tính): chờ ngắn rồi đọc cache; cache
  vẫn trống (compute chậm/Redis chết) → tự tính — fail-open, không trả thiếu.
"""
from unittest import mock

import pytest
from django.test import TestCase, override_settings

from apps.orders.application.services.contact_list_service import ContactListService
from apps.users.tests.helpers import make_admin

@pytest.mark.usefixtures("isolated_cache")
@override_settings(ORDERS_LIST_COUNT_CACHE_TTL=120)
class ContactRowsSingleFlightTests(TestCase):
    def setUp(self):
        self.admin = make_admin("admin-contacts-sf")

    def test_lock_holder_computes_once_and_fills_cache(self):
        with mock.patch.object(
            ContactListService, "_compute_enriched_rows",
            wraps=ContactListService._compute_enriched_rows,
        ) as compute:
            ContactListService.list_contacts(user=self.admin)
            ContactListService.list_contacts(user=self.admin)
        self.assertEqual(compute.call_count, 1)

    def test_lock_not_acquired_falls_back_to_compute_when_cache_stays_empty(self):
        # cache.add trả False = tiến trình khác đang giữ khoá; không ai ghi cache →
        # sau vòng chờ (sleep patch để không tốn 1,5s) phải tự tính, KHÔNG trả rỗng.
        with mock.patch("django.core.cache.cache.add", return_value=False), \
             mock.patch(
                 "apps.orders.application.services.contact_list_service._time_mod.sleep"
             ) as sleep, \
             mock.patch.object(
                 ContactListService, "_compute_enriched_rows",
                 wraps=ContactListService._compute_enriched_rows,
             ) as compute:
            result = ContactListService.list_contacts(user=self.admin)
        self.assertEqual(compute.call_count, 1)
        self.assertEqual(sleep.call_count, 6)
        self.assertIn("items", result)
