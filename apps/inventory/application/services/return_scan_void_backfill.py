"""Dọn các dòng quét NHẬP HOÀN đã chết mà vẫn hiển thị trên tab Nhập hoàn.

Trước bản vá "1 mã = 1 lần bắn", huỷ một lần nhập hoàn chỉ đảo bút toán tồn chứ
không đụng tới dòng quét: dòng kẹt ở ``matched`` nên vẫn hiện, lại mượn SKU × SL
của nhóm đơn còn sống cùng mã (``_attach_return_notes``) → một mã bị bắn nhiều lần
hiện nhiều dòng, nhìn như đã nhập nhiều lần trong khi kho chỉ nhận một.

Tách khỏi file migration để test được bằng dữ liệu dựng tay (cùng nếp với
``stock_document_date_backfill`` / migration 0034).
"""
from __future__ import annotations

# `id DESC` chứ không phải `created_at DESC`: hai lần bắn trong cùng một giây có
# thể trùng created_at, id thì luôn tăng đơn điệu.
#
# Luật dọn — ĐÚNG MỘT luật phủ cả hai nhóm dòng chết:
#   Thu hồi mọi dòng quét return `matched`, TRỪ dòng MỚI NHẤT của mỗi mã khi mã
#   đó còn một nhóm đơn hoàn chưa huỷ.
#     • Mã KHÔNG còn nhóm đơn sống → thu hồi tất cả (lần nhập đã bị huỷ, dòng là xác).
#     • Mã CÒN nhóm đơn sống → giữ dòng mới nhất (chính dòng đã sinh ra nhóm đơn đó:
#       mỗi lần bắn lại đều diễn ra SAU khi huỷ lần trước), thu hồi các dòng cũ hơn.
#
# Chỉ đụng `matched`: dòng pending/failed/invalid/cancelled chưa từng ra nhóm đơn
# hoàn nào và kho PHẢI thấy để xử lý tay — thu hồi chúng là nuốt mã.
#
# Mã khớp HÀNH TRÌNH BẢO HÀNH (`result_ref` = "warranty-journey:…") được loại trừ:
# chúng khớp thành công mà bản chất không sinh nhóm đơn hoàn nào, không có gì chết.
_VOID_SQL = """
UPDATE pending_shipment_scans s
SET voided_at = NOW()
FROM (
    SELECT id FROM (
        SELECT
            p.id,
            row_number() OVER (PARTITION BY p.code ORDER BY p.id DESC) AS rn,
            EXISTS (
                SELECT 1 FROM stock_return_entries e
                WHERE e.shipping_code = p.code AND e.status <> 'cancelled'
            ) AS live_entry
        FROM pending_shipment_scans p
        WHERE p.kind = 'return'
          AND p.status = 'matched'
          AND p.voided_at IS NULL
          AND p.result_ref NOT LIKE 'warranty-journey:%%'
    ) ranked
    WHERE NOT (rn = 1 AND live_entry)
) dead
WHERE s.id = dead.id;
"""


def void_dead_return_scans(connection) -> int:
    """Thu hồi các dòng quét nhập hoàn đã chết. Trả về số dòng đã đóng dấu.

    Không đụng bút toán tồn: tồn của các ca này vốn đã đúng (mỗi lần huỷ đều có
    bút toán đảo), chỉ HIỂN THỊ là sai.
    """
    with connection.cursor() as cursor:
        cursor.execute(_VOID_SQL)
        return cursor.rowcount
