"""Nắn ngày nghiệp vụ cho PHIẾU KHO ghi lùi ngày trước khi có luật
"ngày chứng từ = ngày nghiệp vụ" (`stock_document_service.create_document`).

Trước fix, form phiếu kho cho người dùng chọn ngày (lưu vào `created_at`) nhưng
`occurred_at` của phiếu luôn NULL, nên bút toán lúc duyệt lấy `now()`. Hệ quả:
phiếu ghi ngày D duyệt ngày D+n chỉ hiện ở khung lọc ngày D+n của trang Lịch sử
nhập xuất (trang này lọc theo `occurred_at` — xem `movement_window`), còn ngày D
thì trống. Ca thật: phiếu chuyển kho CK20260815-0001 ghi ngày 14/08 không hiện ở
tab Xuất ngày 14/08 dù tồn kho đã bị trừ.

Tách khỏi file migration để TEST được — migration chỉ gọi vào đây, cùng khuôn với
[`occurred_at_backfill`](occurred_at_backfill.py) và chia lô theo dải id trên
`stock_movements` vì điều kiện lọc nằm trên `reference_id`, không prune được
partition.

Ba lằn ranh (mỗi cái ứng với một test trong
`tests/test_stock_document_date_backfill.py`):

1. CHỈ bút toán GỐC, không đụng bút toán ĐẢO. `cancel_document` ghi bút toán đảo
   CÙNG movement_type/reference_type/reference_id, chỉ khác `note` ("Huỷ phiếu
   ..."), nên `m.note = ''` là cách duy nhất tách được — y hệt nguyên tắc 1 của
   `occurred_at_backfill`. Huỷ phiếu là sự kiện của NGÀY HUỶ.
2. CHỈ phiếu `posted`. Với phiếu đã huỷ, cặp gốc/đảo đang cùng nằm ở ngày ghi sổ
   nên net = 0 ở mọi khung ngày; kéo riêng vế gốc về ngày chứng từ sẽ làm phiếu
   đã huỷ "sống lại" ở ngày đó.
3. CHỈ nắn LÙI (`d.created_at < m.occurred_at`). Vừa giữ bất biến
   `occurred_at <= created_at` (nền của partition pruning), vừa khiến thao tác
   idempotent: chạy lại là no-op.
"""
from __future__ import annotations

# Cùng cỡ lô với 0025/0027 trên chính bảng này — giữ một chuẩn duy nhất cho ledger.
_BATCH = 50_000

# Phiếu cũ chưa có mốc nghiệp vụ: lấp bằng ngày chứng từ để lần duyệt SAU khi
# deploy cũng rơi đúng ngày (phiếu nháp/chờ duyệt tồn đọng).
_DOCUMENTS_SQL = """
UPDATE stock_documents
SET occurred_at = created_at
WHERE occurred_at IS NULL;
"""

# `m.created_at` ở vế phải điều kiện cuối là mốc GHI SỔ của bút toán: dữ liệu cũ
# lọt qua vì trước fix không ai chặn ngày chứng từ tương lai, mà nắn tới đó là
# phá bất biến occurred_at <= created_at.
_MOVEMENTS_SQL = """
UPDATE stock_movements m
SET occurred_at = d.created_at
FROM stock_documents d
WHERE m.reference_type = 'document'
  AND m.reference_id = d.id
  AND m.note = ''
  AND d.status = 'posted'
  AND d.created_at < m.occurred_at
  AND d.created_at <= m.created_at
  AND m.id >= %(low)s AND m.id < %(high)s;
"""


def _run_batched(cur, sql: str) -> int:
    """Chạy `sql` theo từng dải id của stock_movements, trả tổng số dòng đã nắn.

    Dải id lấy một lần trước vòng lặp: bảng append-only, dòng mới ghi sau fix đã
    mang sẵn ngày đúng nên không cần nắn.
    """
    cur.execute(
        "SELECT COALESCE(MIN(id), 0), COALESCE(MAX(id), -1) FROM stock_movements;"
    )
    low, high = cur.fetchone()
    total = 0
    while low <= high:
        cur.execute(sql, {"low": low, "high": low + _BATCH})
        total += cur.rowcount
        low += _BATCH
    return total


def backfill_document_business_date(connection) -> dict:
    """Trả `{documents, movements}` = số dòng đã nắn ở từng bảng."""
    with connection.cursor() as cur:
        cur.execute(_DOCUMENTS_SQL)
        documents = cur.rowcount
        movements = _run_batched(cur, _MOVEMENTS_SQL)
    return {"documents": documents, "movements": movements}
