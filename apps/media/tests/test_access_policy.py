import pytest

from apps.media.application.services.access_policy import authorize
from apps.media.contracts.dtos import Credential
from apps.media.domain.roles import Action
from apps.media.exceptions import MediaForbidden, MediaNotOwner

BASE = "https://media.example"


def test_personal_credential_is_never_checked_locally():
    cred = Credential(kind="personal", api_key="dak_me", role=None, base_url=BASE)
    authorize(cred, Action.CONTENT_DELETE)  # không raise — ads-crm enforce


def test_shared_role_denied_action_raises_forbidden():
    cred = Credential(kind="shared", api_key="dak_s", role="viewer", base_url=BASE)
    with pytest.raises(MediaForbidden):
        authorize(cred, Action.EVALUATION_WRITE)


def test_shared_owner_scoped_uses_owner_check_lazily():
    cred = Credential(kind="shared", api_key="dak_s", role="uploader", base_url=BASE)
    calls = []

    def owner_check():
        calls.append(1)
        return False

    with pytest.raises(MediaNotOwner):
        authorize(cred, Action.CONTENT_EDIT_UPLOAD, owner_check=owner_check)
    assert calls == [1]
    # Admin: không gọi owner_check.
    admin = Credential(kind="shared", api_key="dak_s", role="admin", base_url=BASE)
    authorize(admin, Action.CONTENT_EDIT_UPLOAD, owner_check=lambda: (_ for _ in ()).throw(AssertionError()))


def test_owner_scoped_without_check_is_denied():
    cred = Credential(kind="shared", api_key="dak_s", role="uploader", base_url=BASE)
    with pytest.raises(MediaNotOwner):
        authorize(cred, Action.CONTENT_EDIT_UPLOAD)
