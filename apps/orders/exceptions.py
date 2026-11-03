"""Lỗi nghiệp vụ của orders được tầng transport dịch thẳng sang HTTP.

Dùng ``APIException`` (thay vì ``ValueError``) để DRF giữ nguyên message tiếng Việt
cho người dùng — handler chung ở :mod:`apps.common.exceptions` sẽ bổ sung
``error.code`` (snake_case tên class) cho FE.
"""

from rest_framework import status
from rest_framework.exceptions import APIException


class OrderCancelledError(APIException):
    """Đơn đã hủy (DB hoặc KiotViet) → tuyệt đối không được xuất kho."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "Đơn đã hủy nên không thể đánh dấu Đã xuất kho."
    default_code = "order_cancelled"


class DispatchVerificationUnavailable(APIException):
    """Không hỏi được KiotViet để xác minh đơn còn sống → fail-closed, chặn xuất kho."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = (
        "Chưa xác minh được trạng thái đơn với KiotViet nên tạm thời không thể "
        "đánh dấu Đã xuất kho. Vui lòng thử lại sau ít phút."
    )
    default_code = "verify_unavailable"
