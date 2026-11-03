"""Audit 2026-09-23 C8: unique chỉ áp lên bản ghi CHƯA xoá mềm.

Ba kịch bản cho mỗi model: (1) hai bản ghi sống cùng mã → IntegrityError,
(2) xoá mềm rồi tạo lại cùng mã → OK, (3) bản ghi sống + bản ghi đã xoá cùng
mã cùng tồn tại.
"""

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Product, ProductCategory, ProductGroup


class PartialUniqueSoftDeleteTests(TestCase):
    def _assert_partial_unique(self, create, soft_delete):
        first = create()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                create()
        soft_delete(first)
        second = create()  # tạo lại cùng mã sau khi xoá mềm phải được
        self.assertNotEqual(first.pk, second.pk)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                create()

    def test_product_sku(self):
        self._assert_partial_unique(
            lambda: Product.objects.create(name="SP", sku="SKU-1"),
            lambda p: Product.objects.filter(pk=p.pk).update(deleted_at=timezone.now()),
        )

    def test_product_group_code(self):
        self._assert_partial_unique(
            lambda: ProductGroup.objects.create(code="G1", name="Nhóm"),
            lambda g: ProductGroup.objects.filter(pk=g.pk).update(deleted_at=timezone.now()),
        )

    def test_product_category_code(self):
        self._assert_partial_unique(
            lambda: ProductCategory.objects.create(code="C1", name="Danh mục"),
            lambda c: ProductCategory.objects.filter(pk=c.pk).update(deleted_at=timezone.now()),
        )
