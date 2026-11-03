"""Partial unique cho customers.phone_normalized (audit hardening 2026-07 mục 3.2).

Hai bước, ``atomic = False``:
1. Dedup: gộp các customer active trùng phone_normalized — survivor = bản có nhiều
   order nhất (tie-break: tạo cũ nhất). Repoint mọi FK trỏ tới bản thừa (orders,
   loyal_customer_notes; segment snapshot là projection rebuild định kỳ nên xoá),
   rồi soft-delete bản thừa.
2. Tạo unique index partial CONCURRENTLY (không khoá bảng lớn):
   UNIQUE (phone_normalized) WHERE phone_normalized <> '' AND deleted_at IS NULL.

FK tới Customer đã grep toàn codebase (2026-07-03): orders.Order.customer,
customers.LoyalCustomerNote.customer (1-1), customers.CustomerSegmentSnapshot.customer (1-1).
"""
from django.db import migrations, models, transaction
from django.db.models import Count, Q


def dedup_phone_normalized(apps, schema_editor):
    Customer = apps.get_model("customers", "Customer")
    Order = apps.get_model("orders", "Order")
    LoyalCustomerNote = apps.get_model("customers", "LoyalCustomerNote")
    CustomerSegmentSnapshot = apps.get_model("customers", "CustomerSegmentSnapshot")
    from django.utils import timezone

    dup_phones = list(
        Customer.objects.filter(deleted_at__isnull=True)
        .exclude(phone_normalized="")
        .values("phone_normalized")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .values_list("phone_normalized", flat=True)
    )

    for phone in dup_phones:
        group = list(
            Customer.objects.filter(phone_normalized=phone, deleted_at__isnull=True)
            .annotate(n_orders=Count("orders"))
            .order_by("-n_orders", "created_at", "id")
        )
        if len(group) < 2:
            continue
        survivor, losers = group[0], group[1:]
        loser_ids = [c.id for c in losers]

        with transaction.atomic():
            Order.objects.filter(customer_id__in=loser_ids).update(
                customer_id=survivor.id
            )

            # LoyalCustomerNote là OneToOne: giữ note của survivor nếu có,
            # nếu không thì repoint note đầu tiên của loser sang survivor.
            loser_notes = list(
                LoyalCustomerNote.objects.filter(customer_id__in=loser_ids).order_by("id")
            )
            if loser_notes:
                if LoyalCustomerNote.objects.filter(customer_id=survivor.id).exists():
                    LoyalCustomerNote.objects.filter(
                        id__in=[n.id for n in loser_notes]
                    ).delete()
                else:
                    LoyalCustomerNote.objects.filter(id=loser_notes[0].id).update(
                        customer_id=survivor.id
                    )
                    LoyalCustomerNote.objects.filter(
                        id__in=[n.id for n in loser_notes[1:]]
                    ).delete()

            # Snapshot là projection, cron rebuild lại → xoá bản của loser.
            CustomerSegmentSnapshot.objects.filter(customer_id__in=loser_ids).delete()

            now = timezone.now()
            Customer.objects.filter(id__in=loser_ids).update(
                deleted_at=now, updated_at=now
            )


class Migration(migrations.Migration):
    # CONCURRENTLY không chạy được trong transaction; dedup tự bọc atomic từng nhóm.
    atomic = False

    dependencies = [
        ("customers", "0026_snap_lastbuy_spent_index"),
        ("orders", "0092_search_filter_indexes_round2"),
    ]

    operations = [
        migrations.RunPython(dedup_phone_normalized, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddConstraint(
                    model_name="customer",
                    constraint=models.UniqueConstraint(
                        fields=["phone_normalized"],
                        condition=~Q(phone_normalized="") & Q(deleted_at__isnull=True),
                        name="cust_phone_norm_active_uniq",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
                        "cust_phone_norm_active_uniq ON customers (phone_normalized) "
                        "WHERE phone_normalized <> '' AND deleted_at IS NULL;"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS cust_phone_norm_active_uniq;"
                    ),
                ),
            ],
        ),
    ]
