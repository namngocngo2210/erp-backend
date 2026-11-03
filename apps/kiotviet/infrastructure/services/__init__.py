"""Facade package: re-export tường minh API công khai của 5 module service.

Trước đây dùng ``import *`` 3 tầng (audit backend-architecture 4.5 + 5.2) làm
namespace phẳng ~4700 dòng và buộc pyproject ignore F403/F405. Giữ đúng tập tên
công khai cũ để call site bên ngoài không đổi.
"""

from .common_auth_customer import (  # noqa: F401
    KIOTVIET_HTTP_TIMEOUT,
    KIOTVIET_TOKEN_SCOPE_PUBLIC,
    KIOTVIET_TOKEN_SCOPE_WEB,
    KIOTVIET_TOKEN_URL,
    KIOTVIET_WEB_API_BASE_URL,
    KIOTVIET_WEB_FINGERPRINT_KEY,
    KIOTVIET_WEB_LOGIN_URL,
    KiotVietApiError,
    KiotVietInvoiceNotFound,
    find_customer_by_code,
    find_customer_by_phone,
    get_access_token,
    get_web_access_token,
    http_mutate,
    http_read,
    invalidate_web_access_token,
    wait_for_kiotviet_slot,
)
from .gateway_kship_customer_webhook import (  # noqa: F401
    KSHIP_CREATE_ORDER_TIMEOUT_SECONDS,
    KSHIP_SHIPPER_NOTE_DEFAULT,
    KSHIP_SHIPPER_NOTE_OPTIONS,
    build_kship_create_order_payload,
    create_customer,
    create_kship_delivery_for_invoice,
    create_kship_order,
    get_kship_providers,
    get_kship_providers_and_services,
    get_kship_services,
    get_or_create_customer,
    list_shipper_note_options,
    register_webhook,
    resolve_shipper_note,
)
from .invoice_sync_queries import (  # noqa: F401
    create_invoice,
    create_order,
    get_invoice_by_code,
    get_invoice_by_order_code,
    sync_invoice_new_to_old,
)
from .invoice_mutations_push import (  # noqa: F401
    KOC_NO_COD_ORDER_TYPES,
    delete_invoice_by_id,
    is_koc_non_cod_order,
    push_order_to_kiotviet,
    update_invoice_delivery_price_by_code,
    update_invoice_delivery_status_by_id,
)
from .service_actions import (  # noqa: F401
    CANCELLED_INVOICE_OWN_STATUS,
    KSHIP_INDETERMINATE_MESSAGE,
    KSHIP_RECONCILE_ATTEMPTS,
    KSHIP_RECONCILE_READ_TIMEOUT_SECONDS,
    KSHIP_RECONCILE_SLEEP_SECONDS,
    KiotVietService,
    logger,
)
