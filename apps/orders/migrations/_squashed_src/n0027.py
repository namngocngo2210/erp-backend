# Verbatim copy of apps/orders/migrations/0027_alter_order_customer_prepaid.py, kept so the squashed
# migration 0001_squashed_0089_* can reference its RunPython functions even
# after the original per-step migration files are eventually deleted.
from django.db import migrations, models


def _alter_customer_prepaid_to_decimal(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    table_name = schema_editor.quote_name("orders_order")
    column_name = schema_editor.quote_name("customer_prepaid")

    if vendor == "postgresql":
        schema_editor.execute(
            f"""
            ALTER TABLE {table_name}
            ALTER COLUMN {column_name} TYPE numeric(12, 2)
            USING (
                CASE
                    WHEN {column_name} IS TRUE THEN 1
                    ELSE 0
                END
            )::numeric(12, 2)
            """
        )
        return

    order_model = apps.get_model("orders", "Order")
    old_field = order_model._meta.get_field("customer_prepaid")
    new_field = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    new_field.set_attributes_from_name("customer_prepaid")
    schema_editor.alter_field(order_model, old_field, new_field, strict=True)


def _revert_customer_prepaid_to_boolean(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    table_name = schema_editor.quote_name("orders_order")
    column_name = schema_editor.quote_name("customer_prepaid")

    if vendor == "postgresql":
        schema_editor.execute(
            f"""
            ALTER TABLE {table_name}
            ALTER COLUMN {column_name} TYPE boolean
            USING (COALESCE({column_name}, 0) <> 0)
            """
        )
        return

    order_model = apps.get_model("orders", "Order")
    old_field = order_model._meta.get_field("customer_prepaid")
    new_field = models.BooleanField(default=False)
    new_field.set_attributes_from_name("customer_prepaid")
    schema_editor.alter_field(order_model, old_field, new_field, strict=True)


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0026_sync_crm_need_catalog'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    _alter_customer_prepaid_to_decimal,
                    _revert_customer_prepaid_to_boolean,
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name='order',
                    name='customer_prepaid',
                    field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
                ),
            ],
        ),
    ]
