"""Test `OrdersExport` — definition đăng ký kind "orders" vào engine apps.exports.

Ngoài 4 test đơn giản (registry/validate/permission/write) theo brief, còn có
`OrdersExportSectionParityTests`: với cùng dữ liệu, tập đơn `OrdersExport.write`
sinh ra phải KHỚP CHÍNH XÁC tập mà list endpoint của từng section trả về. Đây là
lưới an toàn chống rò rỉ giữa các trang (koc/outside/landing) — cùng khuôn với
`BulkExportExcelSectionFilterTests` ở test_order_export.py.
"""

import pytest
from rest_framework.exceptions import ValidationError

from apps.exports.application.registry import get_definition
from apps.orders.models import Order
from apps.orders.tests.helpers import make_scoped_user
from apps.users.models import DataScope
from apps.users.rbac_catalog import sync_permissions
from apps.users.tests.helpers import make_user_with_sections

pytestmark = pytest.mark.django_db


def test_validate_defaults_section_to_orders_when_omitted():
    """Mirror `test_section_defaults_to_orders` (test_order_export.py, đã xoá ở
    Task 18): FE cũ không gửi `section` → giữ hành vi cũ (mặc định "orders"),
    không raise ValidationError vì "thiếu section"."""
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-section-default", ["orders"])
    params = definition.validate(user, {"columns": [{"key": "name", "header": "Tên"}]})
    assert params["section"] == "orders"


def test_validate_rejects_unknown_section():
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-1", ["orders"])
    with pytest.raises(ValidationError):
        definition.validate(user, {"section": "khong-co", "columns": [{"key": "name"}]})


def test_validate_rejects_empty_columns():
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-2", ["orders"])
    with pytest.raises(ValidationError):
        definition.validate(user, {"section": "orders", "columns": []})


def test_validate_rejects_non_numeric_ids_with_400_not_500():
    """Phần D (Task 18): `ids: ["abc"]` phải raise ValidationError (→ 400 ở
    tầng views của apps.exports), KHÔNG được để ValueError trần lọt ra ngoài
    (→ 500). Trước fix, `int(v)` không được bọc try/except."""
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-bad-ids", ["orders"])
    with pytest.raises(ValidationError):
        definition.validate(
            user, {"section": "orders", "columns": [{"key": "name"}], "ids": ["abc"]}
        )


# ─────────────────── final-fix-report mục 6: 4 đường 500 còn sót ───────────────
#
# `OrdersExport.validate` từng bọc `ids` sai kiểu (test ở trên) nhưng còn 4 đường
# khác vẫn trả 500 thay vì 400: `filters` không phải dict, `columns` chứa phần tử
# không phải dict, `columns` là scalar không iterable, và `return_date_from`/
# `return_date_to` không phải chuỗi (đi thẳng vào `date.fromisoformat` ở
# repositories.py, không qua `str()` như `date_from`/`date_to`).


def test_validate_rejects_non_dict_filters_with_400_not_500():
    """`filters: "abc"` (chuỗi) → `dict("abc")` ném ValueError trần trước fix."""
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-bad-filters", ["orders"])
    with pytest.raises(ValidationError):
        definition.validate(
            user,
            {"section": "orders", "columns": [{"key": "name"}], "filters": "abc"},
        )


def test_validate_rejects_non_dict_column_entries_with_400_not_500():
    """`columns: ["a", "b"]` (list chuỗi) qua lọt `if not columns` rồi vỡ ở
    `c.get("key")` (str không có `.get`) → AttributeError trần trước fix."""
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-bad-columns", ["orders"])
    with pytest.raises(ValidationError):
        definition.validate(user, {"section": "orders", "columns": ["a", "b"]})


def test_validate_rejects_scalar_columns_with_400_not_500():
    """`columns: 5` (scalar, không iterable) qua lọt `if not columns` (5 truthy)
    rồi vỡ ở `all(... for c in columns)` → TypeError trần trước fix."""
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-scalar-columns", ["orders"])
    with pytest.raises(ValidationError):
        definition.validate(user, {"section": "orders", "columns": 5})


def test_validate_rejects_non_string_return_date_with_400_not_500():
    """`filters.return_date_from` không phải chuỗi (vd số) → `date.fromisoformat`
    ném TypeError (không phải ValueError) ở repositories.py, lọt qua
    `except ValueError` và vỡ ở `estimate()` thành 500 trước fix."""
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-bad-return-date", ["orders"])
    with pytest.raises(ValidationError):
        definition.validate(
            user,
            {
                "section": "orders",
                "columns": [{"key": "name"}],
                "filters": {"return_date_from": 20260101},
            },
        )


# check_permission()/registered/file_name: hợp đồng chung ở
# apps/exports/tests/test_export_definitions_registry.py (kể cả quyền theo ĐÚNG section).


def test_write_emits_header_and_rows(order_factory):
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-4", ["orders"])
    order = order_factory(name="Anh A")
    params = definition.validate(
        user,
        {"section": "orders", "columns": [{"key": "name", "header": "Tên"}], "ids": [order.id]},
    )

    rows = []

    class _W:
        def write_header(self, headers):
            rows.append(list(headers))

        def write_row(self, values):
            rows.append(list(values))

    definition.write(params, _W(), lambda n: None)
    assert rows[0] == ["Tên"]
    assert ["Anh A"] in rows


class _RecordingWriter:
    """Writer giả — chỉ gom lại giá trị cột `name` của các dòng dữ liệu."""

    def __init__(self):
        self.headers = []
        self.rows = []

    def write_header(self, headers):
        self.headers = list(headers)

    def write_row(self, values):
        self.rows.append(list(values))


def _written_names(definition, params) -> set[str]:
    writer = _RecordingWriter()
    definition.write(params, writer, lambda n: None)
    name_idx = writer.headers.index("Tên")
    return {row[name_idx] for row in writer.rows}


def test_write_returns_nothing_when_channel_scope_has_no_assigned_channel(order_factory):
    """Nhánh `scope_empty` của `build_export_orders_queryset` — nhánh BẮT BUỘC theo
    brief: user scope CHANNEL/DEPARTMENT nhưng CHƯA được gán kênh nào thì
    `scope_filters` (từ `_apply_user_channel_scope`) không chứa cả `owner_user_id` lẫn
    `channel`, nên nếu thiếu `if scope_empty: return Order.objects.none()` thì không
    nhánh filter nào bên dưới chạy → không gì lọc cả → rò TOÀN BỘ đơn công ty.

    Bám khuôn `test_order_print.py::test_channel_scope_without_any_channel_prints_
    nothing_in_bulk` (dựng user scope CHANNEL, cố tình KHÔNG tạo `UserChannel` nào)
    — đây là cách dựng scope rỗng đã có sẵn trong codebase, không tự sáng tạo cách
    khác.
    """
    sync_permissions()
    definition = get_definition("orders")
    order_factory(name="Don cong ty")

    # Cố tình KHÔNG tạo UserChannel nào cho user này.
    empty_scope_user = make_scoped_user("ord-exp-scope-empty", "orders", scope=DataScope.CHANNEL)
    empty_params = definition.validate(
        empty_scope_user, {"section": "orders", "columns": [{"key": "name", "header": "Tên"}]}
    )
    assert _written_names(definition, empty_params) == set()

    # Đối chứng: CÙNG dữ liệu đó, user scope ALL (đầy đủ quyền) phải thấy được đơn —
    # chứng minh assert ở trên xanh vì SCOPE chặn, không phải vì DB rỗng/params sai.
    full_scope_user = make_scoped_user("ord-exp-scope-full", "orders", scope=DataScope.ALL)
    full_params = definition.validate(
        full_scope_user, {"section": "orders", "columns": [{"key": "name", "header": "Tên"}]}
    )
    assert _written_names(definition, full_params) == {"Don cong ty"}


def test_write_ignores_ids_when_channel_scope_has_no_assigned_channel(order_factory):
    """`build_export_orders_queryset` có HAI gate `scope_empty` (order_export_view.py):
    một trước `if ids:` (bảo vệ CẢ HAI nhánh), một trong nhánh filter (bảo vệ RIÊNG
    nhánh đó). Test `test_write_returns_nothing_when_channel_scope_has_no_assigned_
    channel` ở trên đi nhánh filter — nếu chỉ có nó thì gate filter luôn "cứu hộ" cho
    gate đầu (`_apply_user_channel_scope` trả `scope_empty` chỉ phụ thuộc
    `resolved_channel_codes(user, section)`, không phụ thuộc filters), nên xoá RIÊNG
    gate đầu mà không đụng gate filter thì test đó vẫn xanh — không phủ được nhánh
    `if ids:` vốn `return orders` thẳng, KHÔNG BAO GIỜ chạm gate filter.

    Test này đi ĐÚNG nhánh `ids` (bám khuôn `test_order_export.py::
    BulkExportExcelScopeTests.test_ids_export_respects_channel_scope`, nhưng CỐ TÌNH
    KHÔNG tạo `UserChannel` nào) để khoá riêng gate đầu: xoá nó đi thì
    `scope_filters.get("owner_user_id")`/`.get("channel")` đều falsy, `ids` branch
    filter KHÔNG áp gì cả → đơn theo `ids` bị trả nguyên, dữ liệu rò ra ngoài scope.
    """
    sync_permissions()
    definition = get_definition("orders")
    order = order_factory(name="Don theo id")

    empty_scope_user = make_scoped_user("ord-exp-scope-ids", "orders", scope=DataScope.CHANNEL)
    params = definition.validate(
        empty_scope_user,
        {"section": "orders", "columns": [{"key": "name", "header": "Tên"}], "ids": [order.id]},
    )
    assert _written_names(definition, params) == set()


# ─────────────────────── scope OWN/CHANNEL (nhánh ids, có dữ liệu) ─────────────
#
# Đối chứng DƯƠNG cho 2 test scope-rỗng ở trên: `_apply_user_channel_scope` phải
# thật sự LỌC đúng theo owner/kênh khi user CÓ được gán channel — không chỉ trả
# rỗng khi scope trống. Mirror `BulkExportExcelScopeTests.test_ids_export_
# respects_own_scope` / `.test_ids_export_respects_channel_scope`
# (test_order_export.py, đã xoá ở Task 18).


def test_write_own_scope_via_ids_only_sees_staffs_own_orders(order_factory):
    sync_permissions()
    definition = get_definition("orders")
    user = make_scoped_user("ord-exp-own-scope", "orders", scope=DataScope.OWN)
    order_mine = order_factory(name="Don cua toi")
    order_mine.staff = user
    order_mine.save(update_fields=["staff"])
    order_other = order_factory(name="Don nguoi khac")

    params = definition.validate(
        user,
        {
            "section": "orders",
            "columns": [{"key": "name", "header": "Tên"}],
            "ids": [order_mine.id, order_other.id],
        },
    )
    assert _written_names(definition, params) == {"Don cua toi"}


def test_write_channel_scope_via_ids_only_sees_assigned_channel(order_factory):
    from apps.channels_core.models import Channel, UserChannel

    sync_permissions()
    definition = get_definition("orders")
    user = make_scoped_user("ord-exp-channel-scope", "orders", scope=DataScope.CHANNEL)
    ch_assigned = Channel.objects.create(code="ord-exp-ch-a", name="Kênh A")
    ch_other = Channel.objects.create(code="ord-exp-ch-b", name="Kênh B")
    UserChannel.objects.create(user=user, channel=ch_assigned, is_active=True)
    from apps.users.rbac import bump_rbac_cache_version

    bump_rbac_cache_version()
    order_in_scope = order_factory(name="Don kenh A", channel=ch_assigned)
    order_out_of_scope = order_factory(name="Don kenh B", channel=ch_other)

    params = definition.validate(
        user,
        {
            "section": "orders",
            "columns": [{"key": "name", "header": "Tên"}],
            "ids": [order_in_scope.id, order_out_of_scope.id],
        },
    )
    assert _written_names(definition, params) == {"Don kenh A"}


@pytest.mark.django_db
class TestOrdersExportSectionParity:
    """`OrdersExport.write` (nhánh export-theo-filter) phải khớp CHÍNH XÁC tập đơn
    mà list endpoint của section đó trả về — bám khuôn
    `BulkExportExcelSectionFilterTests` (test_order_export.py) để không rò dữ liệu
    giữa các trang khi chuyển sang engine export chạy nền.
    """

    def setup_method(self):
        from apps.channels_core.models import Channel
        from apps.customers.models import Customer
        from apps.users.rbac_catalog import sync_permissions

        sync_permissions()
        self.channel = Channel.objects.create(code="parity-export", name="Parity Export")
        self.customer = Customer.objects.create(full_name="Khach Parity")

        def make_order(name, **extra):
            return Order.objects.create(
                channel=self.channel,
                customer=self.customer,
                name=name,
                phone="0909000111",
                address="1 Đường Test",
                total_amount="1.00",
                **extra,
            )

        self.orders = {
            "thuong": make_order("Don Thuong"),
            "koc": make_order("Don KOC", order_source="koc"),
            "koc_an": make_order("Don KOC An", order_source="koc", show_orders=False),
            "ngoai": make_order("Don Ngoai"),
            "landing": make_order("Don Landing", order_source="landing"),
            "messenger": make_order("Don Messenger", order_source="messenger"),
            "shopee": make_order("Don Shopee", order_source="shopee"),
        }
        self.orders["landing_thieu"] = Order.objects.create(
            channel=self.channel,
            customer=self.customer,
            name="Don Landing Thieu",
            phone="0909000111",
            total_amount="1.00",
            order_source="landing",
        )

    def _export(self, section, **extra_filters):
        definition = get_definition("orders")
        user = make_user_with_sections(f"parity-{section}", [section])
        params = definition.validate(
            user,
            {
                "section": section,
                "columns": [{"key": "name", "header": "Tên"}],
                "filters": extra_filters,
            },
        )
        definition.check_permission(user, params)
        return _written_names(definition, params)

    def test_koc_export_locked_to_koc_orders(self):
        assert self._export("koc-orders") == {"Don KOC", "Don KOC An"}

    def test_landing_export_excludes_koc_and_marketplace(self):
        assert self._export("landing-orders") == {
            "Don Landing", "Don Messenger", "Don Landing Thieu",
        }

    def test_landing_sheet1_requires_info_and_excludes_messenger(self):
        assert self._export("landing-orders", sheet="1") == {"Don Landing"}

    def test_landing_sheet2_only_messenger_with_info(self):
        assert self._export("landing-orders", sheet="2") == {"Don Messenger"}

    def test_orders_export_hides_show_orders_false(self):
        assert self._export("orders") == {
            "Don Thuong", "Don KOC", "Don Ngoai", "Don Landing",
            "Don Messenger", "Don Shopee", "Don Landing Thieu",
        }


# ─────────────────── recall columns (Mã đơn thu hồi / Phiếu trả hàng) rỗng ─────
#
# Bug người dùng báo: 4 cột "Mã đơn thu hồi"/"Ngày tạo đơn thu hồi"/"Ngày nhận
# hàng thu hồi"/"Phiếu trả hàng" luôn ra rỗng dù tick chọn. Cả 4 nằm trên
# `OrderRecall` (quan hệ 1-1 `related_name="recall"`), KHÔNG phải field của
# `Order` — khối if/elif cũ (`order_row_values`, export_excel.py) không có
# nhánh nào cho 4 key này nên rơi xuống `getattr(order, key, "")` trần, luôn
# trả rỗng. Lỗi có sẵn từ trước khi tách export sang chạy nền (Task 18), không
# phải hồi quy của đợt đó — bê nguyên khối if/elif cũ kèm cả lỗ hổng.
#
# Task 2: `recall_code`/`recall_created_date`/`recall_received_date` đã tách sang
# `OrderRecallEntry` (1-N, `related_name="recall_entries"`) — `return_slip_code`
# vẫn nằm trên `OrderRecall` (1-1, `related_name="recall"`).


def _recall_columns():
    return [
        {"key": "recall_code", "header": "Mã đơn thu hồi"},
        {"key": "recall_created_date", "header": "Ngày tạo đơn thu hồi"},
        {"key": "recall_received_date", "header": "Ngày nhận hàng thu hồi"},
        {"key": "return_slip_code", "header": "Phiếu trả hàng"},
    ]


def _write_all_rows(definition, params) -> list[dict]:
    """Ghi export ra danh sách dict {header: value} theo từng dòng."""
    writer = _RecordingWriter()
    definition.write(params, writer, lambda n: None)
    return [dict(zip(writer.headers, row)) for row in writer.rows]


def test_shipping_type_column_exports_vietnamese_labels(order_factory):
    """Minor (fix round 2): nhánh `elif key == "shipping_type"` (export_excel.py) chưa
    có test nào chạy qua trước đợt fix này — map mã sang nhãn tiếng Việt (`shipment`
    → "Giao vận", `direct` → "Bán trực tiếp") và rỗng khi cờ NULL (đơn cũ)."""
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-shipping-type", ["orders"])
    order_shipment = order_factory(name="Don Ship", shipping_type="shipment")
    order_direct = order_factory(name="Don Direct", shipping_type="direct")
    order_unknown = order_factory(name="Don Unknown")

    columns = [
        {"key": "name", "header": "Tên"},
        {"key": "shipping_type", "header": "Loại xuất hàng"},
    ]
    params = definition.validate(
        user,
        {
            "section": "orders",
            "columns": columns,
            "ids": [order_shipment.id, order_direct.id, order_unknown.id],
        },
    )
    rows = _write_all_rows(definition, params)
    assert {"Tên": "Don Ship", "Loại xuất hàng": "Giao vận"} in rows
    assert {"Tên": "Don Direct", "Loại xuất hàng": "Bán trực tiếp"} in rows
    assert {"Tên": "Don Unknown", "Loại xuất hàng": ""} in rows


def test_has_address_column_exports_vietnamese_labels(order_factory):
    """Cờ bất biến ``has_address`` xuất nhãn cho người đọc, không xuất True/False."""
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-has-address", ["orders"])
    order_with = order_factory(name="Don Co DC")
    order_without = order_factory(name="Don Khong DC", has_address=False)

    columns = [
        {"key": "name", "header": "Tên"},
        {"key": "has_address", "header": "Địa chỉ khi đổ đơn"},
    ]
    params = definition.validate(
        user,
        {"section": "orders", "columns": columns, "ids": [order_with.id, order_without.id]},
    )
    rows = _write_all_rows(definition, params)
    assert {"Tên": "Don Co DC", "Địa chỉ khi đổ đơn": "Có địa chỉ"} in rows
    assert {"Tên": "Don Khong DC", "Địa chỉ khi đổ đơn": "Không có địa chỉ"} in rows


def test_koc_order_type_column_exports_vietnamese_label(order_factory):
    """Cột xuất nhãn tiếng Việt, không xuất mã (khuôn giống test shipping_type
    ở trên — file này không có helper `order_row_values`, engine export ghi
    qua `definition.write` + `_write_all_rows`)."""
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-koc-order-type", ["orders"])
    order = order_factory(name="Don Gia Han", koc_order_type="renewal")

    columns = [
        {"key": "name", "header": "Tên"},
        {"key": "koc_order_type", "header": "Loại đơn KOC"},
    ]
    params = definition.validate(
        user, {"section": "orders", "columns": columns, "ids": [order.id]}
    )
    rows = _write_all_rows(definition, params)
    assert rows == [{"Tên": "Don Gia Han", "Loại đơn KOC": "Gia hạn"}]


def test_recall_columns_export_full_data_including_multiple_return_slips(order_factory):
    """Đơn có nhóm thu hồi (OrderRecallEntry) + return_slip_code (OrderRecall,
    ArrayField nhiều mã) — cả hai phải nối bằng ", " — mirror đúng `get_recall`/
    `get_recall_entries` (order_core_serializers.py)."""
    from apps.orders.models import OrderRecall, OrderRecallEntry

    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-recall-full", ["orders"])
    order = order_factory(name="Don co recall")
    OrderRecallEntry.objects.create(
        order=order,
        recall_code="RC-1",
        recall_created_date="2026-08-01",
        recall_received_date="2026-08-05",
    )
    OrderRecallEntry.objects.create(order=order, recall_code="RC-2")
    OrderRecall.objects.create(order=order, return_slip_code=["PT-01", "PT-02"])

    params = definition.validate(
        user, {"section": "orders", "columns": _recall_columns(), "ids": [order.id]}
    )
    rows = _write_all_rows(definition, params)
    assert rows == [
        {
            "Mã đơn thu hồi": "RC-1, RC-2",
            "Ngày tạo đơn thu hồi": "2026-08-01, ",
            "Ngày nhận hàng thu hồi": "2026-08-05, ",
            "Phiếu trả hàng": "PT-01, PT-02",
        }
    ]


def test_recall_columns_export_order_without_recall_are_blank_not_error(order_factory):
    """Đơn KHÔNG có nhóm thu hồi lẫn bản ghi `OrderRecall` -> cả 4 cột rỗng, không
    ném RelatedObjectDoesNotExist (truy cập `order.recall` trực tiếp sẽ ném lỗi
    này vì quan hệ 1-1 optional)."""
    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-recall-none", ["orders"])
    order = order_factory(name="Don khong recall")

    params = definition.validate(
        user, {"section": "orders", "columns": _recall_columns(), "ids": [order.id]}
    )
    rows = _write_all_rows(definition, params)
    assert rows == [
        {
            "Mã đơn thu hồi": "",
            "Ngày tạo đơn thu hồi": "",
            "Ngày nhận hàng thu hồi": "",
            "Phiếu trả hàng": "",
        }
    ]


def test_recall_columns_export_null_dates_are_blank(order_factory):
    """`recall_created_date`/`recall_received_date` là DateField null=True — có
    nhóm thu hồi nhưng chưa nhập ngày phải ra rỗng, không phải "None"."""
    from apps.orders.models import OrderRecallEntry

    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-recall-null-dates", ["orders"])
    order = order_factory(name="Don recall thieu ngay")
    OrderRecallEntry.objects.create(order=order, recall_code="RC-002")

    params = definition.validate(
        user, {"section": "orders", "columns": _recall_columns(), "ids": [order.id]}
    )
    rows = _write_all_rows(definition, params)
    assert rows == [
        {
            "Mã đơn thu hồi": "RC-002",
            "Ngày tạo đơn thu hồi": "",
            "Ngày nhận hàng thu hồi": "",
            "Phiếu trả hàng": "",
        }
    ]


def test_recall_columns_keep_positional_alignment(order_factory):
    """Nhóm 2 chưa có ngày nhận → ô ngày nhận là "2026-08-15, " chứ không dồn lại
    (mirror `order_row_values` — file này ghi qua engine export/`_write_all_rows`,
    không gọi `order_row_values` trực tiếp)."""
    from apps.orders.models import OrderRecallEntry

    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-recall-align", ["orders"])
    order = order_factory(name="Don recall lech vi tri")
    OrderRecallEntry.objects.create(
        order=order,
        recall_code="RC-1",
        recall_created_date="2026-08-12",
        recall_received_date="2026-08-15",
    )
    OrderRecallEntry.objects.create(order=order, recall_code="RC-2", recall_created_date="2026-08-20")

    columns = [
        {"key": "recall_code", "header": "Mã đơn thu hồi"},
        {"key": "recall_received_date", "header": "Ngày nhận hàng thu hồi"},
    ]
    params = definition.validate(
        user, {"section": "orders", "columns": columns, "ids": [order.id]}
    )
    rows = _write_all_rows(definition, params)
    assert rows == [
        {"Mã đơn thu hồi": "RC-1, RC-2", "Ngày nhận hàng thu hồi": "2026-08-15, "}
    ]


def test_recall_columns_export_does_not_n_plus_one(order_factory, django_assert_num_queries):
    """Chống N+1 ở quy mô export (tới 50k đơn): `build_export_orders_queryset`
    (repositories.py, nhánh `ids`) phải prefetch_related("recall_entries") — nếu
    không, `order_row_values` truy cập `order.recall_entries.all()` lazy-load 1
    query RIÊNG mỗi dòng.

    Đột biến kiểm chứng ĐO THỰC TẾ (dán trong report): bỏ "recall_entries" khỏi
    prefetch_related ở repositories.py -> 32 query (không phải +8 đơn giản: mỗi
    trong 3 cột recall_code/recall_created_date/recall_received_date gọi lại
    `order.recall_entries.all()` RIÊNG — không có prefetch cache thì mỗi lần gọi
    là 1 query mới, 3 cột × 8 đơn = 24 query rời cộng dồn); khôi phục ->
    XANH đúng 9, số query không đổi dù N tăng.
    """
    from apps.orders.models import OrderRecallEntry

    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    definition = get_definition("orders")
    user = make_user_with_sections("ord-exp-recall-n1", ["orders"])
    orders = [order_factory(name=f"Don recall {i}") for i in range(8)]
    for i, order in enumerate(orders):
        OrderRecallEntry.objects.create(order=order, recall_code=f"RC-{i:03d}")

    def _params(subset):
        return definition.validate(
            user,
            {
                "section": "orders",
                "columns": _recall_columns(),
                "ids": [o.id for o in subset],
            },
        )

    # ĐÍNH CHÍNH 2026-08-20 (audit P2.7): bản cũ assert SỐ CỨNG 9 và đếm luôn cả
    # 6 query hằng số của RBAC resolver — commit 9ba895d5 thêm 1 query
    # RoleFieldGrant vào resolver là test đỏ oan (9 → 10) dù export KHÔNG hề tái
    # phát N+1. Bump 9→10 chỉ dời bẫy sang lần sau. Chuyển sang SO SÁNH HAI CỠ LÔ:
    # 3 đơn vs 8 đơn phải ra CÙNG số query — bất biến với mọi chi phí hằng số
    # per-request, nhưng vẫn bắt đúng đột biến: bỏ "recall_entries" khỏi
    # prefetch_related (repositories.py) → lô 8 nhiều hơn lô 3 đúng 15 query
    # (3 cột recall × 5 đơn chênh) → test ĐỎ.
    # Warm-up: lần chạy ĐẦU trả giá cold-cache của RBAC resolver (7 query hằng
    # số, cache Redis theo user) — chạy nháp một lượt để cả hai lần đo dưới đây
    # cùng ở trạng thái cache ấm, phép so sánh chỉ còn phần phụ thuộc N.
    _write_all_rows(definition, _params(orders[:3]))

    with CaptureQueriesContext(connection) as small:
        rows_small = _write_all_rows(definition, _params(orders[:3]))
    with CaptureQueriesContext(connection) as large:
        rows = _write_all_rows(definition, _params(orders))

    assert len(large.captured_queries) == len(small.captured_queries), (
        f"query theo N: lô 3 = {len(small.captured_queries)}, "
        f"lô 8 = {len(large.captured_queries)}"
    )
    assert {row["Mã đơn thu hồi"] for row in rows_small} == {f"RC-{i:03d}" for i in range(3)}
    assert {row["Mã đơn thu hồi"] for row in rows} == {f"RC-{i:03d}" for i in range(8)}
