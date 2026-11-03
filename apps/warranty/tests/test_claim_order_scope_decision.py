"""Khoá QUYẾT ĐỊNH: phiếu bảo hành KHÔNG record-scope theo sale sở hữu đơn.

Bối cảnh (audit 2026-07-26). Đợt vá trước siết record-scope cho họ endpoint nhận
`order_id`/`ids` từ client ở app `orders` (`scoped_order_or_error` /
`scoped_orders_queryset`). App `warranty` nằm trong danh sách "nghi vấn" vì:

- `POST /api/warranty/claims {order_id}` (`list_create_warranty_claims`) chỉ gate
  `require_section("warranty")` rồi `WarrantyClaimService.create_claim` gọi
  `Order.objects.filter(id=order_id).first()` — không lọc theo scope bản ghi.
- Response 201 nest `WarrantyClaimOrderSerializer` → trả PII của đơn: `name`,
  `phone`, `address`, `product`, `total_after_discount`, `shipping_code`,
  `invoice_code`.
- Các endpoint còn lại (`detail`, `complete`, `bulk-delete`, `export`, journeys)
  khoá theo `claim_id`/`journey_id`, cũng không lọc theo người sở hữu đơn.

KẾT LUẬN SAU KHI XÁC MINH: **KHÔNG siết** — đây là hành vi nghiệp vụ ĐÚNG, không
phải lỗ hổng. Lập luận:

1. Khách mang máy tới bảo hành có thể đã mua từ BẤT KỲ sale nào. NV bảo hành gần
   như không bao giờ là `Order.staff` của đơn đó. Mà `Role.scope_default` mặc
   định là `DataScope.OWN` (apps/users/models.py), nên nếu áp
   `order_record_scope_q(user, ("warranty",))` thì Q sinh ra là
   `Q(staff_id=<nv bảo hành>)` → NV bảo hành KHÔNG tạo/tra được phiếu nào.
   `test_own_scope_would_match_nothing_if_record_scope_were_applied` chứng minh
   điều này bằng code, để lần audit sau không phải suy đoán.
2. `_SECTION_ORDER_IDENTITY_Q["warranty"]` đã được khai TƯỜNG MINH là `Q()` (tập
   cha) trong `apps/orders/transport/views/order_views_impl/_view_helpers.py` với
   đúng lý do trên — quyết định ở đây phải nhất quán với khai báo đó.
3. Rào chắn thật của luồng bảo hành là PERMISSION, không phải record-scope: chỉ
   role có `warranty:view` mới vào được endpoint. Sale (chỉ có section `orders`,
   kể cả scope ALL) bị 403 — `test_user_without_warranty_section_is_forbidden`.

VIỆC CÒN LẠI (khuyến nghị, KHÔNG làm trong đợt này vì rủi ro khoá nhầm role thật ở
prod): `POST /claims` đang gate bằng `warranty:view` — một quyền ĐỌC — trong khi nó
GHI. Nên tách sang `warranty:create`. Cần rà cấu hình role thật trước khi đổi.

ĐỪNG XOÁ FILE NÀY để "dọn test": nó tồn tại để đợt audit sau đọc được kết luận
kèm bằng chứng thay vì điều tra lại từ đầu. Nếu nghiệp vụ đổi (vd bảo hành chia
theo phòng ban), sửa test theo hành vi mới — nhưng phải sửa có chủ đích.
"""

import pytest
from django.db.models import Q
from rest_framework.test import APIClient

from apps.orders.transport.views.order_views_impl._view_helpers import (
    _SECTION_ORDER_IDENTITY_Q,
    order_record_scope_q,
)
from apps.users.models import DataScope
from apps.users.tests.helpers import make_user_with_sections
from apps.warranty.models import WarrantyClaim

# PII mà `WarrantyClaimOrderSerializer` trả về — chính là thứ khiến endpoint này bị
# đưa vào diện nghi vấn. Khoá tường minh để nếu ai đó thêm field nhạy cảm mới vào
# serializer thì phải đi qua đây và cân nhắc lại quyết định "không siết".
_ORDER_PII_FIELDS = (
    "name",
    "phone",
    "address",
    "product",
    "total_after_discount",
    "shipping_code",
    "invoice_code",
)


@pytest.fixture
def sale_user(db):
    """Sale sở hữu đơn (`Order.staff`) — KHÔNG có section warranty."""
    return make_user_with_sections("wty-scope-sale", ["orders"], scope=DataScope.ALL)


@pytest.fixture
def warranty_own_user(db):
    """NV bảo hành với scope hẹp nhất có thể: `own`.

    Đây đúng là cấu hình mặc định của một role mới (`Role.scope_default = own`),
    tức là ca xấu nhất cho lập luận "siết theo owner sẽ phá nghiệp vụ".
    """
    return make_user_with_sections("wty-scope-staff", ["warranty"], scope=DataScope.OWN)


def _other_sale_order(order_factory, sale_user):
    return order_factory(
        staff=sale_user,
        name="Khach Bao Hanh",
        phone="0912345678",
        address="12 Nguyen Trai, Ha Noi",
        product="May loc nuoc X1",
        shipping_code="SPX-WTY-001",
        invoice_code="HD-WTY-001",
        total_after_discount="1500000.00",
    )


@pytest.mark.django_db
def test_own_scope_warranty_user_creates_claim_for_other_sales_order(
    order_factory, sale_user, warranty_own_user
):
    """NGHIỆP VỤ BẮT BUỘC: NV bảo hành tạo được phiếu cho đơn của sale khác.

    Đây vừa là PoC "endpoint không record-scope" vừa là test KHOÁ quyết định giữ
    nguyên hành vi. Nếu ai đó thêm `scoped_order_or_error` vào view này, test đỏ
    và buộc phải đọc docstring module trước khi siết.
    """
    order = _other_sale_order(order_factory, sale_user)
    client = APIClient()
    client.force_authenticate(warranty_own_user)

    resp = client.post("/api/warranty/claims", {"order_id": order.id}, format="json")

    assert resp.status_code == 201, resp.data
    assert resp.data["order"]["id"] == order.id
    # PII của đơn sale khác ĐƯỢC trả về — có chủ đích: NV bảo hành cần tên/điện
    # thoại/địa chỉ để liên hệ và gửi trả máy.
    for field in _ORDER_PII_FIELDS:
        assert field in resp.data["order"], field
    assert resp.data["order"]["phone"] == order.phone
    assert WarrantyClaim.objects.filter(order=order, deleted_at__isnull=True).exists()


@pytest.mark.django_db
def test_own_scope_warranty_user_reads_and_completes_claim_of_other_sales_order(
    order_factory, sale_user, warranty_own_user
):
    """Detail + complete cũng cross-sale: cùng lý do, khoá luôn để khỏi vá lẻ tẻ."""
    order = _other_sale_order(order_factory, sale_user)
    claim = WarrantyClaim.objects.create(order=order)
    client = APIClient()
    client.force_authenticate(warranty_own_user)

    detail = client.get(f"/api/warranty/claims/{claim.id}")
    assert detail.status_code == 200, detail.data
    assert detail.data["order"]["id"] == order.id

    completed = client.post(f"/api/warranty/claims/{claim.id}/complete", {}, format="json")
    assert completed.status_code == 200, completed.data


@pytest.mark.django_db
def test_claim_list_returns_claims_of_other_sales_orders(
    order_factory, sale_user, warranty_own_user
):
    """Danh sách phiếu KHÔNG lọc theo sale — hàng đợi bảo hành là hàng đợi CHUNG."""
    order = _other_sale_order(order_factory, sale_user)
    WarrantyClaim.objects.create(order=order)
    client = APIClient()
    client.force_authenticate(warranty_own_user)

    resp = client.get("/api/warranty/claims?limit=20&offset=0")

    assert resp.status_code == 200, resp.data
    assert [row["order"]["id"] for row in resp.data["results"]] == [order.id]


@pytest.mark.django_db
def test_user_without_warranty_section_is_forbidden(order_factory, sale_user):
    """Rào chắn THẬT của luồng bảo hành: permission section, không phải record-scope.

    Sale scope ALL (thấy mọi đơn ở trang Đơn hàng) vẫn KHÔNG được chạm endpoint
    bảo hành. Nếu ai đó nới `require_section("warranty")` thành `ORDER_SECTIONS`
    thì cả công ty đọc được PII đơn qua đường này — test đỏ ngay.
    """
    order = _other_sale_order(order_factory, sale_user)
    client = APIClient()
    client.force_authenticate(sale_user)

    assert client.post(
        "/api/warranty/claims", {"order_id": order.id}, format="json"
    ).status_code == 403
    assert client.get("/api/warranty/claims?limit=20&offset=0").status_code == 403


@pytest.mark.django_db
def test_warranty_section_is_declared_as_parent_set_in_identity_map():
    """`warranty` phải là tập CHA trong `_SECTION_ORDER_IDENTITY_Q`.

    Map là fail-closed: section chưa khai sẽ raise. Khai `Q()` (tập cha) là quyết
    định có chủ đích — `customer_profile_view` và `order_search_view` dựa vào đó để
    luồng bảo hành tra được đơn của mọi sale.
    """
    assert _SECTION_ORDER_IDENTITY_Q["warranty"] == Q()


@pytest.mark.django_db
def test_own_scope_would_match_nothing_if_record_scope_were_applied(
    order_factory, sale_user, warranty_own_user
):
    """BẰNG CHỨNG cho quyết định "không siết": siết sẽ phá nghiệp vụ.

    Với NV bảo hành scope `own`, `order_record_scope_q` trả `Q(staff_id=<nv>)`.
    Áp Q đó vào endpoint tạo phiếu → 0 đơn khớp → NV bảo hành không tạo được phiếu
    nào cho khách, vì họ không bao giờ là `Order.staff`.
    """
    from apps.orders.models import Order

    order = _other_sale_order(order_factory, sale_user)
    scope_q = order_record_scope_q(warranty_own_user, ("warranty",))

    assert scope_q == Q(staff_id=warranty_own_user.id)
    assert not Order.objects.filter(scope_q).filter(id=order.id).exists()
