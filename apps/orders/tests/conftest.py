import pytest


@pytest.fixture
def sale_role():
    """Role gán cho `sale_client`: scope ALL, mọi permission section "orders", nhưng
    KHÔNG grant field nào (`RoleFieldGrant`) — guard fail-closed áp dụng từ đầu.
    Test tự cấp field lẻ qua `grant(sale_role, ...)`."""
    from apps.users.models import DataScope, Role
    from apps.users.rbac_catalog import sync_permissions

    sync_permissions()
    return Role.objects.create(name="role-field-perm-sale", scope_default=DataScope.ALL)


@pytest.fixture
def sale_client(sale_role):
    """APIClient cho user thường có orders:edit (qua `sale_role`), chưa grant field nào."""
    from rest_framework.test import APIClient

    from apps.users.models import Permission, RolePermission, User, UserRoleAssignment
    from apps.users.rbac import bump_rbac_cache_version

    user = User.objects.create_user(username="field-perm-sale", password="x")
    RolePermission.objects.bulk_create(
        RolePermission(role=sale_role, permission=p)
        for p in Permission.objects.filter(section="orders")
    )
    UserRoleAssignment.objects.create(user=user, role=sale_role)
    bump_rbac_cache_version()
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def admin_client():
    """APIClient cho superuser (role `is_system`) — bypass mọi field grant."""
    from rest_framework.test import APIClient

    from apps.users.models import DataScope, Role, User, UserRoleAssignment
    from apps.users.rbac import bump_rbac_cache_version

    user = User.objects.create_user(username="field-perm-admin", password="x")
    role = Role.objects.create(
        name="System Role (field perm test)", is_system=True, scope_default=DataScope.ALL
    )
    UserRoleAssignment.objects.create(user=user, role=role)
    bump_rbac_cache_version()
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def another_channel():
    """Kênh KHÁC kênh mặc định của `order_factory`, dùng để test so sánh FK theo pk."""
    from apps.channels_core.models import Channel

    return Channel.objects.create(
        code=f"test-ch-other-{Channel.objects.count() + 1}", name="Other Test Channel"
    )


@pytest.fixture
def order_with_items(order_factory):
    """Order có OrderItem kèm theo, giá trị khác mặc định cho các nhánh field_diff
    (FK/datetime/date/bool/items) — dùng chung cho test_order_field_diff.py."""
    from apps.orders.models import Order, OrderItem, OrderStatus
    from apps.catalog.models import Product

    order = order_factory(
        total_amount="200000", status=OrderStatus.NEW, is_export_vat=True
    )
    product = Product.objects.create(
        name="SP Diff", sku=f"SKU-DIFF-{order.id}", price=100000
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        sku=product.sku,
        quantity=2,
        unit_price=100000,
        line_total=200000,
    )
    return Order.objects.get(pk=order.pk)


@pytest.fixture
def order_with_assessment(order_with_items):
    """Đơn có OrderAssessment với vài trường tĩnh + một trường ĐỘNG trong extra_fields.

    Trả `(order, payload)` với `payload` là đúng những gì frontend gửi lại khi KHÔNG
    sửa gì — form spread nguyên object `crm` cũ, nên payload luôn kèm cả khoá echo.
    Mọi test so sánh đều xuất phát từ payload này rồi đổi đúng một trường.
    """
    from apps.orders.models import OrderAssessment

    assessment = OrderAssessment.objects.create(
        order=order_with_items,
        internal_note="Ghi chu cu",
        sales_status_code="closed",
        extra_fields={"truong_dong_x": "gia-tri-cu"},
    )
    payload = {
        "internal_note": assessment.internal_note,
        "sales_status_code": assessment.sales_status_code,
        "truong_dong_x": assessment.extra_fields["truong_dong_x"],
    }
    return order_with_items, payload
