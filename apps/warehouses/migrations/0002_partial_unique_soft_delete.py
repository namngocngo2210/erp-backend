"""Warehouse.code / Warehouse.name: unique tuyệt đối → partial unique trên kho
chưa xoá mềm (audit 2026-09-23 C8). AddConstraint trước, AlterField bỏ unique
sau (bảng warehouses 120kB). Prod 23/09: 0 mã/tên trùng trong tập active.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("warehouses", "0001_initial"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="warehouse",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("code",),
                name="warehouse_code_active_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="warehouse",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("name",),
                name="warehouse_name_active_uniq",
            ),
        ),
        migrations.AlterField(
            model_name="warehouse",
            name="code",
            field=models.CharField(max_length=50),
        ),
        migrations.AlterField(
            model_name="warehouse",
            name="name",
            field=models.CharField(max_length=255),
        ),
    ]
