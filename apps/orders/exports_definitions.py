"""Định nghĩa export của app orders — đăng ký vào engine ở OrdersConfig.ready()."""

from __future__ import annotations

from datetime import date

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.exports.application.registry import ExportDefinition, register_export
from apps.orders.application.services.contact_list_service import ContactListService
from apps.orders.contact_export_excel import (
    CONTACT_EXPORT_HEADERS,
    CONTACT_EXPORT_MAX_ROWS,
    contact_row_values,
)
from apps.orders.domain.contact_rules import ACTION_KEYS
from apps.orders.export_excel import order_row_values
from apps.users.phone_visibility import phone_visibility_for
from apps.orders.repositories import (
    EXPORT_MAX_ROWS,
    _section_filter_overrides,
    build_export_orders_queryset,
)
# `build_orders_filters` đọc thẳng `request.data` (transport concern) nên VẪN ở
# _view_helpers.py — chỉ 2 hàm business (`_section_filter_overrides`,
# `build_export_orders_queryset`) dời sang repositories.py ở Task 18. Import
# này là ghép nối CÓ CHỦ ĐÍCH giữ nguyên từ trước Task 18 (không phải nợ mới):
# lý do + đánh đổi xem docstring `_FiltersRequestShim` ngay dưới.
from apps.orders.transport.views.order_views_impl._view_helpers import build_orders_filters
from apps.users.auth_core import user_has_any_permission
from apps.users.permissions import ORDER_SECTIONS

# Số dòng nạp mỗi lô từ DB. 2000 là cân bằng giữa số round-trip và bộ nhớ giữ tạm
# cho prefetch assessment/needs của lô đó.
_CHUNK = 2000


class _FiltersRequestShim:
    """Giả lập tối thiểu của DRF request để TÁI DÙNG NGUYÊN `build_orders_filters` và
    `_section_filter_overrides` — cả hai hàm chỉ đọc `request.data`/`request.method`.

    Vì sao cần: `validate()` không có request thật (chỉ có `raw_params["filters"]`,
    dict JSON FE gửi lên — đúng vai trò `request.data` của luồng đồng bộ cũ). Không
    có shim này thì `_section_filter_overrides` (khoá cứng koc/landing +
    sheet 1/2) và `build_orders_filters` KHÔNG chạy được ở đây → job async sẽ xuất
    theo ĐÚNG những gì client gửi, mất hẳn lớp khoá "server tự quyết, client không
    nới được" mà luồng đồng bộ đang có — đúng loại rò rỉ mà Task 9 cảnh báo.
    """

    method = "POST"

    def __init__(self, data: dict):
        self.data = data


def _load_user(params: dict):
    """Nạp lại user từ `params`.

    `estimate` và `write` KHÔNG nhận `user` trong chữ ký (write chạy ở worker, nơi
    không có request), nên `validate` ghi `user_id` vào params và hai method kia nạp
    lại. Đây là hợp đồng chung cho MỌI definition cần resolve record-scope.
    """
    from apps.users.models import User

    return User.objects.filter(id=params["user_id"]).first()


class OrdersExport(ExportDefinition):
    kind = "orders"

    def validate(self, user, raw_params: dict) -> dict:
        section = str(raw_params.get("section") or "orders")
        if section not in ORDER_SECTIONS:
            raise ValidationError({"detail": "Section không hợp lệ."})
        columns = raw_params.get("columns") or []
        # `columns` PHẢI là list để `if not columns`/`all(... for c in columns)` bên
        # dưới lặp qua được — client gửi `columns: 5` (scalar không iterable) qua lọt
        # `if not columns` (5 truthy) rồi vỡ ở `all(...)` (`'int' object is not
        # iterable`) → TypeError trần, lọt thành 500 thay vì 400.
        if not isinstance(columns, list):
            raise ValidationError({"detail": "Danh sách cột xuất không hợp lệ."})
        if not columns:
            raise ValidationError({"detail": "Chưa chọn cột xuất."})
        # Mỗi phần tử PHẢI là dict để `.get("key")` dưới đây chạy được — client gửi
        # `columns: ["a", "b"]` (list chuỗi) qua lọt check `if not columns` (list
        # không rỗng) rồi vỡ ở `.get()` (str không có `.get`) → AttributeError trần,
        # lọt thành 500 thay vì 400 đúng hợp đồng `validate()`.
        if not all(isinstance(c, dict) for c in columns):
            raise ValidationError({"detail": "Danh sách cột xuất không hợp lệ."})
        # `int(v)` ném ValueError trần nếu client gửi `ids: ["abc"]` — ValueError
        # KHÔNG được `apps.exports` xử lý như lỗi validate (chỉ bắt ValidationError
        # của DRF), nên sẽ lọt thành 500 thay vì 400 đúng hợp đồng `validate()`.
        # Bọc lại và raise ValidationError để job bị từ chối NGAY lúc tạo, có
        # thông báo rõ ràng cho client thay vì crash worker/API.
        try:
            ids = [int(v) for v in (raw_params.get("ids") or [])]
        except (TypeError, ValueError):
            raise ValidationError({"detail": "Danh sách ids không hợp lệ."}) from None
        # `raw_params["filters"]` là filters THÔ do FE gửi (như `request.data` trước
        # đây) — phải chạy qua ĐÚNG hai hàm luồng đồng bộ dùng để tập xuất khớp tập
        # list endpoint của section: build_orders_filters chuẩn hoá các trục lọc
        # chung, _section_filter_overrides khoá cứng theo trang gọi (đè lên, không
        # để client tự set các khoá đó). Không phụ thuộc `ids`: build_export_orders_
        # queryset tự bỏ qua `filters` khi có `ids`, tính trước ở đây cho đơn giản.
        # `filters` PHẢI là dict — client gửi `filters: "abc"` (chuỗi) thì
        # `dict("abc")` ném ValueError trần ("dictionary update sequence element #0
        # has length 1; 2 is required"), lọt thành 500 thay vì 400.
        raw_filters_input = raw_params.get("filters")
        if raw_filters_input is not None and not isinstance(raw_filters_input, dict):
            raise ValidationError({"detail": "Bộ lọc không hợp lệ."})
        raw_filters = dict(raw_filters_input or {})
        # `return_date_from`/`return_date_to` đi THẲNG vào `date.fromisoformat()`
        # ở `OrderRepository._filter_by_created_and_return_date` (repositories.py)
        # KHÔNG qua `str()` như `date_from`/`date_to` (những field đó đi qua
        # `_parse_local_datetime_boundary`, tự `str(value or "")`) — client gửi một
        # số (vd `return_date_from: 20260101`) thì `date.fromisoformat` ném
        # TypeError (không phải ValueError), lọt qua `except ValueError` của
        # repository và vỡ ở `estimate()` (gọi `queryset.count()`) thành 500.
        for date_key in ("return_date_from", "return_date_to"):
            value = raw_filters.get(date_key)
            if value is not None and not isinstance(value, str):
                raise ValidationError({"detail": "Ngày trả hàng không hợp lệ."})
        shim = _FiltersRequestShim(raw_filters)
        filters = {**build_orders_filters(shim), **_section_filter_overrides(shim, section)}
        return {
            "user_id": user.id,
            "section": section,
            "columns": [
                {"key": str(c.get("key")), "header": str(c.get("header") or c.get("key"))}
                for c in columns
            ],
            "ids": ids,
            "filters": filters,
        }

    def check_permission(self, user, params: dict) -> None:
        if not user_has_any_permission(user, f"{params['section']}:export"):
            raise PermissionDenied("Bạn không có quyền xuất Excel ở trang này.")

    def estimate(self, params: dict) -> int | None:
        if params["ids"]:
            return len(params["ids"])
        queryset = build_export_orders_queryset(
            _load_user(params), params["section"], [], params["filters"]
        )
        matched = queryset.count()
        if matched > EXPORT_MAX_ROWS:
            raise ValidationError(
                {
                    "detail": (
                        f"Bộ lọc khớp {matched} đơn, vượt giới hạn {EXPORT_MAX_ROWS} đơn "
                        "mỗi lần xuất. Vui lòng thu hẹp bộ lọc (theo ngày/nhân viên/kênh) "
                        "rồi thử lại."
                    )
                }
            )
        return matched

    def file_name(self, params: dict) -> str:
        return f"don-hang-{timezone.localtime(timezone.now()):%Y%m%d-%H%M%S}.xlsx"

    def write(self, params: dict, writer, progress) -> None:
        writer.write_header([c["header"] for c in params["columns"]])
        user = _load_user(params)
        queryset = build_export_orders_queryset(
            user, params["section"], params["ids"], params["filters"]
        )
        # Cột SĐT che theo đúng luật của màn hình: đơn không phải của người xuất và
        # người xuất không có `customer-phone:view` → 091****678. Không che ở đây thì
        # một cú bấm "Xuất Excel" vô hiệu hoá toàn bộ lớp che trên giao diện.
        visibility = phone_visibility_for(user)
        count = 0
        for order in queryset.iterator(chunk_size=_CHUNK):
            writer.write_row(order_row_values(order, params["columns"], visibility))
            count += 1
            progress(count)
        progress(count)


register_export(OrdersExport())


# ─────────────────────────── Danh sách contact (trang Sale) ───────────────────
#
# `kind` = "contacts". Khác OrdersExport ở chỗ tập dòng KHÔNG phải một queryset
# stream được: pipeline contact lọc trong Python sau khi DISTINCT ON (xem
# ContactListService._resolve_rows), nên estimate/write đều đi qua service thay vì
# tự dựng queryset — đúng chủ ý "màn hình và file Excel dùng chung một đường lọc".


_CONTACT_INTERACTIONS = ("", "none", "recent")
_CONTACT_CUSTOMER_TYPES = ("", "new", "old")
_CONTACT_OPERATION_BUCKETS = ("overdue", "today", "upcoming", "ended", "none")


def _contact_date(raw, field_label: str):
    """"YYYY-MM-DD" → date, rỗng/None → None. Kiểu sai hoặc chuỗi sai định dạng đều
    thành ValidationError (400) chứ không để `date.fromisoformat` ném TypeError/
    ValueError trần lọt thành 500 — cùng bài học `return_date_from` của OrdersExport."""
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise ValidationError({"detail": f"{field_label} không hợp lệ."})
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValidationError({"detail": f"{field_label} không hợp lệ."}) from None


def _contact_int_list(raw, field_label: str) -> list[int]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValidationError({"detail": f"{field_label} không hợp lệ."})
    try:
        return [int(value) for value in raw]
    except (TypeError, ValueError):
        raise ValidationError({"detail": f"{field_label} không hợp lệ."}) from None


def _contact_str_list(raw, field_label: str) -> list[str]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValidationError({"detail": f"{field_label} không hợp lệ."})
    if not all(isinstance(value, str) for value in raw):
        raise ValidationError({"detail": f"{field_label} không hợp lệ."})
    return list(raw)


def _contact_choice(raw, allowed, field_label: str, *, default=""):
    if raw is None:
        return default
    if not isinstance(raw, str) or raw not in allowed:
        raise ValidationError({"detail": f"{field_label} không hợp lệ."})
    return raw


class ContactsExport(ExportDefinition):
    kind = "contacts"

    def validate(self, user, raw_params: dict) -> dict:
        filters_input = raw_params.get("filters")
        if filters_input is not None and not isinstance(filters_input, dict):
            raise ValidationError({"detail": "Bộ lọc không hợp lệ."})
        raw = dict(filters_input or {})

        q = raw.get("q") or ""
        if not isinstance(q, str):
            raise ValidationError({"detail": "Từ khoá tìm kiếm không hợp lệ."})

        action_needed = _contact_str_list(raw.get("action_needed"), "Tác nghiệp cần")
        unknown = [code for code in action_needed if code not in ACTION_KEYS]
        if unknown:
            raise ValidationError({"detail": "Tác nghiệp cần không hợp lệ."})

        operation_bucket = raw.get("operation_bucket") or None
        if operation_bucket is not None:
            operation_bucket = _contact_choice(
                operation_bucket, _CONTACT_OPERATION_BUCKETS, "Nhóm tác nghiệp"
            )

        # `date_from`/`date_to` giữ dạng chuỗi ISO trong params (params phải
        # JSON-serializable để ghi vào job + băm params_hash); đổi sang `date` ở
        # `_contact_filters` ngay trước khi gọi service.
        date_from = _contact_date(raw.get("date_from"), "Ngày bắt đầu")
        date_to = _contact_date(raw.get("date_to"), "Ngày kết thúc")

        # Khoá của `raw` là ĐÚNG tên query param của list endpoint (channel/order_source/
        # staff/product), không phải tên kwargs của service: frontend gửi thẳng bộ lọc
        # đang áp trên màn hình, không phải dịch sang một bộ tên thứ hai — bộ tên thứ hai
        # là chỗ hai đường lọc bắt đầu trôi khỏi nhau.
        return {
            "user_id": user.id,
            "filters": {
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None,
                "q": q,
                "channel_ids": _contact_int_list(raw.get("channel"), "Kênh"),
                "order_sources": _contact_str_list(raw.get("order_source"), "Nguồn đơn"),
                "staff_ids": _contact_int_list(raw.get("staff"), "Nhân viên"),
                "product_ids": _contact_int_list(raw.get("product"), "Sản phẩm"),
                "action_needed": action_needed,
                "interaction": _contact_choice(
                    raw.get("interaction"), _CONTACT_INTERACTIONS, "Tương tác"
                ),
                "customer_type": _contact_choice(
                    raw.get("customer_type"), _CONTACT_CUSTOMER_TYPES, "Loại khách"
                ),
                "operation_bucket": operation_bucket,
                # Dòng người dùng tích chọn trên bảng. Rỗng = xuất theo bộ lọc (hành vi
                # cũ). KHÔNG đè lên `filters`: tập xuất là GIAO của hai thứ, xem
                # `ContactListService._resolve_rows`.
                "customer_ids": _contact_int_list(raw.get("customer_ids"), "Khách đã chọn"),
            },
        }

    def check_permission(self, user, params: dict) -> None:
        if not user_has_any_permission(user, "contacts:export"):
            raise PermissionDenied("Bạn không có quyền xuất Excel ở trang này.")

    def estimate(self, params: dict) -> int | None:
        matched = ContactListService.count_contacts(
            user=_load_user(params), filters=_contact_filters(params)
        )
        if matched > CONTACT_EXPORT_MAX_ROWS:
            raise ValidationError(
                {
                    "detail": (
                        f"Bộ lọc khớp {matched} khách, vượt giới hạn "
                        f"{CONTACT_EXPORT_MAX_ROWS} khách mỗi lần xuất. Vui lòng thu hẹp "
                        "bộ lọc (theo ngày/kênh/nhân viên) rồi thử lại."
                    )
                }
            )
        return matched

    def file_name(self, params: dict) -> str:
        return f"danh-sach-contact-{timezone.localtime(timezone.now()):%Y%m%d-%H%M%S}.xlsx"

    def write(self, params: dict, writer, progress) -> None:
        writer.write_header(CONTACT_EXPORT_HEADERS)
        # `now` chốt MỘT LẦN cho cả file: cột "Thời gian chờ" là hiệu số tới hiện tại,
        # để mỗi dòng tự gọi timezone.now() thì hai dòng cùng mốc chờ có thể ra "23h"
        # và "1d" chỉ vì job chạy vắt qua ranh giới.
        now = timezone.now()
        count = 0
        for batch in ContactListService.iter_contact_batches(
            user=_load_user(params), filters=_contact_filters(params)
        ):
            for item in batch:
                writer.write_row(contact_row_values(item, now=now))
                count += 1
            progress(count)
        progress(count)


def _contact_filters(params: dict) -> dict:
    """`params["filters"]` (JSON) → kwargs của `ContactListService._resolve_rows`."""
    filters = dict(params["filters"])
    filters["date_from"] = date.fromisoformat(f) if (f := filters.get("date_from")) else None
    filters["date_to"] = date.fromisoformat(t) if (t := filters.get("date_to")) else None
    return filters


register_export(ContactsExport())
