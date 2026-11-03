from django.urls import path

from .views import (
    admin_user_detail,
    admin_users_collection,
    change_password_view,
    department_assignable_users,
    department_collection,
    department_detail,
    department_teams,
    login_view,
    logout_view,
    me,
    order_field_catalog,
    permissions_catalog,
    profile_view,
    role_collection,
    role_detail,
    team_detail,
    token_refresh_view,
)

urlpatterns = [
    path("login", login_view, name="users-login"),
    path("token/refresh", token_refresh_view, name="users-token-refresh"),
    path("logout", logout_view, name="users-logout"),
    path("me", me, name="users-me"),
    path("profile", profile_view, name="users-profile"),
    path("change-password", change_password_view, name="users-change-password"),
    path("admin", admin_users_collection, name="users-admin-collection"),
    path("admin/<int:user_id>", admin_user_detail, name="users-admin-detail"),
    path("departments", department_collection, name="departments-collection"),
    path("departments/<int:department_id>", department_detail, name="departments-detail"),
    path(
        "departments/<int:department_id>/teams",
        department_teams,
        name="department-teams",
    ),
    path(
        "departments/<int:department_id>/assignable-users",
        department_assignable_users,
        name="department-assignable-users",
    ),
    path("teams/<int:team_id>", team_detail, name="team-detail"),
    path("permissions", permissions_catalog, name="rbac-permissions"),
    path("order-field-catalog", order_field_catalog, name="rbac-order-field-catalog"),
    path("roles", role_collection, name="rbac-roles-collection"),
    path("roles/<int:role_id>", role_detail, name="rbac-roles-detail"),
]
