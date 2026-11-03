"""Ma trận role Media phải khớp §3.3 tài liệu ads-crm (spec §5.2)."""

import pytest

from apps.media.domain.roles import ROLES, Action, Verdict, check
from apps.users.models import MediaRole

FULL = set(Action)
VIEWER = {Action.READ, Action.PROPOSAL_CREATE, Action.PROPOSAL_EDIT}
MARKETING = VIEWER | {Action.CONTENT_EDIT_MARKETING, Action.EVALUATION_WRITE}
UPLOADER = VIEWER | {
    Action.CONTENT_CREATE, Action.CONTENT_EDIT_UPLOAD, Action.CONTENT_EDIT_MARKETING,
    Action.TAXONOMY_WRITE_SOURCES,
}
EXPECTED = {"viewer": VIEWER, "marketing": MARKETING, "uploader": UPLOADER, "admin": FULL}


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("action", list(Action))
def test_matrix_allows_exactly_expected_actions(role, action):
    verdict = check(role, action, is_owner=True)
    assert (verdict is Verdict.ALLOW) == (action in EXPECTED[role])


@pytest.mark.parametrize(
    "role,action",
    [
        ("uploader", Action.CONTENT_EDIT_UPLOAD),
        ("uploader", Action.CONTENT_EDIT_MARKETING),
        ("viewer", Action.PROPOSAL_EDIT),
        ("marketing", Action.PROPOSAL_EDIT),
        ("uploader", Action.PROPOSAL_EDIT),
    ],
)
def test_owner_scoped_actions_deny_non_owner(role, action):
    assert check(role, action, is_owner=False) is Verdict.DENY_OWNER
    assert check(role, action, is_owner=True) is Verdict.ALLOW


def test_admin_never_owner_scoped():
    assert check("admin", Action.PROPOSAL_EDIT, is_owner=False) is Verdict.ALLOW


def test_unknown_or_blank_role_denied():
    assert check("", Action.READ) is Verdict.DENY_ROLE
    assert check("boss", Action.READ) is Verdict.DENY_ROLE


def test_roles_mirror_user_enum():
    assert set(ROLES) == set(MediaRole.values) - {""}
