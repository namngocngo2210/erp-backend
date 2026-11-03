"""API cấu hình chia đơn tự động (ma trận NV × sheet) + workload + transfer."""
from __future__ import annotations

from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.orders.application.services.lead_assignment_service import LeadAssignmentService
from apps.orders.application.services.shift_service import ShiftService, shift_intervals
from apps.orders.models import LeadAssignmentShare, ShiftWindow
from apps.orders.sheet_rules import ASSIGNMENT_SHEETS, RESALE_SHEET, SHEET_CHOICES, SHEET_LABELS
from apps.users.constants import DepartmentID
from apps.users.models import User
from apps.users.permissions import can_assign_sale, require_method_permission, require_permission, require_section

SHIFT_ORDER = [s[0] for s in ShiftWindow.SHIFT_CHOICES]  # ["morning","afternoon","evening"]


def _display(user) -> str:
    return getattr(user, "display_name", "") or user.username


def _has_foxia_id(user) -> bool:
    """Cơ hội resale gán sale bằng id nhân viên BÊN FOXIA (`User.foxia_id`, dạng số)
    — người không có id thì chia trúng cũng không gán được bên Foxia."""
    return str(getattr(user, "foxia_id", "") or "").strip().isdigit()


def _sales_staff_qs():
    """Phạm vi của tính năng chia đơn: chỉ nhân viên phòng Sale đang hoạt động.

    NOTE: bỏ filter role=STAFF cũ; department_id=SALES đã đủ phân định (admin
    không gán vào phòng Sales).
    """
    return User.objects.filter(
        department_id=DepartmentID.SALES,
        is_active=True,
        deleted_at__isnull=True,
    )


def _serialize_day(date, shift):
    shares = list(
        LeadAssignmentShare.objects.select_related("user").filter(date=date, shift=shift)
    )
    total_w = dict.fromkeys(ASSIGNMENT_SHEETS, 0)
    total_a = dict.fromkeys(ASSIGNMENT_SHEETS, 0)
    for sh in shares:
        total_w[sh.sheet] += sh.weight
        total_a[sh.sheet] += sh.assigned_count

    by_user: dict[int, dict] = {}
    for sh in shares:
        row = by_user.setdefault(
            sh.user_id,
            {
                "user_id": sh.user_id,
                "username": sh.user.username,
                "display_name": _display(sh.user),
                "cells": {
                    s: {"weight": 0, "assigned_count": 0, "target_pct": 0.0, "actual_pct": 0.0}
                    for s in ASSIGNMENT_SHEETS
                },
            },
        )
        cell = row["cells"][sh.sheet]
        cell["weight"] = sh.weight
        cell["assigned_count"] = sh.assigned_count
        cell["target_pct"] = round(sh.weight / total_w[sh.sheet] * 100, 1) if total_w[sh.sheet] else 0.0
        cell["actual_pct"] = (
            round(sh.assigned_count / total_a[sh.sheet] * 100, 1) if total_a[sh.sheet] else 0.0
        )

    return {
        "date": date.isoformat(),
        "shift": shift,
        "current_shift": ShiftService.current_shift(),
        "sheets": [
            {"key": s, "label": SHEET_LABELS[s], "total_weight": total_w[s]} for s in ASSIGNMENT_SHEETS
        ],
        "rows": sorted(by_user.values(), key=lambda r: r["username"]),
    }


class _RowSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    weights = serializers.DictField(child=serializers.IntegerField(min_value=0))


class _UpsertSerializer(serializers.Serializer):
    date = serializers.DateField()
    shift = serializers.ChoiceField(choices=SHIFT_ORDER)
    rows = _RowSerializer(many=True)

    def validate_rows(self, rows):
        user_ids = [r["user_id"] for r in rows]
        if len(user_ids) != len(set(user_ids)):
            raise serializers.ValidationError("Trùng nhân viên trong danh sách.")
        existing = set(_sales_staff_qs().filter(id__in=user_ids).values_list("id", flat=True))
        missing = [uid for uid in user_ids if uid not in existing]
        if missing:
            raise serializers.ValidationError(
                f"Nhân viên không thuộc phòng Sale hoặc không tồn tại: {missing}"
            )
        for r in rows:
            bad = [k for k in r["weights"] if k not in ASSIGNMENT_SHEETS]
            if bad:
                raise serializers.ValidationError(f"Sheet không hợp lệ: {bad}")
        resale_ids = [r["user_id"] for r in rows if int(r["weights"].get(RESALE_SHEET, 0) or 0) > 0]
        if resale_ids:
            no_foxia = [u.username for u in User.objects.filter(id__in=resale_ids) if not _has_foxia_id(u)]
            if no_foxia:
                raise serializers.ValidationError(
                    f"Nhân viên chưa khai Foxia ID (dạng số) không nhận được cơ hội resale: {sorted(no_foxia)}"
                )
        return rows


# GET/PUT trả ma trận chia đơn của 1 ngày-ca (sheets + rows theo NV);
# DELETE xoá cấu hình 1 ngày → {deleted, date}. PUT nhận body upsert.
# Shape sheets/rows khai tường minh theo _serialize_day (WP6.2).
@extend_schema(
    request=_UpsertSerializer,
    responses={
        200: inline_serializer(
            "LeadAssignmentDayResponse",
            fields={
                "date": drf_serializers.CharField(),
                "shift": drf_serializers.CharField(),
                "current_shift": drf_serializers.CharField(allow_null=True),
                "sheets": inline_serializer(
                    "LeadAssignmentSheetItem",
                    fields={
                        "key": drf_serializers.CharField(),
                        "label": drf_serializers.CharField(),
                        "total_weight": drf_serializers.IntegerField(),
                    },
                    many=True,
                ),
                "rows": inline_serializer(
                    "LeadAssignmentRowItem",
                    fields={
                        "user_id": drf_serializers.IntegerField(),
                        "username": drf_serializers.CharField(),
                        "display_name": drf_serializers.CharField(allow_blank=True),
                        # cells: {sheet_key: {weight, assigned_count, target_pct, actual_pct}}
                        "cells": drf_serializers.DictField(
                            child=inline_serializer(
                                "LeadAssignmentCell",
                                fields={
                                    "weight": drf_serializers.IntegerField(),
                                    "assigned_count": drf_serializers.IntegerField(),
                                    "target_pct": drf_serializers.FloatField(),
                                    "actual_pct": drf_serializers.FloatField(),
                                },
                            )
                        ),
                    },
                    many=True,
                ),
            },
        ),
        400: OpenApiResponse(description="shift không hợp lệ"),
    },
)
@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated, require_method_permission(GET="lead-assignment:view", PUT="lead-assignment:edit", DELETE="lead-assignment:edit")])
def lead_assignment_shares(request):
    if request.method == "GET":
        raw = request.query_params.get("date")
        date = serializers.DateField().to_internal_value(raw) if raw else timezone.localdate()
        # current_shift có thể None (ngoài giờ làm việc) → mặc định ca đầu để hiển thị.
        shift = request.query_params.get("shift") or ShiftService.current_shift() or SHIFT_ORDER[0]
        if shift not in SHIFT_ORDER:
            return Response({"detail": "shift không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_serialize_day(date, shift))

    if request.method == "DELETE":
        # Xoá toàn bộ cấu hình của 1 NGÀY (cả 3 ca) — dứt điểm "xoá rồi vẫn chia".
        raw = request.query_params.get("date")
        date = serializers.DateField().to_internal_value(raw) if raw else timezone.localdate()
        deleted, _ = LeadAssignmentShare.objects.filter(date=date).delete()
        return Response({"deleted": deleted, "date": date.isoformat()}, status=status.HTTP_200_OK)

    serializer = _UpsertSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    date = serializer.validated_data["date"]
    shift = serializer.validated_data["shift"]
    rows = serializer.validated_data["rows"]
    LeadAssignmentService.upsert_shares(date, shift, rows)
    return Response(_serialize_day(date, shift), status=status.HTTP_200_OK)


class _CopySerializer(serializers.Serializer):
    date = serializers.DateField(required=False)


# Copy nguyên ma trận chia đơn (cả 3 ca × 4 cột, kể cả resale) từ NGÀY GẦN NHẤT có cấu hình
# sang ngày đích (mặc định hôm nay). assigned_count reset 0; NV đã rời phòng
# Sale / bị khoá bị bỏ qua. Chặn nếu ngày đích đã có cấu hình (xoá trước rồi copy).
@extend_schema(
    request=_CopySerializer,
    responses={
        200: inline_serializer(
            "LeadAssignmentCopyResponse",
            fields={
                "date": drf_serializers.CharField(),
                "source_date": drf_serializers.CharField(),
                "copied": drf_serializers.IntegerField(),
            },
        ),
        400: OpenApiResponse(
            description="Ngày đích đã có cấu hình, hoặc không có ngày nguồn nào để copy"
        ),
    },
)
@api_view(["POST"])
# Audit 2026-08-19 N2: ghi can quyen ghi, khong gate bang require_section (=:view).
@permission_classes([IsAuthenticated, require_permission("lead-assignment:edit")])
def lead_assignment_copy_shares(request):
    serializer = _CopySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    target = serializer.validated_data.get("date") or timezone.localdate()

    if LeadAssignmentShare.objects.filter(date=target).exists():
        return Response(
            {"detail": f"Ngày {target} đã có cấu hình — xoá cấu hình ngày này trước khi copy."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    source_date = (
        LeadAssignmentShare.objects.filter(date__lt=target)
        .order_by("-date")
        .values_list("date", flat=True)
        .first()
    )
    if source_date is None:
        return Response(
            {"detail": "Chưa có ngày nào trước đó được cấu hình để copy."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    active_ids = set(_sales_staff_qs().values_list("id", flat=True))
    copied = LeadAssignmentService.copy_shares(
        source_date=source_date, target_date=target, allowed_user_ids=active_ids
    )
    return Response(
        {"date": target.isoformat(), "source_date": source_date.isoformat(), "copied": copied}
    )


@extend_schema(
    responses={
        200: inline_serializer(
            "LeadAssignmentStaffItem",
            many=True,
            fields={
                "id": drf_serializers.IntegerField(),
                "username": drf_serializers.CharField(),
                "display_name": drf_serializers.CharField(allow_blank=True),
                "has_foxia_id": drf_serializers.BooleanField(),
            },
        ),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("lead-assignment")])
def lead_assignment_staff(request):
    """Danh sách nhân viên dùng cho cấu hình chia đơn — chỉ phòng Sale (lọc tại BE)."""
    users = _sales_staff_qs().order_by("username")
    items = [
        {"id": u.id, "username": u.username, "display_name": _display(u), "has_foxia_id": _has_foxia_id(u)}
        for u in users
    ]
    return Response(items)


@extend_schema(
    responses={
        200: inline_serializer(
            "LeadAssignmentWorkloadResponse",
            fields={
                "sheet": drf_serializers.CharField(),
                "items": inline_serializer(
                    "LeadAssignmentWorkloadItem",
                    many=True,
                    fields={
                        "user_id": drf_serializers.IntegerField(),
                        "username": drf_serializers.CharField(allow_blank=True),
                        "display_name": drf_serializers.CharField(allow_blank=True),
                        "pending_count": drf_serializers.IntegerField(),
                    },
                ),
            },
        ),
        400: OpenApiResponse(description="sheet không hợp lệ"),
    },
)
@api_view(["GET"])
@permission_classes([IsAuthenticated, require_section("lead-assignment")])
def lead_assignment_workload(request):
    sheet = request.query_params.get("sheet", "")
    if sheet not in SHEET_CHOICES:
        return Response({"detail": "sheet không hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)
    raw_date = request.query_params.get("date")
    date = serializers.DateField().to_internal_value(raw_date) if raw_date else timezone.localdate()
    counts = LeadAssignmentService.workload(sheet, date)
    users = User.objects.filter(id__in=counts.keys())
    name_by_id = {u.id: (u.username, _display(u)) for u in users}
    items = [
        {
            "user_id": uid,
            "username": name_by_id.get(uid, ("", ""))[0],
            "display_name": name_by_id.get(uid, ("", ""))[1],
            "pending_count": cnt,
        }
        for uid, cnt in counts.items()
    ]
    items.sort(key=lambda it: -it["pending_count"])
    return Response({"sheet": sheet, "items": items})


class _TransferSerializer(serializers.Serializer):
    sheet = serializers.ChoiceField(choices=SHEET_CHOICES)
    from_user_id = serializers.IntegerField()
    to_user_id = serializers.IntegerField()
    count = serializers.IntegerField(min_value=1)
    date = serializers.DateField(required=False)

    def validate(self, attrs):
        if attrs["from_user_id"] == attrs["to_user_id"]:
            raise serializers.ValidationError("Nhân viên nguồn và đích phải khác nhau.")
        # Nguồn: bất kỳ NV đang giữ đơn. Đích: bắt buộc thuộc phòng Sale.
        if not User.objects.filter(id=attrs["from_user_id"], deleted_at__isnull=True).exists():
            raise serializers.ValidationError(
                f"Nhân viên nguồn không tồn tại: {attrs['from_user_id']}"
            )
        if not _sales_staff_qs().filter(id=attrs["to_user_id"]).exists():
            raise serializers.ValidationError(
                f"Nhân viên đích phải thuộc phòng Sale: {attrs['to_user_id']}"
            )
        return attrs


@extend_schema(
    request=_TransferSerializer,
    responses={
        200: inline_serializer(
            "LeadAssignmentTransferResponse",
            fields={"transferred_count": drf_serializers.IntegerField()},
        ),
    },
)
@api_view(["POST"])
# Audit 2026-08-19 N2: decorator giữ :view TƯỜNG MINH — gate ghi thật của endpoint
# này là sale-assignment:edit kiểm bên trong (audit 2026-08-07 I1, test khoá:
# test_transfer_not_forbidden_with_sale_assignment_permission). Đòi thêm
# lead-assignment:edit ở đây là AND kép, chặn oan người chia đơn.
@permission_classes([IsAuthenticated, require_permission("lead-assignment:view")])
def lead_assignment_transfer(request):
    # require_section("lead-assignment") chỉ là :view — endpoint này bulk-update
    # Order.staff (ai được tính doanh số), nên phải gác thêm sale-assignment:edit
    # như mọi đường ghi staff khác (audit 2026-08-07 I1: trước fix, ai xem được
    # màn chia đơn là chuyển được đơn của bất kỳ ai).
    if not can_assign_sale(request.user):
        return Response(
            {"detail": "Bạn không có quyền đổi sale phụ trách đơn."},
            status=status.HTTP_403_FORBIDDEN,
        )
    serializer = _TransferSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    v = serializer.validated_data
    date = v.get("date") or timezone.localdate()
    moved = LeadAssignmentService.transfer(
        v["sheet"], v["from_user_id"], v["to_user_id"], v["count"], date,
        actor=request.user,
    )
    return Response({"transferred_count": moved}, status=status.HTTP_200_OK)


class _ShiftWindowRow(serializers.Serializer):
    shift = serializers.ChoiceField(choices=SHIFT_ORDER)
    # Cho phép để trống cả hai = ca tắt (không hoạt động).
    start_time = serializers.TimeField(required=False, allow_null=True)
    end_time = serializers.TimeField(required=False, allow_null=True)


class _ShiftWindowsSerializer(serializers.Serializer):
    windows = _ShiftWindowRow(many=True)

    def validate_windows(self, windows):
        if {w["shift"] for w in windows} != set(SHIFT_ORDER):
            raise serializers.ValidationError("Phải gửi đủ 3 ca: morning/afternoon/evening.")
        spans = []            # mọi đoạn (phút_đầu, phút_cuối) inclusive để soát chồng lấn
        overnight_count = 0   # số ca cuộn qua đêm (start > end); tối đa 1
        for w in windows:
            start, end = w.get("start_time"), w.get("end_time")
            if start is None and end is None:
                continue  # ca tắt
            if start is None or end is None:
                raise serializers.ValidationError(
                    "Mỗi ca phải có đủ cả giờ bắt đầu và kết thúc, hoặc để trống cả hai để tắt ca."
                )
            intervals = shift_intervals(start, end)
            if len(intervals) == 2:
                overnight_count += 1
            elif intervals[0][0] == intervals[0][1]:
                raise serializers.ValidationError(
                    "Giờ bắt đầu và kết thúc không được trùng nhau."
                )
            spans.extend(intervals)
        if overnight_count > 1:
            raise serializers.ValidationError(
                "Chỉ được tối đa 1 ca cuộn qua đêm (giờ bắt đầu sau giờ kết thúc)."
            )
        # Không ca nào chồng lấn: mọi đoạn (kể cả 2 đoạn của ca cuộn qua đêm) phải rời nhau.
        spans.sort()
        for i in range(len(spans) - 1):
            if spans[i][1] >= spans[i + 1][0]:
                raise serializers.ValidationError("Các ca không được chồng lấn thời gian.")
        return windows


def _serialize_windows():
    rows = {w.shift: w for w in ShiftWindow.objects.all()}

    def fmt(t):
        # Kèm giây để UI hiển thị/round-trip đúng giờ kết thúc dạng :59 (vd 17:29:59).
        return t.strftime("%H:%M:%S") if t else None

    return {
        "windows": [
            {
                "shift": s,
                "label": dict(ShiftWindow.SHIFT_CHOICES)[s],
                "start_time": fmt(rows[s].start_time) if s in rows else None,
                "end_time": fmt(rows[s].end_time) if s in rows else None,
            }
            for s in SHIFT_ORDER
        ],
        "current_shift": ShiftService.current_shift(),
    }


@extend_schema(
    request=_ShiftWindowsSerializer,
    responses={
        200: inline_serializer(
            "LeadAssignmentShiftWindowsResponse",
            fields={
                "windows": inline_serializer(
                    "ShiftWindowRowOut",
                    many=True,
                    fields={
                        "shift": drf_serializers.CharField(),
                        "label": drf_serializers.CharField(),
                        "start_time": drf_serializers.CharField(allow_null=True),
                        "end_time": drf_serializers.CharField(allow_null=True),
                    },
                ),
                "current_shift": drf_serializers.CharField(allow_null=True),
            },
        ),
    },
)
@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, require_method_permission(GET="lead-assignment:view", PUT="lead-assignment:edit")])
def lead_assignment_shift_windows(request):
    if request.method == "GET":
        return Response(_serialize_windows())
    serializer = _ShiftWindowsSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    LeadAssignmentService.save_shift_windows(serializer.validated_data["windows"])
    return Response(_serialize_windows(), status=status.HTTP_200_OK)
