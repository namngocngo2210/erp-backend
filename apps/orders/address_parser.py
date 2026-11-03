import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# Thư viện vietnamadminunits so khớp tỉnh trên chuỗi đã bỏ hết khoảng trắng, nên
# từ khoá tắt 2 ký tự ("hn" = Hà Nội) bắt nhầm xuyên qua ranh giới từ:
# "thành phố vinh nghệ an" → "vin[hn]ghean" → Hà Nội. Ta loại các từ khoá ngắn
# đó khỏi pattern của thư viện và tự nhận "HN" khi nó đứng riêng thành một từ.
_SHORT_PROVINCE_KEYWORD_MAX_LEN = 2
_SHORT_PROVINCE_TOKEN_TO_NAME: dict[str, str] = {}
_library_patched = False

# Các chuỗi placeholder mà nguồn đổ đơn gửi sang khi đơn KHÔNG có địa chỉ.
# Phải coi như rỗng để đơn rơi đúng vào tab tư vấn (không có địa chỉ) ở
# trang Landing thay vì bị tính là "đủ thông tin".
_NO_ADDRESS_PLACEHOLDERS = frozenset(
    {
        "không có địa chỉ giao hàng",
        "khong co dia chi giao hang",
        "không có địa chỉ",
        "khong co dia chi",
        "không có",
        "khong co",
        "tư vấn",
        "tu van",
        "n/a",
        "na",
        "null",
        "none",
        "-",
        "--",
    }
)


def is_placeholder_address(raw_address: str) -> bool:
    """True nếu địa chỉ rỗng hoặc là một chuỗi placeholder 'không có địa chỉ'."""
    value = " ".join((raw_address or "").split()).strip().lower()
    if not value:
        return True
    return value in _NO_ADDRESS_PLACEHOLDERS


def normalize_raw_address(raw_address: str) -> str:
    """Chuẩn hoá địa chỉ thô: trả về chuỗi rỗng nếu là placeholder 'không có địa chỉ'."""
    if is_placeholder_address(raw_address):
        return ""
    return raw_address or ""


def format_standard_address(parsed: dict[str, str]) -> str:
    """Format parsed address components into standard Vietnamese address format.

    Input: dict with keys address, ward, district, province (from parse_legacy_level3).
    Output: "street, Phường/Xã, Quận/Huyện, Tỉnh/Thành phố"
    Restores Vietnamese diacritics for the street part.
    """
    from apps.orders.utils.vn_street_diacritics import restore_diacritics

    parts = []
    street = (parsed.get("address") or "").strip()
    if street:
        parts.append(restore_diacritics(street))
    for key in ("ward", "district", "province"):
        val = (parsed.get(key) or "").strip()
        if val:
            parts.append(val)
    return ", ".join(parts)


def _truncate(value: Any, max_len: int = 300) -> str:
    text = str(value)
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}...<truncated:{len(text) - max_len}>"


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "full_name", "label", "title", "value"):
            nested = value.get(key)
            if nested is not None:
                text = str(nested).strip()
                if text:
                    return text
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _as_text(item)
            if text:
                return text
        return ""
    return str(value).strip()


def _pick_value(data: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = _as_text(value)
        if text:
            return text
    return ""


def _deep_pick_value(data: Any, keys: list[str]) -> str:
    if isinstance(data, dict):
        direct = _pick_value(data, keys)
        if direct:
            return direct
        for value in data.values():
            nested = _deep_pick_value(value, keys)
            if nested:
                return nested
    if isinstance(data, (list, tuple)):
        for item in data:
            nested = _deep_pick_value(item, keys)
            if nested:
                return nested
    return ""


def _pick_from_object(obj: Any, keys: list[str]) -> str:
    for key in keys:
        try:
            value = getattr(obj, key)
        except Exception:
            continue
        text = _as_text(value)
        if text:
            return text
    return ""


def _ascii_tokens(value: str) -> str:
    """Bỏ dấu, thường hoá, giữ ranh giới từ (khác key của thư viện là dính liền)."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _patch_short_province_keywords() -> None:
    """Loại từ khoá tỉnh quá ngắn khỏi regex của thư viện (chạy một lần)."""
    global _library_patched
    if _library_patched:
        return
    _library_patched = True
    try:
        from vietnamadminunits.parser import parser_legacy  # type: ignore
    except Exception:
        return
    keywords: list[str] = []
    for info in parser_legacy.DICT_PROVINCE.values():
        for kw in info.get("provinceKeywords", []):
            if len(kw) <= _SHORT_PROVINCE_KEYWORD_MAX_LEN:
                _SHORT_PROVINCE_TOKEN_TO_NAME[kw] = info["province"]
            else:
                keywords.append(kw)
    if not _SHORT_PROVINCE_TOKEN_TO_NAME:
        return
    keywords.sort(key=len, reverse=True)
    parser_legacy.PATTERN_PROVINCE = re.compile("|".join(keywords), flags=re.IGNORECASE)
    logger.info(
        "address.parse.short_province_keywords_patched tokens=%s",
        sorted(_SHORT_PROVINCE_TOKEN_TO_NAME),
    )


def _expand_short_province(value: str) -> str:
    """Thay từ khoá tắt đứng riêng (vd. "... Cầu Giấy HN") bằng tên tỉnh đầy đủ ở cuối.

    Thư viện ưu tiên từ khoá tỉnh bên phải nhất, nên ghi rõ tỉnh vào cuối giúp nó
    không bắt nhầm huyện/tỉnh khác trong phần còn lại của địa chỉ.
    """
    _patch_short_province_keywords()
    if not _SHORT_PROVINCE_TOKEN_TO_NAME:
        return value
    tokens = _ascii_tokens(value).split()
    for token in reversed(tokens):
        name = _SHORT_PROVINCE_TOKEN_TO_NAME.get(token)
        if name:
            stripped = re.sub(rf"\b{re.escape(token)}\b", "", value, flags=re.IGNORECASE)
            stripped = re.sub(r"\s+", " ", stripped).strip(" ,-")
            return f"{stripped}, {name}" if stripped else name
    return value


def _resolve_parse_address():
    try:
        from vietnamadminunits import parse_address  # type: ignore

        return parse_address
    except ImportError as exc_primary:
        try:
            from vietnam_admin_units import parse_address  # type: ignore

            return parse_address
        except ImportError:
            logger.warning(
                "address.parse.library_missing: install required deps for vietnamadminunits (missing=%s)",
                _truncate(exc_primary),
            )
            return None


def parse_legacy_level3(raw_address: str) -> dict[str, str]:
    value = (raw_address or "").strip()
    if not value:
        logger.debug("address.parse.empty_input")
        return {
            "address": "",
            "ward": "",
            "district": "",
            "province": "",
            "short_ward": "",
            "short_district": "",
            "short_province": "",
            "ward_code": "",
            "district_code": "",
            "province_code": "",
        }

    parse_address = _resolve_parse_address()
    if parse_address is None:
        return {
            "address": value,
            "ward": "",
            "district": "",
            "province": "",
            "short_ward": "",
            "short_district": "",
            "short_province": "",
            "ward_code": "",
            "district_code": "",
            "province_code": "",
        }

    try:
        logger.debug("address.parse.start raw=%s", _truncate(value))
        parsed = parse_address(_expand_short_province(value), mode="LEGACY", level=3)
        logger.debug(
            "address.parse.library_result type=%s payload=%s",
            type(parsed).__name__,
            _truncate(parsed, max_len=800),
        )
    except Exception:
        logger.exception("address.parse.failed raw=%s", _truncate(value))
        return {
            "address": value,
            "ward": "",
            "district": "",
            "province": "",
            "short_ward": "",
            "short_district": "",
            "short_province": "",
            "ward_code": "",
            "district_code": "",
            "province_code": "",
        }

    if isinstance(parsed, dict):
        address_keys = [
            "address",
            "street",
            "line",
            "detail",
            "full_address",
            "level_1",
        ]
        ward_keys = [
            "ward",
            "ward_name",
            "commune",
            "xa",
            "phuong",
            "phuong_xa",
            "xa_phuong",
            "level_2",
            "administrative_level_2",
        ]
        district_keys = [
            "district",
            "district_name",
            "quan",
            "huyen",
            "quan_huyen",
            "huyen_quan",
            "administrative_level_district",
        ]
        province_keys = [
            "province",
            "province_name",
            "city",
            "city_name",
            "tinh",
            "tinh_thanh",
            "thanh_pho",
            "level_3",
            "administrative_level_3",
        ]
        short_ward_keys = ["short_ward", "ward_short_name"]
        short_district_keys = ["short_district", "district_short_name"]
        short_province_keys = ["short_province", "province_short_name"]
        ward_code_keys = ["ward_code", "level3_id", "administrative_level_3_code"]
        district_code_keys = ["district_code", "level2_id", "administrative_level_2_code"]
        province_code_keys = ["province_code", "level1_id", "administrative_level_1_code"]

        address = _pick_value(
            parsed,
            address_keys,
        )
        ward = _pick_value(parsed, ward_keys) or _deep_pick_value(parsed, ward_keys)
        district = _pick_value(parsed, district_keys) or _deep_pick_value(parsed, district_keys)
        province = _pick_value(parsed, province_keys) or _deep_pick_value(parsed, province_keys)
        short_ward = _pick_value(parsed, short_ward_keys) or _deep_pick_value(parsed, short_ward_keys)
        short_district = _pick_value(parsed, short_district_keys) or _deep_pick_value(parsed, short_district_keys)
        short_province = _pick_value(parsed, short_province_keys) or _deep_pick_value(parsed, short_province_keys)
        ward_code = _pick_value(parsed, ward_code_keys) or _deep_pick_value(parsed, ward_code_keys)
        district_code = _pick_value(parsed, district_code_keys) or _deep_pick_value(parsed, district_code_keys)
        province_code = _pick_value(parsed, province_code_keys) or _deep_pick_value(parsed, province_code_keys)
        logger.debug(
            "address.parse.dict_extract raw=%s keys=%s address=%s ward=%s district=%s province=%s short_ward=%s short_district=%s short_province=%s ward_code=%s district_code=%s province_code=%s",
            _truncate(value),
            list(parsed.keys())[:50],
            address,
            ward,
            district,
            province,
            short_ward,
            short_district,
            short_province,
            ward_code,
            district_code,
            province_code,
        )
        result = {
            "address": address or value,
            "ward": ward,
            "district": district,
            "province": province,
            "short_ward": short_ward,
            "short_district": short_district,
            "short_province": short_province,
            "ward_code": ward_code,
            "district_code": district_code,
            "province_code": province_code,
        }
        logger.debug("address.parse.mapped_dict raw=%s result=%s", _truncate(value), result)
        return result

    # Some library versions return a rich object with attributes instead of dict.
    address_keys = [
        "address",
        "street",
        "line",
        "detail",
        "full_address",
        "level_1",
    ]
    ward_keys = [
        "ward",
        "ward_name",
        "commune",
        "xa",
        "phuong",
        "phuong_xa",
        "xa_phuong",
        "level_2",
        "administrative_level_2",
        "short_ward",
    ]
    district_keys = [
        "district",
        "district_name",
        "quan",
        "huyen",
        "quan_huyen",
        "huyen_quan",
        "administrative_level_district",
        "short_district",
    ]
    province_keys = [
        "province",
        "province_name",
        "city",
        "city_name",
        "tinh",
        "tinh_thanh",
        "thanh_pho",
        "level_3",
        "administrative_level_3",
        "short_province",
    ]
    short_ward_keys = ["short_ward", "ward_short_name"]
    short_district_keys = ["short_district", "district_short_name"]
    short_province_keys = ["short_province", "province_short_name"]
    ward_code_keys = ["ward_code", "level3_id", "administrative_level_3_code"]
    district_code_keys = ["district_code", "level2_id", "administrative_level_2_code"]
    province_code_keys = ["province_code", "level1_id", "administrative_level_1_code"]

    object_address = _pick_from_object(parsed, address_keys)
    object_ward = _pick_from_object(parsed, ward_keys)
    object_district = _pick_from_object(parsed, district_keys)
    object_province = _pick_from_object(parsed, province_keys)
    object_short_ward = _pick_from_object(parsed, short_ward_keys)
    object_short_district = _pick_from_object(parsed, short_district_keys)
    object_short_province = _pick_from_object(parsed, short_province_keys)
    object_ward_code = _pick_from_object(parsed, ward_code_keys)
    object_district_code = _pick_from_object(parsed, district_code_keys)
    object_province_code = _pick_from_object(parsed, province_code_keys)
    logger.debug(
        "address.parse.object_extract raw=%s parsed_type=%s address=%s ward=%s district=%s province=%s short_ward=%s short_district=%s short_province=%s ward_code=%s district_code=%s province_code=%s",
        _truncate(value),
        type(parsed).__name__,
        object_address,
        object_ward,
        object_district,
        object_province,
        object_short_ward,
        object_short_district,
        object_short_province,
        object_ward_code,
        object_district_code,
        object_province_code,
    )
    if (
        object_address
        or object_ward
        or object_district
        or object_province
        or object_ward_code
        or object_district_code
        or object_province_code
    ):
        result = {
            "address": object_address or value,
            "ward": object_ward,
            "district": object_district,
            "province": object_province,
            "short_ward": object_short_ward,
            "short_district": object_short_district,
            "short_province": object_short_province,
            "ward_code": object_ward_code,
            "district_code": object_district_code,
            "province_code": object_province_code,
        }
        logger.debug("address.parse.mapped_object raw=%s result=%s", _truncate(value), result)
        return result

    if isinstance(parsed, (list, tuple)):
        address = str(parsed[0]).strip() if len(parsed) > 0 and parsed[0] is not None else value
        ward = str(parsed[1]).strip() if len(parsed) > 1 and parsed[1] is not None else ""
        district = str(parsed[2]).strip() if len(parsed) > 2 and parsed[2] is not None else ""
        province = str(parsed[3]).strip() if len(parsed) > 3 and parsed[3] is not None else ""
        result = {
            "address": address or value,
            "ward": ward,
            "district": district,
            "province": province,
            "short_ward": "",
            "short_district": "",
            "short_province": "",
            "ward_code": "",
            "district_code": "",
            "province_code": "",
        }
        logger.debug("address.parse.mapped_seq raw=%s result=%s", _truncate(value), result)
        return result

    result = {
        "address": value,
        "ward": "",
        "district": "",
        "province": "",
        "short_ward": "",
        "short_district": "",
        "short_province": "",
        "ward_code": "",
        "district_code": "",
        "province_code": "",
    }
    logger.debug(
        "address.parse.unknown_shape raw=%s parsed_type=%s fallback=%s",
        _truncate(value),
        type(parsed).__name__,
        result,
    )
    return result
