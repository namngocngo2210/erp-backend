"""Tests cho partial unique customers.phone_normalized (audit hardening 2026-07 mục 3.2).

- Dedup migration: gộp customer trùng phone (survivor = nhiều order nhất, tie-break cũ nhất),
  repoint FK, soft-delete bản thừa.
- Unique index chặn tạo trùng; get_or_create_by_phone chịu được race (IntegrityError → re-fetch).
"""
import importlib

from django.apps import apps as django_apps
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from unittest import mock

from apps.channels_core.models import Channel
from apps.customers.models import Customer, LoyalCustomerNote
from apps.customers.services import CustomerService
from apps.orders.models import Order

_MIGRATION = importlib.import_module(
    "apps.customers.migrations.0027_phone_normalized_unique"
)

UNIQUE_INDEX_NAME = "cust_phone_norm_active_uniq"


def _drop_unique_index():
    """Gỡ unique index trong transaction của test để dựng được trạng thái trùng lặp
    (DDL Postgres có transaction — rollback cuối test sẽ khôi phục index)."""
    with connection.cursor() as cur:
        cur.execute(f"ALTER TABLE customers DROP CONSTRAINT IF EXISTS {UNIQUE_INDEX_NAME}")
        cur.execute(f"DROP INDEX IF EXISTS {UNIQUE_INDEX_NAME}")


class DedupMigrationTests(TestCase):
    def test_dedup_merges_duplicates_and_repoints_fks(self):
        _drop_unique_index()
        channel = Channel.objects.create(code="dupch", name="dup channel")

        loser = Customer.objects.create(full_name="Old", phone_normalized="0900000001")
        survivor = Customer.objects.create(full_name="New", phone_normalized="0900000001")

        # survivor có nhiều order hơn → được giữ dù tạo sau
        Order.objects.create(customer=survivor, channel=channel, status=3)
        Order.objects.create(customer=survivor, channel=channel, status=3)
        loser_order = Order.objects.create(customer=loser, channel=channel, status=1)
        LoyalCustomerNote.objects.create(customer=loser, special_note="note cũ")

        _MIGRATION.dedup_phone_normalized(django_apps, None)

        survivor.refresh_from_db()
        loser.refresh_from_db()
        self.assertIsNone(survivor.deleted_at)
        self.assertIsNotNone(loser.deleted_at)

        loser_order.refresh_from_db()
        self.assertEqual(loser_order.customer_id, survivor.id)
        self.assertEqual(survivor.orders.count(), 3)
        # note được repoint sang survivor (survivor chưa có note)
        self.assertTrue(LoyalCustomerNote.objects.filter(customer=survivor).exists())
        self.assertFalse(LoyalCustomerNote.objects.filter(customer=loser).exists())

    def test_dedup_tie_break_keeps_oldest(self):
        _drop_unique_index()
        first = Customer.objects.create(phone_normalized="0900000002")
        second = Customer.objects.create(phone_normalized="0900000002")

        _MIGRATION.dedup_phone_normalized(django_apps, None)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(first.deleted_at)
        self.assertIsNotNone(second.deleted_at)


class PhoneUniqueConstraintTests(TestCase):
    def test_duplicate_active_phone_rejected(self):
        Customer.objects.create(phone_normalized="0911111111")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Customer.objects.create(phone_normalized="0911111111")

    def test_duplicate_allowed_when_soft_deleted_or_empty(self):
        from django.utils import timezone

        Customer.objects.create(
            phone_normalized="0922222222", deleted_at=timezone.now()
        )
        # Bản active cùng phone vẫn tạo được vì bản kia đã soft-delete
        Customer.objects.create(phone_normalized="0922222222")
        # phone rỗng không bị ràng buộc unique
        Customer.objects.create(phone_normalized="")
        Customer.objects.create(phone_normalized="")

    def test_get_or_create_race_refetches_existing(self):
        existing = Customer.objects.create(phone_normalized="0933333333")

        orig_filter = Customer.objects.filter
        calls = {"n": 0}

        def fake_filter(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # Giả lập race: lượt lookup đầu không thấy (customer được tạo
                # bởi request song song ngay sau đó).
                return Customer.objects.none()
            return orig_filter(*args, **kwargs)

        with mock.patch.object(Customer.objects, "filter", side_effect=fake_filter):
            result = CustomerService.get_or_create_by_phone("0933333333")

        self.assertEqual(result.id, existing.id)
        self.assertEqual(
            Customer.objects.filter(
                phone_normalized="0933333333", deleted_at__isnull=True
            ).count(),
            1,
        )
