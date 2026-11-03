"""Địa chỉ 2 cấp (34 tỉnh, sau 01/07/2025) lưu trên đơn hàng.

Đơn vẫn giữ đủ 3 cấp (`ward/district/province`) như trước; thêm 5 cột
`*_level2` để hiển thị và để đẩy hoá đơn KiotViet theo cấu trúc mới:
`kiotviet_location` = tỉnh mới ("Tỉnh Đắk Lắk"), `kiotviet_ward` = phường mới.
Cả 3 retailer Kiot đều có 34 tỉnh này trong danh mục /locations (kiểm 17/09/2026)
nên KShip lấy ReceiverLocation/ReceiverWard từ hoá đơn sẽ tự ra 2 cấp.

Quy đổi 3→2 cấp dùng lại `address_lookup.resolve_level2` (trang Tra cứu địa chỉ).
"""

from __future__ import annotations

from apps.orders.address_lookup import resolve_level2
from apps.orders.utils.kiotviet_address_mapping import (
    _load_data,
    _normalize,
    _strip_type,
    resolve_kiotviet_address,
)

LEVEL2_FIELDS = (
    "address_level2",
    "ward_level2",
    "province_level2",
    "ward_code_level2",
    "province_code_level2",
)


def empty_level2_fields() -> dict[str, str]:
    return dict.fromkeys(LEVEL2_FIELDS, "")


def resolve_level2_fields(raw_address: str, parsed: dict) -> dict[str, str]:
    """5 cột `*_level2` từ chuỗi thô + kết quả `parse_legacy_level3`."""
    raw = str(raw_address or "").strip()
    if not raw:
        return empty_level2_fields()
    level3 = {
        "street": str(parsed.get("address", "") or ""),
        "ward": str(parsed.get("ward", "") or ""),
        "district": str(parsed.get("district", "") or ""),
        "province": str(parsed.get("province", "") or ""),
    }
    level2 = resolve_level2(raw, level3)
    return {
        "address_level2": level2.get("address", "") or "",
        "ward_level2": level2.get("ward", "") or "",
        "province_level2": level2.get("province", "") or "",
        "ward_code_level2": level2.get("ward_code", "") or "",
        "province_code_level2": level2.get("province_code", "") or "",
    }


def resolve_level2_edit(address_level2: str) -> dict[str, str]:
    """Sale sửa tay ô "Địa chỉ 2 cấp" → 5 cột `*_level2` + Khu vực/Phường xã Kiot.

    Giữ nguyên chuỗi người dùng gõ ở `address_level2`; chỉ suy tỉnh mới/phường mới
    (parse thẳng theo bộ 34 tỉnh, không đi qua địa chỉ thô). `kiotviet_*` rỗng khi
    không khớp danh mục Kiot — caller giữ giá trị cũ thay vì xoá trống.
    """
    text = str(address_level2 or "").strip()
    fields = {**empty_level2_fields(), "address_level2": text}
    if text:
        level2 = resolve_level2(text, {})
        fields.update(
            ward_level2=level2.get("ward", "") or "",
            province_level2=level2.get("province", "") or "",
            ward_code_level2=level2.get("ward_code", "") or "",
            province_code_level2=level2.get("province_code", "") or "",
        )
    location_name, ward_name = _kiotviet_names_from_level2(fields)
    return {**fields, "kiotviet_location": location_name, "kiotviet_ward": ward_name}


def _kiotviet_names_from_level2(level2: dict) -> tuple[str, str]:
    """(locationName, wardName) theo danh mục Kiot; ("", "") nếu thiếu/không khớp."""
    province = str(level2.get("province_level2", "") or "").strip()
    ward = str(level2.get("ward_level2", "") or "").strip()
    if not province or not ward:
        return "", ""
    data = _load_data()
    ward_norm = _normalize(ward)
    # Kiot có thể gọi khác loại đơn vị so với bộ quy đổi (vd "Thành phố Đồng Nai" trong
    # /locations nhưng vietnamadminunits trả "Tỉnh Đồng Nai") → thử đủ hai tiền tố.
    short = _strip_type(province)
    for candidate in (province, f"Thành phố {short}", f"Tỉnh {short}"):
        hit = data["ward_by_loc"].get((_normalize(candidate), ward_norm))
        if hit:
            return hit[0], hit[1]
    return "", ""


def resolve_kiotviet_names(parsed: dict, raw_address: str, level2: dict) -> dict:
    """Khu vực/Phường xã Kiot: ưu tiên 2 cấp, hụt thì về cách cũ "Tỉnh - Quận/Huyện".

    Chỉ dùng 2 cấp khi CẢ tỉnh mới lẫn phường mới có trong danh mục Kiot — gửi tên
    lạ lên Kiot là hoá đơn fail (KvValidateLocationException), còn thiếu phường thì
    hãng vận chuyển không định tuyến được.
    """
    location_name, ward_name = _kiotviet_names_from_level2(level2)
    if location_name and ward_name:
        return {"locationName": location_name, "wardName": ward_name}
    return resolve_kiotviet_address(parsed, raw_address=raw_address)
