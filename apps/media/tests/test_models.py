"""Model Media: connection singleton-ish + bảng owner mô phỏng ownership khi dùng key chung."""

import pytest
from django.db import IntegrityError

from apps.media.models import MediaConnection, MediaContentOwner
from apps.users.models import MediaRole, User

pytestmark = pytest.mark.django_db


def test_get_active_returns_latest_active_connection():
    MediaConnection.objects.create(base_url="https://old.example", shared_api_key="dak_old", is_active=False)
    live = MediaConnection.objects.create(base_url="https://media.example/", shared_api_key="dak_live")
    assert MediaConnection.get_active() == live
    # base_url được chuẩn hoá bỏ dấu "/" cuối khi đọc.
    assert live.resolve_base_url() == "https://media.example"


def test_get_active_none_when_nothing_configured():
    assert MediaConnection.get_active() is None


def test_content_owner_unique_per_remote_id():
    u = User.objects.create_user(username="up1", password="x")
    MediaContentOwner.objects.create(remote_id=12, user=u)
    with pytest.raises(IntegrityError):
        MediaContentOwner.objects.create(remote_id=12, user=u)


def test_user_media_fields_default_blank():
    u = User.objects.create_user(username="u1", password="x")
    assert u.media_role == MediaRole.NONE == ""
    assert u.media_api_key == ""
    assert set(MediaRole.values) == {"", "viewer", "marketing", "uploader", "admin"}
