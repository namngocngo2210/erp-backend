"""Đọc mart bằng SQL thuần — matview không có model Django."""

from django.conf import settings
from django.db import connection
from django.db.utils import OperationalError

from apps.common.db_guards import statement_timeout

# Chỉ phơi các trường có ý nghĩa nghiệp vụ nhất. `dim_customer_profile` sinh
# cột `_src` cho cả 5 trường vô hướng — mở rộng tuple này khi có nhu cầu.
_COVERAGE_FIELDS = ("age_text", "occupation_code", "pain_level_code")


class MartNotReady(RuntimeError):
    """Matview chưa được refresh lần nào."""


def coverage() -> dict:
    """Độ phủ từng trường chân dung, tách nguồn declared / ai."""
    selects = ", ".join(
        f"count(*) FILTER (WHERE {f} IS NOT NULL) AS {f}_total,"
        f" count(*) FILTER (WHERE {f}_src = 'declared') AS {f}_declared,"
        f" count(*) FILTER (WHERE {f}_src = 'ai') AS {f}_ai"
        for f in _COVERAGE_FIELDS
    )
    sql = f"SELECT count(*) AS total, {selects} FROM dwh.customer_360"
    # Mặc định 120 (khác quy ước `getattr(..., 0)` ở các call site khác trong
    # repo): 0 = KHÔNG giới hạn thời gian, mà đây là query trên request path —
    # nếu setting biến mất, muốn fail-safe là "vẫn có trần" chứ không phải
    # "chạy vô thời hạn".
    timeout = getattr(settings, "REPORT_QUERY_TIMEOUT_SECONDS", 120)
    try:
        with statement_timeout(timeout), connection.cursor() as cur:
            cur.execute(sql)
            cols = [c[0] for c in cur.description]
            row = dict(zip(cols, cur.fetchone()))
    except OperationalError as exc:
        # SQLSTATE 55000 = object_not_in_prerequisite_state — chính xác lỗi
        # Postgres ném khi SELECT một matview `WITH NO DATA` (chưa refresh
        # lần nào). Các OperationalError khác (57014 query_canceled từ
        # statement_timeout ở trên, mất kết nối lớp 08,...) là lỗi THẬT,
        # không phải "chưa refresh" — phải ném tiếp để không đánh lừa
        # operator đi chạy refresh trong khi vấn đề là cái khác.
        if getattr(exc.__cause__, "sqlstate", None) != "55000":
            raise
        raise MartNotReady(
            "Mart dwh.customer_360 chưa sẵn sàng — chạy refresh_dwh_marts trước."
        ) from exc

    total = row["total"] or 0
    fields = []
    for f in _COVERAGE_FIELDS:
        n = row[f"{f}_total"]
        fields.append(
            {
                "field": f,
                "declared": row[f"{f}_declared"],
                "ai": row[f"{f}_ai"],
                "total": n,
                "pct": round(100.0 * n / total, 1) if total else 0.0,
            }
        )
    return {"total_customers": total, "fields": fields}


_PROFILE_COLUMNS = """
    c.customer_id, c.code, c.gender, c.created_at, c.is_new_customer,
    c.segment_code, c.segment_name, c.care_priority, c.has_problem,
    c.latest_sales_status_code,
    c.orders_gtc, c.orders_all, c.orders_cancelled,
    c.total_spent, c.avg_order_value,
    c.first_purchase_at, c.last_purchase_at, c.days_since_last_purchase,
    c.repurchase_gap_median_days,
    c.channels, c.provinces, c.top_skus, c.distinct_products,
    c.call_count, c.avg_quality_score,
    c.era, c.price_tier_lifetime, c.purchase_rhythm_lifetime,
    c.recency_bucket, c.channel_affinity, c.product_affinity, c.buyer_role,
    c.age_text, c.age_text_src, c.occupation_code, c.occupation_code_src,
    c.pain_level_code, c.pain_level_code_src, c.pain_duration_code,
    c.used_massage_before,
    -- `coalesce` ở đây chứ không ở matview: matview coalesce mảng bên trong
    -- `dim_customer_profile`, nhưng khách CHƯA TỪNG có phiếu chân dung thì
    -- không có dòng nào ở đó và LEFT JOIN trả NULL cho cả 6 mảng — tức là
    -- 98 trên 100 khách. Trả NULL lẫn [] cho cùng một trường buộc mọi client
    -- phải tự phòng thủ, chỗ nào quên thì `.length` nổ đúng trên ca phổ biến
    -- nhất. (Không viết dấu phần trăm trong SQL: psycopg quét ký tự đó kể cả
    -- trong comment và làm vỡ việc gán tham số.)
    coalesce(c.lifestyle_habit_codes, '{}') AS lifestyle_habit_codes,
    coalesce(c.relax_time_codes, '{}') AS relax_time_codes,
    coalesce(c.purchase_barrier_codes, '{}') AS purchase_barrier_codes,
    coalesce(c.delax_source_codes, '{}') AS delax_source_codes,
    coalesce(c.need_codes, '{}') AS need_codes,
    coalesce(c.need_group_codes, '{}') AS need_group_codes,
    coalesce(c.filled_field_count, 0) AS filled_field_count,
    coalesce(c.ai_field_count, 0) AS ai_field_count
"""

_TIMELINE_COLUMNS = """
    f.order_id, f.order_code, f.created_at, f.created_date, f.status,
    f.is_gtc, f.total_after_discount, f.channel_name, f.staff_name,
    f.item_count, f.primary_sku, f.province, f.district,
    f.call_count, f.quality_total_score
"""

# Trần số đơn trả về cho dòng thời gian. Khách mua nhiều nhất trên prod có
# vài chục đơn nên trần này không cắt ai trong thực tế; nó tồn tại để một
# khách dữ liệu bẩn (hàng nghìn đơn) không kéo sập request path.
TIMELINE_MAX_ROWS = 500

# Ô tìm khách: dưới 2 ký tự thì mọi chuỗi đều khớp hàng trăm nghìn dòng và
# index trigram vô dụng — chặn ở tầng query thay vì để người dùng chờ timeout.
SEARCH_MIN_LENGTH = 2
SEARCH_MAX_ROWS = 20


def _run(sql: str, params: list, mart_label: str) -> list[dict]:
    """Chạy SELECT trên mart, dịch lỗi "matview chưa refresh" thành MartNotReady."""
    timeout = getattr(settings, "REPORT_QUERY_TIMEOUT_SECONDS", 120)
    try:
        with statement_timeout(timeout), connection.cursor() as cur:
            cur.execute(sql, params)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except OperationalError as exc:
        # Xem giải thích đầy đủ ở `coverage()`: chỉ SQLSTATE 55000 mới là
        # "matview WITH NO DATA"; mọi OperationalError khác là lỗi thật.
        if getattr(exc.__cause__, "sqlstate", None) != "55000":
            raise
        raise MartNotReady(
            f"Mart {mart_label} chưa sẵn sàng — chạy refresh_dwh_marts trước."
        ) from exc


def search_customers(*, q: str, limit: int = SEARCH_MAX_ROWS) -> list[dict]:
    """Tìm khách cho ô chọn của màn Hồ sơ 360 — khớp mã, tên, hoặc số điện thoại.

    Lọc trên bảng `customers` (có 3 index trigram GIN cho đúng ba cột này) rồi
    mới JOIN vào mart theo khoá chính, chứ không quét mart trước: mart không
    giữ tên lẫn số điện thoại nên quét nó trước là quét vô ích 473k dòng.

    Khách không có dòng nào trong mart sẽ KHÔNG hiện ra. Đó là chủ ý: mart chỉ
    chứa khách đã từng có đơn, và màn hồ sơ 360 không có gì để nói về một khách
    chưa từng mua.
    """
    term = (q or "").strip()
    if len(term) < SEARCH_MIN_LENGTH:
        return []
    pattern = f"%{term}%"
    sql = """
        SELECT cu.id AS customer_id, cu.code, cu.full_name,
               c.orders_gtc, c.total_spent, c.last_purchase_at,
               c.segment_code, c.segment_name, c.era
        FROM customers cu
        JOIN dwh.customer_360 c ON c.customer_id = cu.id
        WHERE cu.deleted_at IS NULL
          AND (cu.code ILIKE %s OR cu.full_name ILIKE %s OR cu.phone_normalized ILIKE %s)
        ORDER BY c.last_purchase_at DESC NULLS LAST, cu.id DESC
        LIMIT %s
    """  # noqa: S608 — không có phần nào nội suy từ input; mọi giá trị là tham số
    return _run(sql, [pattern, pattern, pattern, limit], "dwh.customer_360")


def customer_profile(customer_id: int) -> dict | None:
    """Một dòng chân dung 360 từ `dwh.customer_360`, kèm tên khách.

    Trả None khi khách không có trong mart — hoặc vì id không tồn tại, hoặc vì
    khách chưa từng có đơn nào. Tầng view phân biệt hai ca đó thành 404 chung:
    với người dùng, "không có hồ sơ để xem" là cùng một kết quả.
    """
    sql = f"""
        SELECT {_PROFILE_COLUMNS}, cu.full_name
        FROM dwh.customer_360 c
        JOIN customers cu ON cu.id = c.customer_id
        WHERE c.customer_id = %s AND cu.deleted_at IS NULL
    """  # noqa: S608 — _PROFILE_COLUMNS là literal cố định trong module này
    rows = _run(sql, [customer_id], "dwh.customer_360")
    return rows[0] if rows else None


def customer_timeline(customer_id: int, *, limit: int = TIMELINE_MAX_ROWS) -> list[dict]:
    """Dòng thời gian mua hàng — MỌI đơn của khách, không chỉ đơn GTC.

    Đơn huỷ và đơn hoàn nằm trong kết quả kèm cờ `is_gtc=false`. Lọc chúng đi
    sẽ khiến hồ sơ nói dối theo hướng nguy hiểm nhất: một khách huỷ 5 đơn liên
    tiếp trông y hệt một khách chưa từng đặt gì, đúng lúc sale cần thấy điều
    ngược lại. Việc phân biệt để cho tầng hiển thị làm.
    """
    sql = f"""
        SELECT {_TIMELINE_COLUMNS}
        FROM dwh.fct_order_enriched f
        WHERE f.customer_id = %s
        ORDER BY f.created_at DESC, f.order_id DESC
        LIMIT %s
    """  # noqa: S608 — _TIMELINE_COLUMNS là literal cố định trong module này
    return _run(sql, [customer_id, limit], "dwh.fct_order_enriched")
