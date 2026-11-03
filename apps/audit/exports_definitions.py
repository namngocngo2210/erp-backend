"""Định nghĩa export của app audit — đăng ký vào engine ở AuditConfig.ready().

`kind` = "audit_logs", `file_ext` = "csv" (khác 6 kind kia đều xlsx). Mirror
`AuditLogViewSet.export` gốc (action đã xoá ở Task 18 cùng endpoint đồng bộ
`GET /api/audit-logs/export/`): cùng 10 cột
`_EXPORT_COLUMNS`, cùng trần cắt-lặng-lẽ `_EXPORT_MAX_ROWS = 10_000` (view cũ
KHÔNG raise khi vượt trần — chỉ `[:cap]`, không cảnh báo gì cả — giữ nguyên y
hệt, không thêm hành vi mới).

Điểm khó nhất của luồng này: quyền xem bị SIẾT theo user ngay trong
`get_queryset` (admin thấy tất cả, người thường chỉ thấy log của chính mình) và
bộ lọc dùng `django_filter.DjangoFilterBackend` + `OrderingFilter` tự sinh từ
`filterset_fields`/`ordering` của ViewSet — không phải một hàm thuần có thể gọi
thẳng như `build_orders_filters`. Thay vì chép lại luật lọc (rủi ro lệch),
`_scoped_queryset` TÁI DÙNG NGUYÊN `AuditLogViewSet.get_queryset`/
`filter_queryset` bằng cách dựng object request giả tối thiểu — cùng kỹ thuật
shim của OrdersExport, chỉ khác: `request.query_params` (django_filter đọc) thay
vì `request.data`.
"""

from __future__ import annotations

from django.http import QueryDict
from rest_framework.exceptions import PermissionDenied

from apps.audit.transport.views.audit_views_impl import AuditLogViewSet
from apps.exports.application.registry import ExportDefinition, register_export
from apps.users.auth_core import user_has_any_permission

_EXPORT_COLUMNS = (
    "id", "user", "action_type", "entity_type", "entity_id",
    "method", "timestamp", "ip_address", "duration_ms", "source",
)
# GIỮ NGUYÊN AuditLogViewSet._EXPORT_MAX_ROWS — view đồng bộ cắt lặng lẽ, không
# raise khi vượt trần (khác warranty_claims/stock), nên ở đây cũng không raise.
_EXPORT_MAX_ROWS = AuditLogViewSet._EXPORT_MAX_ROWS

# Đúng field mà AuditLogViewSet.filterset_fields khai — chỉ nhặt các key này ra
# khỏi raw_params, key lạ bị bỏ qua (django_filter cũng bỏ qua key không khớp
# field, nhặt trước ở đây chỉ để params ghi vào job gọn, không lẫn rác client gửi).
_FILTER_KEYS = ("user", "action_type", "entity_type", "entity_id", "source", "timestamp")


class _AuditRequestShim:
    """Giả lập tối thiểu DRF request để TÁI DÙNG NGUYÊN
    `AuditLogViewSet.get_queryset`/`filter_queryset` — xem docstring module.

    `get_queryset` chỉ đọc `.user`; `DjangoFilterBackend`/`OrderingFilter` chỉ
    đọc `.query_params`. Không dựng full DRF Request vì hai method đó là toàn bộ
    những gì hai method ta cần chạy lại đụng tới.
    """

    def __init__(self, user, query_params: dict):
        self.user = user
        qd = QueryDict(mutable=True)
        for key, value in query_params.items():
            qd[key] = str(value)
        self.query_params = qd


def _load_user(params: dict):
    """Nạp lại user từ `params` — estimate()/write() không nhận user, xem
    hợp đồng chung ở `OrdersExport._load_user`."""
    from apps.users.models import User

    return User.objects.filter(id=params["user_id"]).first()


def _scoped_queryset(user, filters: dict):
    view = AuditLogViewSet()
    view.request = _AuditRequestShim(user, filters)
    view.format_kwarg = None
    # Cùng `.defer(...)` view đồng bộ dùng ở export(): 3 cột nặng (response_data,
    # metadata, curl_command) không xuất ra CSV nên không tải lên. `.defer` đặt
    # TRƯỚC filter_queryset — đúng thứ tự view gốc.
    queryset = view.get_queryset().defer("response_data", "metadata", "curl_command")
    return view.filter_queryset(queryset)


class AuditLogsExport(ExportDefinition):
    kind = "audit_logs"
    file_ext = "csv"

    def validate(self, user, raw_params: dict) -> dict:
        filters = {
            key: raw_params[key]
            for key in _FILTER_KEYS
            if raw_params.get(key) not in (None, "")
        }
        return {"user_id": user.id, "filters": filters}

    def check_permission(self, user, params: dict) -> None:
        # Mirror require_section("audit") = require_permission("audit:view") —
        # view đồng bộ không đòi thêm quyền riêng cho action export.
        if not user_has_any_permission(user, "audit:view"):
            raise PermissionDenied("Bạn không có quyền thực hiện hành động này.")

    def estimate(self, params: dict) -> int | None:
        matched = _scoped_queryset(_load_user(params), params["filters"]).count()
        # Không raise khi vượt trần (xem docstring module) — trả số dòng THỰC SỰ
        # sẽ được ghi (bị cắt ở cap) để progress bar không treo ở một mốc không
        # bao giờ tới.
        return min(matched, _EXPORT_MAX_ROWS)

    def file_name(self, params: dict) -> str:
        return "audit-logs.csv"

    def write(self, params: dict, writer, progress) -> None:
        queryset = _scoped_queryset(_load_user(params), params["filters"])[:_EXPORT_MAX_ROWS]
        writer.write_header(_EXPORT_COLUMNS)
        count = 0
        for log in queryset.iterator(chunk_size=1000):
            writer.write_row(
                [
                    log.id,
                    str(log.user) if log.user_id else "",
                    log.action_type,
                    log.entity_type,
                    log.entity_id or "",
                    log.method or "",
                    log.timestamp.isoformat() if log.timestamp else "",
                    log.ip_address or "",
                    log.duration_ms if log.duration_ms is not None else "",
                    log.source,
                ]
            )
            count += 1
            progress(count)
        progress(count)


register_export(AuditLogsExport())
