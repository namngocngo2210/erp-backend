"""Lưới chặn AddIndex thường trên bảng NÓNG (audit 2026-09-23 T2/P2-10).

`CREATE INDEX` thường giữ SHARE lock (chặn mọi ghi) suốt thời gian quét bảng —
trên orders (560k dòng, 49 index) hay audit_logs (2,2M dòng) là vài chục giây
đến vài phút webhook/cron ghi bị treo. Quy ước: mọi AddIndex trên các bảng dưới
phải là `AddIndexConcurrently` (django.contrib.postgres.operations) và migration
phải `atomic = False`; RunSQL tạo index trên các bảng đó cũng phải có
CONCURRENTLY. Đọc bằng AST nên không cần import migration; model_name được đổi
sang tên bảng qua app registry (model đã dời app thì tra bảng tĩnh).

WHITELIST là các migration ĐÃ APPLY trên prod trước khi có quy ước (không sửa
lịch sử) — chỉ được GIẢM.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from django.apps import apps as django_apps

BACKEND_DIR = Path(__file__).resolve().parents[3]

HOT_TABLES = {
    "orders",
    "order_items",
    "customers",
    "audit_logs",
    "order_assessments",
    "cnv_sync_records",
    "customer_segment_snapshot",
}

# Model đã dời app / đổi tên: (app_label, model_name) → db_table. Chỉ cần cho
# model KHÔNG còn tra được qua registry ở app cũ.
LEGACY_TABLES: dict[tuple[str, str], str] = {}

# Migration đã apply trước quy ước (orders 0119 là mốc audit chỉ ra). Xoá dần
# khi squash. Không thêm mới.
WHITELIST: set[str] = {
    "customers/0002_add_query_indexes.py",
    "customers/0003_remove_address_add_code.py",
    "customers/0005_customer_customer_phone_normalized_idx_and_more.py",
    "customers/0009_customer_deleted_at.py",
    "customers/0010_customer_trgm_indexes.py",
    "customers/0026_snap_lastbuy_spent_index.py",
    "orders/0001_squashed_0089_order_is_export_vat_vat_link.py",
    "orders/0090_order_ord_gtc_customer_idx.py",
    "orders/0099_order_outside_shipping_form.py",
    "orders/0119_alter_order_options_alter_order_managers_and_more.py",
}

_CREATE_INDEX_RE = re.compile(
    r"CREATE\s+(UNIQUE\s+)?INDEX\s+(?!CONCURRENTLY)(IF\s+NOT\s+EXISTS\s+)?\S+\s+ON\s+(?:public\.)?(\w+)",
    re.IGNORECASE,
)


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _kw(node: ast.Call, name: str):
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _const_str(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _const_str(node.left), _const_str(node.right)
        return (left or "") + (right or "")
    if isinstance(node, (ast.Tuple, ast.List)):
        return " ".join(_const_str(e) or "" for e in node.elts)
    return None


def _table_for(app_label: str, model_name: str) -> str | None:
    try:
        return django_apps.get_model(app_label, model_name)._meta.db_table
    except LookupError:
        return LEGACY_TABLES.get((app_label, model_name.lower()))


def _migration_class(tree: ast.Module) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Migration":
            return node
    return None


def _is_non_atomic(cls: ast.ClassDef) -> bool:
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "atomic":
                    return isinstance(stmt.value, ast.Constant) and stmt.value.value is False
    return False


def _operations_calls(cls: ast.ClassDef):
    """Mọi Call trong `operations`, kèm cờ 'chỉ state' khi nằm trong
    SeparateDatabaseAndState(state_operations=[...])."""
    for stmt in cls.body:
        if not (isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "operations" for t in stmt.targets
        )):
            continue
        yield from _walk_calls(stmt.value, state_only=False)


def _walk_calls(node, state_only: bool):
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name == "SeparateDatabaseAndState":
            state = _kw(node, "state_operations")
            db = _kw(node, "database_operations")
            if state is not None:
                yield from _walk_calls(state, state_only=True)
            if db is not None:
                yield from _walk_calls(db, state_only=state_only)
            return
        yield node, state_only
        for arg in node.args:
            yield from _walk_calls(arg, state_only)
        for kw in node.keywords:
            yield from _walk_calls(kw.value, state_only)
    elif isinstance(node, (ast.List, ast.Tuple)):
        for e in node.elts:
            yield from _walk_calls(e, state_only)
    elif isinstance(node, ast.Starred):
        yield from _walk_calls(node.value, state_only)


def _violations_in(path: Path, app_label: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    cls = _migration_class(tree)
    if cls is None:
        return []
    non_atomic = _is_non_atomic(cls)
    out: list[str] = []
    for call, state_only in _operations_calls(cls):
        name = _call_name(call)
        if name in {"AddIndex", "AddIndexConcurrently"}:
            model = _const_str(_kw(call, "model_name") or ast.Constant(""))
            table = _table_for(app_label, model or "")
            if table not in HOT_TABLES or state_only:
                continue
            if name == "AddIndex":
                out.append(f"AddIndex thường trên bảng nóng `{table}` — dùng AddIndexConcurrently")
            elif not non_atomic:
                out.append(f"AddIndexConcurrently trên `{table}` nhưng thiếu `atomic = False`")
        elif name == "RunSQL" and not state_only:
            sql = _const_str(_kw(call, "sql") or (call.args[0] if call.args else ast.Constant("")))
            for m in _CREATE_INDEX_RE.finditer(sql or ""):
                if m.group(3).lower() in HOT_TABLES:
                    out.append(
                        f"RunSQL CREATE INDEX không CONCURRENTLY trên `{m.group(3)}`"
                    )
    return out


def _scan() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted((BACKEND_DIR / "apps").glob("*/migrations/[0-9]*.py")):
        app_label = path.parent.parent.name
        problems = _violations_in(path, app_label)
        if problems:
            found[f"{app_label}/{path.name}"] = problems
    return found


def test_hot_table_indexes_are_created_concurrently():
    found = _scan()
    new = {k: v for k, v in found.items() if k not in WHITELIST}
    assert not new, "AddIndex trên bảng nóng phải CONCURRENTLY + atomic=False:\n" + "\n".join(
        f"  {k}: {'; '.join(v)}" for k, v in sorted(new.items())
    )


def test_whitelist_only_shrinks():
    found = _scan()
    stale = sorted(k for k in WHITELIST if k not in found)
    assert not stale, "Entry WHITELIST không còn vi phạm — xoá khỏi danh sách: " + ", ".join(stale)
