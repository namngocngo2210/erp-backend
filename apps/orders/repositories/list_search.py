"""Tìm kiếm từ khoá (`q`) của danh sách đơn — nhánh đầu tiên của
``apply_list_filters`` (list_queries.py).

Tách nguyên trạng từ ``OrderRepository._apply_list_filters`` (audit T3/P2-3) —
KHÔNG đổi hành vi, chỉ dời chỗ.
"""
import re

from apps.orders.models import Order

from .base import SEARCH_MATCH_ID_CAP


def _keyword_match_parts(keyword: str) -> list:
    """Các id-subquery khớp MỘT từ khoá (SĐT / mã / tên) — UNION lại ở caller."""
    from apps.common.phone_utils import normalize_phone

    normalized = re.sub(r"\D", "", keyword)
    clean = keyword.strip("'").strip('"')
    has_letters = bool(re.search(r"[A-Za-z]", keyword))
    # Candidate phone forms: raw digits + canonical VN form, so that
    # "+84 901 234 567" / "84901234567" still match "0901234567".
    candidates = sorted({c for c in (normalized, normalize_phone(keyword)) if c})

    parts = []
    # Numeric keyword → phone search (full OR partial). Skipped when the
    # keyword contains letters (a code/name is never a phone) so code search
    # never drags in phone rows.
    if not has_letters and len(normalized) >= 3:
        if candidates:
            # Exact canonical on B-tree (order_phone_norm_idx /
            # customer_phone_normalized_idx) — fast path for a full number,
            # incl. +84/84 forms via the canonical candidate.
            parts.append(Order.objects.filter(phone_normalized__in=candidates).values("id", "created_at"))
            parts.append(Order.objects.filter(customer__phone_normalized__in=candidates).values("id", "created_at"))
        # Substring on the CANONICAL digits (trigram: order_phone_norm_trgm_idx
        # / cust_phone_norm_trgm_idx) so a PARTIAL number matches regardless of
        # how the stored phone is formatted (+84 / spaces / dashes). Trigram
        # needs >= 3 chars to apply.
        parts.append(Order.objects.filter(phone_normalized__contains=normalized).values("id", "created_at"))
        parts.append(Order.objects.filter(customer__phone_normalized__contains=normalized).values("id", "created_at"))

    # Code search via __contains (case-sensitive LIKE → uses the bare-column
    # trigram GIN indexes; __icontains wraps in UPPER() and seq-scans 500k
    # rows). Requires >= 3 chars: the trigram GIN index can't serve a 1-2
    # char LIKE, so a too-short code keyword would seq-scan the whole orders
    # table and match a huge slice (e.g. "HD" → every HDxxx invoice_code).
    if len(clean) >= 3:
        parts.append(Order.objects.filter(shipping_code__contains=clean).values("id", "created_at"))
        parts.append(Order.objects.filter(invoice_code__contains=clean).values("id", "created_at"))
        parts.append(Order.objects.filter(old_invoice_code__contains=clean).values("id", "created_at"))
        # order_code (mã đơn) — trigram order_order_code_trgm_idx.
        parts.append(Order.objects.filter(order_code__contains=clean).values("id", "created_at"))
        # Tên khách: trước đây list `q` KHÔNG tìm theo name → gõ tên trả rỗng.
        # __icontains sinh UPPER((name)::text) LIKE UPPER(%s); được phục vụ bởi
        # functional trigram index order_name_upper_trgm_idx (Upper(Cast(name AS
        # text)) gin_trgm_ops) nên không seq-scan. Case-insensitive cho tên tiếng Việt.
        parts.append(Order.objects.filter(name__icontains=clean).values("id", "created_at"))
    return parts


def apply_keyword_search(queryset, raw_q: str):
    """Lọc `queryset` theo `q` (đã strip). Rỗng → trả nguyên `queryset`."""
    if not raw_q:
        return queryset
    keywords = [k.strip() for k in raw_q.split(",") if k.strip()]
    if not keywords:
        return queryset

    # Resolve the keyword match as a UNION of per-field id-subqueries, then
    # filter by id__in. A single big OR — especially one containing
    # `customer_id IN (subquery)` — is turned by Postgres into a hashed SubPlan
    # evaluated over a full Seq Scan of the ~500k-row orders table: it cannot
    # BitmapOr across branches, let alone across relations. Each UNION branch
    # instead resolves independently on its own index.
    match_ids = None
    for keyword in keywords:
        parts = _keyword_match_parts(keyword)
        # A keyword too short for every branch contributes no matches on its own.
        if not parts:
            continue

        keyword_ids = parts[0].union(*parts[1:]) if len(parts) > 1 else parts[0]
        match_ids = keyword_ids if match_ids is None else match_ids.union(keyword_ids)

    # Materialize the matched ids to a bounded Python list BEFORE filtering.
    # Passing the lazy UNION subquery as `id__in` makes Postgres misestimate its
    # cardinality and seq-scan + join the entire orders table (through all the
    # list select_related/prefetch joins) before filtering down to a few rows and
    # sorting by created_at — ~2.4s even for one exact phone. Literal ids let the
    # page fetch index-scan just those rows (~60ms). The cap keeps a pathological
    # broad substring match bounded instead of pulling ~1M ids into Python.
    if match_ids is None:
        return queryset.none()
    # Order the UNION by -created_at BEFORE applying the cap. Without this
    # the cap kept an ARBITRARY 500 ids from an unordered union, so a broad
    # keyword (>500 matches) could drop the very order the user is looking
    # for → "search thỉnh thoảng không ra". Ordering keeps the newest matches,
    # which is also the order the list itself renders.
    match_id_list = [
        row["id"] for row in match_ids.order_by("-created_at")[:SEARCH_MATCH_ID_CAP]
    ]
    return queryset.filter(id__in=match_id_list)
