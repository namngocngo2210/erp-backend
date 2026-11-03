from rest_framework.permissions import BasePermission

# Nhóm section cho endpoint dùng chung giữa 3 trang đơn (orders / landing / koc).
ORDER_SECTIONS = ("orders", "landing-orders", "koc-orders")

# Code hành động dùng cho endpoint GHI dùng chung cả 3 trang đơn (sweep N2 giai
# đoạn 2). Quy ước any-of giống require_section(*ORDER_SECTIONS): endpoint không
# biết trước đơn thuộc trang nào (section nguồn nằm trong payload/record-scope),
# nên gate theo "có quyền hành động ở BẤT KỲ trang đơn nào" — record-scope
# (`order_record_scope_q`) vẫn chốt đơn cụ thể theo đúng section được cấp.
ORDER_CREATE_CODES = tuple(f"{s}:create" for s in ORDER_SECTIONS)
ORDER_EDIT_CODES = tuple(f"{s}:edit" for s in ORDER_SECTIONS)
ORDER_DELETE_CODES = tuple(f"{s}:delete" for s in ORDER_SECTIONS)


def require_permission(*codes: str):
    """Permission pass nếu user có BẤT KỲ permission code nào trong danh sách.

    Nguồn sự thật: apps.users.rbac.resolve_effective_permissions (RBAC+ABAC, có cache).
    Admin / role is_system short-circuit = full quyền.
    """

    class _HasPermission(BasePermission):
        message = "Bạn không có quyền thực hiện hành động này."

        def has_permission(self, request, view):
            # Ủy quyền cho lõi chung auth_core (D8) — một nguồn sự thật về quyền.
            from .auth_core import user_has_any_permission

            return user_has_any_permission(getattr(request, "user", None), *codes)

    return _HasPermission


def require_method_permission(**per_method):
    """Gate theo HTTP method — cho view function nhận nhiều method (audit 2026-08-19 N2).

    require_method_permission(
        GET="warehouses:view", POST="warehouses:create",
        PATCH="warehouses:edit", DELETE="warehouses:delete",
    )
    Giá trị là 1 code hoặc tuple code (pass nếu có BẤT KỲ code — dùng cho nhóm
    ORDER_SECTIONS). Method KHÔNG khai báo → từ chối (fail-closed): thêm method
    mới vào @api_view mà quên khai quyền ở đây phải lộ ngay thành 403, không
    được im lặng thừa hưởng quyền của method khác.
    """

    class _MethodPermission(BasePermission):
        message = "Bạn không có quyền thực hiện hành động này."

        def has_permission(self, request, view):
            from .auth_core import user_has_any_permission

            codes = per_method.get(request.method)
            if codes is None:
                return False
            if isinstance(codes, str):
                codes = (codes,)
            return user_has_any_permission(getattr(request, "user", None), *codes)

    return _MethodPermission


def user_has_permission(user, *codes: str) -> bool:
    """Kiểm quyền NGOÀI luồng `permission_classes` — vd nới/siết một query param
    sau khi request đã qua gate của method.

    Wrapper mỏng quanh ``auth_core.user_has_any_permission``, đặt ở đây để call
    site ngoài app ``users`` không phải import thẳng ``apps.users.auth_core``:
    ``permissions`` đã nằm trong danh sách import được phép của
    ``backend/.importlinter``, còn ``auth_core`` thì không.
    """
    from .auth_core import user_has_any_permission

    return user_has_any_permission(user, *codes)


def require_section(*sections: str):
    """Shim tương thích ngược: 'có truy cập section' = có quyền `{section}:view`.

    Giữ chữ ký variadic của bản cũ; mọi call-site hiện tại không cần đổi. Endpoint
    muốn siết theo hành động cụ thể (create/edit/delete/approve/export) thì chuyển
    sang require_permission trực tiếp.
    """
    return require_permission(*[f"{section}:view" for section in sections])


# Quyền đổi sale phụ trách. Mặc định KHÔNG role nào có (ngoài role is_system) —
# fail-closed cố ý: đây là trường quyết định ai được tính doanh số.
SALE_ASSIGNMENT_PERMISSION = "sale-assignment:edit"


def can_assign_sale(user) -> bool:
    """True nếu user được đổi sale phụ trách (đơn hàng hoặc khách thân thiết)."""
    from .auth_core import user_has_any_permission

    return user_has_any_permission(user, SALE_ASSIGNMENT_PERMISSION)
