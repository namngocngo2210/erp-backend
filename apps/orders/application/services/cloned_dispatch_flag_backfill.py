"""Dọn cờ "đã xuất kho" mà nút Nhân bản đơn copy nhầm sang bản sao.

`duplicate_order` từng clone nguyên `OrderAssessment` của đơn gốc, gồm cả
`delivery_business_status_code`. Bản sao của một đơn đã giao vì thế ra đời với
mã `dispatched` trong khi chưa hề trừ tồn — guard chống xuất trùng đọc thẳng
field này nên kho quét mã vận đơn MỚI bị chặn ("đã xuất kho trước đó").

Điều kiện lọc cố ý CHẶT, vì "không có bút toán" một mình KHÔNG đủ để kết luận
đơn chưa xuất: đơn không khớp được sản phẩm nào xuất kho với ``lines=[]`` nên
không sinh bút toán nào. Ba lớp lọc cộng lại mới an toàn:

1. ``delivery_business_status_code = 'dispatched'`` — chỉ mã này chặn quét kho.
2. Assessment sinh CÙNG LÚC với đơn (≤ 5 giây) — dấu vân tay của bản clone;
   luồng thường phải có người bấm chọn trạng thái sau khi đơn đã tồn tại.
3. Đơn chưa từng có bút toán tồn kho VÀ chưa từng có mã quét nào khớp vào nó.

Đo prod 2026-08-23: đúng 7 đơn thoả, mỗi đơn đều có dòng hàng, 0 mã quét, 0 bút toán.
"""
from __future__ import annotations

_SELECT_CANDIDATES = """
SELECT o.id
FROM orders o
JOIN order_assessments a ON a.order_id = o.id
WHERE o.deleted_at IS NULL
  AND a.delivery_business_status_code = 'dispatched'
  AND a.created_at <= o.created_at + interval '5 seconds'
  AND NOT EXISTS (
      SELECT 1 FROM stock_movements m
      WHERE m.reference_type = 'order' AND m.reference_id = o.id
  )
  AND NOT EXISTS (
      SELECT 1 FROM pending_shipment_scans s WHERE s.matched_order_id = o.id
  )
ORDER BY o.id
"""

_CLEAR_FLAGS = f"""
UPDATE order_assessments a
SET delivery_business_status_code = NULL, updated_at = now()
WHERE a.order_id IN ({_SELECT_CANDIDATES})
"""


def find_cloned_dispatch_flags(connection) -> list[int]:
    """Danh sách order_id đang đeo cờ `dispatched` do nhân bản (chỉ ĐỌC)."""
    with connection.cursor() as cursor:
        cursor.execute(_SELECT_CANDIDATES)
        return [row[0] for row in cursor.fetchall()]


def clear_cloned_dispatch_flags(connection) -> list[int]:
    """Đưa cờ về NULL cho các đơn thoả điều kiện. Trả về danh sách order_id đã sửa.

    Lấy danh sách TRƯỚC khi UPDATE thay vì dùng RETURNING: cần đúng id đơn (bảng
    được cập nhật là `order_assessments`) để log lại dấu vết — chiều ngược của
    migration là noop nên đây là bằng chứng duy nhất về phạm vi tác động.
    """
    order_ids = find_cloned_dispatch_flags(connection)
    if not order_ids:
        return []
    with connection.cursor() as cursor:
        cursor.execute(_CLEAR_FLAGS)
    return order_ids
