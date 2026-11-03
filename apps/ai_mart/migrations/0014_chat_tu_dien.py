"""Seed từ điển cho mart hội thoại chat (spec 2026-09-04 §3.3).

Chạy lại dictionary_rows() nguyên bộ thay vì chỉ 6 nhóm mới: upsert nên vô
hại, và nhân tiện làm tươi nhãn của các nhóm cũ nếu domain đã đổi.
"""

from django.db import migrations


def seed(apps, schema_editor):
    from apps.ai_mart.domain.dictionary import dictionary_rows
    from apps.ai_mart.domain.need_dictionary import refresh_need_dictionary

    with schema_editor.connection.cursor() as cur:
        cur.executemany(
            "INSERT INTO ai.tu_dien_ma (nhom_ma, ma, nhan, thu_tu) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (nhom_ma, ma) DO UPDATE SET nhan = EXCLUDED.nhan,"
            " thu_tu = EXCLUDED.thu_tu",
            dictionary_rows(),
        )
        refresh_need_dictionary(cur)


class Migration(migrations.Migration):
    # need_types/need_groups phải tồn tại trước khi seed đọc chúng. Phụ thuộc
    # migration MỚI NHẤT của app orders (không phải "0001_initial" — app đã
    # squash) để chắc chắn toàn bộ lịch sử seed catalog nhu cầu (tới tận
    # 0052_add_buy_for_self_need_group) đã áp dụng.
    dependencies = [
        ("ai_mart", "0013_don_hang_chi_nhanh_nhom_kenh"),
        ("orders", "0139_order_sales_closed_at_indexes"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(seed, migrations.RunPython.noop)]
        )
    ]
