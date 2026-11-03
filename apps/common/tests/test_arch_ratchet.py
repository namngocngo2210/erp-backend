"""Ratchet kích thước code + nợ ruff (audit 2026-09-23 C3/P1-3).

VÌ SAO TỒN TẠI: giữa hai đợt audit (08-19 → 09-23) LOC backend +20%, file >800 dòng
14→17, hàm >150 dòng 50→57, class >1000 dòng 6→7, nợ ruff dưới `ignore` 405→626 —
không có gì chặn vì mọi cổng chỉ chặn "rule mới", không chặn "file cũ phình thêm".

Baseline: `scripts/arch-baseline.json` (đo bằng `scripts/arch_baseline.py`). Test FAIL khi:
  • file/hàm/class đã có trong baseline TĂNG số dòng,
  • file/hàm/class MỚI vượt trần (file >800, hàm >150, class >1000),
  • số vi phạm ruff của một rule đang ignore tăng (phần này chỉ chạy khi có `ruff`
    trên PATH — trong image prod không có; CI job `backend:lint-imports` chạy đủ).
Trả nợ xong: `python scripts/arch_baseline.py --update` (chỉ được hạ).

Cần thư mục `scripts/` của repo (ngoài `backend/`): chạy trong container chỉ mount
`backend/` thì skip — `scripts/check-backend-arch.sh` và CI vẫn chặn.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPT = _REPO_ROOT / "scripts" / "arch_baseline.py"
_BASELINE = _REPO_ROOT / "scripts" / "arch-baseline.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("arch_baseline", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not (_SCRIPT.exists() and _BASELINE.exists()), reason="thiếu scripts/ (container chỉ mount backend/)")
def test_arch_baseline_not_worse():
    mod = _load_module()
    current = mod.measure(with_ruff=shutil.which("ruff") is not None)
    problems = mod.compare(mod.load_baseline(), current)
    assert not problems, "Ratchet kiến trúc — nợ TĂNG so với scripts/arch-baseline.json:\n  - " + "\n  - ".join(
        problems
    ) + "\nNếu cố ý: python scripts/arch_baseline.py --update --force (ghi lý do trong MR)."


def test_compare_flags_growth_and_new_offenders():
    """Kiểm logic so sánh bằng dữ liệu giả — không phụ thuộc baseline thật."""
    if not _SCRIPT.exists():
        pytest.skip("thiếu scripts/")
    mod = _load_module()
    baseline = {"files": {"a.py": 900}, "functions": {"a.py::f": 160}, "classes": {}, "ruff": {"F401": 10}}
    same = {"files": {"a.py": 900}, "functions": {"a.py::f": 160}, "classes": {}, "ruff": {"F401": 10}}
    assert mod.compare(baseline, same) == []
    lower = {"files": {"a.py": 850}, "functions": {}, "classes": {}, "ruff": {"F401": 3}}
    assert mod.compare(baseline, lower) == []
    worse = {
        "files": {"a.py": 901, "b.py": 801},
        "functions": {"a.py::f": 160},
        "classes": {"c.py::C": 1001},
        "ruff": {"F401": 11, "E501": 1},
    }
    problems = mod.compare(baseline, worse)
    assert len(problems) == 5, problems
    assert any("a.py tăng 900 → 901" in p for p in problems)
    assert any("b.py MỚI vượt trần" in p for p in problems)
    assert any("c.py::C MỚI vượt trần" in p for p in problems)
    assert any("ruff F401: 10 → 11" in p for p in problems)
    assert any("ruff E501: 0 → 1" in p for p in problems)
