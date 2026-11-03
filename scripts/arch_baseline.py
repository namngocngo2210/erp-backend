#!/usr/bin/env python3
"""Ratchet kích thước code backend + nợ ruff (audit 2026-09-23, C3/P1-3).

VÌ SAO TỒN TẠI: từ 08-19 tới 09-23 LOC backend +20%, file >800 dòng 14→17, hàm
>150 dòng 50→57, class >1000 dòng 6→7, nợ ruff dưới `ignore` 405→626 — không có
gì chặn. Script này đo và ghi `scripts/arch-baseline.json`; test
`apps/common/tests/test_arch_ratchet.py` FAIL khi:
  • một file/hàm/class đã có trong baseline TĂNG số dòng,
  • file/hàm/class MỚI vượt trần (file >800, hàm >150, class >1000),
  • số vi phạm ruff của MỘT rule (trong danh sách đang ignore) tăng.

Cách dùng (chạy từ bất kỳ đâu, chỉ cần python3 + ruff nếu muốn đo ruff):
    python scripts/arch_baseline.py            # in số đo hiện tại
    python scripts/arch_baseline.py --check    # so với baseline, exit 1 nếu tệ hơn
    python scripts/arch_baseline.py --update   # ghi baseline mới (CHỈ ĐƯỢC HẠ)
    python scripts/arch_baseline.py --no-ruff  # bỏ qua phần ruff (không có ruff trên PATH)

Baseline CHỈ ĐƯỢC HẠ: `--update` từ chối ghi nếu số nào đó tăng, trừ khi thêm
`--force` (kèm lý do trong MR). Đếm dòng = số dòng nguồn vật lý (end_lineno -
lineno + 1), file = tổng dòng; bỏ migrations, tests, conftest, `__init__.py` rỗng.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
BASELINE_PATH = Path(__file__).resolve().parent / "arch-baseline.json"

FILE_MAX = 800
FUNC_MAX = 150
CLASS_MAX = 1000

# Rule đang nằm trong `ignore` của pyproject: đọc thẳng từ pyproject để không
# phải duy trì hai danh sách.
RUFF_SELECT = "E4,E7,E9,F,B,C4,DTZ,LOG,PIE,RET,SIM,T20,UP"


def _iter_source_files() -> list[Path]:
    out: list[Path] = []
    for base in (BACKEND / "apps", BACKEND / "config"):
        for path in sorted(base.rglob("*.py")):
            parts = path.relative_to(BACKEND).parts
            if "migrations" in parts or "tests" in parts:
                continue
            if path.name.startswith("test_") or path.name == "conftest.py":
                continue
            out.append(path)
    return out


def measure_ast() -> dict[str, dict[str, int]]:
    files: dict[str, int] = {}
    funcs: dict[str, int] = {}
    classes: dict[str, int] = {}
    for path in _iter_source_files():
        rel = path.relative_to(BACKEND).as_posix()
        src = path.read_text(encoding="utf-8")
        n_lines = src.count("\n") + (0 if src.endswith("\n") or not src else 1)
        if n_lines > FILE_MAX:
            files[rel] = n_lines
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                size = node.end_lineno - node.lineno + 1
                if size > CLASS_MAX:
                    classes[f"{rel}::{node.name}"] = size
                for child in node.body:
                    if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                        size = child.end_lineno - child.lineno + 1
                        if size > FUNC_MAX:
                            funcs[f"{rel}::{node.name}.{child.name}"] = size
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                # Hàm cấp module hoặc lồng trong hàm khác (không phải method).
                parent_is_class = False
                for cls in ast.walk(tree):
                    if isinstance(cls, ast.ClassDef) and node in cls.body:
                        parent_is_class = True
                        break
                if parent_is_class:
                    continue
                size = node.end_lineno - node.lineno + 1
                if size > FUNC_MAX:
                    funcs[f"{rel}::{node.name}"] = size
    return {"files": files, "functions": funcs, "classes": classes}


def _ruff_ignore_list() -> list[str]:
    import tomllib

    data = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    return list(data["tool"]["ruff"]["lint"].get("ignore", []))


def measure_ruff() -> dict[str, int] | None:
    ruff = shutil.which("ruff")
    if ruff is None:
        return None
    ignored = set(_ruff_ignore_list())
    proc = subprocess.run(
        [
            ruff,
            "check",
            ".",
            "--no-cache",
            "--isolated",
            "--output-format",
            "json",
            "--line-length",
            "120",
            "--target-version",
            "py313",
            "--extend-exclude",
            "*/migrations/*",
            "--select",
            RUFF_SELECT,
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise SystemExit(f"ruff lỗi: {proc.stderr}")
    counts: dict[str, int] = {}
    for item in json.loads(proc.stdout or "[]"):
        code = item["code"]
        if code in ignored:
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def measure(*, with_ruff: bool = True) -> dict:
    data = measure_ast()
    data["ruff"] = measure_ruff() if with_ruff else None
    return data


def load_baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def compare(baseline: dict, current: dict) -> list[str]:
    """Trả về danh sách vi phạm (rỗng = OK)."""
    problems: list[str] = []
    limits = {"files": FILE_MAX, "functions": FUNC_MAX, "classes": CLASS_MAX}
    for kind, limit in limits.items():
        base = baseline.get(kind, {})
        for key, size in current.get(kind, {}).items():
            if key in base:
                if size > base[key]:
                    problems.append(f"{kind}: {key} tăng {base[key]} → {size} dòng")
            else:
                problems.append(f"{kind}: {key} MỚI vượt trần {limit} ({size} dòng)")
    base_ruff = baseline.get("ruff") or {}
    cur_ruff = current.get("ruff")
    if cur_ruff is not None:
        for code, n in cur_ruff.items():
            if n > base_ruff.get(code, 0):
                problems.append(f"ruff {code}: {base_ruff.get(code, 0)} → {n}")
    return problems


def summary(data: dict) -> str:
    ruff = data.get("ruff")
    ruff_total = sum(ruff.values()) if ruff else 0
    return (
        f"file >{FILE_MAX}: {len(data['files'])}  |  hàm >{FUNC_MAX}: {len(data['functions'])}  |  "
        f"class >{CLASS_MAX}: {len(data['classes'])}  |  ruff (rule ignore): {ruff_total}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="so với baseline, exit 1 nếu tệ hơn")
    parser.add_argument("--update", action="store_true", help="ghi baseline mới (chỉ được hạ)")
    parser.add_argument("--force", action="store_true", help="cho phép --update ghi baseline tăng")
    parser.add_argument("--no-ruff", action="store_true", help="bỏ qua đo ruff")
    parser.add_argument("--json", action="store_true", help="in JSON số đo hiện tại")
    args = parser.parse_args(argv)

    current = measure(with_ruff=not args.no_ruff)
    if args.json:
        print(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.update:
        if BASELINE_PATH.exists() and not args.force:
            problems = compare(load_baseline(), current)
            if problems:
                print("Baseline CHỈ ĐƯỢC HẠ. Các số tăng:", file=sys.stderr)
                for p in problems:
                    print("  -", p, file=sys.stderr)
                print("Dùng --force nếu thật sự cố ý (ghi lý do trong MR).", file=sys.stderr)
                return 1
        if current["ruff"] is None and BASELINE_PATH.exists():
            current["ruff"] = load_baseline().get("ruff")
        BASELINE_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"Đã ghi {BASELINE_PATH.relative_to(ROOT)}: {summary(current)}")
        return 0

    print(summary(current))
    if args.check:
        problems = compare(load_baseline(), current)
        if problems:
            print("Ratchet kiến trúc FAIL (baseline scripts/arch-baseline.json):")
            for p in problems:
                print("  -", p)
            print("Nếu tăng là cố ý: python scripts/arch_baseline.py --update --force (ghi lý do trong MR).")
            return 1
        print("Ratchet kiến trúc OK")
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
