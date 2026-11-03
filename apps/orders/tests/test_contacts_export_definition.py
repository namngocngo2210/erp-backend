"""Test `ContactsExport` — definition đăng ký kind "contacts" vào engine apps.exports.

Trọng tâm là `TestContactsExportParity`: với CÙNG bộ lọc, tập khách mà file Excel sinh
ra phải KHỚP CHÍNH XÁC tập mà list endpoint trả về. Trang contact có 11 trục lọc, trong
đó 4 trục (interaction/customer_type/action_needed/operation_bucket) lọc trong Python
sau khi query — đúng chỗ dễ sinh ra "đường lọc thứ hai" lệch màn hình.
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.channels_core.models import Channel
from apps.customers.models import ContactActivity, Customer
from apps.exports.application.registry import get_definition
from apps.orders.models import Order, OrderAssessment, OrderItem, OrderSource, OrderStatus
from apps.catalog.models import Product
from apps.users.models import DataScope
from apps.users.phone_visibility import CUSTOMER_PHONE_PERMISSION
from apps.users.tests.helpers import make_user_with_permissions, make_user_with_sections

pytestmark = pytest.mark.django_db


class _RecordingWriter:
    def __init__(self):
        self.headers = []
        self.rows = []

    def write_header(self, headers):
        self.headers = list(headers)

    def write_row(self, values):
        self.rows.append(list(values))


def _write(definition, params) -> _RecordingWriter:
    writer = _RecordingWriter()
    definition.write(params, writer, lambda n: None)
    return writer


def _rows_as_dicts(writer: _RecordingWriter) -> list[dict]:
    return [dict(zip(writer.headers, row)) for row in writer.rows]


def _customer(name: str, phone: str, **kwargs) -> Customer:
    return Customer.objects.create(
        full_name=name, phone_raw=phone, phone_normalized=phone, **kwargs
    )


def _order(channel, customer, **kwargs) -> Order:
    created_at = kwargs.pop("created_at", None)
    sales_status_code = kwargs.pop("sales_status_code", None)
    order = Order.objects.create(
        channel=channel,
        customer=customer,
        order_source=kwargs.pop("order_source", OrderSource.LANDING),
        status=kwargs.pop("status", OrderStatus.NEW),
        **kwargs,
    )
    if created_at is not None:
        Order.objects.filter(pk=order.pk).update(created_at=created_at)
        order.refresh_from_db()
    if sales_status_code is not None:
        OrderAssessment.objects.create(order=order, sales_status_code=sales_status_code)
    return order


@pytest.fixture
def channel():
    return Channel.objects.create(code="contacts-export-ch", name="Kênh Contact")


@pytest.fixture
def exporter():
    """User có ĐỦ contacts:view + contacts:export, scope ALL.

    Kèm `customer-phone:view` để cột SĐT trong file là số THẬT — đó là cấu hình của
    role CSKH sau migration users/0050 (grandfather). Phần che SĐT có test riêng ngay
    bên dưới (`test_write_masks_phone_without_customer_phone_permission`).
    """
    from apps.users.models import Permission

    codes = list(
        Permission.objects.filter(section="contacts").values_list("code", flat=True)
    )
    return make_user_with_permissions(
        "contacts-export-user", [*codes, CUSTOMER_PHONE_PERMISSION]
    )


# ───────────────────────────────── registry + validate ────────────────────────


def test_validate_maps_list_endpoint_param_names_to_service_kwargs(exporter):
    """Frontend gửi thẳng bộ lọc của list endpoint (channel/order_source/staff/product);
    definition dịch sang kwargs của service. Sai chỗ này = file xuất bỏ qua bộ lọc."""
    definition = get_definition("contacts")
    params = definition.validate(
        exporter,
        {
            "filters": {
                "date_from": "2026-08-01",
                "date_to": "2026-08-19",
                "q": "Lan",
                "channel": [3, 4],
                "order_source": ["landing"],
                "staff": [7],
                "product": [11],
                "action_needed": ["call_2"],
                "interaction": "none",
                "customer_type": "new",
                "operation_bucket": "overdue",
            }
        },
    )
    assert params["user_id"] == exporter.id
    assert params["filters"] == {
        "date_from": "2026-08-01",
        "date_to": "2026-08-19",
        "q": "Lan",
        "channel_ids": [3, 4],
        "order_sources": ["landing"],
        "staff_ids": [7],
        "product_ids": [11],
        "action_needed": ["call_2"],
        "interaction": "none",
        "customer_type": "new",
        "operation_bucket": "overdue",
        "customer_ids": [],
    }


def test_validate_accepts_empty_filters(exporter):
    """Không có bộ lọc nào = xuất theo cửa sổ ngày mặc định của service, không lỗi."""
    definition = get_definition("contacts")
    params = definition.validate(exporter, {})
    assert params["filters"]["date_from"] is None
    assert params["filters"]["channel_ids"] == []


@pytest.mark.parametrize(
    "bad_filters",
    [
        {"date_from": 20260101},
        {"date_from": "01/08/2026"},
        {"date_to": ["2026-08-01"]},
        {"channel": "1,2"},
        {"channel": ["abc"]},
        {"order_source": [123]},
        {"staff": {"id": 1}},
        {"product": ["x"]},
        {"q": 123},
        {"action_needed": ["khong_co_action_nay"]},
        {"interaction": "linh tinh"},
        {"customer_type": 5},
        {"operation_bucket": "khong_co_bucket"},
    ],
)
def test_validate_rejects_bad_input_with_400_not_500(exporter, bad_filters):
    """Mọi kiểu sai của client phải thành ValidationError (400 ở tầng views của
    apps.exports), KHÔNG được để TypeError/ValueError trần lọt thành 500 — đúng bài
    học đã cắn ở `OrdersExport` (ids/filters/columns/return_date sai kiểu)."""
    definition = get_definition("contacts")
    with pytest.raises(ValidationError):
        definition.validate(exporter, {"filters": bad_filters})


def test_validate_rejects_non_dict_filters(exporter):
    definition = get_definition("contacts")
    with pytest.raises(ValidationError):
        definition.validate(exporter, {"filters": "abc"})


# ───────────────────────────────── quyền ──────────────────────────────────────


# ───────────────────────────────── trần dòng ──────────────────────────────────


def test_estimate_raises_when_over_max_rows(channel, exporter, monkeypatch):
    from apps.orders import exports_definitions

    definition = get_definition("contacts")
    for i in range(3):
        _order(channel, _customer(f"KH {i}", f"090000000{i}"))

    monkeypatch.setattr(exports_definitions, "CONTACT_EXPORT_MAX_ROWS", 2)
    params = definition.validate(exporter, {})
    with pytest.raises(ValidationError):
        definition.estimate(params)


def test_estimate_returns_matched_count(channel, exporter):
    definition = get_definition("contacts")
    for i in range(3):
        _order(channel, _customer(f"KH dem {i}", f"091000000{i}"))

    params = definition.validate(exporter, {})
    assert definition.estimate(params) == 3


# ───────────────────────────────── nội dung file ──────────────────────────────


def test_write_emits_headers_and_mirrors_screen_values(channel, exporter):
    from apps.orders.contact_export_excel import CONTACT_EXPORT_HEADERS

    definition = get_definition("contacts")
    customer = _customer("Nguyễn Thị Lan", "0912345678", is_new_customer=False)
    order = _order(
        channel,
        customer,
        created_at=timezone.now() - timedelta(days=2),
        sales_status_code="no_answer_1",
        total_after_discount="1500000",
        address="Số 1 Ngõ 2, Phường Yên Hòa, Quận Cầu Giấy, Thành phố Hà Nội",
    )
    # `is_new_customer` bị đặt lại lúc tạo đơn, nên ép sau khi có đơn mới ăn.
    Customer.objects.filter(pk=customer.pk).update(is_new_customer=False)
    product_a = Product.objects.create(sku="SKU-A", name="Đai lưng")
    product_b = Product.objects.create(sku="SKU-B", name="Gối")
    OrderItem.objects.create(
        order=order, product=product_a, sku="SKU-A", product_name="Đai lưng", quantity=2
    )
    OrderItem.objects.create(
        order=order, product=product_b, sku="SKU-B", product_name="Gối", quantity=1
    )

    params = definition.validate(exporter, {})
    writer = _write(definition, params)

    assert writer.headers == list(CONTACT_EXPORT_HEADERS)
    row = _rows_as_dicts(writer)[0]
    assert row["Khách hàng"] == "Nguyễn Thị Lan"
    assert row["SĐT"] == "0912345678"
    assert row["Địa chỉ"] == "Số 1 Ngõ 2, Phường Yên Hòa, Quận Cầu Giấy, Thành phố Hà Nội"
    assert row["Nguồn khách"] == "Kênh Contact"
    assert row["Loại khách"] == "Khách cũ"
    # `no_answer_1` → tác nghiệp cần "Gọi lần 2" (derive_action_needed + ACTION_LABELS).
    assert row["Tác nghiệp cần"] == "Gọi lần 2"
    # Cột SKU đổ ĐỦ dòng hàng (màn hình chỉ hiện dòng đầu + badge "+n").
    assert row["SKU"] == "SKU-A ×2, SKU-B ×1"
    assert row["Thời gian chờ"] == "2d"
    assert row["SL đơn"] == 1


def test_write_masks_phone_without_customer_phone_permission(channel):
    """Thiếu `customer-phone:view` → cột SĐT trong file Excel cũng bị che.

    File Excel mang dữ liệu ra khỏi hệ thống: che trên màn hình mà để ngỏ nút Xuất là
    vô hiệu hoá cả lớp che (xem docs/customer-phone-masking.md).
    """
    definition = get_definition("contacts")
    user = make_user_with_sections("contacts-export-masked", ["contacts"])
    _order(channel, _customer("KH bi che", "0912345678"))

    params = definition.validate(user, {})
    row = _rows_as_dicts(_write(definition, params))[0]

    assert row["SĐT"] == "091****678"


def test_write_emits_amount_as_number_not_text(channel, exporter):
    """Cột Giá đơn phải là SỐ để Excel cộng/lọc được — không phải chuỗi đã format."""
    definition = get_definition("contacts")
    _order(channel, _customer("KH tien", "0913333333"), total_after_discount="1500000")

    params = definition.validate(exporter, {})
    row = _rows_as_dicts(_write(definition, params))[0]
    assert isinstance(row["Giá đơn"], float)
    assert row["Giá đơn"] == 1500000.0


def test_write_leaves_operation_blank_when_customer_not_in_flow(channel, exporter):
    definition = get_definition("contacts")
    _order(channel, _customer("KH chua vao flow", "0914444444"))

    params = definition.validate(exporter, {})
    row = _rows_as_dicts(_write(definition, params))[0]
    assert row["Tác nghiệp"] == ""


def test_write_falls_back_to_order_source_label_when_channel_has_no_name(exporter):
    """Kênh chưa đặt tên → cột Nguồn khách hiện nhãn nguồn đơn (mirror `sourceLabel`).

    `Order.channel` là NOT NULL nên không dựng được đơn "không kênh"; nhánh fallback
    chỉ vào được khi `channel.name` rỗng — đúng dữ liệu thật của kênh mới tạo chưa
    điền tên.
    """
    definition = get_definition("contacts")
    blank_channel = Channel.objects.create(code="contacts-export-blank", name="")
    _order(
        blank_channel,
        _customer("KH khong kenh", "0915555555"),
        order_source=OrderSource.MESSENGER,
    )

    params = definition.validate(exporter, {})
    row = _rows_as_dicts(_write(definition, params))[0]
    assert row["Nguồn khách"] == "Messenger"


# ───────────────────────────────── scope bản ghi ──────────────────────────────


def test_write_respects_own_record_scope(channel):
    """Scope OWN: file chỉ có khách của chính người xuất — cùng luật với màn hình.

    Đối chứng dương ở cuối: CÙNG dữ liệu đó, user scope ALL thấy cả 2 khách, chứng
    minh assert đầu xanh vì SCOPE chặn chứ không phải vì DB rỗng.
    """
    definition = get_definition("contacts")
    owner = make_user_with_sections("contacts-export-own", ["contacts"], scope=DataScope.OWN)
    _order(channel, _customer("KH cua toi", "0916666666"), staff=owner)
    _order(channel, _customer("KH nguoi khac", "0917777777"))

    params = definition.validate(owner, {})
    rows = _rows_as_dicts(_write(definition, params))
    assert {row["Khách hàng"] for row in rows} == {"KH cua toi"}

    everyone = make_user_with_sections("contacts-export-all", ["contacts"])
    all_rows = _rows_as_dicts(_write(definition, definition.validate(everyone, {})))
    assert {row["Khách hàng"] for row in all_rows} == {"KH cua toi", "KH nguoi khac"}


# ───────────────────────────────── parity với màn hình ────────────────────────


class TestContactsExportParity:
    """Tập khách trong file phải KHỚP CHÍNH XÁC tập list endpoint trả về."""

    @pytest.fixture(autouse=True)
    def _seed(self, channel, exporter):
        self.channel = channel
        self.user = exporter
        now = timezone.now()

        self.new_customer = _customer("KH Moi", "0921111111", is_new_customer=True)
        _order(channel, self.new_customer, created_at=now - timedelta(days=1))

        self.old_customer = _customer("KH Cu", "0922222222", is_new_customer=False)
        old_order = _order(
            channel,
            self.old_customer,
            created_at=now - timedelta(days=2),
            sales_status_code="no_answer_1",
        )
        # Đã tương tác sau khi data về → rơi vào nhóm interaction="recent".
        activity = ContactActivity.objects.create(
            customer=self.old_customer, kind="note", note="da goi"
        )
        ContactActivity.objects.filter(pk=activity.pk).update(
            created_at=old_order.created_at + timedelta(hours=1)
        )

        self.messenger_customer = _customer("KH Messenger", "0923333333", is_new_customer=False)
        _order(
            channel,
            self.messenger_customer,
            created_at=now - timedelta(days=3),
            order_source=OrderSource.MESSENGER,
        )

        self.delivered_customer = _customer("KH Da Giao", "0924444444", is_new_customer=False)
        _order(
            channel,
            self.delivered_customer,
            created_at=now - timedelta(days=4),
            status=OrderStatus.DELIVERED_SUCCESS,
        )

    def _screen_names(self, **filters) -> set[str]:
        from apps.orders.application.services.contact_list_service import ContactListService

        result = ContactListService.list_contacts(user=self.user, limit=100, offset=0, **filters)
        assert result["total"] == len(result["items"]), "tập test phải gọn trong 1 trang"
        return {item["full_name"] for item in result["items"]}

    def _exported_names(self, **raw_filters) -> set[str]:
        definition = get_definition("contacts")
        params = definition.validate(self.user, {"filters": raw_filters})
        definition.check_permission(self.user, params)
        rows = _rows_as_dicts(_write(definition, params))
        return {row["Khách hàng"] for row in rows}

    @pytest.mark.parametrize(
        "screen_filters,export_filters",
        [
            ({}, {}),
            ({"customer_type": "new"}, {"customer_type": "new"}),
            ({"customer_type": "old"}, {"customer_type": "old"}),
            ({"interaction": "none"}, {"interaction": "none"}),
            ({"interaction": "recent"}, {"interaction": "recent"}),
            ({"action_needed": ["post_sale"]}, {"action_needed": ["post_sale"]}),
            ({"action_needed": ["call_2"]}, {"action_needed": ["call_2"]}),
            (
                {"order_sources": [OrderSource.MESSENGER]},
                {"order_source": [OrderSource.MESSENGER]},
            ),
            ({"operation_bucket": "none"}, {"operation_bucket": "none"}),
            ({"q": "Messenger"}, {"q": "Messenger"}),
        ],
    )
    def test_export_matches_list_endpoint(self, screen_filters, export_filters):
        screen = self._screen_names(**screen_filters)
        assert screen, "bộ lọc phải khớp ít nhất 1 khách, nếu không test luôn xanh vô nghĩa"
        assert self._exported_names(**export_filters) == screen

    def test_export_ignores_pagination_and_returns_every_matching_row(self):
        """Người dùng đang xem trang 1 (20 dòng) vẫn phải xuất ĐỦ tập khớp bộ lọc."""
        assert self._exported_names() == self._screen_names()
        assert len(self._exported_names()) == 4


# ─────────────────── customer_ids: xuất đúng các dòng người dùng tích ──────────
#
# `customer_ids` là MỘT TRỤC LỌC NỮA (giao với `filters`), không phải đường lấy dữ
# liệu riêng — nhờ vậy record-scope RBAC vẫn áp nguyên. Test cuối khoá đúng điểm đó.


def test_validate_accepts_customer_ids(exporter):
    definition = get_definition("contacts")
    params = definition.validate(exporter, {"filters": {"customer_ids": [3, 9]}})
    assert params["filters"]["customer_ids"] == [3, 9]


def test_validate_defaults_customer_ids_to_empty(exporter):
    """Không tích dòng nào → xuất theo bộ lọc, y như trước khi có tính năng chọn dòng."""
    definition = get_definition("contacts")
    params = definition.validate(exporter, {})
    assert params["filters"]["customer_ids"] == []


@pytest.mark.parametrize("bad", [{"customer_ids": "1,2"}, {"customer_ids": ["abc"]}])
def test_validate_rejects_bad_customer_ids(exporter, bad):
    definition = get_definition("contacts")
    with pytest.raises(ValidationError):
        definition.validate(exporter, {"filters": bad})


def test_write_only_exports_selected_customers(channel, exporter):
    definition = get_definition("contacts")
    picked = _customer("KH duoc tich", "0931111111")
    _order(channel, picked)
    _order(channel, _customer("KH khong tich", "0932222222"))

    params = definition.validate(exporter, {"filters": {"customer_ids": [picked.id]}})
    rows = _rows_as_dicts(_write(definition, params))
    assert {row["Khách hàng"] for row in rows} == {"KH duoc tich"}


def test_estimate_counts_only_selected_customers(channel, exporter):
    definition = get_definition("contacts")
    picked = _customer("KH dem 1", "0933333333")
    _order(channel, picked)
    _order(channel, _customer("KH dem 2", "0934444444"))

    params = definition.validate(exporter, {"filters": {"customer_ids": [picked.id]}})
    assert definition.estimate(params) == 1


def test_selected_customer_outside_record_scope_is_dropped(channel):
    """Người dùng scope OWN gửi lên id của khách KHÔNG thuộc mình (sửa payload, hoặc
    tích trước khi bị đổi phân công) → khách đó KHÔNG được lọt vào file.

    Đối chứng dương: khách của chính họ trong cùng request vẫn xuất bình thường, nên
    assert không xanh nhờ file rỗng.
    """
    definition = get_definition("contacts")
    owner = make_user_with_sections("contacts-export-ids-own", ["contacts"], scope=DataScope.OWN)
    mine = _customer("KH cua toi", "0935555555")
    _order(channel, mine, staff=owner)
    others = _customer("KH nguoi khac", "0936666666")
    _order(channel, others)

    params = definition.validate(
        owner, {"filters": {"customer_ids": [mine.id, others.id]}}
    )
    rows = _rows_as_dicts(_write(definition, params))
    assert {row["Khách hàng"] for row in rows} == {"KH cua toi"}


def test_customer_ids_intersect_with_filters_not_replace_them(channel, exporter):
    """Tích 2 khách nhưng bộ lọc chỉ khớp 1 → file chỉ có 1. `customer_ids` KHÔNG được
    phép nới bộ lọc, nếu không người dùng thấy file chứa dòng ngoài tập đang xem."""
    definition = get_definition("contacts")
    landing = _customer("KH landing", "0937777777")
    _order(channel, landing, order_source=OrderSource.LANDING)
    messenger = _customer("KH messenger", "0938888888")
    _order(channel, messenger, order_source=OrderSource.MESSENGER)

    params = definition.validate(
        exporter,
        {
            "filters": {
                "customer_ids": [landing.id, messenger.id],
                "order_source": [OrderSource.MESSENGER],
            }
        },
    )
    rows = _rows_as_dicts(_write(definition, params))
    assert {row["Khách hàng"] for row in rows} == {"KH messenger"}
