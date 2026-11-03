"""Guard: cấm `transaction.atomic` / `select_for_update` / `bulk_create` / `bulk_update`
ở tầng transport (`apps/*/transport/**`).

Audit 2026-09-23 T3/P2-1: 5 điểm view tự mở transaction rồi ghi model ngay trong
hàm view (lead_assignment_view ×3, price_book_views, foxia link_config_views) cộng
thêm 3 khối `bulk_create` OrderKiotVietJob lặp nguyên xi ở order_action_view /
order_crud_view. Nghiệp vụ nằm trong view thì:
- không tái dùng được từ task/command/consumer — mỗi đường gọi lại chép một bản,
  và các bản trôi khỏi nhau (đã xảy ra với khối enqueue job KiotViet);
- test phải đi qua HTTP + auth để chạm tới quy tắc ghi;
- ranh giới transaction gắn với request/response nên khó lý luận về khoá và
  rollback (lock ôm cả đoạn serialize, exception nghiệp vụ lẫn với lỗi HTTP).

Quy tắc: view chỉ parse/validate → gọi service (`apps/<app>/application/services/`)
→ serialize. Mọi `transaction.atomic(...)` (kể cả dạng decorator), `.select_for_update(`,
`.bulk_create(` / `.bulk_update(` phải nằm trong service/repository.

Quét AST mọi file `.py` dưới `apps/*/transport/**` (trừ thư mục tests). Whitelist chỉ
cho chỗ ĐÃ có và có lý do thật — mục tiêu là RỖNG.
"""

from __future__ import annotations

import ast
from pathlib import Path

APPS_DIR = Path(__file__).resolve().parents[2]

# {đường dẫn tương đối apps/: lý do} — chỉ thêm khi thật sự không tách xuống service
# được; ghi rõ vì sao. Mục tiêu giữ RỖNG.
WHITELIST: dict[str, str] = {}

_BULK_METHODS = frozenset({"bulk_create", "bulk_update"})


def _transport_files() -> list[Path]:
    files: list[Path] = []
    for path in APPS_DIR.rglob("*.py"):
        rel = path.relative_to(APPS_DIR)
        parts = rel.parts
        # apps/<app>/transport/**, không tính test nằm bên trong transport.
        if len(parts) < 3 or parts[1] != "transport":
            continue
        if "tests" in parts or rel.name.startswith("test_"):
            continue
        files.append(path)
    return sorted(files)


def _is_atomic_ref(node: ast.AST) -> bool:
    """`transaction.atomic` / `atomic` (đã import trực tiếp) — cả dạng gọi lẫn decorator."""
    if isinstance(node, ast.Attribute):
        return node.attr == "atomic"
    if isinstance(node, ast.Name):
        return node.id == "atomic"
    return False


def _offending_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """(dòng, mô tả) của mọi lời gọi/decorator bị cấm trong một module."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # `@transaction.atomic` không ngoặc là ast.Attribute/Name thuần, không phải Call.
            for deco in node.decorator_list:
                if _is_atomic_ref(deco):
                    found.append((deco.lineno, "@transaction.atomic"))
            continue
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if _is_atomic_ref(func):
            found.append((node.lineno, "transaction.atomic()"))
        elif isinstance(func, ast.Attribute):
            if func.attr == "select_for_update":
                found.append((node.lineno, ".select_for_update()"))
            elif func.attr in _BULK_METHODS:
                found.append((node.lineno, f".{func.attr}()"))
    return sorted(set(found))


def _offenders() -> list[str]:
    offenders: list[str] = []
    for path in _transport_files():
        rel = path.relative_to(APPS_DIR).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits = _offending_calls(tree)
        if hits and rel not in WHITELIST:
            offenders.append(f"{rel}: " + ", ".join(f"L{line} {what}" for line, what in hits))
    return offenders


def test_transport_files_in_scope():
    files = _transport_files()
    assert len(files) > 50, "guard phải quét được các module transport dưới apps/*/transport"
    assert all("transport" in p.relative_to(APPS_DIR).parts for p in files)
    assert not any("tests" in p.relative_to(APPS_DIR).parts for p in files)


def test_detects_all_forbidden_shapes():
    """Tự kiểm: bộ dò phải bắt được đủ 5 dạng — tránh guard xanh vì dò hỏng."""
    sample = (
        "from django.db import transaction\n"
        "from django.db.transaction import atomic\n"
        "@transaction.atomic\n"
        "def a(): pass\n"
        "@atomic()\n"
        "def b(): pass\n"
        "def c():\n"
        "    with transaction.atomic():\n"
        "        M.objects.select_for_update().filter()\n"
        "        M.objects.bulk_create([])\n"
        "        M.objects.bulk_update([], [])\n"
    )
    hits = _offending_calls(ast.parse(sample))
    assert [what for _, what in hits] == [
        "@transaction.atomic",
        "transaction.atomic()",
        "transaction.atomic()",
        ".select_for_update()",
        ".bulk_create()",
        ".bulk_update()",
    ]


def test_no_atomic_or_bulk_write_in_transport():
    offenders = _offenders()
    assert offenders == [], (
        "transaction.atomic / select_for_update / bulk_create / bulk_update không được nằm "
        "trong apps/*/transport — đẩy nghiệp vụ xuống apps/<app>/application/services/ "
        "(audit 2026-09-23 T3/P2-1); nếu bắt buộc, thêm vào WHITELIST kèm lý do:\n  "
        + "\n  ".join(offenders)
    )


def test_whitelist_entries_still_exist():
    """Whitelist chỉ được chứa file còn vi phạm — dọn xong thì gỡ khỏi danh sách."""
    for rel in WHITELIST:
        path = APPS_DIR / rel
        assert path.exists(), f"{rel} trong whitelist không còn tồn tại"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert _offending_calls(tree), f"{rel} không còn vi phạm — gỡ khỏi WHITELIST"
