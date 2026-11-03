import json
import time
import logging
from functools import wraps
from django.contrib.auth import get_user_model

from apps.dispatch import write_audit

from .models import AuditLog

logger = logging.getLogger(__name__)
User = get_user_model()


def _extract_user_from_call(args, kwargs):
    """
    Resolve user from direct object or id-style kwargs.

    Background tasks usually pass actor_id/requested_by_id instead of a request user object.
    """
    for arg in args:
        if hasattr(arg, "is_authenticated") and arg.is_authenticated:
            return arg

    for key in ("user", "request_user"):
        candidate = kwargs.get(key)
        if hasattr(candidate, "is_authenticated") and candidate.is_authenticated:
            return candidate

    for key in ("actor_id", "requested_by_id", "user_id"):
        raw_user_id = kwargs.get(key)
        if raw_user_id in (None, ""):
            continue
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            continue
        user = User.objects.filter(id=user_id).only("id").first()
        if user is not None:
            return user

    return None


def log_kiotviet_call(_func=None, *, action_name=None):
    """
    Decorator để log tất cả các call sang KiotViet
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()

            user = _extract_user_from_call(args, kwargs)

            # Trích xuất order từ kwargs hoặc positional args
            order = kwargs.get("order")
            if order is None:
                for arg in args:
                    if hasattr(arg, "id") and hasattr(arg, "invoice_code") and hasattr(arg, "status"):
                        order = arg
                        break
            order_id = order.id if order else None

            # Chuẩn bị request data cho curl command
            request_info = {
                "args": str(args),
                "kwargs": str(kwargs),
                "action_name": action_name or func.__name__,
            }

            try:
                # Gọi hàm gốc
                result = func(*args, **kwargs)

                # Tính duration
                duration_ms = int((time.time() - start_time) * 1000)

                # Tạo curl command
                curl_cmd = f"curl -X POST 'https://api.kiotviet.vn/{action_name or func.__name__}' -H 'Authorization: Bearer ...' -d '{json.dumps(request_info)}'"

                # Log thành công qua Go audit-writer.
                write_audit(
                    user_id=user.id if user else None,
                    action_type="KIOTVIET_REQUEST",
                    entity_type="ORDER",
                    entity_id=order_id,
                    method="POST",
                    ip_address=None,
                    response_data=result if isinstance(result, dict) else {"result": str(result)},
                    duration_ms=duration_ms,
                    source="kiotviet",
                    curl_command=curl_cmd,
                )

                return result

            except Exception as e:
                duration_ms = int((time.time() - start_time) * 1000)

                # Tạo curl command
                curl_cmd = f"curl -X POST 'https://api.kiotviet.vn/{action_name or func.__name__}' -H 'Authorization: Bearer ...' -d '{json.dumps(request_info)}'"

                # Log thất bại qua Go audit-writer.
                write_audit(
                    user_id=user.id if user else None,
                    action_type="KIOTVIET_REQUEST",
                    entity_type="ORDER",
                    entity_id=order_id,
                    method="POST",
                    ip_address=None,
                    response_data={"error": str(e)},
                    duration_ms=duration_ms,
                    source="kiotviet",
                    curl_command=curl_cmd,
                )

                # Rethrow exception
                raise

        return wrapper

    # Support both usages:
    # 1) @log_kiotviet_call
    # 2) @log_kiotviet_call(action_name="...")
    if _func is not None and callable(_func):
        return decorator(_func)
    return decorator
