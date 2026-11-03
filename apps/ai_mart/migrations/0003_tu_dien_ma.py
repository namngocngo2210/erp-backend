"""Bảng từ điển + 2 hàm SQL nền của schema ai.

Seed bằng RunPython đọc domain/dictionary.py — một nguồn, migration không chép tay.
Migration này chỉ seed lần đầu; đổi nhãn về sau đi bằng data migration mới
(INSERT ... ON CONFLICT DO UPDATE) để giữ lịch sử migrate tuyến tính.
"""

from django.db import migrations

DDL = """
CREATE TABLE IF NOT EXISTS ai.tu_dien_ma (
    nhom_ma text NOT NULL,
    ma      text NOT NULL,
    nhan    text NOT NULL,
    mo_ta   text,
    thu_tu  int  NOT NULL DEFAULT 0,
    PRIMARY KEY (nhom_ma, ma)
);
COMMENT ON TABLE ai.tu_dien_ma IS
    'Từ điển mã → nhãn tiếng Việt. Muốn biết một cột *_ma có những giá trị nào, SELECT theo nhom_ma.';
COMMENT ON COLUMN ai.tu_dien_ma.nhom_ma IS 'Nhóm mã, vd trang_thai_don, trang_thai_sale';
COMMENT ON COLUMN ai.tu_dien_ma.ma IS 'Mã gốc trong dữ liệu vận hành';
COMMENT ON COLUMN ai.tu_dien_ma.nhan IS 'Nhãn tiếng Việt hiển thị cho người đọc';
COMMENT ON COLUMN ai.tu_dien_ma.mo_ta IS 'Giải thích thêm ngữ nghĩa/bẫy của mã, có thể NULL';
COMMENT ON COLUMN ai.tu_dien_ma.thu_tu IS 'Thứ tự trình bày trong nhóm';

CREATE OR REPLACE FUNCTION ai.nhan(p_nhom text, p_ma text) RETURNS text AS $$
    SELECT t.nhan FROM ai.tu_dien_ma t WHERE t.nhom_ma = p_nhom AND t.ma = p_ma;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION ai.che_sdt(p text) RETURNS text AS $$
    SELECT CASE
        WHEN NULLIF(btrim(coalesce(p, '')), '') IS NULL THEN NULL
        WHEN length(btrim(p)) >= 8 THEN left(btrim(p), 4) || '***' || right(btrim(p), 3)
        ELSE '***'
    END;
$$ LANGUAGE sql IMMUTABLE;
"""

DROP = """
DROP FUNCTION IF EXISTS ai.che_sdt(text);
DROP FUNCTION IF EXISTS ai.nhan(text, text);
DROP TABLE IF EXISTS ai.tu_dien_ma;
"""


def seed(apps, schema_editor):
    from apps.ai_mart.domain.dictionary import dictionary_rows

    with schema_editor.connection.cursor() as cur:
        cur.executemany(
            "INSERT INTO ai.tu_dien_ma (nhom_ma, ma, nhan, thu_tu) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (nhom_ma, ma) DO UPDATE SET nhan = EXCLUDED.nhan,"
            " thu_tu = EXCLUDED.thu_tu",
            dictionary_rows(),
        )
        # mo_ta cho các mã có bẫy ngữ nghĩa đã biết
        cur.execute(
            "UPDATE ai.tu_dien_ma SET mo_ta ="
            " 'Đã hỏi nhưng khách không nói — KHÁC với NULL nghĩa là sale chưa hỏi'"
            " WHERE nhom_ma = 'nhom_tuoi' AND ma = 'not_disclosed'"
        )
        cur.execute(
            "UPDATE ai.tu_dien_ma SET mo_ta ="
            " 'Giá trị lịch sử trước khi tách phiếu ngày — movement mới không dùng'"
            " WHERE nhom_ma = 'loai_chung_tu_kho' AND ma = 'return'"
        )


class Migration(migrations.Migration):
    dependencies = [("ai_mart", "0002_dw_updated_at")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=DDL, reverse_sql=DROP),
                migrations.RunPython(seed, migrations.RunPython.noop),
            ]
        )
    ]
