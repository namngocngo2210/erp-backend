import io
from unittest.mock import patch

import pytest

from apps.exports.application.export_job_service import ExportJobService
from apps.exports.models import ExportJob, ExportJobStatus

pytestmark = pytest.mark.django_db


def test_download_writes_audit_log(fake_export, export_user, export_client):
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS, file_key="exports/x/y.xlsx", file_size=2
    )
    with patch("apps.exports.transport.views.export_storage") as storage, patch(
        "apps.exports.transport.views.record_export_audit"
    ) as audit:
        storage.open_stream.return_value = io.BytesIO(b"ab")
        export_client.get(f"/api/exports/{job.id}/download")
    audit.assert_called_once()


def test_create_writes_audit_log(fake_export, export_client):
    with patch("apps.exports.application.export_job_service.transaction.on_commit"), patch(
        "apps.exports.transport.views.record_export_audit"
    ) as audit:
        export_client.post("/api/exports", {"kind": "fake", "params": {"n": 1}}, format="json")
    audit.assert_called_once()
    assert audit.call_args.kwargs["action"] == "export.create"


def test_create_dedupe_writes_audit_only_once(fake_export, export_client):
    """Dedupe (bấm Xuất lần 2 khi job cũ chưa xong) không tạo job mới -> không thêm
    dòng audit CREATE thứ hai cho cùng job.id (xem fix round 1 — Important 1)."""
    with patch("apps.exports.application.export_job_service.transaction.on_commit"), patch(
        "apps.exports.transport.views.record_export_audit"
    ) as audit:
        first = export_client.post(
            "/api/exports", {"kind": "fake", "params": {"n": 1}}, format="json"
        )
        second = export_client.post(
            "/api/exports", {"kind": "fake", "params": {"n": 1}}, format="json"
        )
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.data["id"] == first.data["id"]
    audit.assert_called_once()
    assert audit.call_args.kwargs["action"] == "export.create"


def test_download_survives_audit_failure(
    fake_export, export_user, export_client, read_streaming_response
):
    """Best-effort: audit hỏng không được làm hỏng lượt tải — file vẫn về 200."""
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS, file_key="exports/x/y.xlsx", file_size=2
    )
    with patch("apps.exports.transport.views.export_storage") as storage, patch(
        "apps.exports.transport.views.write_audit", side_effect=RuntimeError("boom")
    ):
        storage.open_stream.return_value = io.BytesIO(b"ab")
        r = export_client.get(f"/api/exports/{job.id}/download")
    assert r.status_code == 200
    assert read_streaming_response(r) == b"ab"


def test_record_export_audit_writes_expected_payload(fake_export, export_user, export_client):
    """Payload tối thiểu: user, job.id, job.kind, job.file_name, và file_size khi tải."""
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    ExportJob.objects.filter(id=job.id).update(
        status=ExportJobStatus.SUCCESS, file_key="exports/x/y.xlsx", file_size=2
    )
    with patch("apps.exports.transport.views.export_storage") as storage, patch(
        "apps.exports.transport.views.write_audit"
    ) as audit:
        storage.open_stream.return_value = io.BytesIO(b"ab")
        export_client.get(f"/api/exports/{job.id}/download")
    audit.assert_called_once()
    kwargs = audit.call_args.kwargs
    assert kwargs["user_id"] == export_user.id
    assert kwargs["entity_id"] == str(job.id)
    assert kwargs["response_data"]["job_id"] == str(job.id)
    assert kwargs["response_data"]["kind"] == job.kind
    assert kwargs["response_data"]["file_name"] == job.file_name
    assert kwargs["response_data"]["file_size"] == 2
    assert kwargs["metadata"]["action"] == "export.download"
