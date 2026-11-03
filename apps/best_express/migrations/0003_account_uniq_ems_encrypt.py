"""ems_key → EncryptedCharField + unique (username, environment) — audit 2026-07-14 vòng 2.

`EncryptedCharField.from_db_value` fallback về giá trị thô khi decrypt lỗi, nên dòng cũ
(plaintext) vẫn đọc được sau khi đổi field; bước RunPython dưới đây ghi lại chúng để
thực sự nằm dạng mã hoá trong DB/backup. An toàn khi chạy lại (giá trị đã mã hoá thì
decrypt ra chính nó rồi bỏ qua).
"""

# EncryptedCharField đã chuyển về apps/common/fields.py (audit 2026-08-19); đổi
# path ở đây là state-safe: cùng một class, cùng db_type, không đổi schema.
import apps.common.fields
from django.db import migrations, models

# Bảng chưa từng có unique (username, environment) → có thể đã tồn tại dòng trùng (tạo
# qua shell/admin). Không dọn trước thì AddConstraint fail và deploy kẹt giữa chừng
# (audit 2026-07-14 vòng 3). Giữ dòng ĐANG active, nếu hoà thì giữ id nhỏ nhất.
DEDUPE_ACCOUNTS_SQL = """
DELETE FROM best_express_accounts a
USING best_express_accounts b
WHERE a.username = b.username
  AND a.environment = b.environment
  AND (b.is_active, -b.id) > (a.is_active, -a.id);
"""


def reencrypt_ems_keys(apps_registry, schema_editor):
    """Mã hoá lại ems_key đang nằm plaintext.

    Đọc/ghi bằng SQL thô thay vì model THẬT: model thật kéo theo mọi cột hiện tại, nên
    khi migration sau này thêm cột và ai đó replay từ đầu trên DB sạch (CI/staging), câu
    SELECT sẽ tham chiếu cột chưa tồn tại → ProgrammingError. Cũng không dùng
    `.exclude(ems_key="")` của ORM: get_prep_value MÃ HOÁ cả chuỗi rỗng nên điều kiện đó
    so với một ciphertext ngẫu nhiên — không lọc được gì (audit 2026-07-14 vòng 3).
    """
    from apps.common.fields import _DECRYPT_ERRORS, get_encryption

    encryption = get_encryption()
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, ems_key FROM best_express_accounts WHERE ems_key <> ''"
        )
        rows = cursor.fetchall()
        for pk, raw in rows:
            try:
                encryption.decrypt(raw)
                continue  # đã mã hoá rồi
            except _DECRYPT_ERRORS:
                pass
            cursor.execute(
                "UPDATE best_express_accounts SET ems_key = %s WHERE id = %s",
                [encryption.encrypt(raw), pk],
            )


def noop_reverse(apps_registry, schema_editor):
    # Không giải mã ngược: rollback field về CharField vẫn đọc được nhờ from_db_value
    # fallback ở tầng ứng dụng; ghi plaintext lại là hạ cấp bảo mật.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('best_express', '0002_best_event_dedupe_retention_idx'),
    ]

    operations = [
        migrations.AlterField(
            model_name='bestexpressaccount',
            name='ems_key',
            field=apps.common.fields.EncryptedCharField(blank=True, default='', max_length=512),
        ),
        migrations.RunPython(reencrypt_ems_keys, noop_reverse),
        migrations.RunSQL(DEDUPE_ACCOUNTS_SQL, reverse_sql=migrations.RunSQL.noop),
        migrations.AddConstraint(
            model_name='bestexpressaccount',
            constraint=models.UniqueConstraint(
                fields=('username', 'environment'), name='best_account_username_env_uniq'
            ),
        ),
    ]
