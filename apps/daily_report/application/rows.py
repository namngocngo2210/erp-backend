"""Dịch dòng SQL của mart ``ai.*`` sang dòng JSON tiếng Anh cho Hermes.

Thuần dict → dict, không chạm DB. Cột ``*_ma`` + ``*_text`` gộp thành
``{"code", "label"}``; nơi CRM có luật rổ (trạng thái đơn, trạng thái sale) kèm
``"bucket"`` để Hermes cộng theo rổ đúng như màn hình mà không phải tra
``/definitions`` từng dòng. Object con có mọi trường NULL vẫn trả object đủ khoá
(đường dẫn cố định); ngoại lệ ``staff`` và ``channel`` trả ``null`` khi không có id —
đúng cách API cũ biểu diễn "chưa gán".
"""

from __future__ import annotations

from datetime import date, datetime

from apps.daily_report.domain import definitions as d
from apps.daily_report.domain.periods import TZ, iso


def _ts(value: datetime | None) -> str | None:
    return value.astimezone(TZ).isoformat() if value else None


def _day(value: date | None) -> str | None:
    return iso(value) if value else None


def _vnd(value) -> int | None:
    return int(round(value)) if value is not None else None


def _num(value) -> float | None:
    return float(value) if value is not None else None


def _code_label(code, label) -> dict:
    return {"code": code, "label": label}


def _ref(id_, name) -> dict | None:
    return {"id": id_, "name": name} if id_ is not None else None


def _channel(r: dict) -> dict | None:
    if r["kenh_id"] is None:
        return None
    return {"id": r["kenh_id"], "name": r["kenh_ten"], "type": r["loai_kenh_text"]}


def _sales_status(r: dict) -> dict:
    code = r["trang_thai_sale_ma"]
    # NULL = chưa đánh giá (số gộp cũ gọi là unassessed), không phải rổ "other".
    return {**_code_label(code, r["trang_thai_sale_text"]), "bucket": d.bucket_of(code) if code else None}


def _common(r: dict) -> dict:
    """Phần dùng chung giữa dòng đơn và dòng lead mở."""
    return {
        "id": r["don_id"],
        "code": r["ma_don"],
        "created_at": _ts(r["ngay_tao"]),
        "created_date": _day(r["ngay_tao_ngay"]),
        "source": _code_label(r["nguon_don_ma"], r["nguon_don_text"]),
        "channel": _channel(r),
        "channel_group": r["nhom_kenh_ban"],
        "fb_campaign_id": r["chien_dich_fb_id"],
        "fb_ad_id": r["quang_cao_fb_id"],
        "staff": _ref(r["nhan_vien_id"], r["nhan_vien_ten"]),
        "customer": {
            "id": r["khach_id"],
            "code": r["ma_khach"],
            "name": r["ten_khach"],
            "phone_masked": r["sdt_che"],
            "province": r["tinh_thanh"],
            "district": r["quan_huyen"],
            "is_new": r["la_khach_moi"],
        },
        "amounts": {
            "goods": _vnd(r["tien_hang"]),
            "discount": _vnd(r["giam_gia"]),
            "shipping": _vnd(r["phi_ship"]),
            "total": _vnd(r["tong_sau_giam"]),
            "quantity": r["so_luong"],
        },
        "items": {
            "line_count": r["so_dong_sp"],
            "skus": list(r["danh_sach_sku"] or []),
            "skus_label": r["danh_sach_sku_text"],
            "main_sku": r["sku_chinh"],
        },
        "sales_status": _sales_status(r),
        "calls": {
            "count": r["so_cuoc_goi"],
            "seconds": r["tong_giay_goi"],
            "has_transcript": r["co_transcript"],
            "ai_extracted": r["da_boc_ai"],
            "quality_score": _num(r["diem_chat_luong"]),
        },
        "synced_at": _ts(r["dong_bo_luc"]),
        "cursor": r["don_id"],
    }


def order_row(r: dict) -> dict:
    """Dòng của /leads/stats và /orders/stats (cùng shape, khác bộ lọc)."""
    status = r["trang_thai_don_ma"]
    return {
        **_common(r),
        "closed_at": _ts(r["ngay_chot"]),
        "closed_date": _day(r["ngay_chot_ngay"]),
        "order_status": {**_code_label(status, r["trang_thai_don_text"]), "bucket": d.order_bucket_of(status)},
        "invoice_status": _code_label(r["trang_thai_hoa_don_ma"], r["trang_thai_hoa_don_text"]),
        "is_valid_revenue": bool(r["la_don_gtc"]),
        "order_type": _code_label(r["loai_don_ma"], r["loai_don_text"]),
        "koc_type": _code_label(r["loai_don_koc_ma"], r["loai_don_koc_text"]),
        "delivery_method": _code_label(r["hinh_thuc_giao_ma"], r["hinh_thuc_giao_text"]),
        "payment_method": r["phuong_thuc_tt"],
        "branch_id": r["chi_nhanh_id"],
        "reason_not_closed": _code_label(r["ly_do_khong_chot_ma"], r["ly_do_khong_chot_text"]),
        "delivery_business_status": _code_label(r["trang_thai_giao_nghiep_vu_ma"], r["trang_thai_giao_nghiep_vu_text"]),
        "bounce_reason_group": _code_label(r["nhom_ly_do_bom_ma"], r["nhom_ly_do_bom_text"]),
        "customer_class": _code_label(r["phan_loai_khach_ma"], r["phan_loai_khach_text"]),
        "needs": {"codes": list(r["nhu_cau_ma"] or []), "label": r["nhu_cau_text"]},
        "age": {"label": r["tuoi_text"], "source": r["tuoi_nguon_text"]},
        "occupation": _code_label(r["nghe_nghiep_ma"], r["nghe_nghiep_text"]),
        "pain_level": _code_label(r["muc_do_dau_ma"], r["muc_do_dau_text"]),
    }


def open_lead_row(r: dict) -> dict:
    """Dòng của /pipeline: bản rút gọn + tuổi lead (ngày) tính đến as_of."""
    return {**_common(r), "age_days": r["tuoi"]}


def order_line_row(r: dict) -> dict:
    """Dòng của /revenue: 1 sản phẩm trong 1 đơn."""
    return {
        "line_id": r["dong_id"],
        "order_id": r["don_id"],
        "order_code": r["ma_don"],
        "created_date": _day(r["ngay_tao_ngay"]),
        "sku": r["sku"],
        "product_name": r["ten_sp"],
        "product_group": r["nhom_sp_text"],
        "quantity": r["so_luong"],
        "unit_price": _vnd(r["don_gia"]),
        "amount": _vnd(r["thanh_tien"]),
        "cursor": r["dong_id"],
    }
