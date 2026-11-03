from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0018_customerresalefollowup"),
    ]

    operations = [
        migrations.AddField(
            model_name="customerresalefollowup",
            name="appointment_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="customerresalefollowup",
            name="appointment_channel",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="customerresalefollowup",
            name="appointment_note",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="customerresalefollowup",
            name="appointment_purpose",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="customerresalefollowup",
            name="contact_logs",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="customerresalefollowup",
            name="resale_orders",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
