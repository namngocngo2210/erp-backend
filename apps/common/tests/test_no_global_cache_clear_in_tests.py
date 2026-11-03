"""Guard: cấm `cache.clear()` / `event_bus.clear()` mới trong bộ test.

- `cache.clear()` trên `CACHES["default"]` = `RedisCache.clear()` = FLUSHDB trên Redis
  THẬT dùng chung (giữa mọi test, giữa các worktree chạy song song, với dev server):
  xoá lock/counter/throttle của test khác đang chạy → flaky không truy được. Test
  cần cache sạch phải dùng fixture `isolated_cache` (backend/conftest.py).
- `event_bus.clear()` xoá luôn subscriber production đăng ký lúc `ready()`. Autouse
  `_isolate_event_bus` (backend/conftest.py) đã khôi phục sau mỗi test nên các chỗ
  cũ vô hại, nhưng test mới nên tạo `EventBus()` riêng hoặc `subscribe` rồi để
  fixture dọn — không cần clear.

Quét AST mọi file test dưới `apps/`; whitelist chỉ cho chỗ ĐÃ có và có lý do.
"""

from __future__ import annotations

import ast
from pathlib import Path

APPS_DIR = Path(__file__).resolve().parents[2]

# {đường dẫn tương đối apps/: lý do} — chỉ thêm khi thật sự không thể thay bằng
# fixture; `cache.clear()` KHÔNG có ngoại lệ.
EVENT_BUS_CLEAR_WHITELIST: dict[str, str] = {
    "common/tests/test_events.py": "test chính EventBus.clear(); bus được autouse khôi phục sau test",
    "hr/tests/test_employee_service.py": "gỡ lambda subscriber ở tearDown; autouse khôi phục handler production",
    "inventory/tests/test_stock_reissue_on_revive.py": "cần bus chỉ có subscriber inventory; autouse khôi phục sau test",
    "kiotviet/tests/test_invoice_webhook.py": "dựng bus chỉ gồm inventory+users subscriber; autouse khôi phục sau test",
}
CACHE_CLEAR_WHITELIST: dict[str, str] = {}


def _test_files() -> list[Path]:
    files: list[Path] = []
    for path in APPS_DIR.rglob("*.py"):
        rel = path.relative_to(APPS_DIR)
        if "tests" in rel.parts or rel.name == "tests.py":
            files.append(path)
    return sorted(files)


def _global_clear_calls(tree: ast.AST, receiver: str) -> list[int]:
    """Dòng có lời gọi `<receiver>.clear()` với receiver là tên biến module-level."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "clear":
            continue
        target = node.func.value
        if isinstance(target, ast.Name) and target.id == receiver:
            lines.append(node.lineno)
    return lines


def _offenders(receiver: str, whitelist: dict[str, str]) -> list[str]:
    offenders: list[str] = []
    for path in _test_files():
        rel = path.relative_to(APPS_DIR).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines = _global_clear_calls(tree, receiver)
        if lines and rel not in whitelist:
            offenders.append(f"{rel}:{','.join(map(str, lines))}")
    return offenders


def test_files_in_scope():
    files = _test_files()
    assert len(files) > 300, "guard phải quét được toàn bộ file test dưới apps/"
    assert any(p.name == "test_no_global_cache_clear_in_tests.py" for p in files)


def test_no_cache_clear_in_tests():
    offenders = _offenders("cache", CACHE_CLEAR_WHITELIST)
    assert offenders == [], (
        "cache.clear() = FLUSHDB Redis dùng chung. Dùng fixture `isolated_cache` "
        "(backend/conftest.py) thay vì clear:\n  " + "\n  ".join(offenders)
    )


def test_no_new_event_bus_clear_in_tests():
    offenders = _offenders("event_bus", EVENT_BUS_CLEAR_WHITELIST)
    assert offenders == [], (
        "event_bus.clear() xoá subscriber production. Dùng `EventBus()` riêng hoặc "
        "`subscribe` rồi để autouse `_isolate_event_bus` dọn; nếu bắt buộc, thêm vào "
        "EVENT_BUS_CLEAR_WHITELIST kèm lý do:\n  " + "\n  ".join(offenders)
    )


def test_whitelist_entries_still_exist():
    """Whitelist chỉ được chứa file còn gọi clear — dọn xong thì gỡ khỏi danh sách."""
    for rel in list(CACHE_CLEAR_WHITELIST) + list(EVENT_BUS_CLEAR_WHITELIST):
        path = APPS_DIR / rel
        assert path.exists(), f"{rel} trong whitelist không còn tồn tại"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        receiver = "cache" if rel in CACHE_CLEAR_WHITELIST else "event_bus"
        assert _global_clear_calls(tree, receiver), f"{rel} không còn gọi {receiver}.clear() — gỡ khỏi whitelist"
