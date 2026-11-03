"""Số header trong `.importlinter` phải khớp số entry thật (audit 2026-09-23 P1-2/P2-10).

Audit 08-19 và 09-23 đều thấy header sai (A ghi 30 thực 61; B ghi 3 thực 106): header là
thứ người đọc dùng để biết nợ đang ở đâu, sai số = không ai biết nợ có giảm hay không.
Test đếm entry `x -> y` dưới từng mốc "KHỐI A" / "KHỐI B" và so với số trong ngoặc
"(N entry" của chính dòng mốc đó. Sửa entry thì sửa luôn số ở header.
"""

from __future__ import annotations

import re
from pathlib import Path

IMPORTLINTER = Path(__file__).resolve().parents[3] / ".importlinter"
_HEADER_RE = re.compile(r"# KHỐI (?P<block>[AB]) .*?\((?P<count>\d+) entry")


def _count_blocks() -> tuple[dict[str, int], dict[str, int]]:
    declared: dict[str, int] = {}
    actual: dict[str, int] = {}
    block: str | None = None
    for raw in IMPORTLINTER.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        m = _HEADER_RE.match(line)
        if m:
            block = m.group("block")
            declared[block] = int(m.group("count"))
            actual.setdefault(block, 0)
            continue
        if line.startswith("[importlinter:contract"):
            block = None
        if block and line and not line.startswith("#") and " -> " in line:
            actual[block] += 1
    return declared, actual


def test_block_headers_match_entry_counts():
    declared, actual = _count_blocks()
    assert set(declared) == {"A", "B"}, declared
    assert declared == actual, f"header {declared} ≠ đếm thật {actual} — sửa số trong dòng 'KHỐI A/B'"


def test_block_b_only_decreases():
    """Trần khối B ghim tại 100 (2026-09-23). Tăng = tạo nợ kiến trúc mới → chặn ở CI."""
    _, actual = _count_blocks()
    assert actual["B"] <= 100, f"khối B tăng lên {actual['B']} — không được thêm nợ mới vào .importlinter"
