# Verbatim copy of apps/orders/migrations/0045_productpackage.py, kept so the squashed
# migration 0001_squashed_0089_* can reference its RunPython functions even
# after the original per-step migration files are eventually deleted.
from django.db import migrations, models


def backfill_qty1_packages(apps, schema_editor):
    Product = apps.get_model("orders", "Product")
    ProductPackage = apps.get_model("orders", "ProductPackage")
    to_create = []
    for product in Product.objects.all().only("id", "price", "weight", "length", "width", "height"):
        to_create.append(
            ProductPackage(
                product=product,
                quantity=1,
                price=product.price,
                weight=product.weight,
                length=product.length,
                width=product.width,
                height=product.height,
            )
        )
    if to_create:
        ProductPackage.objects.bulk_create(to_create, ignore_conflicts=True)


def reverse_backfill(apps, schema_editor):
    ProductPackage = apps.get_model("orders", "ProductPackage")
    ProductPackage.objects.filter(quantity=1).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0044_product_weight_dimensions"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductPackage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.PositiveIntegerField()),
                ("price", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("weight", models.DecimalField(decimal_places=3, default=0, max_digits=10)),
                ("length", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("width", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("height", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="packages",
                        to="orders.product",
                    ),
                ),
            ],
            options={
                "ordering": ["quantity"],
            },
        ),
        migrations.AddConstraint(
            model_name="productpackage",
            constraint=models.UniqueConstraint(fields=("product", "quantity"), name="uniq_product_package_qty"),
        ),
        migrations.AddIndex(
            model_name="productpackage",
            index=models.Index(fields=["product", "quantity"], name="product_package_pq_idx"),
        ),
        migrations.RunPython(backfill_qty1_packages, reverse_backfill),
    ]
