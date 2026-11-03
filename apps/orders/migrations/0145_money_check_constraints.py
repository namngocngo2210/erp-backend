"""CHECK ≥ 0 cho tiền (audit 2026-09-23 T2/P2-9).

Đo prod 23/09/2026 TRƯỚC khi viết: orders.discount_amount<0 = 0,
shipping_fee<0 = 0; order_items.unit_price<0 = 0, discount<0 = 0. KHÔNG siết
total_amount/total_after_discount (8 đơn âm) và order_items.line_total (10
dòng âm) — cần quyết định nghiệp vụ trước. KHÔNG CHECK status dù prod 0 giá
trị lạ: sync KiotViet ghi thẳng int(status_val) không qua whitelist, siết ở
DB là sync chết khi KiotViet thêm mã mới (xem comment Order.Meta.constraints).

Vì sao NOT VALID + VALIDATE và atomic=False: `ADD CONSTRAINT ... CHECK` thường
lấy ACCESS EXCLUSIVE và giữ suốt lúc quét 560k dòng orders / 1,1M order_items.
Tách hai bước: ADD ... NOT VALID chỉ lấy khoá trong mili-giây (không quét);
VALIDATE CONSTRAINT quét bảng dưới SHARE UPDATE EXCLUSIVE — không chặn
đọc/ghi. Muốn hai bước ở hai transaction thì migration phải atomic=False;
nếu VALIDATE hỏng giữa chừng, constraint vẫn đứng ở trạng thái NOT VALID
(ghi mới đã bị kiểm) — chạy lại migration hoặc `migrate orders 0144` để gỡ.

Phía state dùng CheckConstraint để Meta.constraints và lịch sử migration khớp
(makemigrations --check sạch).
"""

from django.db import migrations, models


def _add_check(table: str, name: str, expr: str) -> list[migrations.RunSQL]:
    return [
        migrations.RunSQL(
            sql=f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expr}) NOT VALID",
            reverse_sql=f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}",
        ),
        migrations.RunSQL(
            sql=f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]


class Migration(migrations.Migration):
    # VALIDATE phải chạy ở transaction riêng sau ADD ... NOT VALID (xem docstring).
    atomic = False

    dependencies = [
        ("orders", "0144_order_assessments_autovacuum"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                *_add_check("orders", "ord_discount_nonneg", "discount_amount >= 0"),
                *_add_check("orders", "ord_shipping_fee_nonneg", "shipping_fee >= 0"),
                *_add_check("order_items", "ord_item_unit_price_nonneg", "unit_price >= 0"),
                *_add_check("order_items", "ord_item_discount_nonneg", "discount >= 0"),
            ],
            state_operations=[
                migrations.AddConstraint(
                    model_name="order",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("discount_amount__gte", 0)),
                        name="ord_discount_nonneg",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="order",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("shipping_fee__gte", 0)),
                        name="ord_shipping_fee_nonneg",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="orderitem",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("unit_price__gte", 0)),
                        name="ord_item_unit_price_nonneg",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="orderitem",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("discount__gte", 0)),
                        name="ord_item_discount_nonneg",
                    ),
                ),
            ],
        ),
    ]
