"""customers.phone_raw / phone_normalized: text → varchar(64) / varchar(20)
(audit 2026-09-23 T2/P2-8). Đo prod 23/09 TRƯỚC khi chọn trần: phone_raw max
52 (4 dòng > 32, phân bố 22..52), phone_normalized max 11.

CHI PHÍ TRÊN PROD — chạy giờ thấp điểm (xem runbook 2026-09-23-audit-ops.md):
text → varchar(n) là siết kiểu nên Postgres phải quét 768k dòng kiểm độ dài
và DỰNG LẠI mọi index trên hai cột (customer_phone_normalized_idx,
customer_code_phone_raw_idx, cust_phone_raw_trgm_idx, cust_phone_norm_trgm_idx,
cust_phone_norm_active_uniq — 2 GIN trgm là phần đắt) dưới ACCESS EXCLUSIVE.
Ước ~30-90s khoá bảng customers. Reverse (varchar → text) nới kiểu, rẻ hơn.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0037_drop_duplicate_resale_order_index"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customer",
            name="phone_normalized",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AlterField(
            model_name="customer",
            name="phone_raw",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
