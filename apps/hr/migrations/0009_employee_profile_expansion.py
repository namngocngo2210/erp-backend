"""Mở rộng hồ sơ nhân sự + đổi quan hệ Employee↔User sang 1-N.

Thứ tự an toàn để KHÔNG mất liên kết tài khoản hiện có trên prod:
  1. Thêm các field hồ sơ mới.
  2. Tạo bảng ``EmployeeAccount`` (nối Employee 1-N User) và ``EmployeeChild``.
  3. Copy ``Employee.user`` hiện có -> ``EmployeeAccount`` (is_primary=True).
  4. Mới xoá field ``Employee.user``.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def copy_user_to_account(apps, schema_editor):
    Employee = apps.get_model("hr", "Employee")
    EmployeeAccount = apps.get_model("hr", "EmployeeAccount")
    rows = Employee.objects.exclude(user_id__isnull=True).values_list("id", "user_id")
    EmployeeAccount.objects.bulk_create(
        [
            EmployeeAccount(employee_id=emp_id, user_id=user_id, is_primary=True)
            for emp_id, user_id in rows
        ]
    )


def restore_user_from_account(apps, schema_editor):
    # Reverse: gán lại tài khoản chính về Employee.user (field đã được re-add).
    Employee = apps.get_model("hr", "Employee")
    EmployeeAccount = apps.get_model("hr", "EmployeeAccount")
    for acc in EmployeeAccount.objects.filter(is_primary=True):
        Employee.objects.filter(id=acc.employee_id).update(user_id=acc.user_id)


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0008_attendanceevent_hr_att_evt_unique_kind_per_day"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="employee", name="bank_account_no",
            field=models.CharField(blank=True, default="", max_length=40)),
        migrations.AddField(model_name="employee", name="bank_name",
            field=models.CharField(blank=True, default="", max_length=100)),
        migrations.AddField(model_name="employee", name="education_level",
            field=models.CharField(blank=True, default="", max_length=150)),
        migrations.AddField(model_name="employee", name="emergency_contact_address",
            field=models.CharField(blank=True, default="", max_length=255)),
        migrations.AddField(model_name="employee", name="emergency_contact_name",
            field=models.CharField(blank=True, default="", max_length=150)),
        migrations.AddField(model_name="employee", name="emergency_contact_phone",
            field=models.CharField(blank=True, default="", max_length=20)),
        migrations.AddField(model_name="employee", name="emergency_contact_relation",
            field=models.CharField(blank=True, default="", max_length=50)),
        migrations.AddField(model_name="employee", name="facebook_url",
            field=models.CharField(blank=True, default="", max_length=300)),
        migrations.AddField(model_name="employee", name="id_issued_date",
            field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name="employee", name="id_issued_place",
            field=models.CharField(blank=True, default="", max_length=255)),
        migrations.AddField(model_name="employee", name="lark_account",
            field=models.CharField(blank=True, default="", max_length=150)),
        migrations.AddField(model_name="employee", name="major",
            field=models.CharField(blank=True, default="", max_length=150)),
        migrations.AddField(model_name="employee", name="marital_status",
            field=models.CharField(blank=True, choices=[("single", "Độc thân"), ("married", "Đã kết hôn"), ("divorced", "Đã ly hôn"), ("other", "Khác")], default="", max_length=20)),
        migrations.AddField(model_name="employee", name="official_date",
            field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name="employee", name="permanent_address",
            field=models.CharField(blank=True, default="", max_length=255)),
        migrations.AddField(model_name="employee", name="position_level",
            field=models.CharField(blank=True, choices=[("director", "Ban giám đốc"), ("team_lead", "Trưởng nhóm"), ("specialist", "Chuyên viên"), ("staff", "Nhân viên"), ("collaborator", "Cộng tác viên")], default="", max_length=20)),
        migrations.AddField(model_name="employee", name="position_title",
            field=models.CharField(blank=True, default="", max_length=150)),
        migrations.AddField(model_name="employee", name="resign_reason",
            field=models.CharField(blank=True, default="", max_length=500)),
        migrations.AddField(model_name="employee", name="school",
            field=models.CharField(blank=True, default="", max_length=255)),
        migrations.AddField(model_name="employee", name="social_insurance_no",
            field=models.CharField(blank=True, default="", max_length=30)),
        migrations.AddField(model_name="employee", name="tax_code",
            field=models.CharField(blank=True, default="", max_length=30)),
        migrations.AddField(model_name="employee", name="vehicle_info",
            field=models.CharField(blank=True, default="", max_length=255)),
        migrations.AddField(model_name="employee", name="work_type",
            field=models.CharField(blank=True, choices=[("fulltime", "Toàn thời gian"), ("parttime", "Bán thời gian"), ("remote", "Remote")], default="", max_length=20)),
        migrations.CreateModel(
            name="EmployeeAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_primary", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="accounts", to="hr.employee")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="employee_account", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "hr_employee_accounts",
                "indexes": [models.Index(fields=["employee"], name="hr_emp_acc_emp_idx")],
                "constraints": [models.UniqueConstraint(condition=models.Q(("is_primary", True)), fields=("employee",), name="hr_emp_acc_one_primary")],
            },
        ),
        migrations.CreateModel(
            name="EmployeeChild",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(blank=True, default="", max_length=150)),
                ("birth_year", models.PositiveIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="children", to="hr.employee")),
            ],
            options={
                "db_table": "hr_employee_children",
                "indexes": [models.Index(fields=["employee"], name="hr_emp_child_emp_idx")],
            },
        ),
        # Copy liên kết user hiện có TRƯỚC khi xoá field user.
        migrations.RunPython(copy_user_to_account, restore_user_from_account),
        migrations.RemoveField(model_name="employee", name="user"),
    ]
