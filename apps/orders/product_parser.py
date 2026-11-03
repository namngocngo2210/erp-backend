import re

# Trash/old SKU markers. Keep in sync with migration
# ``0079_cleanup_and_backfill_product_groups`` (which inlines its own copy by
# design): ``{DEL`` literal marker, plus the case-insensitive ``\.C[ŨU]`` regex
# that covers ".CŨ", ".CU" and ".cũ". Anchored on the literal dot so it does not
# match arbitrary mid-word "cu" sequences.
_TRASH_DEL_MARKER = "{DEL"
_TRASH_CU_REGEX = re.compile(r"\.C[ŨU]", re.IGNORECASE)


# Landing (Ladipage) format số ≥ 1000 kiểu VN trước khi gửi: product_id 3681 đến
# dạng "3.681" (đôi khi "3,681"); id < 1000 vẫn đến dạng số JSON. Chỉ nhận nhóm
# đúng 3 chữ số sau dấu để không nhầm với SKU dạng "SQA03-1.7M".
_THOUSANDS_ID_REGEX = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")


def normalize_landing_product_id(raw):
    """Chuẩn hoá ``product_id`` từ landing: chuỗi toàn số / số có dấu phân cách hàng
    nghìn → ``int`` (tra theo id); chuỗi khác giữ nguyên (SKU hoặc "SKU1,SKU2");
    ``None``/``bool`` → ``None``."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if text.isdigit():
            return int(text)
        if _THOUSANDS_ID_REGEX.match(text):
            return int(re.sub(r"[.,]", "", text))
        return raw
    return raw


def is_trash_sku(sku: str) -> bool:
    """Return True for soft-deleted "trash"/"old" SKUs that must never be resurrected."""
    if not sku:
        return False
    return _TRASH_DEL_MARKER in sku or bool(_TRASH_CU_REGEX.search(sku))


SKU_RULES: list[tuple[tuple[str, ...], str]] = [
    (("đệm ghế massage",), "GMS05"),
    (("bồn ngâm chân", "máy massage chân"), "BNC02"),
    (("nệm massage", "thảm massage"), "TMS01-PROMAX"),
    (("tủ",), "SQA03-1.7M"),
    (("massage chân bấm huyệt",), "MSC02-CAOCAP"),
    (("đai lưng",), "DTL01-XL"),
    (("máy massage chân rung",), "MSC06"),
    (("gói thảo dược giá 99.000 + 30.000 phí ship",), "BNC01-NGAICUU"),
]

QUANTITY_REGEX_RULES: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\bmua\s*30\b", re.IGNORECASE), 1),
    (re.compile(r"\bmua\s*2\b", re.IGNORECASE), 2),
    (re.compile(r"\bmua\s*1\b", re.IGNORECASE), 1),
    (re.compile(r"\b2\s*th[ảa]m\b", re.IGNORECASE), 2),
    (re.compile(r"\b1\s*th[ảa]m\b", re.IGNORECASE), 1),
    (re.compile(r"\b2\s*b[ồo]n\b", re.IGNORECASE), 2),
    (re.compile(r"\b1\s*b[ồo]n\b", re.IGNORECASE), 1),
]

TOTAL_AMOUNT_VALUES: list[int] = [
    5300000,
    3300000,
    3200000,
    3250000,
    3199000,
    3100000,
    2800000,
    2400000,
    2200000,
    2300000,
    1699000,
    1600000,
    1650000,
    1599000,
    1500000,
    1290000,
    1250000,
    1150000,
    1200000,
    1100000,
    1000000,
    1550000,
    1499000,
    1490000,
    1465000,
    1350000,
    1399000,
    1299000,
    599000,
]

SEPARATOR_PATTERN = r"[.,\s]*"
SPECIAL_TOTAL_AMOUNT_RULES: list[tuple[re.Pattern[str], int]] = [
    (re.compile(rf"\b99{SEPARATOR_PATTERN}000\s*\+\s*30{SEPARATOR_PATTERN}000\b", re.IGNORECASE), 129000),
]


def _compile_amount_pattern(amount: int) -> re.Pattern[str]:
    groups = f"{amount:,}".split(",")
    # (?<!\d) + (?<!\d[.,\s]): cấm khớp vào PHẦN ĐUÔI của một số lớn hơn.
    # "\b" đơn thuần không đủ: dấu ngăn nghìn tạo word boundary nên "1.599.000"
    # từng bị nhận thành 599.000 (đơn 0388581760, 2026-08-04) — sai cả snapshot
    # landing_confirmed_amount lẫn total_amount lúc ingest.
    pattern = r"(?<!\d)(?<!\d[.,\s])" + groups[0]
    for group in groups[1:]:
        pattern += rf"{SEPARATOR_PATTERN}{group}"
    pattern += r"\b"
    return re.compile(pattern, re.IGNORECASE)


TOTAL_AMOUNT_REGEX_RULES: list[tuple[re.Pattern[str], int]] = [
    *SPECIAL_TOTAL_AMOUNT_RULES,
    *[(_compile_amount_pattern(amount), amount) for amount in TOTAL_AMOUNT_VALUES],
]


def normalize_product_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _contains(text: str, needle: str) -> bool:
    return normalize_product_text(needle) in text


def derive_sku(product_text: str) -> str:
    text = normalize_product_text(product_text)
    for needles, sku in SKU_RULES:
        if any(_contains(text, needle) for needle in needles):
            return sku
    return ""


def derive_quantity(product_text: str) -> int:
    text = normalize_product_text(product_text)
    for pattern, quantity in QUANTITY_REGEX_RULES:
        if pattern.search(text):
            return quantity
    return 0


def derive_total_amount(product_text: str) -> int:
    text = normalize_product_text(product_text)
    for pattern, amount in TOTAL_AMOUNT_REGEX_RULES:
        if pattern.search(text):
            return amount
    return 0
