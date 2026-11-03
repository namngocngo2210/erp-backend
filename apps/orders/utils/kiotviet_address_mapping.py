"""
KiotViet address mapping utilities.

Flow: raw_address → vietnamadminunits parse → match against kiotviet_address_final.json
     → store in order.kiotviet_location and order.kiotviet_ward

Data source: kiotviet_address_final.json
  Structure: { "locationName": ["Ward1", "Ward2", ...] }
  ~720 locations, ~10087 wards
  locationName format: "Province - District" (from KiotViet API)

Output format:
  kiotviet_location: "Đắk Nông - Huyện Cư Jút"  (province - district)
  kiotviet_ward: "Xã Cư Knia"                    (full ward name)
"""

import json
import logging
import unicodedata
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _normalize(value: str) -> str:
    """Normalize Vietnamese text for matching: strip diacritics, lowercase,
    and strip leading zeros from numeric tokens (e.g., "phuong 05" → "phuong 5")."""
    t = (value or "").strip().lower()
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[^a-z0-9]+", " ", t)
    # Strip leading zeros from numeric tokens
    t = re.sub(r"\b0+(\d)", r"\1", t)
    return " ".join(t.split())


def _strip_type(name: str) -> str:
    """Strip administrative type prefix."""
    for prefix in ("Thành phố ", "Tỉnh ", "Quận ", "Huyện ", "Thị xã ", "Thị Xã ",
                    "Phường ", "Xã ", "Thị trấn ", "Thị Trấn "):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _parse_location_name(location_name: str) -> tuple[str, str]:
    """Parse 'Province - District' into (province, district).

    Handles provinces containing ' - ' like 'Bà Rịa - Vũng Tàu'.
    """
    parts = location_name.split(" - ")
    if len(parts) == 3:
        return (f"{parts[0]} - {parts[1]}", parts[2])
    elif len(parts) == 2:
        return (parts[0], parts[1])
    return (location_name, "")


@lru_cache(maxsize=1)
def _load_data() -> dict:
    """Load kiotviet_address_final.json and build lookup indexes.

    Returns dict with:
      - location_by_norm: {norm_location: original_location_name}
      - province_by_norm: {norm_province: set of original location names}
      - district_by_norm: {(norm_province, norm_district_short): location_name}
      - ward_by_norm: {(norm_province, norm_ward_full): (location_name, ward)}
        (province-scoped fallback when district unknown)
      - ward_by_loc: {(norm_location, norm_ward_full): (location_name, ward)}
        (location-scoped precise lookup)
    """
    path = _DATA_DIR / "kiotviet_address_final.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("kiotviet_address_final.load_failed path=%s", path)
        return {
            "location_by_norm": {}, "province_by_norm": {},
            "district_by_norm": {}, "ward_by_norm": {}, "ward_by_loc": {},
        }

    location_by_norm: dict[str, str] = {}
    province_by_norm: dict[str, str] = {}
    district_by_norm: dict[tuple[str, str], str] = {}
    ward_by_norm: dict[tuple[str, str], tuple[str, str]] = {}
    ward_by_loc: dict[tuple[str, str], tuple[str, str]] = {}

    for location_name, wards in raw.items():
        loc_norm = _normalize(location_name)
        location_by_norm[loc_norm] = location_name

        prov, dist = _parse_location_name(location_name)
        prov_norm = _normalize(prov)
        if prov_norm:
            province_by_norm[prov_norm] = prov

        if dist:
            dist_short_norm = _normalize(_strip_type(dist))
            if dist_short_norm:
                district_by_norm[(prov_norm, dist_short_norm)] = location_name

        if not isinstance(wards, list):
            continue

        for ward in wards:
            ward_norm = _normalize(ward)
            if not ward_norm:
                continue
            ward_by_norm[(prov_norm, ward_norm)] = (location_name, ward)
            if loc_norm:
                ward_by_loc[(loc_norm, ward_norm)] = (location_name, ward)

    logger.info(
        "kiotviet_address_final.loaded locations=%d provinces=%d wards=%d",
        len(location_by_norm), len(province_by_norm),
        sum(1 for k in ward_by_norm),
    )
    return {
        "location_by_norm": location_by_norm,
        "province_by_norm": province_by_norm,
        "district_by_norm": district_by_norm,
        "ward_by_norm": ward_by_norm,
        "ward_by_loc": ward_by_loc,
    }


def resolve_kiotviet_address(parsed: dict, raw_address: str = "") -> dict:
    """Resolve kiotviet_location and kiotviet_ward from parser output.

    Args:
        parsed: output of parse_legacy_level3 (province, district, ward, short_* fields)
        raw_address: original raw address for fallback ward extraction

    Returns:
        {"locationName": str, "wardName": str}
        locationName format: "Province - District" (exact KiotViet location name)
        wardName format: "Xã/Phường/Thị trấn Name"
    """
    data = _load_data()

    # Extract parsed components
    province_name = _strip_type(
        (parsed.get("short_province") or parsed.get("province") or "").strip()
    )
    district_name = _strip_type(
        (parsed.get("short_district") or parsed.get("district") or "").strip()
    )
    ward_full = (parsed.get("ward") or "").strip()

    prov_norm = _normalize(province_name)
    if not prov_norm or prov_norm not in data["province_by_norm"]:
        return {"locationName": "", "wardName": ""}

    # Resolve location (province - district)
    location_name = ""
    loc_norm = ""
    if district_name:
        dist_norm = _normalize(district_name)
        matched_loc = data["district_by_norm"].get((prov_norm, dist_norm))
        if matched_loc:
            location_name = matched_loc
            loc_norm = _normalize(matched_loc)

    # Resolve ward — prefer location-scoped lookup to avoid collisions
    ward_result = ""
    if ward_full:
        ward_norm = _normalize(ward_full)
        match = None
        if loc_norm:
            match = data["ward_by_loc"].get((loc_norm, ward_norm))
        if not match:
            match = data["ward_by_norm"].get((prov_norm, ward_norm))
        if match:
            ward_result = match[1]
            # If location wasn't found from parser, infer from ward match
            if not location_name:
                location_name = match[0]
        else:
            ward_result = ward_full

    # Fallback: extract ward from raw_address when parser missed it
    if not ward_result and raw_address:
        ward_result = _extract_and_match_ward(raw_address, prov_norm, data)
        if ward_result:
            ward_norm = _normalize(ward_result)
            match = data["ward_by_norm"].get((prov_norm, ward_norm))
            if match and not location_name:
                location_name = match[0]

    # Fallback: extract district from raw_address when parser missed it
    if not location_name and raw_address:
        dist_match = _extract_and_match_district(raw_address, prov_norm, data)
        if dist_match:
            location_name = dist_match

    return {"locationName": location_name, "wardName": ward_result}


def _extract_and_match_ward(raw_address: str, prov_norm: str, data: dict) -> str:
    """Extract ward-like pattern from raw address and match against KiotViet data."""
    text = (raw_address or "").strip()
    if not text:
        return ""

    pattern = r"(?:[Pp]hường|[Pp]\.?\s|[Xx]ã|[Tt]hị\s*[Tt]rấn)\s*([^,]+)"
    match = re.search(pattern, text)
    if not match:
        return ""

    raw_name = match.group(1).strip()
    raw_name = re.split(
        r"\s+(?:[Qq]uận|[Hh]uyện|[Tt]ỉnh|[Tt][Pp]\.?\s|[Tt]hành\s*[Pp]hố|[Tt]hị\s*[Xx]ã)",
        raw_name,
    )[0].strip()
    words = raw_name.split()
    if len(words) > 4:
        raw_name = " ".join(words[:4])
    if not raw_name:
        return ""

    for prefix in ("Phường ", "Xã ", "Thị trấn "):
        full = f"{prefix}{raw_name}"
        ward_norm = _normalize(full)
        match_result = data["ward_by_norm"].get((prov_norm, ward_norm))
        if match_result:
            return match_result[1]

    return ""


def _extract_and_match_district(raw_address: str, prov_norm: str, data: dict) -> str:
    """Extract district name from raw address and match against KiotViet data.

    Returns the full location name (e.g. "Hà Nội - Quận Hai Bà Trưng") or empty string.
    """
    text_norm = _normalize(raw_address)
    if not text_norm:
        return ""

    best = ""
    best_len = 0
    for (p, d), loc_name in data["district_by_norm"].items():
        if p != prov_norm:
            continue
        if len(d) < 3:
            continue
        if d in text_norm and len(d) > best_len:
            best = loc_name
            best_len = len(d)

    return best
