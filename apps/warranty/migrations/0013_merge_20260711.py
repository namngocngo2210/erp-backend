from django.db import migrations


class Migration(migrations.Migration):
    """Gộp 2 nhánh 0012 (journey status label / claim activation fields) merge song song vào master."""

    dependencies = [
        ('warranty', '0012_alter_warrantyjourney_status'),
        ('warranty', '0012_claim_activation_warranty_months'),
    ]

    operations = []
