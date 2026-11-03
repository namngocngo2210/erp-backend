from django.db.models import Q

from apps.common import db_lookups  # noqa: F401  (đăng ký lookup `__ilike_contains`)

from .models import Customer


class CustomerRepository:
    @staticmethod
    def list_customers_queryset(q: str):
        queryset = Customer.objects.filter(deleted_at__isnull=True).order_by("-created_at")
        if q:
            normalized_query = "".join(ch for ch in q if ch.isdigit())
            # `icontains` sinh `UPPER(col) LIKE` → planner bỏ trigram GIN bare-column,
            # seq-scan 756k dòng customers mỗi lần gõ ô tìm kiếm (đo pg_stat_statements
            # 2026-08-19: 352 calls × 2,3s). `__ilike_contains` (apps/common/db_lookups)
            # dùng ILIKE nên khớp cả 4 index trgm bare-column, vẫn case-insensitive.
            filters = (
                Q(full_name__ilike_contains=q)
                | Q(phone_raw__ilike_contains=q)
                | Q(code__ilike_contains=q)
            )
            if normalized_query:
                filters = filters | Q(phone_normalized__ilike_contains=normalized_query)
            queryset = queryset.filter(filters)
        return queryset

    @staticmethod
    def list_customers(q: str, *, limit: int, offset: int):
        return CustomerRepository.list_customers_queryset(q)[offset : offset + limit]

    @staticmethod
    def get_customer_by_id(customer_id: int) -> Customer | None:
        return Customer.objects.filter(id=customer_id, deleted_at__isnull=True).first()
