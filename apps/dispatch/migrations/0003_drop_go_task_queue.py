from django.db import migrations


class Migration(migrations.Migration):
    """Drop the Go task-queue tables after returning async work to Celery.

    Order matters: GoCronRun has a FK to Job, so delete it first.
    """

    dependencies = [
        ("dispatch", "0002_alter_job_payload_result_encoder"),
    ]

    operations = [
        migrations.DeleteModel(name="GoCronRun"),
        migrations.DeleteModel(name="Job"),
        migrations.DeleteModel(name="AuditBatchMark"),
    ]
