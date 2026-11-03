import hashlib
from collections import defaultdict
from functools import lru_cache
from typing import Any

from .catalog_core import (
    AddressCatalog,
    DistrictNode,
    ProvinceNode,
    WardNode,
    _clean_text,
    _district_key,
    _label,
    _legacy_nested_to_canonical,
    _legacy_object_to_canonical,
    _load_sorted_source,
    _normalize_text,
    _ward_key,
    logger,
)

def _extract_ward_from_address(raw_address: str) -> str:
    """Try to extract a ward name from raw address text.

    Searches all wards in the raw text by checking if any normalized ward name
    from the address appears in the catalog. Returns the longest match.
    This is only used as fallback when parser didn't return ward info.
    """
    # This function returns a normalized ward name to match against catalog.
    # The actual matching is done by the caller searching wards in the province.
    # We extract candidate names from "Phường/Xã/Thị trấn + words" patterns.
    import re as _re
    text = (raw_address or "").strip()
    if not text:
        return ""
    # Match ward type + following words, stop at comma
    # Supports abbreviations: "p " / "P " / "P." for Phường, "x " for Xã
    pattern = r"(?:[Pp]hường|[Pp]\.?\s|[Xx]ã|[Xx]\s|[Tt]hị\s*[Tt]rấn)\s*([^,]+)"
    match = _re.search(pattern, text)
    if not match:
        return ""
    raw_name = match.group(1).strip()
    # Split at known boundary keywords and take the first part
    raw_name = _re.split(
        r"\s+(?:[Qq]uận|[Hh]uyện|[Tt]ỉnh|[Tt][Pp]\.?\s|[Tt]hành\s*[Pp]hố|[Tt]hị\s*[Xx]ã|[Tt]hị\s*[Tt]rấn)",
        raw_name,
    )[0].strip()
    # Also split at known province/city names to avoid capturing them
    # Limit to max 4 words (ward names are typically 1-3 words)
    words = raw_name.split()
    if len(words) > 4:
        raw_name = " ".join(words[:4])
    if not raw_name:
        return ""
    return _normalize_text(raw_name)


def _as_canonical(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("provinces"), list) and isinstance(payload.get("districts"), list):
        return payload

    if isinstance(payload, list) and payload:
        first_item = payload[0]
        if isinstance(first_item, dict):
            return _legacy_object_to_canonical([item for item in payload if isinstance(item, dict)])
        if isinstance(first_item, (list, tuple)):
            return _legacy_nested_to_canonical(payload)

    if isinstance(payload, list):
        return _legacy_nested_to_canonical(payload)
    return {"provinces": [], "districts": [], "wards": [], "indexes": {}}


def _build_catalog_from_canonical(canonical: dict[str, Any]) -> AddressCatalog:
    provinces_rows = canonical.get("provinces") or []
    districts_rows = canonical.get("districts") or []
    wards_rows = canonical.get("wards") or []
    indexes = canonical.get("indexes") or {}

    districts_by_province_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for district in districts_rows:
        province_code = _clean_text(district.get("province_code"))
        if province_code:
            districts_by_province_rows[province_code].append(district)

    wards_by_district_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ward in wards_rows:
        district_key = _clean_text(ward.get("district_key"))
        if not district_key:
            district_key = _district_key(_clean_text(ward.get("province_code")), _clean_text(ward.get("district_code")))
        if district_key:
            wards_by_district_rows[district_key].append(ward)

    district_keys_by_province_idx = indexes.get("district_keys_by_province") or {}
    ward_keys_by_district_idx = indexes.get("ward_keys_by_district") or {}

    ward_rows_by_key: dict[str, dict[str, Any]] = {}
    for ward in wards_rows:
        ward_key = _clean_text(ward.get("key"))
        if not ward_key:
            ward_key = _ward_key(ward.get("province_code"), ward.get("district_code"), ward.get("code"))
        if ward_key:
            ward_rows_by_key[ward_key] = ward

    district_rows_by_key: dict[str, dict[str, Any]] = {}
    for district in districts_rows:
        district_key = _clean_text(district.get("key"))
        if not district_key:
            district_key = _district_key(district.get("province_code"), district.get("code"))
        if district_key:
            district_rows_by_key[district_key] = district

    provinces: list[ProvinceNode] = []
    districts_by_province: dict[str, tuple[DistrictNode, ...]] = {}
    wards_by_district_key: dict[str, tuple[WardNode, ...]] = {}
    district_by_key: dict[str, DistrictNode] = {}
    ward_by_key: dict[str, WardNode] = {}
    district_keys_by_code: dict[str, set[str]] = defaultdict(set)
    ward_keys_by_code: dict[str, set[str]] = defaultdict(set)

    for province_item in provinces_rows:
        province_code = _clean_text(province_item.get("code"))
        province_name = _clean_text(province_item.get("name"))
        province_type = _clean_text(province_item.get("type"))
        province_label = _clean_text(province_item.get("label")) or _label(province_name, province_type)
        if not province_code or not province_name:
            continue

        district_keys = [str(item).strip() for item in district_keys_by_province_idx.get(province_code, []) if str(item).strip()]
        district_rows: list[dict[str, Any]] = []
        if district_keys:
            for key in district_keys:
                row = district_rows_by_key.get(key)
                if row is not None:
                    district_rows.append(row)
        else:
            district_rows = list(districts_by_province_rows.get(province_code, []))

        district_nodes: list[DistrictNode] = []
        for district_row in district_rows:
            district_code = _clean_text(district_row.get("code"))
            district_name = _clean_text(district_row.get("name"))
            district_type = _clean_text(district_row.get("type"))
            district_key = _clean_text(district_row.get("key")) or _district_key(province_code, district_code)
            district_label = _clean_text(district_row.get("label")) or _label(district_name, district_type)
            if not district_code or not district_name or not district_key:
                continue

            ward_keys = [str(item).strip() for item in ward_keys_by_district_idx.get(district_key, []) if str(item).strip()]
            ward_rows: list[dict[str, Any]] = []
            if ward_keys:
                for key in ward_keys:
                    row = ward_rows_by_key.get(key)
                    if row is not None:
                        ward_rows.append(row)
            else:
                ward_rows = list(wards_by_district_rows.get(district_key, []))

            ward_nodes: list[WardNode] = []
            for ward_row in ward_rows:
                ward_code = _clean_text(ward_row.get("code"))
                ward_name = _clean_text(ward_row.get("name"))
                ward_type = _clean_text(ward_row.get("type"))
                ward_label = _clean_text(ward_row.get("label")) or _label(ward_name, ward_type)
                ward_key = _clean_text(ward_row.get("key")) or _ward_key(province_code, district_code, ward_code)
                if not ward_code or not ward_name or not ward_key:
                    continue
                ward_node = WardNode(
                    code=ward_code,
                    key=ward_key,
                    name=ward_name,
                    type=ward_type,
                    label=ward_label,
                    district_code=district_code,
                    province_code=province_code,
                    district_key=district_key,
                )
                ward_nodes.append(ward_node)
                ward_by_key[ward_key] = ward_node
                ward_keys_by_code[ward_code].add(ward_key)

            district_node = DistrictNode(
                code=district_code,
                key=district_key,
                name=district_name,
                type=district_type,
                label=district_label,
                province_code=province_code,
                wards=tuple(ward_nodes),
            )
            district_nodes.append(district_node)
            district_by_key[district_key] = district_node
            district_keys_by_code[district_code].add(district_key)
            wards_by_district_key[district_key] = district_node.wards

        province_node = ProvinceNode(
            code=province_code,
            name=province_name,
            type=province_type,
            label=province_label,
            districts=tuple(district_nodes),
        )
        provinces.append(province_node)
        districts_by_province[province_code] = province_node.districts

    return AddressCatalog(
        provinces=tuple(provinces),
        districts_by_province=districts_by_province,
        wards_by_district_key=wards_by_district_key,
        district_by_key=district_by_key,
        ward_by_key=ward_by_key,
        district_keys_by_code={code: tuple(sorted(keys)) for code, keys in district_keys_by_code.items()},
        ward_keys_by_code={code: tuple(sorted(keys)) for code, keys in ward_keys_by_code.items()},
    )


@lru_cache(maxsize=1)
def get_address_catalog() -> AddressCatalog:
    payload = _load_sorted_source()
    canonical = _as_canonical(payload)
    catalog = _build_catalog_from_canonical(canonical)
    logger.debug(
        "address.catalog.loaded provinces=%s districts=%s wards=%s",
        len(catalog.provinces),
        len(catalog.district_by_key),
        len(catalog.ward_by_key),
    )
    return catalog


def _get_catalog_with_recovery() -> AddressCatalog:
    catalog = get_address_catalog()
    if catalog.provinces:
        return catalog
    # Recover from stale empty cache entries (e.g. file path issue fixed at runtime).
    get_address_catalog.cache_clear()
    get_catalog_etag.cache_clear()
    return get_address_catalog()


def build_catalog_payload() -> dict[str, Any]:
    catalog = _get_catalog_with_recovery()
    return {
        "provinces": [
            {"code": item.code, "name": item.name, "type": item.type, "label": item.label}
            for item in catalog.provinces
        ],
        "districts_by_province": {
            province_code: [
                {
                    "code": district.code,
                    "key": district.key,
                    "name": district.name,
                    "type": district.type,
                    "label": district.label,
                    "province_code": district.province_code,
                }
                for district in districts
            ]
            for province_code, districts in catalog.districts_by_province.items()
        },
        "wards_by_district_key": {
            district_key: [
                {
                    "code": ward.code,
                    "key": ward.key,
                    "name": ward.name,
                    "type": ward.type,
                    "label": ward.label,
                    "district_code": ward.district_code,
                    "province_code": ward.province_code,
                    "district_key": ward.district_key,
                }
                for ward in wards
            ]
            for district_key, wards in catalog.wards_by_district_key.items()
        },
    }


@lru_cache(maxsize=1)
def get_catalog_etag() -> str:
    catalog = get_address_catalog()
    digest = hashlib.sha1()
    for province in catalog.provinces:
        digest.update(f"p:{province.code}|{province.name}|{province.type}\n".encode())
        for district in province.districts:
            digest.update(f"d:{district.key}|{district.code}|{district.name}|{district.type}\n".encode())
            for ward in district.wards:
                digest.update(f"w:{ward.key}|{ward.code}|{ward.name}|{ward.type}\n".encode())
    return digest.hexdigest()


def build_provinces_payload() -> list[dict[str, str]]:
    catalog = _get_catalog_with_recovery()
    return [{"code": item.code, "name": item.name, "type": item.type, "label": item.label} for item in catalog.provinces]


def build_districts_payload(province_code: str) -> list[dict[str, str]]:
    catalog = _get_catalog_with_recovery()
    code = (province_code or "").strip()
    if not code:
        return []
    districts = catalog.districts_by_province.get(code, ())
    return [
        {
            "code": district.code,
            "key": district.key,
            "name": district.name,
            "type": district.type,
            "label": district.label,
            "province_code": district.province_code,
        }
        for district in districts
    ]


def build_wards_payload(province_code: str, district_code: str) -> list[dict[str, str]]:
    catalog = _get_catalog_with_recovery()
    province = (province_code or "").strip()
    district = (district_code or "").strip()
    if not province or not district:
        return []
    district_key = _district_key(province, district)
    wards = catalog.wards_by_district_key.get(district_key, ())
    return [
        {
            "code": ward.code,
            "key": ward.key,
            "name": ward.name,
            "type": ward.type,
            "label": ward.label,
            "district_code": ward.district_code,
            "province_code": ward.province_code,
            "district_key": ward.district_key,
        }
        for ward in wards
    ]


def _find_district(catalog: AddressCatalog, province: ProvinceNode | None, district_code: str) -> DistrictNode | None:
    if not district_code:
        return None
    if province is not None:
        return catalog.district_by_key.get(_district_key(province.code, district_code))
    keys = catalog.district_keys_by_code.get(district_code, ())
    if not keys:
        return None
    return catalog.district_by_key.get(keys[0])


def _find_ward(catalog: AddressCatalog, district: DistrictNode | None, ward_code: str) -> WardNode | None:
    if not ward_code:
        return None
    if district is not None:
        result = catalog.ward_by_key.get(_ward_key(district.province_code, district.code, ward_code))
        if result:
            return result
        # Ward may have moved to a different district (e.g., admin reorganization).
        # Fallback: search by ward_code across all districts.
    keys = catalog.ward_keys_by_code.get(ward_code, ())
    if not keys:
        return None
    return catalog.ward_by_key.get(keys[0])


def map_to_catalog(parsed: dict[str, str], raw_input: str = "") -> dict[str, str]:
    catalog = get_address_catalog()
    province_code = (parsed.get("province_code") or "").strip()
    district_code = (parsed.get("district_code") or "").strip()
    ward_code = (parsed.get("ward_code") or "").strip()
    # raw_input is the original unprocessed address text (before parser).
    # parsed["address"] is just the street portion after parser splits it.
    _raw_input = (raw_input or parsed.get("address") or "").strip()

    province = next((item for item in catalog.provinces if item.code == province_code), None)
    if province is None:
        province_key = _normalize_text(parsed.get("short_province") or parsed.get("province") or "")
        if province_key:
            province = next((item for item in catalog.provinces if _normalize_text(item.name) == province_key), None)
            province_code = province.code if province else ""

    district = _find_district(catalog, province, district_code)
    if district is None and province is not None:
        district_key = _normalize_text(parsed.get("short_district") or parsed.get("district") or "")
        if district_key:
            district = next(
                (
                    item
                    for item in catalog.districts_by_province.get(province.code, ())
                    if _normalize_text(item.name) == district_key
                ),
                None,
            )
            district_code = district.code if district else ""
    if district is not None and province is not None and district.province_code != province.code:
        district = None
        district_code = ""

    ward = _find_ward(catalog, district, ward_code)
    if ward is None and district is not None:
        ward_key = _normalize_text(parsed.get("short_ward") or parsed.get("ward") or "")
        if ward_key:
            ward = next(
                (
                    item
                    for item in catalog.wards_by_district_key.get(district.key, ())
                    if _normalize_text(item.name) == ward_key
                ),
                None,
            )
            ward_code = ward.code if ward else ""
    # If ward was found in a different district (e.g., admin reorganization),
    # update district to match the ward's actual location.
    if ward is not None and district is not None and ward.district_key != district.key:
        if province is not None and ward.province_code == province.code:
            # Same province — ward moved to different district, follow it
            district = catalog.district_by_key.get(ward.district_key)
            district_code = district.code if district else ""
        else:
            ward = None
            ward_code = ""

    # Validate: if parser returned a ward but its name doesn't appear in the
    # original raw input, try to find a better matching ward.
    # e.g., parser maps "đông xuyên" -> "Bình Xuyên" but raw text has "đông xuyên"
    if ward is not None and province is not None and _raw_input:
        raw_norm = _normalize_text(_raw_input)
        ward_norm = _normalize_text(ward.name)
        if raw_norm and ward_norm and ward_norm not in raw_norm:
            # Ward name doesn't appear in raw address - search entire province
            found = False
            for prov_district in catalog.districts_by_province.get(province.code, ()):
                for w in catalog.wards_by_district_key.get(prov_district.key, ()):
                    w_norm = _normalize_text(w.name)
                    if w_norm and w_norm in raw_norm and w.code != ward.code:
                        ward = w
                        ward_code = w.code
                        district = catalog.district_by_key.get(w.district_key)
                        if district:
                            district_code = district.code
                        found = True
                        break
                if found:
                    break

    # When province is found but district is missing, try to infer district
    # from ward name by searching all wards within the province.
    # e.g., "Phường Đông Thành TP Ninh Bình" -> parser only gets province,
    # but we can find "Đông Thành" ward and infer its parent district.
    if province is not None and district is None:
        ward_key = _normalize_text(parsed.get("short_ward") or parsed.get("ward") or "")
        if not ward_key:
            # Try extracting ward-like names from the raw address text
            raw_addr = parsed.get("address") or ""
            ward_key = _extract_ward_from_address(raw_addr)
        if ward_key:
            # Search all wards across all districts in this province
            for prov_district in catalog.districts_by_province.get(province.code, ()):
                for w in catalog.wards_by_district_key.get(prov_district.key, ()):
                    if _normalize_text(w.name) == ward_key:
                        ward = w
                        district = catalog.district_by_key.get(w.district_key)
                        if district:
                            district_code = district.code
                        ward_code = w.code
                        break
                if ward is not None:
                    break
        if district is None:
            district_code = ""
            ward_code = ""

    if district is not None and ward is None:
        ward_code = ""

    return {
        "province": province.name if province else (parsed.get("province") or ""),
        "district": district.name if district else (parsed.get("district") or ""),
        "ward": ward.name if ward else (parsed.get("ward") or ""),
        "province_code": province.code if province else province_code,
        "district_code": district.code if district else district_code,
        "ward_code": ward.code if ward else ward_code,
    }


def normalize_codes(province_code: str, district_code: str, ward_code: str) -> dict[str, str]:
    catalog = get_address_catalog()
    province = next((item for item in catalog.provinces if item.code == (province_code or "").strip()), None)
    district = _find_district(catalog, province, (district_code or "").strip())
    ward = _find_ward(catalog, district, (ward_code or "").strip())

    if district is not None and province is not None and district.province_code != province.code:
        district = None
    if ward is not None and district is not None and ward.district_key != district.key:
        ward = None

    return {
        "province": province.name if province else "",
        "district": district.name if district else "",
        "ward": ward.name if ward else "",
        "province_code": province.code if province else "",
        "district_code": district.code if district else "",
        "ward_code": ward.code if ward else "",
    }
