import pytest
from django.utils import timezone

from apps.exports.models import ExportJob, ExportJobStatus
from apps.users.tests.helpers import make_user_with_sections

pytestmark = pytest.mark.django_db


def test_job_defaults_to_pending():
    user = make_user_with_sections("exp-1", ["orders"])
    job = ExportJob.objects.create(
        user=user,
        kind="orders",
        params={"section": "orders"},
        params_hash="a" * 64,
        file_name="orders.xlsx",
        expires_at=timezone.now(),
    )
    assert job.status == ExportJobStatus.PENDING
    assert job.progress_rows == 0
    assert job.id is not None


def test_is_downloadable_only_when_success_and_not_expired():
    user = make_user_with_sections("exp-2", ["orders"])
    job = ExportJob.objects.create(
        user=user,
        kind="orders",
        params={},
        params_hash="b" * 64,
        file_name="orders.xlsx",
        expires_at=timezone.now() + timezone.timedelta(hours=1),
        status=ExportJobStatus.SUCCESS,
        file_key="exports/1/x/orders.xlsx",
    )
    assert job.is_downloadable() is True

    job.expires_at = timezone.now() - timezone.timedelta(seconds=1)
    assert job.is_downloadable() is False
