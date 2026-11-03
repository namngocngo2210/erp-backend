"""Header/field/channel mapping tables for Excel & text order import.

Extracted verbatim from import_excel.py to keep the parser module focused on logic.
"""

from __future__ import annotations

import unicodedata
from typing import Any


def _normalize_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("đ", "d")
    return " ".join(text.split())


IMPORT_TEMPLATE_HEADERS = [
    "Kênh Kiot",
    "Mã nhân viên",
    "Kênh Bán",
    "Tên KH",
    "Giới Tính",
    "SĐT",
    "Địa chỉ",
    "Mã SP",
    "SL",
    "Giá",
    "Giảm Giá",
    "Giảm Giá Phiếu Đặt",
    "Trọng Lượng",
    "Kích Thước",
    "Kho",
    "Loại Đơn",
    "Khách TT Trước",
]

IMPORT_TEMPLATE_SAMPLE_ROW = {
    "Kênh Kiot": "old_kiot",
    "Mã nhân viên": "admin",
    "Kênh Bán": "Facebook",
    "Tên KH": "Nguyen Van A",
    "Giới Tính": "true",
    "SĐT": "0909123456",
    "Địa chỉ": "123 Nguyen Trai, Quan 1, TP HCM",
    "Mã SP": "BNC02,TSM01",
    "SL": "2,2",
    "Giá": "500000,1000000",
    "Giảm Giá": "100000",
    "Giảm Giá Phiếu Đặt": "0",
    "Trọng Lượng": "0.5",
    "Kích Thước": "50,50,50",
    "Kho": "",
    "Loại Đơn": "đơn thường",
    "Khách TT Trước": "0",
}

IMPORT_PAYLOAD_FIELDS = [
    "kiotviet_channel_id",
    "staff_username",
    "channel_code",
    "name",
    "customer_gender",
    "phone",
    "raw_address",
    "sku_list",
    "quantity_list",
    "price_list",
    "discount_amount",
    "booking_voucher_discount",
    "weight",
    "dimensions",
    "branch_id",
    "order_type",
    "customer_prepaid",
]

# Layout positional riêng cho import đơn KOC (19 cột). Khác IMPORT_PAYLOAD_FIELDS:
# thêm "customer_code" (Mã KH) ở cột 2, hai placeholder cho "Khu vực"/"Phường-Xã"
# (chỉ chiếm vị trí, không map vào payload — địa chỉ vẫn parse từ raw_address), và
# đảo thứ tự booking_voucher_discount/discount_amount. Không có cột customer_prepaid.
KOC_IMPORT_PAYLOAD_FIELDS = [
    "kiotviet_channel_id",
    "customer_code",
    "staff_username",
    "channel_code",
    "name",
    "customer_gender",
    "phone",
    "raw_address",
    "_ignore_area",
    "_ignore_ward",
    "sku_list",
    "quantity_list",
    "price_list",
    "booking_voucher_discount",
    "discount_amount",
    "weight",
    "dimensions",
    "branch_id",
    "order_type",
]

HEADER_ALIASES = {
    "kênh kiot": "kiotviet_channel_id",
    "kenh kiot": "kiotviet_channel_id",
    "kênh kiotviet": "kiotviet_channel_id",
    "kenh kiotviet": "kiotviet_channel_id",
    "kiotviet_channel_id": "kiotviet_channel_id",
    "mã nhân viên": "staff_username",
    "ma nhan vien": "staff_username",
    "staff_username": "staff_username",
    "username": "staff_username",
    "kênh bán": "channel_code",
    "kenh ban": "channel_code",
    "channel_code": "channel_code",
    "tên kh": "name",
    "ten kh": "name",
    "name": "name",
    "giới tính": "customer_gender",
    "gioi tinh": "customer_gender",
    "customer_gender": "customer_gender",
    "sđt": "phone",
    "sdt": "phone",
    "phone": "phone",
    "địa chỉ": "raw_address",
    "dia chi": "raw_address",
    "địa chỉ giao hàng": "raw_address",
    "dia chi giao hang": "raw_address",
    "raw_address": "raw_address",
    "mã sp": "sku_list",
    "ma sp": "sku_list",
    "sku": "sku_list",
    "sku_list": "sku_list",
    "sl": "quantity_list",
    "số lượng": "quantity_list",
    "so luong": "quantity_list",
    "quantity": "quantity_list",
    "quantity_list": "quantity_list",
    "giá": "price_list",
    "gia": "price_list",
    "đơn giá": "price_list",
    "don gia": "price_list",
    "price": "price_list",
    "unit_price": "price_list",
    "total_amount": "price_list",
    "price_list": "price_list",
    "giảm giá": "discount_amount",
    "giam gia": "discount_amount",
    "discount_amount": "discount_amount",
    "giảm giá phiếu đặt": "booking_voucher_discount",
    "giam gia phieu dat": "booking_voucher_discount",
    "bookin_voucher_discount": "booking_voucher_discount",
    "booking_voucher_discount": "booking_voucher_discount",
    "trọng lượng": "weight",
    "trong luong": "weight",
    "weight": "weight",
    "kích thước": "dimensions",
    "kich thuoc": "dimensions",
    "dimensions": "dimensions",
    "kho (branch_id)": "branch_id",
    "kho": "branch_id",
    "branch_id": "branch_id",
    "loại đơn (order_type)": "order_type",
    "loai don (order_type)": "order_type",
    "loại đơn": "order_type",
    "loai don": "order_type",
    "order_type": "order_type",
    "khách tt trước": "customer_prepaid",
    "khach tt truoc": "customer_prepaid",
    "customer_prepaid": "customer_prepaid",
}

CHANNEL_CODE_MAP = {
    "facebook cường": "facebook-cuong",
    "facebook cuong": "facebook-cuong",
    "facebook lợi": "facebook-loi",
    "facebook loi": "facebook-loi",
    "facebook": "facebook",
    "web": "web",
    "google ads": "google-ads",
    "hàng mẫu koc": "koc",
    "hang mau koc": "koc",
    "google ads/web": "google-ads-web",
    "google ads / web": "google-ads-web",
    "facebook lan": "facebook-lan",
    "sale fb": "sale-fb",
    "facebook huy": "facebook-huy",
    "facebook thành": "facebook-thanh",
    "facebook thanh": "facebook-thanh",
    "facebook ân": "facebook-an",
    "facebook an": "facebook-an",
    "facebook đức": "facebook-duc",
    "facebook duc": "facebook-duc",
    "facebook khánh": "facebook-khanh",
    "facebook khanh": "facebook-khanh",
}

KIOT_CHANNEL_ID_MAP = {
    "old_kiot": 5,
    "new_kiot": 4,
    "hkd_kiot": 6,
    "old kiot": 5,
    "new kiot": 4,
    "hkd kiot": 6,
}

NORMALIZED_HEADER_ALIASES = {_normalize_key(k): v for k, v in HEADER_ALIASES.items()}
NORMALIZED_CHANNEL_CODE_MAP = {_normalize_key(k): v for k, v in CHANNEL_CODE_MAP.items()}
NORMALIZED_KIOT_CHANNEL_ID_MAP = {_normalize_key(k): v for k, v in KIOT_CHANNEL_ID_MAP.items()}
