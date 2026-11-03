import json
import hashlib
import logging
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WardNode:
    code: str
    key: str
    name: str
    type: str
    label: str
    district_code: str
    province_code: str
    district_key: str


@dataclass(frozen=True)
class DistrictNode:
    code: str
    key: str
    name: str
    type: str
    label: str
    province_code: str
    wards: tuple[WardNode, ...]


@dataclass(frozen=True)
class ProvinceNode:
    code: str
    name: str
    type: str
    label: str
    districts: tuple[DistrictNode, ...]


@dataclass(frozen=True)
class AddressCatalog:
    provinces: tuple[ProvinceNode, ...]
    districts_by_province: dict[str, tuple[DistrictNode, ...]]
    wards_by_district_key: dict[str, tuple[WardNode, ...]]
    district_by_key: dict[str, DistrictNode]
    ward_by_key: dict[str, WardNode]
    district_keys_by_code: dict[str, tuple[str, ...]]
    ward_keys_by_code: dict[str, tuple[str, ...]]


def _normalize_text(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\b(tinh|thanh pho|tp\.?|quan|huyen|thi xa|thanh pho thuoc tinh|phuong|xa|thi tran)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _pick(item: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def _label(name: str, type_name: str) -> str:
    type_clean = (type_name or "").strip()
    name_clean = (name or "").strip()
    if not name_clean:
        return ""
    if type_clean:
        return f"{type_clean} {name_clean}".strip()
    return name_clean


def _district_key(province_code: str, district_code: str) -> str:
    return f"{(province_code or '').strip()}:{(district_code or '').strip()}"


def _ward_key(province_code: str, district_code: str, ward_code: str) -> str:
    return f"{_district_key(province_code, district_code)}:{(ward_code or '').strip()}"


def _iter_children(item: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("children", "level2s", "level3s", "districts", "wards"):
        value = item.get(key)
        if isinstance(value, list):
            return [child for child in value if isinstance(child, dict)]
    return []


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _repair_mojibake(value: str) -> str:
    text = (value or "").strip()
    if not text or "Ã" not in text:
        return text
    try:
        return text.encode("latin1").decode("utf-8")
    except Exception:
        return text


def _clean_text(value: Any) -> str:
    return _repair_mojibake(str(value or "").strip())


def _load_sorted_source() -> Any:
    env_path = os.getenv("DVHCVN_SORTED_JSON_PATH", "").strip()
    path_candidates: list[Path] = []
    if env_path:
        path_candidates.append(Path(env_path))
    module_dir = Path(__file__).resolve().parent
    path_candidates.append(module_dir / "data" / "sorted.json")
    # Walk up ancestor folders and try "<ancestor>/data/sorted.json" defensively.
    # This avoids brittle fixed parent-index assumptions across runtime layouts.
    for parent in module_dir.parents:
        path_candidates.append(parent / "data" / "sorted.json")

    # De-duplicate while preserving order.
    deduped_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in path_candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped_candidates.append(candidate)

    for candidate in deduped_candidates:
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            logger.info(
                "address.catalog.loaded_local path=%s type=%s",
                candidate,
                type(payload).__name__,
            )
            return payload
        except Exception:
            logger.exception("address.catalog.load_local_failed path=%s", candidate)

    logger.error("address.catalog.missing_local_json path_candidates=%s", [str(path) for path in deduped_candidates])
    return []


def _legacy_object_to_canonical(payload: list[dict[str, Any]]) -> dict[str, Any]:
    provinces: list[dict[str, Any]] = []
    districts: list[dict[str, Any]] = []
    wards: list[dict[str, Any]] = []
    district_keys_by_province: dict[str, list[str]] = defaultdict(list)
    ward_keys_by_district: dict[str, list[str]] = defaultdict(list)

    for province_item in payload:
        province_code = _clean_text(_pick(province_item, ("id", "code", "province_code")))
        province_name = _clean_text(_pick(province_item, ("name", "province", "province_name")))
        province_type = _clean_text(_pick(province_item, ("type", "province_type")))
        if not province_code or not province_name:
            continue

        provinces.append(
            {
                "code": province_code,
                "name": province_name,
                "type": province_type,
                "label": _label(province_name, province_type),
            }
        )

        for district_item in _iter_children(province_item):
            district_code = _clean_text(_pick(district_item, ("id", "code", "district_code")))
            district_name = _clean_text(_pick(district_item, ("name", "district", "district_name")))
            district_type = _clean_text(_pick(district_item, ("type", "district_type")))
            if not district_code or not district_name:
                continue
            district_key = _district_key(province_code, district_code)
            districts.append(
                {
                    "code": district_code,
                    "province_code": province_code,
                    "name": district_name,
                    "type": district_type,
                    "label": _label(district_name, district_type),
                    "key": district_key,
                }
            )
            district_keys_by_province[province_code].append(district_key)

            for ward_item in _iter_children(district_item):
                ward_code = _clean_text(_pick(ward_item, ("id", "code", "ward_code")))
                ward_name = _clean_text(_pick(ward_item, ("name", "ward", "ward_name")))
                ward_type = _clean_text(_pick(ward_item, ("type", "ward_type")))
                if not ward_code or not ward_name:
                    continue
                key = _ward_key(province_code, district_code, ward_code)
                wards.append(
                    {
                        "code": ward_code,
                        "province_code": province_code,
                        "district_code": district_code,
                        "district_key": district_key,
                        "name": ward_name,
                        "type": ward_type,
                        "label": _label(ward_name, ward_type),
                        "key": key,
                    }
                )
                ward_keys_by_district[district_key].append(key)

    return {
        "meta": {"version": "legacy-converted", "source": "legacy-object"},
        "provinces": provinces,
        "districts": districts,
        "wards": wards,
        "indexes": {
            "district_keys_by_province": district_keys_by_province,
            "ward_keys_by_district": ward_keys_by_district,
        },
    }


def _legacy_nested_to_canonical(payload: list[Any]) -> dict[str, Any]:
    provinces: list[dict[str, Any]] = []
    districts: list[dict[str, Any]] = []
    wards: list[dict[str, Any]] = []
    district_keys_by_province: dict[str, list[str]] = defaultdict(list)
    ward_keys_by_district: dict[str, list[str]] = defaultdict(list)

    for province_item in payload:
        province_row = _as_list(province_item)
        if len(province_row) < 5:
            continue
        province_code = _clean_text(province_row[0])
        province_name = _clean_text(province_row[1])
        province_type = _clean_text(province_row[2])
        district_rows = _as_list(province_row[4])
        if not province_code or not province_name:
            continue

        provinces.append(
            {
                "code": province_code,
                "name": province_name,
                "type": province_type,
                "label": _label(province_name, province_type),
            }
        )

        for district_item in district_rows:
            district_row = _as_list(district_item)
            if len(district_row) < 5:
                continue
            district_code = _clean_text(district_row[0])
            district_name = _clean_text(district_row[1])
            district_type = _clean_text(district_row[2])
            ward_rows = _as_list(district_row[4])
            if not district_code or not district_name:
                continue

            district_key = _district_key(province_code, district_code)
            districts.append(
                {
                    "code": district_code,
                    "province_code": province_code,
                    "name": district_name,
                    "type": district_type,
                    "label": _label(district_name, district_type),
                    "key": district_key,
                }
            )
            district_keys_by_province[province_code].append(district_key)

            for ward_item in ward_rows:
                ward_row = _as_list(ward_item)
                if len(ward_row) < 3:
                    continue
                ward_code = _clean_text(ward_row[0])
                ward_name = _clean_text(ward_row[1])
                ward_type = _clean_text(ward_row[2])
                if not ward_code or not ward_name:
                    continue
                key = _ward_key(province_code, district_code, ward_code)
                wards.append(
                    {
                        "code": ward_code,
                        "province_code": province_code,
                        "district_code": district_code,
                        "district_key": district_key,
                        "name": ward_name,
                        "type": ward_type,
                        "label": _label(ward_name, ward_type),
                        "key": key,
                    }
                )
                ward_keys_by_district[district_key].append(key)

    return {
        "meta": {"version": "legacy-converted", "source": "legacy-nested-list"},
        "provinces": provinces,
        "districts": districts,
        "wards": wards,
        "indexes": {
            "district_keys_by_province": district_keys_by_province,
            "ward_keys_by_district": ward_keys_by_district,
        },
    }


