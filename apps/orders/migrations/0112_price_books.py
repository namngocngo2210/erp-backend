"""Pha 1 (expand): tạo price_books + product_package_prices, seed facebook/web,
backfill giá hiện có vào bảng mặc định. Chưa đụng cột product_packages.price."""
from django.db import migrations, models
import django.db.models.deletion


def seed_and_backfill(apps, schema_editor):
    PriceBook = apps.get_model("orders", "PriceBook")
    ProductPackage = apps.get_model("orders", "ProductPackage")
    ProductPackagePrice = apps.get_model("orders", "ProductPackagePrice")

    facebook, _ = PriceBook.objects.get_or_create(
        code="facebook", defaults={"name": "Facebook", "is_default": True, "sort_order": 0}
    )
    PriceBook.objects.get_or_create(
        code="web", defaults={"name": "Web", "is_default": False, "sort_order": 1}
    )

    batch = []
    for pkg_id, price in ProductPackage.objects.values_list("id", "price").iterator(chunk_size=2000):
        batch.append(
            ProductPackagePrice(package_id=pkg_id, price_book_id=facebook.id, price=price)
        )
        if len(batch) >= 2000:
            ProductPackagePrice.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        ProductPackagePrice.objects.bulk_create(batch, ignore_conflicts=True)


def unseed(apps, schema_editor):
    apps.get_model("orders", "ProductPackagePrice").objects.all().delete()
    apps.get_model("orders", "PriceBook").objects.filter(code__in=["facebook", "web"]).delete()


class Migration(migrations.Migration):
    dependencies = [("orders", "0111_order_restored_from")]

    operations = [
        migrations.CreateModel(
            name="PriceBook",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=50)),
                ("name", models.CharField(max_length=255)),
                ("is_default", models.BooleanField(default=False)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "price_books", "ordering": ["sort_order", "id"]},
        ),
        migrations.CreateModel(
            name="ProductPackagePrice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("price", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("package", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="book_prices", to="orders.productpackage")),
                ("price_book", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="package_prices", to="orders.pricebook")),
            ],
            options={"db_table": "product_package_prices"},
        ),
        migrations.AddIndex(
            model_name="pricebook",
            index=models.Index(fields=["deleted_at"], name="pricebook_deleted_idx"),
        ),
        migrations.AddConstraint(
            model_name="pricebook",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("code",),
                name="pricebook_code_active_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="pricebook",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True), ("is_default", True)),
                fields=("is_default",),
                name="pricebook_one_default",
            ),
        ),
        migrations.AddIndex(
            model_name="productpackageprice",
            index=models.Index(fields=["price_book", "package"], name="pkgprice_book_pkg_idx"),
        ),
        migrations.AddConstraint(
            model_name="productpackageprice",
            constraint=models.UniqueConstraint(fields=("package", "price_book"), name="uniq_package_price_book"),
        ),
        migrations.RunPython(seed_and_backfill, unseed),
    ]
