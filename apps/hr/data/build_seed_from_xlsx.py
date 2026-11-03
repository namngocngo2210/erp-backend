"""Sinh ``employee_profiles_seed.json`` từ file ``HCNS_DESI.xlsx`` (sheet
``DATA NHÂN SỰ CHUẨN``).

Đây là công cụ one-off để backfill hồ sơ nhân sự: nó CHỈ chạy thủ công khi cần
tái sinh seed, không chạy lúc runtime/migration. File xlsx KHÔNG nằm trong repo
(dữ liệu nhạy cảm) — chỉ commit kết quả JSON đã chuẩn hoá.

Cách dùng::

    python backend/apps/hr/data/build_seed_from_xlsx.py /đường/dẫn/HCNS_DESI.xlsx

Phụ thuộc: ``openpyxl`` (chỉ môi trường dev, không phải dependency của app).
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from collections import Counter
from pathlib import Path

SHEET = "DATA NHÂN SỰ CHUẨN"

# Chỉ số cột (0-based) trong sheet chuẩn — cố định theo layout hiện tại.
COL = {
    "status_onoff": 1,      # ON / OFF
    "code": 2,              # Mã nhân viên (D001…)  — có TRÙNG, sẽ khử ở dưới
    "full_name": 3,
    "lark_account": 4,
    "gender": 5,
    "department": 6,        # Khối phòng ban
    "position_level": 7,    # Chức danh
    "position_title": 8,    # Vị trí cụ thể
    "work_type": 9,         # Hình thức làm việc
    "hire_date": 10,
    "official_date": 13,    # Ngày chính thức
    "resign_date": 14,
    "status_text": 15,      # Tình trạng (Chính thức / Thử việc)
    "phone": 16,
    "personal_email": 17,
    "dob": 18,
    "id_number": 21,        # CMND/CCCD
    "id_issued_date": 22,   # Ngày cấp
    "id_issued_place": 23,  # Nơi cấp
    "permanent_address": 24,  # Hộ khẩu thường trú
    "address": 25,          # Địa chỉ liên hệ
    "education_level": 26,  # Trình độ chuyên môn
    "major": 27,            # Chuyên ngành
    "school": 28,           # Tên trường
    "tax_code": 29,         # MST TNCN
    "social_insurance_no": 30,  # Số sổ BHXH
    "bank_account_no": 31,  # STK Chính chủ
    "bank_name": 32,        # Ngân hàng
    "ec_name": 33,          # Người liên hệ khẩn — họ tên
    "ec_relation": 34,      # Mối quan hệ
    "ec_phone": 35,         # SĐT
    "ec_address": 36,       # Địa chỉ
    "marital_status": 37,   # Tình trạng hôn nhân
    "child1": 38,
    "child2": 39,
    "child3": 40,
    "facebook_url": 41,
    "vehicle_info": 42,     # Thông tin đăng ký gửi xe
    "resign_reason": 44,    # Lý do nghỉ
}

# Giá trị rỗng/nhiễu coi như không có dữ liệu.
NULLISH = {"", ".", "`", "không", "ko", "n/a", "na", "chưa có", "chưa"}


def _s(v) -> str:
    """Chuẩn hoá ô về chuỗi sạch (strip backtick/khoảng trắng thừa)."""
    if v is None:
        return ""
    if isinstance(v, datetime.datetime):
        return v.date().isoformat()
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip().strip("`").strip()


def _date(v) -> str | None:
    if isinstance(v, datetime.datetime):
        return v.date().isoformat()
    if isinstance(v, datetime.date):
        return v.isoformat()
    return None


def _gender(v) -> str:
    t = _s(v).lower()
    if t.startswith("nam"):
        return "male"
    if t.startswith("nữ") or t.startswith("nu"):
        return "female"
    return ""


def _work_type(v) -> str:
    t = _s(v).lower()
    if "toàn thời gian" in t or "full" in t:
        return "fulltime"
    if "bán thời gian" in t or "part" in t:
        return "parttime"
    if "remote" in t:
        return "remote"
    return ""


def _position_level(v) -> str:
    t = _s(v).lower()
    if "giám đốc" in t:
        return "director"
    if "trưởng" in t:
        return "team_lead"
    if "chuyên viên" in t:
        return "specialist"
    if "cộng tác" in t or "ctv" in t:
        return "collaborator"
    if "nhân viên" in t:
        return "staff"
    return ""


def _employment_status(onoff: str, status_text: str) -> str:
    if _s(onoff).upper() == "OFF":
        return "resigned"
    if "thử việc" in _s(status_text).lower():
        return "probation"
    return "active"


def _marital(v) -> str:
    t = _s(v).lower()
    if t in NULLISH:
        return ""
    if "ly hôn" in t or "ly dị" in t:
        return "divorced"
    if "chưa" in t or "độc thân" in t or "ny" in t or "single" in t:
        return "single"
    if "kết hôn" in t or "gia đình" in t or "married" in t:
        return "married"
    return "other"


_YEAR_RE = re.compile(r"((?:19|20)\d{2})")


def _child(v) -> dict | None:
    raw = _s(v)
    if raw.lower() in NULLISH:
        return None
    year = None
    m = _YEAR_RE.search(raw)
    if m:
        year = int(m.group(1))
    # Bỏ phần năm + ký tự phân tách để lấy tên.
    name = _YEAR_RE.sub("", raw)
    name = re.sub(r"(sinh năm|năm sinh|sn)", "", name, flags=re.IGNORECASE)
    name = name.strip(" ,-/:").strip()
    if not name and not year:
        return None
    return {"name": name, "birth_year": year}


def _clean(v) -> str:
    t = _s(v)
    return "" if t.lower() in NULLISH else t


def build(xlsx_path: str) -> list[dict]:
    import openpyxl

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[SHEET]
    ws.reset_dimensions()
    rows = list(ws.iter_rows(values_only=True))
    data = [r for r in rows[1:] if r[COL["code"]] and _s(r[COL["code"]])]

    # Khử trùng mã: D100 → D100, D100-2, D100-3 (theo thứ tự xuất hiện).
    seen: Counter = Counter()
    out: list[dict] = []
    for r in data:
        base_code = _s(r[COL["code"]])
        seen[base_code] += 1
        code = base_code if seen[base_code] == 1 else f"{base_code}-{seen[base_code]}"

        children = [
            c for c in (_child(r[COL[k]]) for k in ("child1", "child2", "child3")) if c
        ]
        rec = {
            "employee_code": code,
            "source_code": base_code,
            "full_name": _s(r[COL["full_name"]]),
            "lark_account": _clean(r[COL["lark_account"]]),
            "gender": _gender(r[COL["gender"]]),
            "department_name": _clean(r[COL["department"]]),
            "position_level": _position_level(r[COL["position_level"]]),
            "position_title": _clean(r[COL["position_title"]]),
            "work_type": _work_type(r[COL["work_type"]]),
            "employment_status": _employment_status(
                r[COL["status_onoff"]], r[COL["status_text"]]
            ),
            "hire_date": _date(r[COL["hire_date"]]),
            "official_date": _date(r[COL["official_date"]]),
            "resign_date": _date(r[COL["resign_date"]]),
            "phone": _clean(r[COL["phone"]]),
            "personal_email": _clean(r[COL["personal_email"]]),
            "dob": _date(r[COL["dob"]]),
            "id_number": _clean(r[COL["id_number"]]),
            "id_issued_date": _date(r[COL["id_issued_date"]]),
            "id_issued_place": _clean(r[COL["id_issued_place"]]),
            "permanent_address": _clean(r[COL["permanent_address"]]),
            "address": _clean(r[COL["address"]]),
            "education_level": _clean(r[COL["education_level"]]),
            "major": _clean(r[COL["major"]]),
            "school": _clean(r[COL["school"]]),
            "tax_code": _clean(r[COL["tax_code"]]),
            "social_insurance_no": _clean(r[COL["social_insurance_no"]]),
            "bank_account_no": _clean(r[COL["bank_account_no"]]),
            "bank_name": _clean(r[COL["bank_name"]]),
            "emergency_contact_name": _clean(r[COL["ec_name"]]),
            "emergency_contact_relation": _clean(r[COL["ec_relation"]]),
            "emergency_contact_phone": _clean(r[COL["ec_phone"]]),
            "emergency_contact_address": _clean(r[COL["ec_address"]]),
            "marital_status": _marital(r[COL["marital_status"]]),
            "facebook_url": _clean(r[COL["facebook_url"]]),
            "vehicle_info": _clean(r[COL["vehicle_info"]]),
            "resign_reason": _clean(r[COL["resign_reason"]]),
            "children": children,
        }
        out.append(rec)
    return out


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    records = build(sys.argv[1])
    out_path = Path(__file__).with_name("employee_profiles_seed.json")
    out_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Đã ghi {len(records)} bản ghi -> {out_path}")


if __name__ == "__main__":
    main()
