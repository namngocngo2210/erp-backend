from django.urls import path

from apps.hr.transport.views.attendance_checkin_views import (
    attendance_check_in,
    attendance_config,
)
from apps.hr.transport.views.attendance_event_views import (
    attendance_event_photo,
    attendance_events,
)
from apps.hr.transport.views.attendance_export_views import attendance_export
from apps.hr.transport.views.compensation_views import (
    compensation_list,
    compensation_update,
    payroll_export,
)
from apps.hr.transport.views.attendance_location_views import (
    attendance_location_detail,
    attendance_locations,
)
from apps.hr.transport.views.contract_salary_views import (
    employee_contracts,
    employee_salaries,
)
from apps.hr.transport.views.department_views import list_departments
from apps.hr.transport.views.department_org_views import (
    department_head_set,
    department_org,
    department_teams,
    team_detail,
)
from apps.hr.transport.views.account_views import (
    assignable_users,
    employee_account_detail,
    employee_account_link,
    employee_accounts,
)
from apps.hr.transport.views.employee_views import (
    employee_detail,
    employee_org_chart,
    employee_resign,
    list_create_employees,
)
from apps.hr.transport.views.leave_attendance_views import (
    employee_attendance,
    leave_approve,
    leave_reject,
    leave_requests,
)
from apps.hr.transport.views.self_service_views import (
    me_attendance,
    me_leave_requests,
    me_profile,
)

urlpatterns = [
    path("departments", list_departments, name="hr-departments"),
    path("department-org", department_org, name="hr-department-org"),
    path("departments/<int:department_id>/head", department_head_set, name="hr-department-head"),
    path("departments/<int:department_id>/teams", department_teams, name="hr-department-teams"),
    path("teams/<int:team_id>", team_detail, name="hr-team-detail"),
    path("employees", list_create_employees, name="hr-employees"),
    path("employees/<int:employee_id>", employee_detail, name="hr-employee-detail"),
    path("employees/<int:employee_id>/org-chart", employee_org_chart, name="hr-employee-org"),
    path("employees/<int:employee_id>/resign", employee_resign, name="hr-employee-resign"),
    path("assignable-users", assignable_users, name="hr-assignable-users"),
    path("employees/<int:employee_id>/accounts", employee_accounts, name="hr-employee-accounts"),
    path("employees/<int:employee_id>/accounts/link", employee_account_link, name="hr-employee-account-link"),
    path("employees/<int:employee_id>/accounts/<int:account_id>", employee_account_detail, name="hr-employee-account-detail"),
    path("employees/<int:employee_id>/salaries", employee_salaries, name="hr-employee-salaries"),
    path("employees/<int:employee_id>/contracts", employee_contracts, name="hr-employee-contracts"),
    path("leave-requests", leave_requests, name="hr-leave-requests"),
    path("leave-requests/<int:leave_id>/approve", leave_approve, name="hr-leave-approve"),
    path("leave-requests/<int:leave_id>/reject", leave_reject, name="hr-leave-reject"),
    path("employees/<int:employee_id>/attendance", employee_attendance, name="hr-employee-attendance"),
    path("attendance/config", attendance_config, name="hr-attendance-config"),
    path("attendance/check-in", attendance_check_in, name="hr-attendance-checkin"),
    path("attendance/locations", attendance_locations, name="hr-attendance-locations"),
    path("attendance/locations/<int:location_id>", attendance_location_detail, name="hr-attendance-location-detail"),
    path("attendance/events", attendance_events, name="hr-attendance-events"),
    path("attendance/export", attendance_export, name="hr-attendance-export"),
    path("compensations", compensation_list, name="hr-compensations"),
    path("compensations/<int:employee_id>", compensation_update, name="hr-compensation-update"),
    path("payroll/export", payroll_export, name="hr-payroll-export"),
    path("attendance/events/<int:event_id>/photo", attendance_event_photo, name="hr-attendance-event-photo"),
    path("me/profile", me_profile, name="hr-me-profile"),
    path("me/attendance", me_attendance, name="hr-me-attendance"),
    path("me/leave-requests", me_leave_requests, name="hr-me-leave-requests"),
]
