"""Ma trận quyền Media — mirror §3.3 tài liệu ads-crm. Thuần Python, không Django.

Dùng khi request đi bằng KEY CHUNG: ads-crm chỉ thấy một tài khoản service nên
desi-order phải tự chặn. Với key riêng, ads-crm enforce — module này chỉ còn dùng
để frontend ẩn nút (qua /api/media/me).
"""

from __future__ import annotations

from enum import Enum

VIEWER = "viewer"
MARKETING = "marketing"
UPLOADER = "uploader"
ADMIN = "admin"
ROLES: tuple[str, ...] = (VIEWER, MARKETING, UPLOADER, ADMIN)


class Action(str, Enum):
    READ = "read"
    CONTENT_CREATE = "content.create"
    CONTENT_EDIT_UPLOAD = "content.edit_upload"
    CONTENT_EDIT_MARKETING = "content.edit_marketing"
    CONTENT_DELETE = "content.delete"
    VIDEO_CACHE_FORCE = "video.cache_force"
    EVALUATION_WRITE = "evaluation.write"
    TAXONOMY_WRITE = "taxonomy.write"
    TAXONOMY_WRITE_SOURCES = "taxonomy.write_sources"
    TAXONOMY_INCLUDE_INACTIVE = "taxonomy.include_inactive"
    PROPOSAL_CREATE = "proposal.create"
    PROPOSAL_EDIT = "proposal.edit"
    PROPOSAL_DELETE = "proposal.delete"


class Verdict(Enum):
    ALLOW = "allow"
    DENY_ROLE = "deny_role"
    DENY_OWNER = "deny_owner"


_VIEWER = frozenset({Action.READ, Action.PROPOSAL_CREATE, Action.PROPOSAL_EDIT})
ROLE_MATRIX: dict[str, frozenset[Action]] = {
    VIEWER: _VIEWER,
    MARKETING: _VIEWER | {Action.CONTENT_EDIT_MARKETING, Action.EVALUATION_WRITE},
    UPLOADER: _VIEWER
    | {
        Action.CONTENT_CREATE,
        Action.CONTENT_EDIT_UPLOAD,
        Action.CONTENT_EDIT_MARKETING,
        Action.TAXONOMY_WRITE_SOURCES,
    },
    ADMIN: frozenset(Action),
}

# (role, action) chỉ được phép trên bản ghi do chính user tạo. Admin không bao giờ bị giới hạn.
OWNER_SCOPED: frozenset[tuple[str, Action]] = frozenset(
    {
        (UPLOADER, Action.CONTENT_EDIT_UPLOAD),
        (UPLOADER, Action.CONTENT_EDIT_MARKETING),
        (VIEWER, Action.PROPOSAL_EDIT),
        (MARKETING, Action.PROPOSAL_EDIT),
        (UPLOADER, Action.PROPOSAL_EDIT),
    }
)


def is_owner_scoped(role: str, action: Action) -> bool:
    return (role, action) in OWNER_SCOPED


def check(role: str, action: Action, *, is_owner: bool = False) -> Verdict:
    allowed = ROLE_MATRIX.get(role)
    if allowed is None or action not in allowed:
        return Verdict.DENY_ROLE
    if is_owner_scoped(role, action) and not is_owner:
        return Verdict.DENY_OWNER
    return Verdict.ALLOW
