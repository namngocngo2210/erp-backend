from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.exports.application.export_job_service import ExportJobService
from apps.exports.models import ExportJob, ExportJobStatus
from apps.users.tests.helpers import make_user_with_sections

pytestmark = pytest.mark.django_db


def test_create_returns_202_and_job(fake_export, export_client):
    with patch("apps.exports.application.export_job_service.transaction.on_commit"):
        r = export_client.post("/api/exports", {"kind": "fake", "params": {"n": 3}}, format="json")
    assert r.status_code == 202
    assert r.data["kind"] == "fake"
    assert r.data["status"] == "pending"
    assert r.data["total_rows"] == 3


def test_create_unknown_kind_returns_400(export_client):
    r = export_client.post("/api/exports", {"kind": "khong-co", "params": {}}, format="json")
    assert r.status_code == 400


def test_create_dedupe_returns_200_with_same_id(fake_export, export_client):
    with patch("apps.exports.application.export_job_service.transaction.on_commit"):
        first = export_client.post(
            "/api/exports", {"kind": "fake", "params": {"n": 3}}, format="json"
        )
        second = export_client.post(
            "/api/exports", {"kind": "fake", "params": {"n": 3}}, format="json"
        )
    assert second.status_code == 200
    assert second.data["id"] == first.data["id"]


def test_list_returns_only_own_jobs(fake_export, export_user, export_client):
    other = make_user_with_sections("export-other-2", ["orders"])
    ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJobService.create(other, "fake", {"n": 1})
    r = export_client.get("/api/exports")
    assert r.status_code == 200
    assert len(r.data["items"]) == 1


def test_download_forbidden_for_other_user(fake_export, export_user):
    from rest_framework.test import APIClient

    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS, file_key="exports/x/y.xlsx"
    )
    other = make_user_with_sections("export-other-3", ["orders"])
    client = APIClient()
    client.force_authenticate(other)
    r = client.get(f"/api/exports/{job.id}/download")
    assert r.status_code == 404


def test_download_expired_returns_410(fake_export, export_user, export_client):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS,
        file_key="exports/x/y.xlsx",
        expires_at=timezone.now() - timezone.timedelta(seconds=1),
    )
    r = export_client.get(f"/api/exports/{job.id}/download")
    assert r.status_code == 410


def test_download_streams_file(fake_export, export_user, export_client):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS, file_key="exports/x/y.xlsx", file_size=2
    )
    with patch("apps.exports.transport.views.export_storage") as storage:
        storage.open_stream.return_value = __import__("io").BytesIO(b"ab")
        r = export_client.get(f"/api/exports/{job.id}/download")
    assert r.status_code == 200
    assert "attachment" in r["Content-Disposition"]


def test_cancel_sets_cancelled(fake_export, export_user, export_client):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    r = export_client.delete(f"/api/exports/{job.id}")
    assert r.status_code == 204
    job.refresh_from_db()
    assert job.status == ExportJobStatus.CANCELLED


def test_download_streams_async_without_buffering_whole_file(
    fake_export, export_user, export_client, read_streaming_response
):
    """Response tải file phải là async iterator — không chỉ "tải được là xong".

    Prod chạy gunicorn -k UvicornWorker nên response đi đường async. Nếu body là
    iterator ĐỒNG BỘ, Django rơi vào nhánh dự phòng của
    `StreamingHttpResponse.__aiter__` là `sync_to_async(list)(...)`: nạp TRỌN file
    vào RAM rồi mới gửi byte đầu tiên — đúng thứ mà cả thiết kế export bằng
    stream sinh ra để tránh (xem apps/exports/infrastructure/export_storage.py).

    Test tải file thông thường KHÔNG bắt được: client test của Django chạy đường
    sync, ở đó iterator đồng bộ hoạt động bình thường. Chỉ `is_async` mới chốt được.
    """
    import io

    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS, file_key="exports/x/y.xlsx", file_size=2
    )
    with patch("apps.exports.transport.views.export_storage") as storage:
        storage.open_stream.return_value = io.BytesIO(b"ab")
        r = export_client.get(f"/api/exports/{job.id}/download")

    assert r.status_code == 200
    assert r.is_async is True
    assert read_streaming_response(r) == b"ab"


def test_download_keeps_file_name_and_content_type(fake_export, export_user, export_client):
    """Header của lượt tải phải giữ nguyên như thời còn dùng FileResponse.

    FileResponse chỉ dựng Content-Disposition/Content-Type ở nhánh nhận file-like;
    chuyển sang stream async là tự dựng header, nên chốt lại ở đây — tên file
    tiếng Việt có dấu phải mã hoá theo RFC 6266 chứ không rơi mất.
    """
    import io

    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS,
        file_key="exports/x/y.xlsx",
        file_name="đơn hàng.xlsx",
        file_size=2,
    )
    with patch("apps.exports.transport.views.export_storage") as storage:
        storage.open_stream.return_value = io.BytesIO(b"ab")
        r = export_client.get(f"/api/exports/{job.id}/download")

    assert r.status_code == 200
    assert r["Content-Disposition"] == "attachment; filename*=utf-8''%C4%91%C6%A1n%20h%C3%A0ng.xlsx"
    assert r["Content-Type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert r["Content-Length"] == "2"
