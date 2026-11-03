"""Lưới chặn cấu trúc cho phân quyền hành động (audit 2026-08-19 N2).

Lớp lỗi đã tái diễn ≥3 lần: endpoint có method GHI (POST/PATCH/PUT/DELETE) nhưng
permission chỉ có ``require_section`` (= ``{section}:view``) — ai xem được là
ghi được. Đã sweep hai đợt (đợt 1: nhóm quản trị, a28d9bf6; đợt 2 2026-08-20:
sale-flow + kho-staff, đi kèm migration users/0039 GRANDFATHER — cấp action ghi
cho role đang có ``:view`` để hành vi prod không đổi ngày deploy, việc siết
thật là thu-hồi-từng-role trên UI). Test này bảo đảm không mọc lại: view
function mới có method ghi mà gate bằng ``require_section`` trần → đỏ ngay.

Kỹ thuật cùng họ với ``apps/orders/tests/test_record_scope_coverage.py``: quét
source bằng AST, không cần DB. Quét MỌI file ``*.py`` trong ``apps/*/`` (trừ
tests/migrations) vì view nằm cả ở ``views.py`` phẳng lẫn ``transport/views/``.

Mở rộng 2026-09-23 (audit security M2 — ba điểm mù của lưới cũ):
(a) view ghi mà ``permission_classes`` CHỈ có ``IsAuthenticated`` (gate nằm trong
    thân hàm: HR ``_can(...)``, ``ChannelService.can_manage``, exports lọc
    ``user=request.user``…) → phải whitelist KÈM LÝ DO nêu rõ gate đó là gì;
(b) ``require_permission("x:view")`` / ``require_method_permission(POST="x:view")``
    trên method ghi → đỏ trừ whitelist (3 view có check ``:edit`` bên trong);
(c) class-based view (APIView/ViewSet…) có method ghi cũng bị quét
    ``permission_classes =`` như view function.
Decorator ``@superuser_only`` (pancake/foxia) được nhận là gate thật — nó chặn
trước khi chạm DB bằng ``resolve_effective_permissions(...).is_superuser``.
``AllowAny`` (webhook/ingest/portal) CỐ Ý nằm ngoài lưới này: chúng là endpoint
public có token/throttle riêng, thêm ``AllowAny`` vào view nội bộ lộ ngay ở review.

Whitelist là tài liệu sống: ngoại lệ phải ghi rõ LÝ DO. Entry hết hiệu lực (file
đổi/đã fix) sẽ làm test đỏ để không tích xác chết — cùng nguyên tắc
``unmatched_ignore_imports_alerting`` của .importlinter.
"""

from __future__ import annotations

import ast
from pathlib import Path

APPS_DIR = Path(__file__).resolve().parents[2]

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# (đường-dẫn-tương-đối-so-với-apps/, tên function) → lý do miễn trừ.
# CHỈ thêm entry khi có kiểm soát ghi TƯƠNG ĐƯƠNG bên trong view và ghi rõ nó là gì.
WHITELIST: dict[tuple[str, str], str] = {
    ("orders/transport/views/order_views_impl/order_list_view.py", "list_orders"):
        ("POST chỉ là ĐỌC: body chứa bộ lọc/dữ liệu cần parse, không ghi gì. Đổi sang GET là đổi hợp đồng API — không thuộc phạm vi sweep N2."),
    ("orders/transport/views/order_views_impl/order_list_view.py", "list_koc_orders"):
        ("POST chỉ là ĐỌC: body chứa bộ lọc/dữ liệu cần parse, không ghi gì. Đổi sang GET là đổi hợp đồng API — không thuộc phạm vi sweep N2."),
    ("orders/transport/views/order_views_impl/order_list_view.py", "list_landing_orders"):
        ("POST chỉ là ĐỌC: body chứa bộ lọc/dữ liệu cần parse, không ghi gì. Đổi sang GET là đổi hợp đồng API — không thuộc phạm vi sweep N2."),
    ("orders/transport/views/order_views_impl/order_list_view.py", "list_shipment_orders"):
        ("POST chỉ là ĐỌC: body chứa bộ lọc/dữ liệu cần parse, không ghi gì. Đổi sang GET là đổi hợp đồng API — không thuộc phạm vi sweep N2."),
    ("orders/transport/views/order_views_impl/sheet3_view.py", "list_sheet3_orders"):
        ("POST chỉ là ĐỌC: body chứa bộ lọc/dữ liệu cần parse, không ghi gì. Đổi sang GET là đổi hợp đồng API — không thuộc phạm vi sweep N2."),
    ("facebook_ads/transport/views/facebook_ads_views_impl/insights_views.py", "insights_batch"):
        ("POST chỉ là ĐỌC: body chứa bộ lọc/dữ liệu cần parse, không ghi gì. Đổi sang GET là đổi hợp đồng API — không thuộc phạm vi sweep N2."),
    ("inventory/transport/views/import_views.py", "parse_import_lines"):
        ("POST chỉ là ĐỌC: body chứa bộ lọc/dữ liệu cần parse, không ghi gì. Đổi sang GET là đổi hợp đồng API — không thuộc phạm vi sweep N2."),
    ("orders/transport/views/order_views_impl/order_address_view.py", "parse_raw_address"):
        ("POST chỉ là ĐỌC: body chứa bộ lọc/dữ liệu cần parse, không ghi gì. Đổi sang GET là đổi hợp đồng API — không thuộc phạm vi sweep N2."),
    ("orders/transport/views/order_views_impl/order_crud_view.py", "get_kship_options"):
        ("POST chỉ là ĐỌC: body chứa bộ lọc/dữ liệu cần parse, không ghi gì. Đổi sang GET là đổi hợp đồng API — không thuộc phạm vi sweep N2."),
    ("orders/transport/views/order_views_impl/order_export_view.py", "bulk_download_invoices"):
        ("In hoá đơn là ĐỌC (không ghi cờ nào — khác delivery-slip đã có WRITE codes); record-scope + cap ids đã áp từ 2026-07-26 N1."),
    ("callcenter/transport/views/callcenter_views_impl.py", "webphone_token"):
        ("POST sinh token phiên webphone cho chính user gọi — không ghi dữ liệu."),
    ("orders/transport/views/order_views_impl/order_address_view.py", "parse_address_level2"):
        ("POST chỉ là ĐỌC: preview tách chuỗi 'Địa chỉ 2 cấp' → tỉnh/phường mới + Khu vực Kiot (resolve_level2_edit thuần hàm), không ghi gì; PATCH đơn mới ghi và đã gate field-grant."),
    ("orders/transport/views/order_views_impl/order_address_view.py", "lookup_raw_address"):
        ("POST chỉ là ĐỌC: tra cứu địa chỉ thô → 3 cấp/2 cấp (lookup_address thuần hàm) cho trang Tiện ích, gate section riêng address-lookup chỉ có :view — không có dữ liệu để sửa."),
    # ── (a) chỉ IsAuthenticated, gate trong thân hàm ──────────────────────────
    ("channels_core/transport/views/channel_views_impl.py", "channels_collection"):
        "Thân hàm: `ChannelService.can_manage(request.user)` → 403 trước mọi thao tác ghi (POST tạo kênh).",
    ("channels_core/transport/views/channel_views_impl.py", "channel_detail"):
        "Thân hàm: `ChannelService.can_manage(request.user)` → 403 ngay dòng đầu.",
    ("channels_core/transport/views/channel_views_impl.py", "bulk_delete_channels"):
        "Thân hàm: `ChannelService.can_manage(request.user)` → 403 ngay dòng đầu.",
    ("channels_core/transport/views/channel_views_impl.py", "channel_assignments"):
        "Thân hàm: `ChannelService.can_manage(request.user)` → 403 ngay dòng đầu.",
    ("channels_core/transport/views/channel_views_impl.py", "channel_assignment_detail"):
        "Thân hàm: `ChannelService.can_manage(request.user)` → 403 ngay dòng đầu.",
    ("channels_core/transport/views/channel_views_impl.py", "kiotviet_channels_collection"):
        "Thân hàm: `ChannelService.can_manage(request.user)` → 403 ngay dòng đầu.",
    ("channels_core/transport/views/channel_views_impl.py", "kiotviet_channel_detail"):
        "Thân hàm: `ChannelService.can_manage(request.user)` → 403 ngay dòng đầu.",
    ("channels_core/transport/views/channel_views_impl.py", "bulk_delete_kiotviet_channels"):
        "Thân hàm: `ChannelService.can_manage(request.user)` → 403 ngay dòng đầu.",
    ("exports/transport/views.py", "exports_collection"):
        "Self-service: POST tạo job export của CHÍNH user (queryset lọc `user=request.user`); quyền xuất dữ liệu gate ở từng export definition (`check_permission`).",
    ("exports/transport/views.py", "export_detail"):
        "Self-service: DELETE chỉ tìm job `user=request.user` — không chạm job của người khác.",
    ("hr/transport/views/account_views.py", "employee_accounts"):
        "Thân hàm: `_can(request, \"employees:edit\")` → 403.",
    ("hr/transport/views/account_views.py", "employee_account_link"):
        "Thân hàm: `_can(request, \"employees:edit\")` → 403.",
    ("hr/transport/views/account_views.py", "employee_account_detail"):
        "Thân hàm: `_can(request, \"employees:edit\")` → 403.",
    ("hr/transport/views/attendance_checkin_views.py", "attendance_check_in"):
        "Self-service: chấm công cho hồ sơ nhân viên gắn với CHÍNH tài khoản gọi (`_employee(request)`), không nhận employee_id từ client.",
    ("hr/transport/views/attendance_location_views.py", "attendance_locations"):
        "Thân hàm: GET `can(\"employees:view\")`, POST `can(\"employees:edit\")` → 403.",
    ("hr/transport/views/attendance_location_views.py", "attendance_location_detail"):
        "Thân hàm: `can(request, \"employees:edit\")` → 403 ngay dòng đầu.",
    ("hr/transport/views/compensation_views.py", "compensation_update"):
        "Thân hàm: `_can(request, \"salaries:edit\")` → 403.",
    ("hr/transport/views/contract_salary_views.py", "employee_salaries"):
        "Thân hàm: GET `salaries:view`, POST `salaries:edit` qua `_can` → 403.",
    ("hr/transport/views/contract_salary_views.py", "employee_contracts"):
        "Thân hàm: GET `salaries:view`, POST `salaries:edit` qua `_can` → 403.",
    ("hr/transport/views/department_org_views.py", "department_head_set"):
        "Thân hàm: `_is_dept_admin(request)` → 403 ngay dòng đầu.",
    ("hr/transport/views/department_org_views.py", "department_teams"):
        "Thân hàm: `_is_dept_admin(request)` → 403 ngay dòng đầu.",
    ("hr/transport/views/department_org_views.py", "team_detail"):
        "Thân hàm: `_is_dept_admin(request)` → 403 ngay dòng đầu.",
    ("hr/transport/views/employee_views.py", "list_create_employees"):
        "Thân hàm: GET `employees:view`, POST `employees:create` qua `_can` → 403.",
    ("hr/transport/views/employee_views.py", "employee_detail"):
        "Thân hàm: GET `employees:view`, DELETE `employees:delete`, PATCH `employees:edit` qua `_can` → 403.",
    ("hr/transport/views/employee_views.py", "employee_resign"):
        "Thân hàm: `_can(request, \"employees:delete\")` → 403.",
    ("hr/transport/views/leave_attendance_views.py", "leave_requests"):
        "Thân hàm: GET `employees:view`, POST `employees:edit` qua `_can` → 403.",
    ("hr/transport/views/leave_attendance_views.py", "leave_approve"):
        "Thân hàm: `_can_approve_leave(request, leave)` (employees:approve hoặc trưởng phòng của nhân sự) → 403.",
    ("hr/transport/views/leave_attendance_views.py", "leave_reject"):
        "Thân hàm: `_can_approve_leave(request, leave)` (employees:approve hoặc trưởng phòng của nhân sự) → 403.",
    ("hr/transport/views/leave_attendance_views.py", "employee_attendance"):
        "Thân hàm: GET `employees:view`, POST `employees:edit` qua `_can` → 403.",
    ("hr/transport/views/self_service_views.py", "me_leave_requests"):
        "Self-service: tạo đơn nghỉ cho hồ sơ gắn với CHÍNH tài khoản gọi (`_employee(request)`), không nhận employee_id từ client.",
    ("users/transport/views/users_views_impl/auth_views.py", "logout_view"):
        "Self-service: huỷ phiên của chính user gọi.",
    ("users/transport/views/users_views_impl/auth_views.py", "profile_view"):
        "Self-service: chỉ sửa display_name/avatar của `request.user`.",
    ("users/transport/views/users_views_impl/auth_views.py", "change_password_view"):
        "Self-service: đổi mật khẩu của `request.user`, yêu cầu mật khẩu cũ + bump token_version.",
    # ── (b) require_permission(":view") trên method ghi, có check ghi bên trong ──
    ("best_express/transport/api_views.py", "estimate_best_fee"):
        "POST chỉ là ĐỌC: ước tính phí Best theo body tham số, không ghi gì.",
    ("best_express/transport/api_views.py", "list_create_best_shipments"):
        "Thân hàm: POST kiểm `require_permission(\"shipments:edit\")().has_permission(...)` → 403 trước khi tạo vận đơn (:190).",
    ("best_express/transport/api_views.py", "trace_best_shipments"):
        "POST chỉ là ĐỌC: tra hành trình theo mã vận đơn (đã `_assert_codes_in_scope`), không ghi gì.",
    ("best_express/transport/api_views.py", "print_best_waybills"):
        "POST chỉ là ĐỌC: lấy URL in vận đơn từ Best theo mã (đã `_assert_codes_in_scope`), không ghi gì ở ta.",
    ("orders/transport/views/order_views_impl/lead_assignment_view.py", "lead_assignment_transfer"):
        "Thân hàm: `can_assign_sale(request.user)` (= sale-assignment:edit) → 403 trước bulk-update staff (:377).",
    ("orders/transport/views/product_profile_views.py", "product_profile"):
        "Thân hàm: PUT kiểm `user_has_any_permission(request.user, \"products:edit\")` → 403 (:66).",
}




# Decorator được coi là gate thật dù permission_classes chỉ có IsAuthenticated.
_GATE_DECORATORS = {"superuser_only"}

# Base class DRF: có mặt trong bases ⇒ là class-based view cần quét.
_CBV_BASES = {
    "APIView", "GenericAPIView", "ViewSet", "GenericViewSet", "ModelViewSet",
    "ReadOnlyModelViewSet", "ListCreateAPIView", "RetrieveUpdateAPIView",
    "RetrieveUpdateDestroyAPIView", "CreateAPIView", "UpdateAPIView", "DestroyAPIView",
    "ListAPIView", "RetrieveAPIView",
}
# Base class mixin sẵn method ghi (không cần khai post/create trong class).
_CBV_WRITE_BASES = {
    "ModelViewSet", "ListCreateAPIView", "RetrieveUpdateAPIView",
    "RetrieveUpdateDestroyAPIView", "CreateAPIView", "UpdateAPIView", "DestroyAPIView",
}
_CBV_WRITE_HANDLERS = {
    "post", "put", "patch", "delete",
    "create", "update", "partial_update", "destroy",
}


def _decorator_call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _api_view_write_methods(dec: ast.Call) -> set[str]:
    methods: set[str] = set()
    for arg in dec.args:
        if isinstance(arg, (ast.List, ast.Tuple)):
            for elt in arg.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    methods.add(elt.value.upper())
    return methods & WRITE_METHODS


def _classify_gates(perm_expr: ast.AST) -> dict:
    """Bóc các gate trong biểu thức permission_classes (list/tuple/args)."""
    info: dict = {
        "names": set(), "section": False, "perm_codes": [], "perm_nonliteral": False,
        "method_map": {}, "method_nonliteral": False,
    }
    for node in ast.walk(perm_expr):
        if isinstance(node, ast.Name):
            info["names"].add(node.id)
        if not isinstance(node, ast.Call):
            continue
        name = _decorator_call_name(node)
        if name == "require_section":
            info["section"] = True
        elif name == "require_permission":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    info["perm_codes"].append(arg.value)
                else:
                    info["perm_nonliteral"] = True
        elif name == "require_method_permission":
            for kw in node.keywords:
                value = kw.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    info["method_map"][kw.arg] = (value.value,)
                elif isinstance(value, (ast.Tuple, ast.List)) and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts
                ):
                    info["method_map"][kw.arg] = tuple(e.value for e in value.elts)
                else:
                    info["method_nonliteral"] = True
    return info


def _judge(write_methods: set[str], info: dict) -> str | None:
    """Trả mã lỗi hoặc None nếu gate đủ. Hằng không-literal coi là gate thật
    (vd `require_permission(*_DELIVERY_SLIP_WRITE_CODES)`)."""
    if "AllowAny" in info["names"]:
        return None
    has_gate = (
        info["section"] or info["perm_codes"] or info["perm_nonliteral"]
        or info["method_map"] or info["method_nonliteral"]
    )
    if not has_gate:
        return "isauth_only"
    if info["method_map"] or info["method_nonliteral"]:
        view_only = sorted(
            m for m in write_methods
            if m in info["method_map"] and all(c.endswith(":view") for c in info["method_map"][m])
        )
        if view_only:
            return "method_permission_view:" + ",".join(view_only)
        return None
    if info["perm_codes"] and not info["perm_nonliteral"] and all(
        c.endswith(":view") for c in info["perm_codes"]
    ):
        return "require_permission_view"
    if info["section"] and not info["perm_codes"] and not info["perm_nonliteral"]:
        return "bare_require_section"
    return None


def _iter_view_files():
    for path in APPS_DIR.rglob("*.py"):
        rel = path.relative_to(APPS_DIR).as_posix()
        parts = rel.split("/")
        if "tests" in parts or "migrations" in parts or parts[-1].startswith("test_"):
            continue
        yield path, rel


def _scan_function(node: ast.FunctionDef) -> str | None:
    api_view_dec = None
    perm_dec = None
    decorator_names: set[str] = set()
    for dec in node.decorator_list:
        name = _decorator_call_name(dec)
        decorator_names.add(name or "")
        if name == "api_view":
            api_view_dec = dec
        elif name == "permission_classes":
            perm_dec = dec
    if api_view_dec is None:
        return None
    write_methods = _api_view_write_methods(api_view_dec)
    if not write_methods:
        return None
    if perm_dec is None:
        # Không khai permission_classes = mặc định DRF (IsAuthenticated) — cùng lỗi (a).
        info = {"names": set(), "section": False, "perm_codes": [], "perm_nonliteral": False,
                "method_map": {}, "method_nonliteral": False}
    else:
        info = _classify_gates(perm_dec)
    verdict = _judge(write_methods, info)
    if verdict == "isauth_only" and decorator_names & _GATE_DECORATORS:
        return None
    return verdict


def _scan_class(node: ast.ClassDef) -> str | None:
    bases = {_decorator_call_name(b) for b in node.bases}
    if not bases & _CBV_BASES:
        return None
    write_methods: set[str] = set()
    if bases & _CBV_WRITE_BASES:
        write_methods.add("MIXIN")
    for item in node.body:
        if not isinstance(item, ast.FunctionDef):
            continue
        if item.name in _CBV_WRITE_HANDLERS:
            write_methods.add(item.name.upper())
        for dec in item.decorator_list:
            if _decorator_call_name(dec) == "action" and isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        write_methods |= {
                            e.value.upper() for e in kw.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        } & WRITE_METHODS
    if not write_methods:
        return None
    perm_assign = next(
        (
            item.value for item in node.body
            if isinstance(item, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "permission_classes" for t in item.targets)
        ),
        None,
    )
    if perm_assign is None:
        return "isauth_only"
    return _judge(write_methods, _classify_gates(perm_assign))


def _scan() -> tuple[list[tuple[tuple[str, str], int, str]], set[tuple[str, str]]]:
    """Trả (findings, seen_whitelist). findings = ((rel, tên), dòng, mã lỗi)."""
    findings: list[tuple[tuple[str, str], int, str]] = []
    seen: set[tuple[str, str]] = set()
    for path, rel in _iter_view_files():
        source = path.read_text(encoding="utf-8")
        if "api_view" not in source and "permission_classes" not in source:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                verdict = _scan_function(node)
            elif isinstance(node, ast.ClassDef):
                verdict = _scan_class(node)
            else:
                continue
            if verdict is None:
                continue
            key = (rel, node.name)
            if key in WHITELIST:
                seen.add(key)
                continue
            findings.append((key, node.lineno, verdict))
    return findings, seen


def _format(findings, category: str) -> list[str]:
    return [
        f"{rel}:{lineno} {name} [{verdict}]"
        for (rel, name), lineno, verdict in findings
        if verdict.startswith(category)
    ]


def test_write_endpoints_do_not_gate_with_require_section():
    findings, _ = _scan()
    offenders = _format(findings, "bare_require_section")
    assert not offenders, (
        "View có method GHI nhưng permission chỉ có require_section (=:view). "
        "Dùng require_permission/require_method_permission với action đúng "
        "(POST=:create, PATCH/PUT=:edit, DELETE=:delete), hoặc thêm whitelist "
        "KÈM LÝ DO nếu có kiểm soát tương đương bên trong:\n  "
        + "\n  ".join(offenders)
    )


def test_write_endpoints_do_not_rely_on_isauthenticated_only():
    """(a)+(c): view/class có method ghi mà permission_classes không có gate action nào."""
    findings, _ = _scan()
    offenders = _format(findings, "isauth_only")
    assert not offenders, (
        "View có method GHI nhưng permission_classes chỉ có IsAuthenticated (hoặc "
        "thiếu). Gate bằng require_permission/require_method_permission, hoặc nếu "
        "gate nằm trong thân hàm (_can(...), can_manage, self-service) thì thêm "
        "whitelist KÈM LÝ DO nêu rõ gate đó:\n  " + "\n  ".join(offenders)
    )


def test_write_endpoints_do_not_gate_with_view_action():
    """(b): require_permission("x:view") / require_method_permission(POST="x:view")."""
    findings, _ = _scan()
    offenders = _format(findings, "require_permission_view") + _format(
        findings, "method_permission_view"
    )
    assert not offenders, (
        "View có method GHI nhưng action gate là :view. Dùng action ghi thật "
        "(:create/:edit/:delete/:approve), hoặc whitelist KÈM LÝ DO nếu thân hàm "
        "kiểm quyền ghi:\n  " + "\n  ".join(offenders)
    )


def test_whitelist_has_no_stale_entries():
    _, seen = _scan()
    stale = set(WHITELIST) - seen
    assert not stale, (
        "Entry whitelist không còn khớp view nào (đã fix hoặc file đổi) — xoá đi "
        f"để danh sách không tích xác chết: {sorted(stale)}"
    )
