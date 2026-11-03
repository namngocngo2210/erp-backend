"""Nhận diện nhà mạng VN theo đầu số di động (3 số đầu sau khi normalize).

Bảng đầu số mirror với frontend ``src/lib/phone-carrier.ts`` — sửa một bên
phải sửa bên kia. Giới hạn đã thống nhất: số "chuyển mạng giữ số" (MNP) vẫn
tính theo nhà mạng gốc của đầu số.
"""

CARRIER_PREFIXES: dict[str, tuple[str, ...]] = {
    "viettel": (
        "032", "033", "034", "035", "036", "037", "038", "039",
        "086", "096", "097", "098",
    ),
    "vinaphone": ("081", "082", "083", "084", "085", "088", "091", "094"),
    "mobifone": ("070", "076", "077", "078", "079", "089", "090", "093"),
    "vietnamobile": ("052", "056", "058", "092"),
    "gmobile": ("059", "099"),
    "itel": ("087",),
    "wintel": ("055",),
}

CARRIER_LABELS: dict[str, str] = {
    "viettel": "Viettel",
    "vinaphone": "Vinaphone",
    "mobifone": "MobiFone",
    "vietnamobile": "Vietnamobile",
    "gmobile": "Gmobile",
    "itel": "iTel",
    "wintel": "Wintel",
}

UNKNOWN_CARRIER = "unknown"
UNKNOWN_CARRIER_LABEL = "Không xác định"

PREFIX_TO_CARRIER: dict[str, str] = {
    prefix: carrier
    for carrier, prefixes in CARRIER_PREFIXES.items()
    for prefix in prefixes
}


def carrier_for_prefix(prefix: str) -> str:
    """Key nhà mạng cho 3 số đầu đã normalize; ``unknown`` nếu không khớp."""
    return PREFIX_TO_CARRIER.get(prefix, UNKNOWN_CARRIER)
