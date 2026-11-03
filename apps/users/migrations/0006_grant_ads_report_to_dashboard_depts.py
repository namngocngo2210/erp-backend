from django.db import migrations


SECTION = "ads-report"
PARENT_SECTION = "dashboard"


def grant_ads_report(apps, schema_editor):
    Department = apps.get_model("users", "Department")
    for dept in Department.objects.all():
        sections = list(dept.allowed_sections or [])
        if PARENT_SECTION in sections and SECTION not in sections:
            sections.append(SECTION)
            dept.allowed_sections = sections
            dept.save(update_fields=["allowed_sections"])


def revoke_ads_report(apps, schema_editor):
    Department = apps.get_model("users", "Department")
    for dept in Department.objects.all():
        sections = [s for s in (dept.allowed_sections or []) if s != SECTION]
        if sections != (dept.allowed_sections or []):
            dept.allowed_sections = sections
            dept.save(update_fields=["allowed_sections"])


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0005_department_model"),
    ]

    operations = [
        migrations.RunPython(grant_ads_report, revoke_ads_report),
    ]
