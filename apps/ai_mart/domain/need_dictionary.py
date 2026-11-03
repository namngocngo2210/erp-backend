"""nhu_cau / nhom_nhu_cau đọc THẲNG từ bảng need_types / need_groups.

Không chép crm_catalog.NEED_TYPE_SEED: đo 04/09/2026 thấy bảng có 36 mã còn
seed chỉ 29 — 7 mã kia do admin thêm sau, chép hằng số là để chúng thành mã
mồ côi trên mart (cột _ma có giá trị mà _text NULL, đúng cái bệnh spec ai_mart
sinh ra để chữa).
"""

_UPSERT_NHOM = """
INSERT INTO ai.tu_dien_ma (nhom_ma, ma, nhan, thu_tu)
SELECT 'nhom_nhu_cau', g.code, g.name_vi, coalesce(g.sort_order, 0)
FROM need_groups g
ON CONFLICT (nhom_ma, ma) DO UPDATE
SET nhan = EXCLUDED.nhan, thu_tu = EXCLUDED.thu_tu
"""

# mo_ta: mã ngừng dùng VẪN được seed (nhãn lịch sử trên mart phải tra được),
# nhưng phải nói rõ đã ngừng. NULL cho mã còn hiệu lực — không nhồi chữ thừa.
_UPSERT_NHU_CAU = """
INSERT INTO ai.tu_dien_ma (nhom_ma, ma, nhan, mo_ta, thu_tu)
SELECT 'nhu_cau', t.code, t.name_vi,
       CASE WHEN t.is_active THEN NULL
            ELSE 'Mã đã ngừng dùng — chỉ còn xuất hiện ở dữ liệu lịch sử' END,
       coalesce(t.sort_order, 0)
FROM need_types t
ON CONFLICT (nhom_ma, ma) DO UPDATE
SET nhan = EXCLUDED.nhan, mo_ta = EXCLUDED.mo_ta, thu_tu = EXCLUDED.thu_tu
"""


def refresh_need_dictionary(cursor) -> int:
    """Đồng bộ 2 nhóm mã nhu cầu vào ai.tu_dien_ma. Idempotent."""
    total = 0
    for sql in (_UPSERT_NHOM, _UPSERT_NHU_CAU):
        cursor.execute(sql)
        total += cursor.rowcount
    return total
