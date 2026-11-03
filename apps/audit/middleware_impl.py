import json
import re
import time
import logging
from django.conf import settings
from django.http import RawPostDataException
from django.utils.deprecation import MiddlewareMixin
from django.contrib.auth import get_user_model

from apps.common.masking import mask_keep_last
from apps.dispatch import write_audit

from .models import AuditLog

logger = logging.getLogger(__name__)
User = get_user_model()

# Một key (không phân biệt hoa/thường) bị coi là nhạy cảm nếu:
#  - tên chứa bất kỳ token "dài" nào trong SENSITIVE_SUBSTRINGS (an toàn để khớp
#    theo substring vì không trùng với tên trường nghiệp vụ thông thường), HOẶC
#  - một thành phần (tách theo snake_case/kebab-case/camelCase) khớp ĐÚNG một
#    token "ngắn" trong SENSITIVE_WORD_PARTS. Khớp nguyên-từ để tránh dương tính
#    giả, ví dụ 'shipping' KHÔNG bị che dù có chứa chuỗi con 'pin'.
# Đặt ở module-level để dễ mở rộng.
SENSITIVE_SUBSTRINGS = (
    'password',
    'passwd',
    'secret',
    'token',
    'refresh',
    'authorization',
    'api_key',
    'apikey',
    'credential',
    'signature',
)
SENSITIVE_WORD_PARTS = (
    'access',
    'otp',
    'pin',
    'phone',
)

# Trong các key nhạy cảm ở trên, các key thuộc nhóm này được che DẠNG MASK (giữ vài
# ký tự cuối) thay vì che trắng hoàn toàn — vẫn cần đối soát được (vd tra SĐT khách
# theo audit log) mà không lộ toàn bộ số.
MASK_INSTEAD_OF_BLANK_WORD_PARTS = (
    'phone',
)

REDACTED_PLACEHOLDER = '***REDACTED***'

# Chỉ những header THỰC SỰ đổi cách API xử lý request mới được chép vào
# `curl_command`. So khớp theo tên đã lower-case.
#
# Vì sao whitelist chứ không phải blacklist (đo prod 2026-08-17):
#   1. Dung lượng — `curl_command` trung bình 756 byte/dòng, ~1.6GB trên tổng
#      3.4GB heap của `audit_logs`. Phần lớn là `User-Agent`, `Sec-Ch-Ua-*`,
#      `Accept-Language`, `Referer` của trình duyệt: lặp gần như y hệt trên hàng
#      triệu dòng, không cần để tái hiện request. `User-Agent` và `Referer` vẫn
#      được `_build_metadata` lưu ở cột `metadata` (jsonb, query được) nên đây là
#      bỏ TRÙNG LẶP, không mất dữ liệu điều tra.
#   2. An toàn — blacklist chỉ chặn được những header đã biết tên. Một header
#      mang bí mật mà chưa ai nghĩ tới (`X-Api-Key`, `X-Channel-Token`…) sẽ lọt
#      thẳng vào audit log. Whitelist mặc định từ chối, nên header mới phải được
#      thêm có chủ đích.
CURL_HEADER_WHITELIST = frozenset({
    'content-type',
    'accept',
    'x-requested-with',
})

# Trần dung lượng body được buffer để ghi audit. Payload lớn hơn ngưỡng này bị bỏ
# qua (chỉ mất phần curl -d, request vẫn chạy bình thường) — không giữ cả MB trong
# RAM chỉ để log. Đổi được qua settings.AUDIT_BODY_MAX_BYTES.
AUDIT_BODY_MAX_BYTES_DEFAULT = 1 * 1024 * 1024

# Tách camelCase -> snake để chuẩn hoá trước khi so khớp nguyên-từ.
_CAMEL_BOUNDARY = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')


class AuditLogMiddleware(MiddlewareMixin):
    """
    Middleware để capture và log tất cả API requests
    Chỉ lưu curl command và response ngắn gọn
    Sử dụng Go audit-writer để ghi log async
    """

    # Các paths không cần log
    # /healthz: probe uptime ngoài gọi mỗi 1-5' — log sẽ spam audit_logs vô nghĩa.
    SKIP_PATHS = ['/health/', '/healthz', '/static/', '/media/', '/ws/', '/favicon.ico']

    def process_request(self, request):
        """Lưu thời gian bắt đầu"""
        request.start_time = time.time()

        # Lưu IP address
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            request.ip_address = x_forwarded_for.split(',')[0].strip()
        else:
            request.ip_address = request.META.get('REMOTE_ADDR')

        request.audit_body = self._buffer_request_body(request)

    def _buffer_request_body(self, request):
        """Đọc body NGAY Ở ĐÂY, trước khi view DRF gọi ``request.data``.

        DRF parse JSON bằng cách đọc thẳng stream; Django chỉ cache ``_body`` nếu
        ``request.body`` được truy cập TRƯỚC. Nếu để tới ``process_response`` mới
        đọc thì nhận ``RawPostDataException`` và audit mất sạch body (curl không có
        ``-d``, ``request_size_bytes``=0). Truy cập ở đây khiến Django cache ``_body``
        và thay ``_stream`` bằng BytesIO — DRF phía sau vẫn parse bình thường.

        CHỈ áp cho JSON dưới ngưỡng: multipart (upload ảnh chấm công lên MinIO) phải
        để nguyên cho streaming parser, buffer vào RAM là lãng phí và rủi ro.
        """
        if request.method not in ('POST', 'PUT', 'PATCH'):
            return b""
        if not (request.META.get('CONTENT_TYPE') or '').startswith('application/json'):
            return b""

        max_bytes = getattr(settings, 'AUDIT_BODY_MAX_BYTES', AUDIT_BODY_MAX_BYTES_DEFAULT)
        try:
            if int(request.META.get('CONTENT_LENGTH') or 0) > max_bytes:
                return b""
            return request.body or b""
        except Exception as exc:
            logger.debug("audit middleware: cannot buffer request body (%s)", exc)
            return b""

    def process_response(self, request, response):
        """Ghi log sau khi response được trả về"""
        # Kiểm tra nếu có start_time
        if not hasattr(request, 'start_time'):
            return response

        # Tính duration
        duration_ms = int((time.time() - request.start_time) * 1000)

        # Bỏ qua các paths không cần log
        if any(path in request.path for path in self.SKIP_PATHS):
            return response

        # Bỏ qua OPTIONS requests
        if request.method == 'OPTIONS':
            return response

        # Bỏ qua request ĐỌC thành công (GET 2xx, POST-list 2xx) trừ đường đọc nhạy
        # cảm — audit 2026-09-23 T1 §2.10, xem settings AUDIT_LOG_SKIP_READS.
        if self._is_skippable_read(request, response):
            return response

        try:
            # Xác định user
            user = getattr(request, 'user', None)
            user_id = user.id if user and user.is_authenticated else None

            # Xác định action type và entity type
            action_type = 'API_REQUEST'
            entity_type = 'OTHER'
            entity_id = None

            # Phân tích path để xác định entity
            path = request.path

            if '/api/orders/' in path:
                entity_type = 'ORDER'
                action_type = self._get_action_type_from_method(request.method)
                entity_id = self._extract_id_from_path(path, 'orders')
            elif '/api/customers/' in path:
                entity_type = 'CUSTOMER'
                action_type = self._get_action_type_from_method(request.method)
                entity_id = self._extract_id_from_path(path, 'customers')
            elif '/api/products/' in path:
                entity_type = 'PRODUCT'
                action_type = self._get_action_type_from_method(request.method)
                entity_id = self._extract_id_from_path(path, 'products')
            elif '/api/channels/' in path:
                entity_type = 'CHANNEL'
                action_type = self._get_action_type_from_method(request.method)
                entity_id = self._extract_id_from_path(path, 'channels')
            elif '/api/kiotviet/' in path:
                entity_type = 'KIOTVIET_JOB'
                action_type = 'API_REQUEST'
                entity_id = self._extract_id_from_path(path, 'jobs')
            elif '/api/users/' in path:
                # Quản trị user (đổi role / is_active / mật khẩu) — cần audit rõ ràng.
                entity_type = 'USER'
                action_type = self._get_action_type_from_method(request.method)
                entity_id = self._extract_id_from_path(path, 'users')

            # Tạo curl command
            curl_cmd = self._create_curl_command(request)

            # Lấy response data - chỉ lấy status và summary.
            # GET thành công (2xx) mặc định KHÔNG lưu tóm tắt body (chiếm phần lớn
            # dung lượng audit_logs mà giá trị điều tra thấp) — bật lại qua env
            # AUDIT_LOG_GET_RESPONSE=1 (audit hardening 2026-07 mục 3.1).
            if (
                request.method == 'GET'
                and 200 <= response.status_code < 300
                and not getattr(settings, 'AUDIT_LOG_GET_RESPONSE', False)
            ):
                response_data = {'status': response.status_code}
            else:
                response_data = self._get_response_summary(response)
            metadata = self._build_metadata(request, response)

            self._write_audit_log(
                user_id=user_id,
                action_type=action_type,
                entity_type=entity_type,
                entity_id=entity_id,
                method=request.method,
                ip_address=request.ip_address,
                response_data=response_data,
                duration_ms=duration_ms,
                source='api_request',
                curl_command=curl_cmd,
                metadata=metadata,
            )
        except Exception as e:
            # Nếu có lỗi khi ghi log, chỉ log ra console, không làm crash request
            logger.error(f"Failed to write audit log: {e}")

        return response

    def _is_skippable_read(self, request, response) -> bool:
        """True nếu request là đọc thành công không nhạy cảm → không ghi audit.

        "Đọc" = GET, hoặc POST tới endpoint list (AUDIT_LOG_POST_LIST_PATHS — trang
        Đơn/Landing/Sheet3/Vận đơn lọc bằng body). Chỉ áp cho 2xx: 4xx/5xx vẫn ghi
        để còn truy vết truy cập bị từ chối. Đường nhạy cảm
        (AUDIT_LOG_SENSITIVE_READ_PATHS: reveal SĐT, export, in hoá đơn, audit log,
        quản trị) luôn ghi.
        """
        if not getattr(settings, 'AUDIT_LOG_SKIP_READS', True):
            return False
        if not (200 <= int(getattr(response, 'status_code', 0) or 0) < 300):
            return False
        path = request.path or ''
        if request.method == 'GET':
            is_read = True
        elif request.method == 'POST':
            normalized = path.rstrip('/')
            is_read = normalized in {
                p.rstrip('/') for p in getattr(settings, 'AUDIT_LOG_POST_LIST_PATHS', [])
            }
        else:
            is_read = False
        if not is_read:
            return False
        lowered = path.lower()
        return not any(
            token.lower() in lowered
            for token in getattr(settings, 'AUDIT_LOG_SENSITIVE_READ_PATHS', [])
        )

    def _write_audit_log(self, **payload):
        """
        Ưu tiên ghi async qua Go audit-writer; nếu lỗi
        thì fallback ghi trực tiếp DB.
        """
        try:
            write_audit(**payload)
            return
        except Exception as enqueue_error:
            logger.warning(
                "Audit enqueue failed, fallback to sync DB write: %s", enqueue_error
            )

        try:
            AuditLog.objects.create(**payload)
        except Exception as db_error:
            logger.error("Audit sync DB write failed: %s", db_error)

    def _build_metadata(self, request, response):
        query_params = {}
        try:
            # Query params trước đây được lưu NGUYÊN VĂN — token/mật khẩu/SĐT truyền
            # qua query string sẽ lộ thẳng vào audit log. Chạy qua cùng bộ lọc dùng
            # cho curl body (_redact_sensitive) trước khi lưu.
            query_params = self._redact_sensitive(dict(request.GET.lists()))
        except Exception:
            query_params = {}

        # Dùng bản buffer từ process_request: tới lúc này stream đã bị DRF đọc,
        # `request.body` sẽ ném RawPostDataException.
        request_size = len(getattr(request, 'audit_body', b"") or b"")

        return {
            "path": request.path,
            "query_params": query_params,
            "status_code": response.status_code,
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            "referer": request.META.get("HTTP_REFERER", ""),
            "request_content_type": request.META.get("CONTENT_TYPE", ""),
            "response_content_type": response.get("Content-Type", ""),
            "request_size_bytes": request_size,
        }

    def _create_curl_command(self, request):
        """Tạo curl command từ request"""
        method = request.method
        url = f"{request.scheme}://{request.get_host()}{request.path}"

        # Build curl command
        curl = f"curl -X {method} '{url}'"

        # Chỉ chép các header trong whitelist (xem CURL_HEADER_WHITELIST).
        for header, value in request.headers.items():
            if header.lower() in CURL_HEADER_WHITELIST:
                curl += f" -H '{header}: {value}'"

        # Add body nếu có (bản buffer từ process_request — xem _buffer_request_body)
        if request.method in ['POST', 'PUT', 'PATCH']:
            raw_body = getattr(request, 'audit_body', b"") or b""
            try:
                if raw_body:
                    body = json.loads(raw_body.decode('utf-8'))
                    # Che các trường nhạy cảm trước khi đưa vào curl command
                    redacted_body = self._redact_sensitive(body)
                    # Giới hạn body trong curl command (sau khi đã che)
                    body_str = json.dumps(redacted_body)[:500]
                    curl += f" -d '{body_str}'"
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError, RawPostDataException) as exc:
                logger.debug("audit middleware: skip body in curl (%s)", exc)

        return curl

    def _redact_sensitive(self, value):
        """
        Trả về một bản sao của ``value`` với các trường nhạy cảm bị che.

        Hoạt động đệ quy qua dict và list lồng nhau. Một key trong dict bị che
        nếu ``_is_sensitive_key`` trả về True: mặc định che trắng
        (``***REDACTED***``), riêng key thuộc nhóm ``_is_mask_key`` (vd SĐT) được
        mask giữ vài ký tự cuối thay vì che trắng (vẫn cần đối soát được). Không
        làm thay đổi đối tượng gốc (luôn tạo bản sao).
        """
        if isinstance(value, dict):
            result = {}
            for key, val in value.items():
                if isinstance(key, str) and self._is_sensitive_key(key):
                    result[key] = self._mask_sensitive_value(key, val)
                else:
                    result[key] = self._redact_sensitive(val)
            return result

        if isinstance(value, list):
            return [self._redact_sensitive(item) for item in value]

        return value

    def _mask_sensitive_value(self, key, val):
        """Che giá trị của 1 key đã xác định là nhạy cảm (`_is_sensitive_key`).

        Giữ nguyên hình dạng list (query_params luôn là list-of-values từ
        ``request.GET.lists()``) thay vì suy biến thành 1 chuỗi.
        """
        if isinstance(val, list):
            return [self._mask_sensitive_value(key, item) for item in val]
        if self._is_mask_key(key):
            return mask_keep_last(val)
        return REDACTED_PLACEHOLDER

    def _key_components(self, key):
        """Tách snake_case/kebab-case/camelCase thành các thành phần thường."""
        normalized = _CAMEL_BOUNDARY.sub('_', key).lower()
        return re.split(r'[^a-z0-9]+', normalized)

    def _is_sensitive_key(self, key):
        """Kiểm tra một key có nhạy cảm hay không (không phân biệt hoa/thường)."""
        lowered = key.lower()
        if any(token in lowered for token in SENSITIVE_SUBSTRINGS):
            return True
        # Tách snake_case/kebab-case/camelCase thành các thành phần và khớp nguyên-từ.
        components = self._key_components(key)
        return any(comp in SENSITIVE_WORD_PARTS for comp in components)

    def _is_mask_key(self, key):
        """True nếu key nhạy cảm này thuộc nhóm mask-giữ-vài-ký-tự-cuối (vd SĐT)."""
        components = self._key_components(key)
        return any(comp in MASK_INSTEAD_OF_BLANK_WORD_PARTS for comp in components)

    def _get_response_summary(self, response):
        """Tạo response summary ngắn gọn"""
        summary = {
            'status': response.status_code,
        }

        try:
            if hasattr(response, 'content') and response.content:
                content = response.content.decode('utf-8')
                content_type = response.get('Content-Type', '')

                if 'application/json' in content_type:
                    # Chỉ lấy các key chính, không lưu toàn bộ data
                    data = json.loads(content)
                    summary['keys'] = list(data.keys())[:10]  # Chỉ lấy 10 keys đầu
                    if 'count' in data:
                        summary['count'] = data['count']
                    if 'results' in data and isinstance(data['results'], list):
                        summary['results_count'] = len(data['results'])
                    if 'error' in data or 'detail' in data:
                        summary['error'] = str(data.get('error', data.get('detail', '')))[:200]
                else:
                    summary['content_preview'] = content[:200]
        except Exception as exc:
            # Không để lỗi tóm tắt response làm hỏng request; log ở mức debug
            # thay vì nuốt im lặng để còn truy vết khi cần.
            logger.debug("audit middleware: skip response summary (%s)", exc)

        return summary

    def _get_action_type_from_method(self, method):
        """Chuyển HTTP method thành action type"""
        action_map = {
            'POST': 'CREATE',
            'PUT': 'UPDATE',
            'PATCH': 'UPDATE',
            'DELETE': 'DELETE',
            'GET': 'READ',
        }
        return action_map.get(method, 'API_REQUEST')

    def _extract_id_from_path(self, path, entity_name):
        """Trích xuất ID từ URL path"""
        try:
            parts = path.split('/')
            for i, part in enumerate(parts):
                if part == entity_name and i + 1 < len(parts):
                    next_part = parts[i + 1]
                    if next_part.isdigit():
                        return next_part
        except Exception as exc:
            logger.debug("audit middleware: cannot extract id from path %s (%s)", path, exc)
        return None
