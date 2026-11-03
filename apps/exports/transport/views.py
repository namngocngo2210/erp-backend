import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.http_streaming import streaming_file_response
from apps.dispatch import write_audit
from apps.exports.application.export_job_service import ExportJobService, TooManyActiveJobs
from apps.exports.application.registry import UnknownExportKind
from apps.exports.content_types import guess_content_type
from apps.exports.infrastructure import export_storage
from apps.exports.models import ACTIVE_STATUSES, ExportJob
from apps.exports.transport.serializers import ExportCreateSerializer, ExportJobSerializer
from apps.exports.transport.throttling import ExportCreateThrottle

logger = logging.getLogger(__name__)

_LIST_LIMIT = 20

# Action -> action_type hợp lệ của AuditLog (xem apps.audit.models.ACTION_TYPES).
# Không có entity_type riêng cho export trong enum sẵn có nên dùng "OTHER" —
# chuỗi action gốc ("export.create"/"export.download") được giữ nguyên trong
# metadata để không mất thông tin phân loại.
_AUDIT_ACTION_TYPE = {
    "export.create": "CREATE",
    "export.download": "READ",
}


def record_export_audit(request, job, action: str) -> None:
    """Ghi audit cho một lượt tạo/tải file export — best-effort.

    File xuất mang dữ liệu cá nhân khách hàng ở quy mô lớn; đây là vết duy nhất
    trả lời "ai đã lấy dữ liệu gì, lúc nào". Nuốt lỗi + log giống cách
    ``publish_export_event`` đang làm (xem infrastructure/event_publisher.py):
    audit hỏng không được phép làm hỏng lượt tải/lượt tạo thật.
    """
    try:
        user = getattr(request, "user", None)
        user_id = user.id if user is not None and user.is_authenticated else None
        response_data = {
            "job_id": str(job.id),
            "kind": job.kind,
            "file_name": job.file_name,
        }
        if action == "export.download":
            response_data["file_size"] = job.file_size
        write_audit(
            user_id=user_id,
            action_type=_AUDIT_ACTION_TYPE.get(action, "API_REQUEST"),
            entity_type="OTHER",
            entity_id=str(job.id),
            method=request.method,
            ip_address=getattr(request, "ip_address", None) or request.META.get("REMOTE_ADDR"),
            response_data=response_data,
            duration_ms=None,
            source="user_action",
            curl_command=None,
            metadata={"action": action},
        )
    except Exception:
        logger.exception("Không ghi được audit export job_id=%s action=%s", job.id, action)


def _create_job(request):
    """Thân xử lý POST. Là hàm THƯỜNG (không decorator) vì `GET /api/exports` và
    `POST /api/exports` dùng chung một path nên chỉ có MỘT view DRF ở đó."""
    serializer = ExportCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    kind = serializer.validated_data["kind"]
    params = serializer.validated_data.get("params") or {}

    try:
        job, created = ExportJobService.create(request.user, kind, params)
    except UnknownExportKind:
        return Response(
            {"detail": "Loại xuất file không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST
        )
    except TooManyActiveJobs:
        return Response(
            {"detail": "Bạn đang có quá nhiều file chờ xử lý. Đợi xong rồi xuất tiếp."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    except PermissionDenied as exc:
        return Response({"detail": str(exc.detail)}, status=status.HTTP_403_FORBIDDEN)
    except ValidationError:
        raise

    if created:
        ExportJobService.enqueue(job)
        # Chỉ ghi audit lượt tạo THẬT. Dedupe (created=False) trả lại job cũ, không
        # tạo gì mới — ghi CREATE ở nhánh này sẽ nhân đôi audit cho cùng một job.id
        # mỗi lần người dùng bấm lại trong lúc job cũ chưa xong, thổi phồng số liệu
        # "đã tạo bao nhiêu job" và làm người tra audit không phân biệt được lượt tạo
        # thật với lượt bị chặn double-click.
        record_export_audit(request, job, action="export.create")
    return Response(
        ExportJobSerializer(job).data,
        # Dedupe trả 200 (không tạo gì mới) để FE phân biệt được với lượt tạo thật.
        status=status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK,
    )


@extend_schema(exclude=True)
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
@throttle_classes([ExportCreateThrottle])
def exports_collection(request):
    """GET = danh sách job của chính mình; POST = tạo job mới."""
    if request.method == "GET":
        queryset = ExportJob.objects.filter(user=request.user)
        if request.query_params.get("active") == "1":
            queryset = queryset.filter(status__in=ACTIVE_STATUSES)
        jobs = queryset.order_by("-created_at")[:_LIST_LIMIT]
        return Response({"items": ExportJobSerializer(jobs, many=True).data})
    return _create_job(request)


@extend_schema(exclude=True)
@api_view(["GET", "DELETE"])
@permission_classes([IsAuthenticated])
def export_detail(request, job_id):
    job = ExportJob.objects.filter(id=job_id, user=request.user).first()
    if job is None:
        return Response({"detail": "Không tìm thấy."}, status=status.HTTP_404_NOT_FOUND)
    if request.method == "DELETE":
        ExportJobService.cancel(job)
        return Response(status=status.HTTP_204_NO_CONTENT)
    return Response(ExportJobSerializer(job).data)


@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_export(request, job_id):
    # Lọc luôn theo user trong query: file của người khác phải là 404 chứ không phải
    # 403 — 403 xác nhận job đó tồn tại, và id là UUID nên 404 không mơ hồ với ai cả.
    job = ExportJob.objects.filter(id=job_id, user=request.user).first()
    if job is None:
        return Response({"detail": "Không tìm thấy file."}, status=status.HTTP_404_NOT_FOUND)
    if not job.is_downloadable():
        return Response(
            {"detail": "File đã hết hạn hoặc chưa sẵn sàng."}, status=status.HTTP_410_GONE
        )
    try:
        body = export_storage.open_stream(job.file_key)
    except Exception:
        logger.exception("Không mở được file export %s", job.id)
        return Response(
            {"detail": "Không đọc được file."}, status=status.HTTP_502_BAD_GATEWAY
        )
    record_export_audit(request, job, action="export.download")
    # KHÔNG dùng FileResponse: nó nhận file-like đồng bộ, mà prod chạy UvicornWorker
    # nên Django sẽ gom trọn file vào RAM trước khi gửi byte đầu (xem
    # apps.common.http_streaming). Ở đây file export chính là thứ không được phép
    # nạp hết vào RAM — cả apps/exports/infrastructure/export_storage.py sinh ra
    # để tránh đúng chuyện đó.
    return streaming_file_response(
        body,
        content_type=guess_content_type(job.file_name),
        filename=job.file_name,
        as_attachment=True,
        content_length=job.file_size,
    )
