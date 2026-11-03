"""FK thật cho SalaryRecord.created_by_id + LeaveRequest.approver_id (audit 2026-07 mục 3.8).

Cột DB giữ nguyên tên (db_column) và kiểu (bigint) — chỉ NULL-hoá orphan rồi thêm
FK constraint + index (mirror những gì Django tạo cho FK). Bảng HR nhỏ nên ADD
CONSTRAINT trực tiếp, không cần NOT VALID/VALIDATE hai bước.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def null_orphan_user_ids(apps, schema_editor):
    """NULL-hoá các giá trị trỏ tới user không tồn tại trước khi add constraint."""
    User = apps.get_model("users", "User")
    SalaryRecord = apps.get_model("hr", "SalaryRecord")
    LeaveRequest = apps.get_model("hr", "LeaveRequest")

    valid_ids = User.objects.values_list("id", flat=True)
    SalaryRecord.objects.exclude(created_by_id=None).exclude(
        created_by_id__in=valid_ids
    ).update(created_by_id=None)
    LeaveRequest.objects.exclude(approver_id=None).exclude(
        approver_id__in=valid_ids
    ).update(approver_id=None)


class Migration(migrations.Migration):
    dependencies = [
        ("hr", "0010_alter_attendancerecord_source"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(null_orphan_user_ids, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="salaryrecord", name="created_by_id"),
                migrations.AddField(
                    model_name="salaryrecord",
                    name="created_by",
                    field=models.ForeignKey(
                        blank=True,
                        db_column="created_by_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                migrations.RemoveField(model_name="leaverequest", name="approver_id"),
                migrations.AddField(
                    model_name="leaverequest",
                    name="approver",
                    field=models.ForeignKey(
                        blank=True,
                        db_column="approver_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX IF NOT EXISTS hr_salary_created_by_id_idx "
                        "ON hr_salary_records (created_by_id);\n"
                        "ALTER TABLE hr_salary_records "
                        "ADD CONSTRAINT hr_salary_created_by_id_fk "
                        "FOREIGN KEY (created_by_id) REFERENCES users(id) "
                        "DEFERRABLE INITIALLY DEFERRED;"
                    ),
                    reverse_sql=(
                        "ALTER TABLE hr_salary_records "
                        "DROP CONSTRAINT IF EXISTS hr_salary_created_by_id_fk;\n"
                        "DROP INDEX IF EXISTS hr_salary_created_by_id_idx;"
                    ),
                ),
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX IF NOT EXISTS hr_leave_approver_id_idx "
                        "ON hr_leave_requests (approver_id);\n"
                        "ALTER TABLE hr_leave_requests "
                        "ADD CONSTRAINT hr_leave_approver_id_fk "
                        "FOREIGN KEY (approver_id) REFERENCES users(id) "
                        "DEFERRABLE INITIALLY DEFERRED;"
                    ),
                    reverse_sql=(
                        "ALTER TABLE hr_leave_requests "
                        "DROP CONSTRAINT IF EXISTS hr_leave_approver_id_fk;\n"
                        "DROP INDEX IF EXISTS hr_leave_approver_id_idx;"
                    ),
                ),
            ],
        ),
    ]
