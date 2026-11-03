"""Rà data-scope RBAC của từng role — in ma trận thật + cảnh báo cấu hình nguy hiểm.

Vì sao cần command này: role được sinh ĐỘNG theo phòng ban
(`0015_backfill_roles_from_legacy` tạo `Phòng: {tên}`), nên danh sách role thật chỉ
tồn tại trong DB của từng môi trường — không đọc được từ code. Muốn rà scope phải
chạy trên chính môi trường đó.

Bối cảnh (audit 2026-07-26): migration 0015 seed MỌI role với `scope_default = all`,
0022 set `landing-orders = all`. Với scope `all`, `order_record_scope_q` trả `Q()`
rỗng → record-scope KHÔNG siết gì. Toàn bộ lớp phân quyền bản ghi vừa dựng chỉ có
tác dụng sau khi hạ scope xuống mức tối thiểu đủ dùng cho từng role.

Command này CHỈ ĐỌC. Nó không sửa gì.

    python manage.py audit_rbac_scope              # ma trận + cảnh báo
    python manage.py audit_rbac_scope --role "Phòng: Sale"
    python manage.py audit_rbac_scope --show-users # kèm số user mỗi role
"""

from django.core.management.base import BaseCommand

from apps.users.models import (
    DataScope,
    Role,
    RolePermission,
    RoleSectionScope,
    UserRoleAssignment,
)

# Chỉ 9 section dưới đây thực sự được code resolve data-scope. Với mọi section khác,
# đặt scope gì cũng KHÔNG có tác dụng — nó chỉ tạo ảo giác an toàn. Danh sách này lấy
# từ call site thật của resolved_channel_codes / order_record_scope_q / scope_for:
#   orders, koc-orders, landing-orders, shipments, customers,
#   warranty, resale-tracking  → apps/orders/.../_view_helpers.py (_SECTION_ORDER_IDENTITY_Q)
#   contacts                   → apps/orders/application/services/contact_list_service.py
#   ads-report                 → apps/orders/_repositories_reports.py
SCOPE_AWARE_SECTIONS = (
    "orders",
    "koc-orders",
    "landing-orders",
    "shipments",
    "customers",
    "warranty",
    "resale-tracking",
    "contacts",
    "ads-report",
)

# Section mà scope hẹp (own/channel/department) sẽ PHÁ nghiệp vụ, kèm lý do.
# Đây là kết luận từ code + test, không phải phỏng đoán.
MUST_BE_ALL = {
    "landing-orders": (
        "Trang Landing là POOL CHUNG, đơn chưa phân có staff=NULL. Scope own → user "
        "không thấy đơn nào chưa phân. Chính là lỗi mà migration 0022 phải đi sửa."
    ),
    "shipments": (
        "Kho xử lý mọi đơn và KHÔNG BAO GIỜ là Order.staff. Scope own → kho không "
        "PATCH được trạng thái giao hàng của bất kỳ đơn nào."
    ),
    "warranty": (
        "Khách mang máy tới bảo hành có thể đã mua từ bất kỳ sale nào; NV bảo hành "
        "không phải Order.staff. Scope own → không tạo được phiếu bảo hành nào "
        "(có test khoá kết luận này: apps/warranty/tests)."
    ),
}

# Section nên siết: đây là nơi record-scope có giá trị bảo mật thật.
SHOULD_NARROW = {
    "orders": "Đơn hàng — nguồn dữ liệu khách nhạy cảm nhất.",
    "koc-orders": "Cùng nhóm với orders.",
    "customers": "Hồ sơ khách + lịch sử đơn.",
    "resale-tracking": "Danh sách tái mua — tài sản cạnh tranh giữa sale.",
    "contacts": "Danh sách liên hệ — tài sản cạnh tranh giữa sale.",
}

_SCOPE_LABEL = {
    DataScope.OWN: "own",
    DataScope.CHANNEL: "channel",
    DataScope.DEPARTMENT: "department",
    DataScope.ALL: "all",
}


class Command(BaseCommand):
    help = "Rà data-scope RBAC của từng role (chỉ đọc). Xem docstring để biết bối cảnh."

    def add_arguments(self, parser):
        parser.add_argument("--role", help="Chỉ rà 1 role theo tên (khớp chính xác).")
        parser.add_argument(
            "--show-users",
            action="store_true",
            help="Kèm số user đang được gán role đó.",
        )

    def handle(self, *args, **options):
        roles = Role.objects.filter(is_active=True).order_by("is_system", "name")
        if options.get("role"):
            roles = roles.filter(name=options["role"])
        roles = list(roles)
        if not roles:
            self.stdout.write(self.style.WARNING("Không có role nào khớp."))
            return

        role_ids = [r.id for r in roles]
        overrides: dict[int, dict[str, str]] = {}
        for row in RoleSectionScope.objects.filter(role_id__in=role_ids):
            overrides.setdefault(row.role_id, {})[row.section] = row.scope

        granted: dict[int, set[str]] = {}
        for role_id, section in RolePermission.objects.filter(
            role_id__in=role_ids
        ).values_list("role_id", "permission__section"):
            granted.setdefault(role_id, set()).add(section)

        user_counts: dict[int, int] = {}
        if options.get("show_users"):
            for row in UserRoleAssignment.objects.filter(role_id__in=role_ids).values_list(
                "role_id", flat=True
            ):
                user_counts[row] = user_counts.get(row, 0) + 1

        warnings: list[str] = []
        no_effect = 0

        for role in roles:
            header = f"── {role.name}  (mặc định: {role.scope_default})"
            if role.is_system:
                header += "  [is_system → BỎ QUA MỌI SCOPE, full quyền]"
            if options.get("show_users"):
                header += f"  · {user_counts.get(role.id, 0)} user"
            self.stdout.write(self.style.MIGRATE_HEADING(header))

            if role.is_system:
                self.stdout.write(
                    "    resolve_effective_permissions short-circuit theo cờ is_system "
                    "→ scope không được đọc tới.\n"
                )
                continue

            role_sections = granted.get(role.id, set())
            if not role_sections:
                self.stdout.write("    (chưa cấp quyền section nào)\n")
                continue

            role_overrides = overrides.get(role.id, {})
            for section in sorted(role_sections):
                scope = role_overrides.get(section, role.scope_default)
                label = _SCOPE_LABEL.get(scope, scope)
                source = "override" if section in role_overrides else "mặc định"

                if section not in SCOPE_AWARE_SECTIONS:
                    no_effect += 1
                    continue

                note = ""
                style = self.style.SUCCESS
                if section in MUST_BE_ALL and scope != DataScope.ALL:
                    note = "  ← NGUY HIỂM: sẽ mất chức năng"
                    style = self.style.ERROR
                    warnings.append(f"{role.name} · {section}={label} — {MUST_BE_ALL[section]}")
                elif section in SHOULD_NARROW and scope == DataScope.ALL:
                    note = "  ← không siết gì"
                    style = self.style.WARNING
                    warnings.append(
                        f"{role.name} · {section}=all — {SHOULD_NARROW[section]} "
                        "Record-scope không lọc gì với scope all."
                    )
                elif section == "ads-report" and scope != DataScope.ALL:
                    note = "  ← báo cáo ads sẽ bị từ chối (code chỉ chấp nhận all)"
                    style = self.style.WARNING

                self.stdout.write(style(f"    {section:<18} {label:<11} ({source}){note}"))
            self.stdout.write("")

        if no_effect:
            self.stdout.write(
                self.style.NOTICE(
                    f"Đã bỏ qua {no_effect} cặp (role, section) vì section KHÔNG được code "
                    "resolve scope — đặt scope ở đó không có tác dụng gì.\n"
                    f"Chỉ {len(SCOPE_AWARE_SECTIONS)} section có ý nghĩa: "
                    f"{', '.join(SCOPE_AWARE_SECTIONS)}\n"
                )
            )

        if warnings:
            self.stdout.write(self.style.MIGRATE_HEADING("── Cần xử lý"))
            for line in warnings:
                self.stdout.write(f"  • {line}")
        else:
            self.stdout.write(self.style.SUCCESS("Không có cảnh báo nào."))
