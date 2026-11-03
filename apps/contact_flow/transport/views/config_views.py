"""CRUD cấu hình Tác nghiệp CSKH (Stage/Result/Transition) + import Excel.

View mỏng theo pattern floating_pool_view.py: 1 permission_classes chung ở mức
`contact_flow:view` (require_section), ghi (POST/PATCH/DELETE/import) tự kiểm thêm
`contact_flow:edit` bằng user_has_any_permission — vì @api_view gộp nhiều method
trong 1 function nên không tách permission_classes theo method được.
"""
from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.upload_guards import validate_xlsx_upload
from apps.contact_flow.application.services.import_service import FlowImportError, FlowImportService
from django.core.cache import cache

from apps.contact_flow.cache_utils import (
    CF_CONFIG_CACHE_TTL,
    bump_config_cache_version,
    get_config_cache_version,
)
from apps.contact_flow.models import OperationResult, OperationStage, OperationTransition
from apps.contact_flow.transport.serializers.flow_serializers import (
    ResultSerializer,
    StageSerializer,
    TransitionSerializer,
)
from apps.users.auth_core import user_has_any_permission
from apps.users.permissions import require_method_permission, require_permission, require_section

_EDIT_CODE = "contact_flow:edit"


def _cache_key(serializer_cls) -> str:
    return f"contact_flow:cfg:{serializer_cls.__name__}:v{get_config_cache_version()}"


def _forbidden_edit() -> Response:
    return Response({"detail": f"Cần quyền {_EDIT_CODE}."}, status=status.HTTP_403_FORBIDDEN)


def _has_edit(request) -> bool:
    return user_has_any_permission(request.user, _EDIT_CODE)


def _list_create(request, queryset, serializer_cls):
    """Shared GET-list / POST-create body for stages/results/transitions."""
    if request.method == "GET":
        # Danh mục gần tĩnh, đọc mỗi lần mở trang contacts — cache versioned-bust
        # (audit 2026-08-19 P2), mọi đường ghi bên dưới đều bump version.
        key = _cache_key(serializer_cls)
        data = cache.get(key)
        if data is None:
            data = serializer_cls(queryset, many=True).data
            cache.set(key, data, CF_CONFIG_CACHE_TTL)
        return Response(data)
    if not _has_edit(request):
        return _forbidden_edit()
    serializer = serializer_cls(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    bump_config_cache_version()
    return Response(serializer.data, status=status.HTTP_201_CREATED)


def _update(request, pk, model, serializer_cls):
    """Shared PATCH body: edit-permission check runs before object lookup."""
    if not _has_edit(request):
        return _forbidden_edit()
    obj = get_object_or_404(model, pk=pk)
    serializer = serializer_cls(obj, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    bump_config_cache_version()
    return Response(serializer.data)


# -- Stages -------------------------------------------------------------
@extend_schema(methods=["GET"], operation_id="contact_flow_stages_list",
                responses={200: StageSerializer(many=True)})
@extend_schema(methods=["POST"], operation_id="contact_flow_stages_create",
                request=StageSerializer,
                responses={201: StageSerializer, 400: OpenApiResponse(description="Dữ liệu không hợp lệ")})
@api_view(["GET", "POST"])
# Section "contacts" được đọc danh sách bước: dropdown cột Tác nghiệp trên trang
# Danh sách contact cần nó, mà sale thì không có section contact_flow. Ghi (POST) vẫn
# đòi `contact_flow:edit` trong `_list_create` nên mở đọc ở đây không nới quyền ghi —
# cùng cách `results_list_create` đã mở cho dropdown cột Kết quả.
@permission_classes([IsAuthenticated, require_method_permission(GET=("contact_flow:view", "contacts:view"), POST="contact_flow:edit")])
def stages_list_create(request):
    return _list_create(request, OperationStage.objects.all(), StageSerializer)


@extend_schema(operation_id="contact_flow_stage_update", request=StageSerializer,
                responses={200: StageSerializer, 400: OpenApiResponse(description="Dữ liệu không hợp lệ")})
@api_view(["PATCH"])
@permission_classes([IsAuthenticated, require_permission("contact_flow:edit")])
def stage_update(request, pk: int):
    return _update(request, pk, OperationStage, StageSerializer)


# -- Results --------------------------------------------------------------
@extend_schema(methods=["GET"], operation_id="contact_flow_results_list",
                responses={200: ResultSerializer(many=True)})
@extend_schema(methods=["POST"], operation_id="contact_flow_results_create",
                request=ResultSerializer,
                responses={201: ResultSerializer, 400: OpenApiResponse(description="Dữ liệu không hợp lệ")})
@api_view(["GET", "POST"])
# Sale ở trang contacts cần đọc danh mục kết quả cho dialog Ghi KQ (final review C1)
@permission_classes([IsAuthenticated, require_method_permission(GET=("contact_flow:view", "contacts:view"), POST="contact_flow:edit")])
def results_list_create(request):
    return _list_create(request, OperationResult.objects.all(), ResultSerializer)


@extend_schema(operation_id="contact_flow_result_update", request=ResultSerializer,
                responses={200: ResultSerializer, 400: OpenApiResponse(description="Dữ liệu không hợp lệ")})
@api_view(["PATCH"])
@permission_classes([IsAuthenticated, require_permission("contact_flow:edit")])
def result_update(request, pk: int):
    return _update(request, pk, OperationResult, ResultSerializer)


# -- Transitions ------------------------------------------------------------
_TRANSITION_QS = OperationTransition.objects.select_related("from_stage", "result", "to_stage")


@extend_schema(methods=["GET"], operation_id="contact_flow_transitions_list",
                responses={200: TransitionSerializer(many=True)})
@extend_schema(methods=["POST"], operation_id="contact_flow_transitions_create",
                request=TransitionSerializer,
                responses={201: TransitionSerializer, 400: OpenApiResponse(description="Dữ liệu không hợp lệ")})
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, require_method_permission(GET="contact_flow:view", POST="contact_flow:edit")])
def transitions_list_create(request):
    return _list_create(request, _TRANSITION_QS.all(), TransitionSerializer)


@extend_schema(methods=["PATCH"], operation_id="contact_flow_transition_update",
                request=TransitionSerializer,
                responses={200: TransitionSerializer, 400: OpenApiResponse(description="Dữ liệu không hợp lệ")})
@extend_schema(methods=["DELETE"], operation_id="contact_flow_transition_delete",
                responses={204: OpenApiResponse(description="Đã xóa")})
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(PATCH="contact_flow:edit", DELETE="contact_flow:edit")])
def transition_update_delete(request, pk: int):
    if not _has_edit(request):
        return _forbidden_edit()
    if request.method == "DELETE":
        transition = get_object_or_404(_TRANSITION_QS, pk=pk)
        transition.delete()
        bump_config_cache_version()
        return Response(status=status.HTTP_204_NO_CONTENT)
    return _update(request, pk, _TRANSITION_QS, TransitionSerializer)


# -- Import Excel -------------------------------------------------------
def _count_fields():
    return {"created": drf_serializers.IntegerField(), "updated": drf_serializers.IntegerField()}


@extend_schema(
    operation_id="contact_flow_import",
    request=inline_serializer("ImportContactFlowRequest", fields={"file": drf_serializers.FileField()}),
    responses={
        200: inline_serializer("ImportContactFlowResponse", fields={
            "stages": inline_serializer("ImportContactFlowStagesCount", fields=_count_fields()),
            "results": inline_serializer("ImportContactFlowResultsCount", fields=_count_fields()),
            "transitions": inline_serializer("ImportContactFlowTransitionsCount", fields=_count_fields()),
        }),
        400: OpenApiResponse(description="Thiếu file hoặc dữ liệu không hợp lệ ({errors: [{sheet,row,error}]})"),
    },
)
@api_view(["POST"])
# Audit 2026-08-19 N2: ghi can quyen ghi, khong gate bang require_section (=:view).
@permission_classes([IsAuthenticated, require_permission("contact_flow:edit")])
def import_workbook(request):
    if not _has_edit(request):
        return _forbidden_edit()
    upload = request.FILES.get("file")
    if upload is None:
        return Response({"detail": "Thiếu file Excel."}, status=status.HTTP_400_BAD_REQUEST)
    # Trần 5MB + đuôi + magic ZIP trước khi đưa vào openpyxl (audit 2026-09-23 L4).
    upload_error = validate_xlsx_upload(upload)
    if upload_error:
        return Response({"detail": upload_error}, status=status.HTTP_400_BAD_REQUEST)
    try:
        report = FlowImportService.import_workbook(upload)
    except FlowImportError as exc:
        return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)
    bump_config_cache_version()
    return Response(report, status=status.HTTP_200_OK)
