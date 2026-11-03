import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("users", "0012_user_token_version"),
    ]

    operations = [
        migrations.CreateModel(
            name="Permission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=100, unique=True)),
                ("section", models.CharField(max_length=50)),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("view", "Xem"),
                            ("create", "Tạo"),
                            ("edit", "Sửa"),
                            ("delete", "Xóa"),
                            ("approve", "Duyệt"),
                            ("export", "Xuất"),
                        ],
                        max_length=20,
                    ),
                ),
                ("label", models.CharField(blank=True, default="", max_length=150)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "rbac_permissions",
                "ordering": ["section", "action"],
                "indexes": [models.Index(fields=["section"], name="rbacperm_section_idx")],
                "unique_together": {("section", "action")},
            },
        ),
        migrations.CreateModel(
            name="Role",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("description", models.CharField(blank=True, default="", max_length=255)),
                ("is_system", models.BooleanField(default=False)),
                (
                    "scope_default",
                    models.CharField(
                        choices=[
                            ("all", "Toàn bộ"),
                            ("department", "Theo phòng ban"),
                            ("channel", "Theo kênh"),
                            ("own", "Của tôi"),
                        ],
                        default="own",
                        max_length=20,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "rbac_roles",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="RolePermission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "permission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_links",
                        to="users.permission",
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_permissions",
                        to="users.role",
                    ),
                ),
            ],
            options={
                "db_table": "rbac_role_permissions",
                "indexes": [models.Index(fields=["role", "permission"], name="rbac_rp_role_perm_idx")],
                "unique_together": {("role", "permission")},
            },
        ),
        migrations.CreateModel(
            name="RoleSectionScope",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("section", models.CharField(max_length=50)),
                (
                    "scope",
                    models.CharField(
                        choices=[
                            ("all", "Toàn bộ"),
                            ("department", "Theo phòng ban"),
                            ("channel", "Theo kênh"),
                            ("own", "Của tôi"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="section_scopes",
                        to="users.role",
                    ),
                ),
            ],
            options={
                "db_table": "rbac_role_section_scopes",
                "indexes": [models.Index(fields=["role", "section"], name="rbac_rss_role_sec_idx")],
                "unique_together": {("role", "section")},
            },
        ),
        migrations.CreateModel(
            name="RoleFieldRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("section", models.CharField(max_length=50)),
                ("field", models.CharField(max_length=100)),
                (
                    "rule",
                    models.CharField(
                        choices=[("hidden", "Ẩn"), ("readonly", "Chỉ đọc")],
                        max_length=20,
                    ),
                ),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="field_rules",
                        to="users.role",
                    ),
                ),
            ],
            options={
                "db_table": "rbac_role_field_rules",
                "unique_together": {("role", "section", "field")},
            },
        ),
        migrations.CreateModel(
            name="UserRoleAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_assignments",
                        to="users.role",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_assignments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "rbac_user_roles",
                "indexes": [models.Index(fields=["user"], name="rbac_ur_user_idx")],
                "unique_together": {("user", "role")},
            },
        ),
        migrations.CreateModel(
            name="UserDepartment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_primary", models.BooleanField(default=False)),
                (
                    "department",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_links",
                        to="users.department",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="department_links",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "rbac_user_departments",
                "indexes": [models.Index(fields=["user"], name="rbac_ud_user_idx")],
                "unique_together": {("user", "department")},
            },
        ),
    ]
