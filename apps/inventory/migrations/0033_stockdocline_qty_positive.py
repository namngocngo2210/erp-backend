from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0032_stock_movements_db_fks"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="stockdocumentline",
            constraint=models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="stockdocline_quantity_positive",
            ),
        ),
    ]
