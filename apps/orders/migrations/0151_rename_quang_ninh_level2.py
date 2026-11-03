# Quảng Ninh lên thành phố: đổi "Tỉnh Quảng Ninh" → "Thành phố Quảng Ninh" ở các đơn
# đã lưu địa chỉ 2 cấp (prod 06/10/2026: 102 đơn). Đơn mới đi qua
# `address_lookup.PROVINCE_RENAMES`. Kiot vẫn khớp vì tra danh mục thử cả hai tiền tố.

from django.db import migrations

OLD = "Tỉnh Quảng Ninh"
NEW = "Thành phố Quảng Ninh"


def _rename(old: str, new: str):
    def run(apps, schema_editor):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE orders SET province_level2 = %s, "
                "address_level2 = replace(address_level2, %s, %s) "
                "WHERE province_level2 = %s",
                [new, old, new, old],
            )

    return run


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0150_leadassignmentshare_resale_sheet"),
    ]

    operations = [
        migrations.RunPython(_rename(OLD, NEW), _rename(NEW, OLD)),
    ]
