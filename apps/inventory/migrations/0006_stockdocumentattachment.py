import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("inventory", "0005_stockdocument_approved_at_stockdocument_approved_by_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="StockDocumentAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file_key", models.CharField(max_length=300)),
                ("content_type", models.CharField(max_length=100)),
                ("original_name", models.CharField(blank=True, default="", max_length=255)),
                ("size_bytes", models.PositiveIntegerField(default=0)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="inventory.stockdocument",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="uploaded_stock_doc_attachments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "stock_document_attachments",
                "ordering": ["uploaded_at", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="stockdocumentattachment",
            index=models.Index(fields=["document", "uploaded_at"], name="stockdoc_attach_doc_idx"),
        ),
    ]
