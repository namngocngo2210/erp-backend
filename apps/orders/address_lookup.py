"""Tra cứu địa chỉ thô → địa chỉ chuẩn 3 cấp (63 tỉnh, trước 01/07/2025) và
2 cấp (34 tỉnh, sau sáp nhập).

Dùng cho trang Tiện ích › Tra cứu địa chỉ. Không đụng tới dữ liệu đơn hàng.

- 3 cấp: đi qua đúng pipeline hiện có của đơn hàng (`parse_legacy_level3` +
  `format_standard_address`) để kết quả khớp với những gì hệ thống đang lưu.
- 2 cấp: ưu tiên `vietnamadminunits.convert_address` (bảng quy đổi 63→34 tỉnh
  kèm phường/xã mới). Nếu phường cũ bị tách và có số nhà/đường, thư viện gọi
  geocoder ArcGIS (mạng ngoài) để chọn phường mới gần nhất — lỗi mạng thì thử
  lại KHÔNG kèm đường để rơi về phường mặc định. Cuối cùng mới parse thẳng theo
  bộ 34 tỉnh (`FROM_2025`) cho địa chỉ khách đã ghi theo cấu trúc mới.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.orders.address_parser import (
    _expand_short_province,
    format_standard_address,
    parse_legacy_level3,
)

logger = logging.getLogger(__name__)

# Nguồn của kết quả 2 cấp — FE hiện chú thích tương ứng.
SOURCE_CONVERTED = "converted"  # quy đổi từ địa chỉ 3 cấp
SOURCE_PARSED = "parsed"  # parse thẳng theo bộ 34 tỉnh
SOURCE_NONE = ""

# Tên tỉnh đã đổi sau khi `vietnamadminunits` chốt dữ liệu — thư viện vẫn trả tên cũ.
PROVINCE_RENAMES = {
    "Tỉnh Quảng Ninh": "Thành phố Quảng Ninh",
}


def _empty_level3() -> dict[str, str]:
    return {
        "address": "",
        "street": "",
        "ward": "",
        "district": "",
        "province": "",
        "ward_code": "",
        "district_code": "",
        "province_code": "",
    }


def _empty_level2() -> dict[str, str]:
    return {
        "address": "",
        "street": "",
        "ward": "",
        "province": "",
        "ward_code": "",
        "province_code": "",
        "source": SOURCE_NONE,
    }


def _unit_to_level2(unit: Any, source: str) -> dict[str, str]:
    street = str(getattr(unit, "street", "") or "")
    ward = str(getattr(unit, "ward", "") or "")
    province = str(getattr(unit, "province", "") or "")
    province = PROVINCE_RENAMES.get(province, province)
    return {
        "address": ", ".join(p for p in (street, ward, province) if p),
        "street": street,
        "ward": ward,
        "province": province,
        "ward_code": str(getattr(unit, "ward_code", "") or ""),
        "province_code": str(getattr(unit, "province_code", "") or ""),
        "source": source,
    }


def _try_import_library():
    try:
        from vietnamadminunits import ConvertMode, ParseMode, convert_address, parse_address

        return convert_address, parse_address, ConvertMode, ParseMode
    except ImportError:
        logger.warning("address.lookup.library_missing")
        return None


def _convert_to_2025(convert_address, ConvertMode, text: str) -> Any | None:
    try:
        return convert_address(text, mode=ConvertMode.CONVERT_2025)
    except Exception:
        logger.info("address.lookup.convert_failed text=%s", text[:120], exc_info=True)
        return None


def resolve_level2(raw_address: str, level3: dict[str, str]) -> dict[str, str]:
    """Địa chỉ 2 cấp từ chuỗi thô + kết quả 3 cấp đã parse."""
    lib = _try_import_library()
    if lib is None:
        return _empty_level2()
    convert_address, parse_address, ConvertMode, ParseMode = lib

    expanded = _expand_short_province(raw_address)
    converted = _convert_to_2025(convert_address, ConvertMode, expanded)

    # Geocoder lỗi/timeout → thử lại không kèm đường: thư viện sẽ lấy phường mới
    # mặc định của phường cũ thay vì bỏ cuộc.
    if converted is None and level3.get("ward"):
        no_street = ", ".join(
            p for p in (level3.get("ward"), level3.get("district"), level3.get("province")) if p
        )
        converted = _convert_to_2025(convert_address, ConvertMode, no_street)
        if converted is not None and level3.get("street") and not getattr(converted, "street", None):
            converted.street = level3["street"]

    if converted is not None and getattr(converted, "ward", None):
        return _unit_to_level2(converted, SOURCE_CONVERTED)

    # Địa chỉ đã ghi theo cấu trúc mới (vd "Phường Xuân Hòa, TP.HCM") — parser
    # 63 tỉnh không thấy phường, nhưng bộ 34 tỉnh thì có.
    try:
        parsed_new = parse_address(expanded, mode=ParseMode.FROM_2025)
    except Exception:
        logger.info("address.lookup.parse_2025_failed text=%s", expanded[:120], exc_info=True)
        parsed_new = None

    if parsed_new is not None and getattr(parsed_new, "ward", None):
        return _unit_to_level2(parsed_new, SOURCE_PARSED)
    if converted is not None and getattr(converted, "province", None):
        return _unit_to_level2(converted, SOURCE_CONVERTED)
    if parsed_new is not None and getattr(parsed_new, "province", None):
        return _unit_to_level2(parsed_new, SOURCE_PARSED)
    return _empty_level2()


def lookup_address(raw_address: str) -> dict[str, Any]:
    value = (raw_address or "").strip()
    if not value:
        return {"raw_address": "", "level3": _empty_level3(), "level2": _empty_level2()}

    parsed = parse_legacy_level3(value)
    level3 = _empty_level3()
    level3.update(
        {
            "address": format_standard_address(parsed) if parsed.get("province") else "",
            "street": str(parsed.get("address", "") or ""),
            "ward": str(parsed.get("ward", "") or ""),
            "district": str(parsed.get("district", "") or ""),
            "province": str(parsed.get("province", "") or ""),
            "ward_code": str(parsed.get("ward_code", "") or ""),
            "district_code": str(parsed.get("district_code", "") or ""),
            "province_code": str(parsed.get("province_code", "") or ""),
        }
    )
    level2 = resolve_level2(value, level3)
    return {"raw_address": value, "level3": level3, "level2": level2}
