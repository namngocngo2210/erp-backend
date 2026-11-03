"""Serializers + payload helpers cho runtime tác nghiệp (apply-result / edit-due).

`bucket_of` là NGUỒN SỰ THẬT cho việc phân loại due_at (spec §4) — từ audit
2026-09-23 (T3/P2-2) hàm này sống ở
``apps.contact_flow.application.services.buckets`` (application của orders cũng
dùng, không được import lên transport); ở đây chỉ re-export cho transport.
"""
from __future__ import annotations

from rest_framework import serializers

from apps.contact_flow.application.services.buckets import BUCKETS, bucket_of  # noqa: F401


def state_payload(state) -> dict:
    """`CustomerOperationState` -> dict trả về cho client (shape dùng chung 2 API)."""
    return {
        "stage_name": state.current_stage.name,
        "stage_color": state.current_stage.color,
        "due_at": state.due_at,
        "bucket": bucket_of(state.due_at),
    }


class ApplyResultRequestSerializer(serializers.Serializer):
    result_id = serializers.IntegerField()
    scheduled_at = serializers.DateTimeField(required=False, allow_null=True, default=None)


class EditDueRequestSerializer(serializers.Serializer):
    due_at = serializers.DateTimeField()


class SetStageRequestSerializer(serializers.Serializer):
    """Đổi bước tác nghiệp tay từ dropdown cột Tác nghiệp.

    `due_at` BẮT BUỘC (không optional): dialog luôn hỏi giờ hẹn trước khi gửi, và một
    bước không có hạn thì rơi thẳng vào nhóm "Kết thúc" — không phải ý của thao tác này.
    """

    stage_id = serializers.IntegerField()
    due_at = serializers.DateTimeField()


class RuntimeStateResponseSerializer(serializers.Serializer):
    stage_name = serializers.CharField()
    stage_color = serializers.CharField()
    due_at = serializers.DateTimeField(allow_null=True)
    bucket = serializers.ChoiceField(choices=BUCKETS)
