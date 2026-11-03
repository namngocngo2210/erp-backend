"""Retention ảnh chấm công MinIO (audit followups 2026-07 mục 3.1).

Chỉ object MinIO + `photo_key` bị xoá; AttendanceEvent (giờ vào/ra) LUÔN được giữ.
"""
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from apps.hr.models import AttendanceEvent, AttendanceEventKind
from apps.hr.tests.factories import create_employee

pytestmark = pytest.mark.django_db


@pytest.fixture
def employee():
    return create_employee(employee_code="NV-PURGE", full_name="Purge Test")


def _make_event(employee, *, days_old, photo_key="k.jpg", kind=AttendanceEventKind.CHECK_IN):
    occurred_at = timezone.now() - timedelta(days=days_old)
    return AttendanceEvent.objects.create(
        employee=employee, kind=kind, occurred_at=occurred_at,
        work_date=occurred_at.date(), photo_key=photo_key,
        latitude=10.0, longitude=106.0, distance_m=5,
    )


def test_purges_old_photo_keeps_event(settings, employee):
    from apps.hr.tasks import purge_old_attendance_photos

    settings.ATTENDANCE_PHOTO_RETENTION_DAYS = 180
    old = _make_event(employee, days_old=200, kind=AttendanceEventKind.CHECK_IN)
    recent = _make_event(employee, days_old=10, kind=AttendanceEventKind.CHECK_OUT)

    with mock.patch(
        "apps.hr.infrastructure.photo_storage.delete_photo", return_value=True
    ) as del_mock:
        result = purge_old_attendance_photos()

    old.refresh_from_db()
    recent.refresh_from_db()
    assert result == {"deleted": 1, "failed": 0}
    assert old.photo_key == ""
    assert recent.photo_key == "k.jpg"
    # Event row itself is kept — only the photo reference is cleared.
    assert AttendanceEvent.objects.filter(pk=old.pk).exists()
    del_mock.assert_called_once_with("k.jpg")


def test_retention_zero_disables_purge(settings, employee):
    from apps.hr.tasks import purge_old_attendance_photos

    settings.ATTENDANCE_PHOTO_RETENTION_DAYS = 0
    old = _make_event(employee, days_old=400)

    with mock.patch("apps.hr.infrastructure.photo_storage.delete_photo") as del_mock:
        result = purge_old_attendance_photos()

    old.refresh_from_db()
    assert result == {"deleted": 0, "failed": 0}
    assert old.photo_key == "k.jpg"
    del_mock.assert_not_called()


def test_delete_failure_keeps_photo_key_for_retry(settings, employee):
    from apps.hr.tasks import purge_old_attendance_photos

    settings.ATTENDANCE_PHOTO_RETENTION_DAYS = 180
    old = _make_event(employee, days_old=200)

    with mock.patch(
        "apps.hr.infrastructure.photo_storage.delete_photo", return_value=False
    ):
        result = purge_old_attendance_photos()

    old.refresh_from_db()
    assert result == {"deleted": 0, "failed": 1}
    # MinIO delete failed (network/permission) → keep photo_key so next run retries;
    # clearing it here would orphan the object in MinIO forever, untracked.
    assert old.photo_key == "k.jpg"
    assert AttendanceEvent.objects.filter(pk=old.pk).exists()


def test_skips_when_lock_held(settings, employee):
    from apps.hr import tasks as hr_tasks

    settings.ATTENDANCE_PHOTO_RETENTION_DAYS = 180
    old = _make_event(employee, days_old=200)

    with mock.patch("apps.hr.tasks.single_flight") as sf:
        sf.return_value.__enter__.return_value = False
        result = hr_tasks.purge_old_attendance_photos()

    assert result == {"deleted": -1, "failed": 0}
    old.refresh_from_db()
    assert old.photo_key == "k.jpg"


def test_ignores_events_without_photo(settings, employee):
    from apps.hr.tasks import purge_old_attendance_photos

    settings.ATTENDANCE_PHOTO_RETENTION_DAYS = 180
    _make_event(employee, days_old=200, photo_key="")

    with mock.patch("apps.hr.infrastructure.photo_storage.delete_photo") as del_mock:
        result = purge_old_attendance_photos()

    assert result == {"deleted": 0, "failed": 0}
    del_mock.assert_not_called()


def test_purges_in_batches(settings, employee):
    from apps.hr.tasks import purge_old_attendance_photos

    settings.ATTENDANCE_PHOTO_RETENTION_DAYS = 180
    # work_date khác nhau (days_old khác nhau) để không đụng unique
    # (employee, work_date, kind).
    for days_old in (200, 201, 202):
        _make_event(employee, days_old=days_old)

    with mock.patch(
        "apps.hr.infrastructure.photo_storage.delete_photo", return_value=True
    ) as del_mock:
        result = purge_old_attendance_photos(batch_size=1)

    assert result == {"deleted": 3, "failed": 0}
    assert del_mock.call_count == 3
    assert not AttendanceEvent.objects.exclude(photo_key="").exists()
    assert AttendanceEvent.objects.count() == 3  # events themselves are kept


def test_delete_photo_returns_false_on_network_error():
    """BotoCoreError (EndpointConnectionError...) là hierarchy RIÊNG, không phải
    subclass của ClientError — delete_photo phải nuốt cả hai và trả False, nếu
    không MinIO sập sẽ làm exception xuyên thủng purge task giữa batch."""
    from botocore.exceptions import EndpointConnectionError

    from apps.hr.infrastructure import photo_storage

    client = mock.Mock()
    client.delete_object.side_effect = EndpointConnectionError(
        endpoint_url="http://minio:9000"
    )
    with mock.patch.object(photo_storage, "_client", return_value=client):
        assert photo_storage.delete_photo("k.jpg") is False


def test_delete_photo_returns_false_on_client_error():
    from botocore.exceptions import ClientError

    from apps.hr.infrastructure import photo_storage

    client = mock.Mock()
    client.delete_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "DeleteObject"
    )
    with mock.patch.object(photo_storage, "_client", return_value=client):
        assert photo_storage.delete_photo("k.jpg") is False


def test_delete_photo_returns_true_on_success():
    from apps.hr.infrastructure import photo_storage

    client = mock.Mock()
    with mock.patch.object(photo_storage, "_client", return_value=client):
        assert photo_storage.delete_photo("k.jpg") is True
    client.delete_object.assert_called_once()


def test_purge_continues_when_minio_down(settings, employee):
    """MinIO sập giữa chừng: task không crash, đếm failed, giữ photo_key để retry."""
    from botocore.exceptions import EndpointConnectionError

    from apps.hr.tasks import purge_old_attendance_photos

    settings.ATTENDANCE_PHOTO_RETENTION_DAYS = 180
    old_a = _make_event(employee, days_old=200)
    old_b = _make_event(employee, days_old=201)

    client = mock.Mock()
    client.delete_object.side_effect = EndpointConnectionError(
        endpoint_url="http://minio:9000"
    )
    with mock.patch(
        "apps.hr.infrastructure.photo_storage._client", return_value=client
    ):
        result = purge_old_attendance_photos()

    old_a.refresh_from_db()
    old_b.refresh_from_db()
    assert result == {"deleted": 0, "failed": 2}
    assert old_a.photo_key == "k.jpg"
    assert old_b.photo_key == "k.jpg"
