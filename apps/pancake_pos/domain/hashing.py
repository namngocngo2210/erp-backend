"""Hash payload ổn định: cùng nội dung → cùng hash, bất kể thứ tự khoá dict.

Dùng để quyết định có cần PUT lại hay không (docs/pancake-pos-product-sync.md §3.3).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_hash(payload: Any) -> str:
    """sha256 hex (64 ký tự) của JSON chuẩn hoá (sort_keys, không khoảng trắng, giữ Unicode)."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
