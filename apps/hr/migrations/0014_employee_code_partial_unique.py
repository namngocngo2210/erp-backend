"""Employee.employee_code: unique tuyệt đối → partial unique trên nhân sự chưa
xoá mềm (audit 2026-09-23 C8). AddConstraint trước, AlterField bỏ unique sau
(bảng hr_employees 224kB — khoá không đáng kể). Prod 23/09: 0 mã trùng active.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("hr", "0013_team_teammember_departmenthead_team_hr_team_dept_idx_and_more"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="employee",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("employee_code",),
                name="hr_emp_code_active_uniq",
            ),
        ),
        migrations.AlterField(
            model_name="employee",
            name="employee_code",
            field=models.CharField(max_length=20),
        ),
    ]
