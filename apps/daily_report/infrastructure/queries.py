"""SQL đọc mart ai.* cho API Daily Report: trả từng dòng có keyset, Python chỉ
dịch tên cột. Tham số đi qua psycopg (list → ARRAY), KHÔNG format chuỗi từ input.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.conf import settings
from django.db import connection

from apps.common.db_guards import statement_timeout
from apps.daily_report.domain import definitions as d

DICTIONARY_GROUPS = ("trang_thai_don", "trang_thai_hoa_don", "trang_thai_sale", "nguon_don")


def run(sql: str, params: dict) -> list[dict]:
    with statement_timeout(settings.REPORT_QUERY_TIMEOUT_SECONDS):
        with connection.cursor() as cur:
            cur.execute(sql, params)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


# Mọi cột nghiệp vụ của ai.don_hang (migration 0005 + 0013). Không SELECT * để
# cột thêm sau này không lọt ra API mà chưa được dịch tên ở application/rows.py.
ORDER_COLUMNS: tuple[str, ...] = (
    "don_id", "ma_don", "ngay_tao", "ngay_tao_ngay", "ngay_chot", "ngay_chot_ngay",
    "trang_thai_don_ma", "trang_thai_don_text", "trang_thai_hoa_don_ma", "trang_thai_hoa_don_text",
    "la_don_gtc", "loai_don_ma", "loai_don_text", "loai_don_koc_ma", "loai_don_koc_text",
    "nguon_don_ma", "nguon_don_text", "hinh_thuc_giao_ma", "hinh_thuc_giao_text", "phuong_thuc_tt",
    "kenh_id", "kenh_ten", "loai_kenh_text", "nhom_kenh_ban", "chi_nhanh_id",
    "nhan_vien_id", "nhan_vien_ten",
    "khach_id", "ma_khach", "ten_khach", "sdt_che", "tinh_thanh", "quan_huyen", "la_khach_moi",
    "tien_hang", "giam_gia", "phi_ship", "tong_sau_giam", "so_luong",
    "so_dong_sp", "danh_sach_sku", "danh_sach_sku_text", "sku_chinh",
    "trang_thai_sale_ma", "trang_thai_sale_text", "ly_do_khong_chot_ma", "ly_do_khong_chot_text",
    "trang_thai_giao_nghiep_vu_ma", "trang_thai_giao_nghiep_vu_text",
    "nhom_ly_do_bom_ma", "nhom_ly_do_bom_text", "phan_loai_khach_ma", "phan_loai_khach_text",
    "nhu_cau_ma", "nhu_cau_text", "tuoi_text", "tuoi_nguon_text",
    "nghe_nghiep_ma", "nghe_nghiep_text", "muc_do_dau_ma", "muc_do_dau_text",
    "so_cuoc_goi", "tong_giay_goi", "co_transcript", "da_boc_ai", "diem_chat_luong", "dong_bo_luc",
    "chien_dich_fb_id", "quang_cao_fb_id",
)


def _select(alias: str = "") -> str:
    return ", ".join(f"{alias}{c}" for c in ORDER_COLUMNS)


def _invoiced_filter(alias: str = "") -> tuple[str, dict]:
    """Tập đơn = có hoá đơn, không huỷ, loại chi nhánh ẩn — đúng total_q của Báo cáo tổng."""
    sql = f"""
  AND {alias}trang_thai_hoa_don_ma IS NOT NULL
  AND {alias}trang_thai_don_ma IS DISTINCT FROM %(cancelled)s
  AND {alias}trang_thai_hoa_don_ma IS DISTINCT FROM %(cancelled_invoice)s
  AND coalesce({alias}chi_nhanh_id, '') <> ALL(%(excluded_branches)s)"""
    params = {
        "cancelled": d.CANCELLED_STATUS,
        "cancelled_invoice": d.CANCELLED_INVOICE,
        "excluded_branches": list(d.EXCLUDED_BRANCHES),
    }
    return sql, params


def _after_clause(column: str, after: int | None, params: dict) -> str:
    """Keyset: không dùng `%(after)s IS NULL OR …` vì psycopg không suy được kiểu NULL."""
    if after is None:
        return ""
    params["after"] = after
    return f"\n  AND {column} > %(after)s"


def order_rows(d_from: date, d_to: date, *, invoiced_only: bool, after: int | None, limit: int) -> list[dict]:
    """Từng đơn tạo trong kỳ. ``invoiced_only=False`` = tập lead (mọi trạng thái),
    ``True`` = tập đơn có hoá đơn của Báo cáo tổng."""
    params: dict = {"from": d_from, "to": d_to, "limit": limit}
    extra = ""
    if invoiced_only:
        extra, extra_params = _invoiced_filter()
        params.update(extra_params)
    sql = f"""
SELECT {_select()}
FROM ai.don_hang
WHERE ngay_tao_ngay BETWEEN %(from)s AND %(to)s{extra}{_after_clause("don_id", after, params)}
ORDER BY don_id
LIMIT %(limit)s
"""
    return run(sql, params)


def order_line_rows(d_from: date, d_to: date, *, after: int | None, limit: int) -> list[dict]:
    """Từng dòng hàng của tập đơn có hoá đơn (cùng bộ lọc với order_rows(invoiced_only=True)).
    Lọc theo ngày của ĐƠN để hai endpoint chắc chắn cùng tập đơn."""
    extra, params = _invoiced_filter("h.")
    params.update({"from": d_from, "to": d_to, "limit": limit})
    sql = f"""
SELECT l.dong_id, l.don_id, l.ma_don, h.ngay_tao_ngay,
       l.sku, l.ten_sp, l.nhom_sp_text, l.so_luong, l.don_gia, l.thanh_tien
FROM ai.dong_don_hang l
JOIN ai.don_hang h ON h.don_id = l.don_id
WHERE h.ngay_tao_ngay BETWEEN %(from)s AND %(to)s{extra}{_after_clause("l.dong_id", after, params)}
ORDER BY l.dong_id
LIMIT %(limit)s
"""
    return run(sql, params)


def open_lead_rows(as_of: date, lookback_days: int, *, after: int | None, limit: int) -> list[dict]:
    """Lead còn có thể chốt tính đến cuối ngày as_of, kèm ``tuoi`` (ngày)."""
    params: dict = {
        "from": as_of - timedelta(days=lookback_days),
        "as_of": as_of,
        "cancelled": d.CANCELLED_STATUS,
        "terminal": list(d.PIPELINE_TERMINAL_CODES),
        "limit": limit,
    }
    sql = f"""
SELECT {_select()},
       (%(as_of)s::date - ngay_tao_ngay)::int AS tuoi
FROM ai.don_hang
WHERE ngay_tao_ngay BETWEEN %(from)s AND %(as_of)s
  AND trang_thai_hoa_don_ma IS NULL
  AND trang_thai_don_ma IS DISTINCT FROM %(cancelled)s
  AND (trang_thai_sale_ma IS NULL OR NOT (trang_thai_sale_ma = ANY(%(terminal)s))){_after_clause("don_id", after, params)}
ORDER BY don_id
LIMIT %(limit)s
"""
    return run(sql, params)


def campaign_attribution_rows(d_from: date, d_to: date) -> list[dict]:
    """Lead/đơn/doanh thu quy về chiến dịch Facebook theo (ngày tạo × chien_dich_fb_id).

    ``leads`` = số dòng /leads/stats; ``closed`` = trong đó sales_status.bucket = closed;
    ``orders``/``revenue`` = số dòng và Σ ``amounts.total`` của /orders/stats (cùng bộ lọc
    ``_invoiced_filter``). Chỉ đơn có id chiến dịch — phần không nối được xem
    ``attribution_coverage``.
    """
    invoiced, params = _invoiced_filter()
    params.update({"from": d_from, "to": d_to, "closed_codes": list(d.CLOSED_CODES)})
    sql = f"""
SELECT ngay_tao_ngay AS ngay, chien_dich_fb_id,
       count(*)::int AS leads,
       count(*) FILTER (WHERE trang_thai_sale_ma = ANY(%(closed_codes)s))::int AS closed,
       count(*) FILTER (WHERE TRUE{invoiced})::int AS orders,
       coalesce(sum(tong_sau_giam) FILTER (WHERE TRUE{invoiced}), 0) AS revenue
FROM ai.don_hang
WHERE ngay_tao_ngay BETWEEN %(from)s AND %(to)s AND chien_dich_fb_id IS NOT NULL
GROUP BY 1, 2
ORDER BY 1, 2
"""
    return run(sql, params)


def attribution_coverage(d_from: date, d_to: date) -> dict:
    """Bao nhiêu lead trong kỳ nối được chiến dịch — toàn bộ và riêng nguồn landing
    (nguồn duy nhất mang URL có utm)."""
    sql = """
SELECT count(*)::int AS leads,
       count(chien_dich_fb_id)::int AS leads_with_campaign,
       count(*) FILTER (WHERE nguon_don_ma = %(landing)s)::int AS landing_leads,
       count(chien_dich_fb_id) FILTER (WHERE nguon_don_ma = %(landing)s)::int AS landing_leads_with_campaign
FROM ai.don_hang
WHERE ngay_tao_ngay BETWEEN %(from)s AND %(to)s
"""
    return run(sql, {"from": d_from, "to": d_to, "landing": d.LANDING_SOURCE})[0]


def dictionary_rows() -> list[dict]:
    return run(
        "SELECT nhom_ma, ma, nhan FROM ai.tu_dien_ma WHERE nhom_ma = ANY(%(groups)s)"
        " ORDER BY nhom_ma, thu_tu, ma",
        {"groups": list(DICTIONARY_GROUPS)},
    )
