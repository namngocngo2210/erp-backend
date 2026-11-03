import pytest

from apps.media.application.services.credential_resolver import resolve
from apps.media.exceptions import MediaDisabled, MediaNotProvisioned
from apps.media.models import MediaConnection
from apps.users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def conn():
    return MediaConnection.objects.create(base_url="https://media.example/", shared_api_key="dak_shared")


def test_personal_key_wins_over_role(conn):
    u = User.objects.create_user(username="a", password="x", media_api_key="dak_me", media_role="viewer")
    cred = resolve(u)
    assert (cred.kind, cred.api_key, cred.role, cred.base_url) == ("personal", "dak_me", None, "https://media.example")


def test_shared_key_with_role(conn):
    u = User.objects.create_user(username="b", password="x", media_role="uploader")
    cred = resolve(u)
    assert (cred.kind, cred.api_key, cred.role) == ("shared", "dak_shared", "uploader")


def test_not_provisioned(conn):
    u = User.objects.create_user(username="c", password="x")
    with pytest.raises(MediaNotProvisioned):
        resolve(u)


def test_disabled_when_no_active_connection():
    u = User.objects.create_user(username="d", password="x", media_api_key="dak_me")
    with pytest.raises(MediaDisabled):
        resolve(u)


def test_shared_mode_requires_shared_key_configured():
    MediaConnection.objects.create(base_url="https://media.example", shared_api_key="")
    u = User.objects.create_user(username="e", password="x", media_role="admin")
    with pytest.raises(MediaDisabled):
        resolve(u)
