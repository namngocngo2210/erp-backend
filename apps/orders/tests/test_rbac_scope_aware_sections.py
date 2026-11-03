"""Khoá danh sách section thực sự được resolve data-scope (audit 2026-07-26).

Command `audit_rbac_scope` (apps/users) báo cáo cho người vận hành biết section nào
đặt scope mới có tác dụng. Nếu ai đó thêm/bớt call site resolve scope mà quên cập
nhật danh sách đó, command sẽ im lặng báo cáo SAI — người vận hành tưởng đã siết
trong khi không siết gì, hoặc tốn công siết một section vô tác dụng.

Vì sao test nằm ở `apps/orders` chứ không nằm cạnh command: `apps.users` nằm DƯỚI
`apps.orders` trong contract phân tầng (`.importlinter`), nên test ở `apps/users`
không được phép import `apps.orders`. Chiều ngược lại thì hợp lệ.
"""
from django.test import SimpleTestCase

from apps.orders.transport.views.order_views_impl._view_helpers import (
    _SECTION_ORDER_IDENTITY_Q,
)
from apps.users.management.commands.audit_rbac_scope import SCOPE_AWARE_SECTIONS


class ScopeAwareSectionsContractTests(SimpleTestCase):
    def test_scope_aware_sections_match_code_reality(self):
        # 8 section của record-scope đơn hàng + 2 section được resolve ở nơi khác:
        # `contacts` (contact_list_service.py) và `ads-report` (_repositories_reports.py).
        expected = set(_SECTION_ORDER_IDENTITY_Q) | {"contacts", "ads-report"}
        self.assertEqual(
            set(SCOPE_AWARE_SECTIONS),
            expected,
            "SCOPE_AWARE_SECTIONS trong audit_rbac_scope đã lệch khỏi thực tế code. "
            "Thêm call site resolve scope thì phải cập nhật cả danh sách đó.",
        )
