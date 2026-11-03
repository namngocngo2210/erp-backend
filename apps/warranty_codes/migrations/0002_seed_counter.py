from django.db import migrations


def seed(apps, schema_editor):
    Counter = apps.get_model("warranty_codes", "WarrantyCodeCounter")
    if not Counter.objects.exists():
        Counter.objects.create(last_seq=0)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("warranty_codes", "0001_initial")]
    operations = [migrations.RunPython(seed, noop)]
