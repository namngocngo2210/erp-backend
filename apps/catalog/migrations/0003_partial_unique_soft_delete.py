"""Partial unique cho Product.sku / ProductGroup.code / ProductCategory.code
(audit 2026-09-23 C8, database.md P1-5).

Trước: `unique=True` tuyệt đối trên model có soft-delete → xoá mềm một sản phẩm
rồi tạo lại cùng SKU nổ IntegrityError. Sau: unique partial `deleted_at IS NULL`
(mẫu PriceBook.pricebook_code_active_uniq cùng app).

Thứ tự BẮT BUỘC: AddConstraint TRƯỚC, AlterField (bỏ unique cũ) SAU — tại mọi
thời điểm bảng luôn có ít nhất một ràng buộc chống trùng trên tập đang sống.
Ba bảng nhỏ (products 1,6MB, product_groups 216kB, product_categories 96kB đo
prod 23/09) nên CREATE UNIQUE INDEX thường + DROP unique cũ khoá rất ngắn.
Prod đã kiểm 23/09: 0 mã trùng trong tập active ở cả ba bảng.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_productprofile_display_name"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("sku",),
                name="product_sku_active_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="productgroup",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("code",),
                name="prodgroup_code_active_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="productcategory",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("code",),
                name="prodcat_code_active_uniq",
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="sku",
            field=models.CharField(max_length=100),
        ),
        migrations.AlterField(
            model_name="productgroup",
            name="code",
            field=models.CharField(max_length=64),
        ),
        migrations.AlterField(
            model_name="productcategory",
            name="code",
            field=models.CharField(max_length=64),
        ),
    ]
