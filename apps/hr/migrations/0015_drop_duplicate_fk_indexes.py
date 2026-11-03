"""Bỏ 8 index khai lại đúng cột FK — trùng tuyệt đối với index Django tự sinh
cho ForeignKey (audit 2026-09-23 T2/P2-3). Bảng hr_* đều < 1MB."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("hr", "0014_employee_code_partial_unique"),
    ]

    operations = [
        migrations.RemoveIndex(model_name="employee", name="hr_emp_dept_idx"),
        migrations.RemoveIndex(model_name="employee", name="hr_emp_mgr_idx"),
        migrations.RemoveIndex(model_name="employeeaccount", name="hr_emp_acc_emp_idx"),
        migrations.RemoveIndex(model_name="employeechild", name="hr_emp_child_emp_idx"),
        migrations.RemoveIndex(model_name="employmentcontract", name="hr_contract_emp_idx"),
        migrations.RemoveIndex(model_name="departmenthead", name="hr_dept_head_emp_idx"),
        migrations.RemoveIndex(model_name="team", name="hr_team_dept_idx"),
        migrations.RemoveIndex(model_name="teammember", name="hr_team_member_team_idx"),
    ]
