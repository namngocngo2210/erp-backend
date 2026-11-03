"""Backend file length guardrail.

Policy:
- simple files: <= 200 lines
- complex files: <= 500 lines

This script excludes migrations, staticfiles, __pycache__, tests, and virtual env folders.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT

EXCLUDE_PARTS = {
    "migrations",
    "staticfiles",
    "__pycache__",
    "tests",
    ".venv",
    "venv",
    "node_modules",
}

COMPLEX_HINTS = (
    "service",
    "services",
    "serializer",
    "serializers",
    "task",
    "tasks",
    "view",
    "views",
    "catalog",
    "model",
    "models",
    "repository",
    "repositories",
    "command",
    "settings",
    "parser",
    "import",
    "impl",
    "implementation",
)

SIMPLE_LIMIT = 200
COMPLEX_LIMIT = 500


def should_skip(path: Path) -> bool:
    return any(part in EXCLUDE_PARTS for part in path.parts)


def is_complex(path: Path) -> bool:
    name = path.stem.lower()
    rel_parts = [part.lower() for part in path.parts]
    return any(hint in name for hint in COMPLEX_HINTS) or any(
        any(hint in part for hint in COMPLEX_HINTS) for part in rel_parts
    )


def count_lines(path: Path) -> int:
    return sum(1 for _ in path.open("r", encoding="utf-8"))


def main() -> int:
    py_files = [p for p in BACKEND.rglob("*.py") if not should_skip(p)]
    violations: list[tuple[Path, int, int, str]] = []

    for path in py_files:
        line_count = count_lines(path)
        complex_file = is_complex(path)
        limit = COMPLEX_LIMIT if complex_file else SIMPLE_LIMIT
        if line_count > limit:
            violations.append((path, line_count, limit, "complex" if complex_file else "simple"))

    print(f"Checked {len(py_files)} python files under {BACKEND}")
    if not violations:
        print("PASS: no length violations")
        return 0

    print("FAIL: length violations detected")
    for path, lines, limit, kind in sorted(violations, key=lambda x: x[1], reverse=True):
        rel = path.relative_to(ROOT)
        print(f"- {rel}: {lines} lines > {limit} ({kind})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
