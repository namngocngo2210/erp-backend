import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def repair_mojibake(value: str) -> str:
    text = (value or "").strip()
    if not text or "Ã" not in text:
        return text
    try:
        return text.encode("latin1").decode("utf-8")
    except Exception:
        return text


def clean_text(value: Any) -> str:
    return repair_mojibake(str(value or "").strip())


def district_key(province_code: str, district_code: str) -> str:
    return f"{province_code}:{district_code}"


def ward_key(province_code: str, district_code: str, ward_code: str) -> str:
    return f"{province_code}:{district_code}:{ward_code}"


def convert_legacy_nested(payload: list[Any]) -> dict[str, Any]:
    provinces: list[dict[str, Any]] = []
    districts: list[dict[str, Any]] = []
    wards: list[dict[str, Any]] = []
    district_keys_by_province: dict[str, list[str]] = defaultdict(list)
    ward_keys_by_district: dict[str, list[str]] = defaultdict(list)

    for province_item in payload:
        province_row = as_list(province_item)
        if len(province_row) < 5:
            continue
        province_code = clean_text(province_row[0])
        province_name = clean_text(province_row[1])
        province_type = clean_text(province_row[2])
        province_label = clean_text(province_row[3]) or f"{province_type} {province_name}".strip()
        district_rows = as_list(province_row[4])
        if not province_code or not province_name:
            continue

        provinces.append(
            {
                "code": province_code,
                "name": province_name,
                "type": province_type,
                "label": province_label,
            }
        )

        for district_item in district_rows:
            district_row = as_list(district_item)
            if len(district_row) < 5:
                continue
            dist_code = clean_text(district_row[0])
            dist_name = clean_text(district_row[1])
            dist_type = clean_text(district_row[2])
            dist_label = clean_text(district_row[3]) or f"{dist_type} {dist_name}".strip()
            ward_rows = as_list(district_row[4])
            if not dist_code or not dist_name:
                continue
            dist_key = district_key(province_code, dist_code)

            districts.append(
                {
                    "code": dist_code,
                    "province_code": province_code,
                    "name": dist_name,
                    "type": dist_type,
                    "label": dist_label,
                    "key": dist_key,
                }
            )
            district_keys_by_province[province_code].append(dist_key)

            for ward_item in ward_rows:
                ward_row = as_list(ward_item)
                if len(ward_row) < 3:
                    continue
                w_code = clean_text(ward_row[0])
                w_name = clean_text(ward_row[1])
                w_type = clean_text(ward_row[2])
                w_label = clean_text(ward_row[3]) if len(ward_row) > 3 else ""
                if not w_code or not w_name:
                    continue
                if not w_label:
                    w_label = f"{w_type} {w_name}".strip()
                w_key = ward_key(province_code, dist_code, w_code)

                wards.append(
                    {
                        "code": w_code,
                        "province_code": province_code,
                        "district_code": dist_code,
                        "district_key": dist_key,
                        "name": w_name,
                        "type": w_type,
                        "label": w_label,
                        "key": w_key,
                    }
                )
                ward_keys_by_district[dist_key].append(w_key)

    canonical = {
        "meta": {"version": "2026-03-19", "source": "dvhcvn"},
        "provinces": provinces,
        "districts": districts,
        "wards": wards,
        "indexes": {
            "district_keys_by_province": district_keys_by_province,
            "ward_keys_by_district": ward_keys_by_district,
        },
    }
    checksum = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    canonical["meta"]["checksum"] = f"sha256:{checksum}"
    return canonical


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert legacy sorted.json to canonical indexed format")
    parser.add_argument("--input", required=True, help="Path to input sorted.json")
    parser.add_argument("--output", required=True, help="Path to output sorted.json")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and {"provinces", "districts", "wards"}.issubset(payload.keys()):
        canonical = payload
    elif isinstance(payload, list):
        canonical = convert_legacy_nested(payload)
    else:
        raise RuntimeError(f"Unsupported payload type: {type(payload).__name__}")

    output_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Converted catalog: provinces={len(canonical.get('provinces', []))} "
        f"districts={len(canonical.get('districts', []))} wards={len(canonical.get('wards', []))}"
    )


if __name__ == "__main__":
    main()
