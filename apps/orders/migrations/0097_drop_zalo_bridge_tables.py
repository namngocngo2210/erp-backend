# Gỡ hoàn toàn tích hợp Zalo (app zalo_bridge đã bị xóa khỏi codebase).
# App không còn trong INSTALLED_APPS nên phải dọn bảng + metadata bằng SQL thuần:
#   - drop 3 bảng dữ liệu của zalo_bridge
#   - xóa row django_migrations của app để một app trùng tên trong tương lai
#     không bị coi là "đã migrate"
#   - xóa content types / permissions mồ côi
from django.db import migrations

DROP_SQL = """
DROP TABLE IF EXISTS zalo_bridge_order_notifications CASCADE;
DROP TABLE IF EXISTS zalo_bridge_contacts CASCADE;
DROP TABLE IF EXISTS zalo_cskh_accounts CASCADE;
DELETE FROM auth_permission
WHERE content_type_id IN (
    SELECT id FROM django_content_type WHERE app_label = 'zalo_bridge'
);
DELETE FROM django_content_type WHERE app_label = 'zalo_bridge';
DELETE FROM django_migrations WHERE app = 'zalo_bridge';
"""


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0096_alter_orderassessment_not_closed_reason_code_and_more"),
    ]

    operations = [
        migrations.RunSQL(sql=DROP_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
