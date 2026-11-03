import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


def forwards(apps, schema_editor):
    """Chuyển data: 1 phiếu cũ → 1 ReturnEntry (chưa gộp ngày ở bước này).

    Viết hoàn toàn set-based (4 câu UPDATE + 1 INSERT ... SELECT) thay vì loop
    từng phiếu: bảng prod lớn, loop giữ ACCESS EXCLUSIVE lock hàng phút.
    """
    execute = schema_editor.execute

    # 1. 1 phiếu cũ → 1 entry. created_at copy nguyên (auto_now_add chỉ áp ở
    #    tầng ORM, INSERT thẳng SQL nên giữ đúng mốc thời gian phiếu cũ).
    execute(
        """
        INSERT INTO stock_return_entries (
            return_note_id, source_order_id, shipping_code, customer_name,
            customer_phone, source_note, note, status, posted_at,
            order_status_before, scan_batch_id, created_by_id, created_at
        )
        SELECT n.id, n.source_order_id, n.shipping_code, n.customer_name,
               n.customer_phone, n.source_note, n.note, n.status, n.posted_at,
               n.order_status_before, n.scan_batch_id, n.created_by_id,
               n.created_at
        FROM stock_return_notes n
        """
    )

    # 2. Dòng trỏ sang entry; return_type rỗng → lấy của phiếu (mặc định hàng hoàn).
    execute(
        """
        UPDATE stock_return_note_lines l
        SET entry_id = e.id,
            return_type = CASE
                WHEN COALESCE(l.return_type, '') = ''
                    THEN COALESCE(NULLIF(n.return_type, ''), 'delivery_returned')
                ELSE l.return_type
            END
        FROM stock_return_entries e
        JOIN stock_return_notes n ON n.id = e.return_note_id
        WHERE l.return_note_id = e.return_note_id
        """
    )

    # 3. Movement trỏ sang entry (stock_movements là bảng partitioned — chỉ sửa
    #    cột thường, không đụng partition key created_at nên không phải move row).
    execute(
        """
        UPDATE stock_movements m
        SET reference_type = 'return_entry',
            reference_id = e.id
        FROM stock_return_entries e
        WHERE m.reference_type = 'return'
          AND m.reference_id = e.return_note_id
        """
    )

    # 4. Phiếu cũ = chứng từ 1 đơn → ngày chứng từ theo GIỜ VN và CHỐT lại để
    #    không vướng ràng buộc "1 phiếu mở / ngày / kho".
    execute(
        """
        UPDATE stock_return_notes
        SET doc_date = (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date,
            status = 'closed',
            closed_at = created_at
        """
    )

    # 5. FK của Django là DEFERRABLE INITIALLY DEFERRED → các UPDATE trên xếp
    #    hàng trigger event; operation kế tiếp (ALTER COLUMN entry_id SET NOT NULL)
    #    sẽ chết với "cannot ALTER TABLE ... because it has pending trigger events".
    #    Ép check FK ngay tại đây để dọn hàng đợi trước khi ALTER.
    execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("orders", "0001_initial"),
        ("inventory", "0021_alter_pendingshipmentscan_status"),
    ]

    operations = [
        # --- 1. Model mới ReturnEntry (chưa gắn unique shipping_code) ---
        migrations.CreateModel(
            name="ReturnEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("shipping_code", models.CharField(blank=True, default="", max_length=64)),
                ("customer_name", models.CharField(blank=True, default="", max_length=255)),
                ("customer_phone", models.CharField(blank=True, default="", max_length=32)),
                ("source_note", models.TextField(blank=True, default="")),
                ("note", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[("draft", "Nháp"), ("posted", "Đã ghi sổ"), ("cancelled", "Đã huỷ")],
                        default="draft",
                        max_length=20,
                    ),
                ),
                ("posted_at", models.DateTimeField(blank=True, null=True)),
                ("order_status_before", models.IntegerField(blank=True, null=True)),
                ("scan_batch_id", models.UUIDField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "return_note",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entries",
                        to="inventory.returnnote",
                    ),
                ),
                (
                    "source_order",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="return_entries",
                        to="orders.order",
                    ),
                ),
            ],
            options={
                "db_table": "stock_return_entries",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="returnentry",
            index=models.Index(fields=["return_note"], name="returnentry_note_idx"),
        ),
        migrations.AddIndex(
            model_name="returnentry",
            index=models.Index(fields=["source_order"], name="returnentry_order_idx"),
        ),
        migrations.AddIndex(
            model_name="returnentry",
            index=models.Index(fields=["shipping_code"], name="returnentry_shipcode_idx"),
        ),
        migrations.AddIndex(
            model_name="returnentry",
            index=models.Index(fields=["status"], name="returnentry_status_idx"),
        ),
        migrations.AddIndex(
            model_name="returnentry",
            index=models.Index(fields=["scan_batch_id"], name="returnentry_batch_idx"),
        ),
        # --- 2. FK tạm null trên dòng phiếu ---
        migrations.AddField(
            model_name="returnnoteline",
            name="entry",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="lines",
                to="inventory.returnentry",
            ),
        ),
        # --- 3. Trường mới của phiếu ngày (doc_date tạm null) ---
        migrations.AddField(
            model_name="returnnote",
            name="doc_date",
            field=models.DateField(null=True),
        ),
        migrations.AddField(
            model_name="returnnote",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="returnnote",
            name="closed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="closed_return_notes",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # --- 4. Chuyển data ---
        # reverse noop (audit 2026-09-23 T2/P2-10): trước đây không có reverse nên
        # `migrate inventory 0021` chết bằng IrreversibleError NGAY cả khi chỉ cần
        # lùi một migration khác phía sau. Noop KHÔNG khôi phục dữ liệu đã chuyển —
        # rollback thật vẫn phải restore backup; nó chỉ cho phép đi ngược qua node này.
        migrations.RunPython(forwards, reverse_code=migrations.RunPython.noop),
        # --- 5. Siết non-null sau khi data đã đầy ---
        migrations.AlterField(
            model_name="returnnote",
            name="doc_date",
            field=models.DateField(),
        ),
        migrations.AlterField(
            model_name="returnnoteline",
            name="entry",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="lines",
                to="inventory.returnentry",
            ),
        ),
        # --- 6. Gỡ ràng buộc/index cũ trước khi xoá cột ---
        migrations.RemoveConstraint(
            model_name="returnnote",
            name="returnnote_shipcode_draft_uniq",
        ),
        migrations.RemoveConstraint(
            model_name="returnnote",
            name="returnnote_return_type_valid",
        ),
        migrations.RemoveIndex(
            model_name="returnnote",
            name="returnnote_type_status_idx",
        ),
        migrations.RemoveIndex(
            model_name="returnnote",
            name="returnnote_order_idx",
        ),
        migrations.RemoveIndex(
            model_name="returnnote",
            name="returnnote_created_idx",
        ),
        migrations.RemoveIndex(
            model_name="returnnote",
            name="returnnote_shipcode_idx",
        ),
        migrations.RemoveIndex(
            model_name="returnnote",
            name="returnnote_scanbatch_idx",
        ),
        migrations.RemoveConstraint(
            model_name="returnnoteline",
            name="uniq_return_line",
        ),
        # --- 7. Xoá cột cũ ---
        migrations.RemoveField(
            model_name="returnnoteline",
            name="return_note",
        ),
        migrations.RemoveField(model_name="returnnote", name="return_type"),
        migrations.RemoveField(model_name="returnnote", name="source_order"),
        migrations.RemoveField(model_name="returnnote", name="shipping_code"),
        migrations.RemoveField(model_name="returnnote", name="customer_name"),
        migrations.RemoveField(model_name="returnnote", name="customer_phone"),
        migrations.RemoveField(model_name="returnnote", name="source_note"),
        migrations.RemoveField(model_name="returnnote", name="note"),
        migrations.RemoveField(model_name="returnnote", name="posted_at"),
        migrations.RemoveField(model_name="returnnote", name="order_status_before"),
        migrations.RemoveField(model_name="returnnote", name="scan_batch_id"),
        # --- 8. Trạng thái phiếu ngày + thứ tự mặc định ---
        migrations.AlterModelOptions(
            name="returnnote",
            options={"ordering": ["-doc_date", "-id"]},
        ),
        migrations.AlterField(
            model_name="returnnote",
            name="status",
            field=models.CharField(
                choices=[("open", "Đang mở"), ("closed", "Đã chốt")],
                default="open",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="returnnoteline",
            name="return_type",
            field=models.CharField(
                choices=[
                    ("delivery_returned", "Hàng hoàn"),
                    ("external_return", "Trả hàng ngoài"),
                    ("warranty", "Bảo hành"),
                    ("defect", "Đổi lỗi"),
                ],
                max_length=20,
            ),
        ),
        # --- 9. Index/ràng buộc mới ---
        migrations.AddIndex(
            model_name="returnnote",
            index=models.Index(fields=["doc_date"], name="returnnote_docdate_idx"),
        ),
        migrations.AddIndex(
            model_name="returnnote",
            index=models.Index(fields=["warehouse", "doc_date"], name="returnnote_wh_date_idx"),
        ),
        migrations.AddConstraint(
            model_name="returnnote",
            constraint=models.UniqueConstraint(
                condition=Q(status="open"),
                fields=("doc_date", "warehouse"),
                name="returnnote_open_day_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="returnentry",
            constraint=models.UniqueConstraint(
                condition=Q(shipping_code__gt="")
                & Q(source_order__isnull=True)
                & Q(status="draft"),
                fields=("shipping_code",),
                name="returnentry_shipcode_draft_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="returnnoteline",
            constraint=models.UniqueConstraint(
                fields=("entry", "product"), name="uniq_return_line"
            ),
        ),
        migrations.AddConstraint(
            model_name="returnnoteline",
            constraint=models.CheckConstraint(
                condition=Q(
                    return_type__in=[
                        "delivery_returned",
                        "external_return",
                        "warranty",
                        "defect",
                    ]
                ),
                name="returnline_return_type_valid",
            ),
        ),
        # --- 10. Movement: thêm choice return_entry (no-op ở DB) ---
        migrations.AlterField(
            model_name="stockmovement",
            name="reference_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("order", "Đơn hàng"),
                    ("document", "Phiếu kho"),
                    ("return", "Phiếu trả hàng"),
                    ("return_entry", "Nhóm đơn hoàn"),
                    ("reclass", "Chuyển trạng thái tồn"),
                ],
                max_length=20,
                null=True,
            ),
        ),
    ]
