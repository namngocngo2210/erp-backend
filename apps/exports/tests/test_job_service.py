import pytest
from django.db import IntegrityError, transaction

from apps.exports.application.export_job_service import ExportJobService, TooManyActiveJobs
from apps.exports.models import ExportJob, ExportJobStatus

pytestmark = pytest.mark.django_db


def test_create_returns_new_job(fake_export, export_user):
    job, created = ExportJobService.create(export_user, "fake", {"n": 3})
    assert created is True
    assert job.status == ExportJobStatus.PENDING
    assert job.total_rows == 3
    assert job.file_name == "fake.xlsx"
    assert job.params == {"n": 3}


def test_create_dedupes_identical_active_job(fake_export, export_user):
    first, _ = ExportJobService.create(export_user, "fake", {"n": 3})
    second, created = ExportJobService.create(export_user, "fake", {"n": 3})
    assert created is False
    assert second.id == first.id
    assert ExportJob.objects.count() == 1


def test_create_does_not_dedupe_across_users(fake_export, export_user):
    from apps.users.tests.helpers import make_user_with_sections

    other = make_user_with_sections("export-other", ["orders"])
    ExportJobService.create(export_user, "fake", {"n": 3})
    _, created = ExportJobService.create(other, "fake", {"n": 3})
    assert created is True
    assert ExportJob.objects.count() == 2


def test_create_rejects_when_too_many_active(fake_export, export_user, settings):
    settings.EXPORT_MAX_ACTIVE_JOBS_PER_USER = 2
    ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJobService.create(export_user, "fake", {"n": 2})
    with pytest.raises(TooManyActiveJobs):
        ExportJobService.create(export_user, "fake", {"n": 3})


def test_cancel_sets_status(fake_export, export_user):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 3})
    ExportJobService.cancel(job)
    job.refresh_from_db()
    assert job.status == ExportJobStatus.CANCELLED
    assert job.finished_at is not None


def test_mark_success_records_file(fake_export, export_user):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 3})
    ExportJobService.mark_success(job.id, "exports/1/f.xlsx", 120, 3)
    job.refresh_from_db()
    assert job.status == ExportJobStatus.SUCCESS
    assert job.file_key == "exports/1/f.xlsx"
    assert job.file_size == 120
    assert job.progress_rows == 3


def test_duplicate_active_job_blocked_by_db_constraint(fake_export, export_user):
    """Chèn thẳng bản ghi trùng, bỏ qua create(), để chứng minh DB chặn chứ không phải logic ứng dụng."""
    job, _ = ExportJobService.create(export_user, "fake", {"n": 3})
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ExportJob.objects.create(
                user=export_user,
                kind="fake",
                params=job.params,
                params_hash=job.params_hash,
                file_name="fake.xlsx",
                expires_at=job.expires_at,
            )


def test_create_after_previous_job_finished_is_not_blocked(fake_export, export_user):
    """Job cũ đã SUCCESS không nằm trong ACTIVE_STATUSES nên không chặn lượt xuất mới cùng params."""
    first, _ = ExportJobService.create(export_user, "fake", {"n": 3})
    ExportJobService.mark_success(first.id, "exports/1/f.xlsx", 120, 3)

    second, created = ExportJobService.create(export_user, "fake", {"n": 3})
    assert created is True
    assert second.id != first.id
    assert ExportJob.objects.count() == 2
