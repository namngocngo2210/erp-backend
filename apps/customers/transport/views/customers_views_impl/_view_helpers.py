"""Helper dùng chung cho các view của app customers.

Gom phân trang + parse query param — hai thứ được nhiều nhóm view (CRUD, loyalty,
resale, profile) dùng lại.

`_resale_channel_scope_error` ĐÃ BỊ XOÁ khỏi đây (audit 2026-07-26 N1): nó chỉ hỏi
`OrderService.can_edit_channel` → `UserService.can_edit_order`, vốn fail-open cho scope
OWN/ALL. Bản thay thế `_resale_order_scope_error` nằm NGAY TRONG `resale_tracking_view.py`
— cùng module với 3 endpoint duy nhất dùng nó — vì lưới chặn cấu trúc
(`apps/orders/tests/test_record_scope_coverage.py`) chỉ đi theo helper CÙNG MODULE khi
kiểm view có record-scope hay không; guard nằm ở module khác sẽ vô hình với lưới đó.
"""

from apps.common.pagination import parse_required_pagination


def _parse_required_pagination(request):
    # Thin wrapper kept for backward-compatible imports; logic lives in apps.common.
    return parse_required_pagination(request)


def _query_int(request, key: str) -> int | None:
    try:
        return int(request.query_params.get(key))
    except (TypeError, ValueError):
        return None
