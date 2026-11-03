# Verbatim copy of apps/orders/migrations/0019_replace_order_status_with_status.py, kept so the squashed
# migration 0001_squashed_0089_* can reference its RunPython functions even
# after the original per-step migration files are eventually deleted.
from django.db import migrations, models
from django.db.models import Case, IntegerField, Value, When


def copy_order_status_to_status(apps, schema_editor):
    Order = apps.get_model("orders", "Order")

    Order.objects.update(
        status=Case(
            When(order_status="new", then=Value(0)),
            When(order_status="confirmed", then=Value(1)),
            When(order_status="not_shipped", then=Value(1)),
            When(order_status="shipping", then=Value(2)),
            When(order_status="delivered", then=Value(3)),
            When(order_status="returning", then=Value(4)),
            When(order_status="returned", then=Value(5)),
            When(order_status="cancelled", then=Value(6)),
            When(order_status="0", then=Value(0)),
            When(order_status="1", then=Value(1)),
            When(order_status="2", then=Value(2)),
            When(order_status="3", then=Value(3)),
            When(order_status="4", then=Value(4)),
            When(order_status="5", then=Value(5)),
            When(order_status="6", then=Value(6)),
            default=Value(0),
            output_field=IntegerField(),
        )
    )


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0018_product_soft_delete"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="status",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (0, "New"),
                    (1, "Not shipped"),
                    (2, "Shipping"),
                    (3, "Delivered"),
                    (4, "Returning"),
                    (5, "Returned"),
                    (6, "Cancelled"),
                ],
                default=0,
            ),
        ),
        migrations.RunPython(copy_order_status_to_status, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name="order",
            name="order_status_idx",
        ),
        migrations.RemoveIndex(
            model_name="order",
            name="order_ch_st_cr_idx",
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["status"], name="status_idx"),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["channel", "status", "created_at"], name="order_ch_st_cr_idx"),
        ),
        migrations.RemoveField(
            model_name="order",
            name="order_status",
        ),
    ]
