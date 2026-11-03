"""Helper dùng chung cho test của apps.orders.

Được tách ra từ ~12 bản copy y hệt nhau rải rác trong các file test (tái cấu trúc
2026-08-05, xem docs/test-restructure-2026-08-05.md). KHÔNG import `apps.kiotviet`
từ module này — vi phạm layering contract trong `.importlinter`; mọi mock KiotViet
patch bằng string path.
"""

from contextlib import contextmanager
from unittest.mock import patch

from apps.inventory.models import MovementType, StockMovement
from apps.orders.models import Order
from apps.sysconfig import sysconfig
from apps.users.models import (
    DataScope,
    Permission,
    Role,
    RolePermission,
    User,
    UserRoleAssignment,
)
from apps.users.rbac import bump_rbac_cache_version


def make_scoped_user(username: str, *sections: str, scope=DataScope.ALL) -> User:
    """User có role cấp toàn bộ permission của các section, scope_default=scope.

    KHÔNG đặt tên role "Administrator" — migration users/0015 đã seed sẵn tên đó
    (unique constraint).
    """
    user = User.objects.create_user(username=username, password="x")
    role = Role.objects.create(name=f"role-{username}", scope_default=scope)
    RolePermission.objects.bulk_create(
        RolePermission(role=role, permission=p)
        for p in Permission.objects.filter(section__in=sections)
    )
    UserRoleAssignment.objects.create(user=user, role=role)
    bump_rbac_cache_version()
    return user


def make_scoped_user_by_codes(username: str, *codes: str, scope=DataScope.ALL) -> User:
    """User có role cấp ĐÚNG các permission code liệt kê (không phải cả section).

    Khác `make_scoped_user` (cấp trọn section): dùng khi trục đang test là ACTION
    (view vs edit) nên phải cấp lẻ từng code.
    """
    user = User.objects.create_user(username=username, password="x")
    role = Role.objects.create(name=f"role-{username}", scope_default=scope)
    RolePermission.objects.bulk_create(
        RolePermission(role=role, permission=p)
        for p in Permission.objects.filter(code__in=codes)
    )
    UserRoleAssignment.objects.create(user=user, role=role)
    bump_rbac_cache_version()
    return user


@contextmanager
def mock_kiotviet():
    """Chặn mọi round-trip HTTP sang KiotViet, ghi lại đơn nào ĐƯỢC gửi đi.

    Yield dict `printed` — chỉ cần một đơn ngoài scope lọt vào queryset là nó sẽ
    xuất hiện trong `printed` (in hóa đơn = 1 lượt gọi/đơn). Test không cần đo
    thì bỏ qua giá trị yield.
    """
    printed: dict[str, list[str]] = {"invoice_codes": [], "invoice_ids": []}

    # Chữ ký mirror hàm thật (view/invoice.py gọi bằng keyword arg).
    def fake_invoice(invoice_code, order, channel):
        printed["invoice_codes"].append(invoice_code)
        return {"code": invoice_code}

    def fake_detail(invoice_id, order, channel):
        printed["invoice_ids"].append(invoice_id)
        return {"Code": order.invoice_code}

    with (
        patch("apps.orders.invoice.get_invoice_by_code", side_effect=fake_invoice),
        patch("apps.orders.invoice._fetch_invoice_detail_web", side_effect=fake_detail),
    ):
        yield printed


def ingest_headers() -> dict[str, str]:
    """Header X-API-TOKEN cho endpoint landing ingest (nếu settings có token)."""
    headers = {}
    token = sysconfig.get("ORDERS_INGEST_API_TOKEN")
    if token:
        headers["HTTP_X_API_TOKEN"] = token
    return headers


def no_sale_movement(order: Order) -> bool:
    """True nếu đơn CHƯA phát sinh movement SALE nào (guard dispatch không trừ tồn)."""
    return not StockMovement.objects.filter(
        reference_type="order",
        reference_id=order.id,
        movement_type=MovementType.SALE,
    ).exists()
