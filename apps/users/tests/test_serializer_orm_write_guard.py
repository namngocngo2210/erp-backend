"""Lưới chặn cấu trúc: serializer không được chứa transaction/bulk-write.

Audit backend-architecture 4.6: business logic + transaction nằm trong DRF
serializer — nặng nhất là ``product_item_crm_serializers`` (3 cặp create/update
có ``@transaction.atomic`` + 2 chỗ ``bulk_create``), đã dời xuống
``apps/orders/application/services/catalog_write_service.py``. Test này bảo đảm
lớp lỗi không mọc lại: file serializer mới có ``transaction.atomic`` hoặc
``bulk_create(`` → đỏ ngay; chỗ đứng đúng của chúng là ``application/services``.

Kỹ thuật cùng họ với ``test_write_permission_coverage.py``: quét source bằng
AST, không cần DB. Phạm vi quét: mọi ``*.py`` nằm trong thư mục ``serializers``
(``transport/serializers/**``) và mọi module đặt tên kiểu serializer
(``serializers.py`` / ``*_serializers*.py``) — vì serializer nằm cả ở dạng
package lẫn file phẳng (``apps/customers/serializers.py``).

Trạng thái nợ sau đợt trả 2026-08-20 (grep toàn bộ 35 điểm ``.objects./save()/
atomic`` của audit): KHÔNG còn file serializer nào chứa hai marker này —
whitelist rỗng. Các điểm ``.objects.`` còn lại trong serializer đều là ĐỌC,
test này cố ý không flag chúng:
  • ``queryset=`` của DRF relation field — cần ngay lúc import module (xem chú
    thích KiotVietChannel trong product_item_crm_serializers và khối B
    .importlinter), không defer được trong phạm vi này;
  • query exists-check trong ``validate_*`` (vd ``PriceBookSerializer.
    validate_code``) — validation là việc của serializer;
  • đọc để shape ở ``to_representation`` (order_core_serializers,
    channel_serializers_impl, flow_serializers, order_action_serializers,
    kiotviet_landing_serializers, customers/serializers).
Nếu sau này phát sinh file chưa kịp trả, thêm entry whitelist KÈM LÝ DO
("trả dần khi chạm"); entry hết hiệu lực sẽ làm test đỏ để không tích xác chết
— cùng nguyên tắc ``unmatched_ignore_imports_alerting`` của .importlinter.
"""

from __future__ import annotations

import ast
from pathlib import Path

APPS_DIR = Path(__file__).resolve().parents[2]

MARKER_ATOMIC = "transaction.atomic"
MARKER_BULK_CREATE = "bulk_create"

# (đường-dẫn-tương-đối-so-với-apps/, marker) → lý do miễn trừ.
# CHỈ thêm entry cho nợ tồn đọng có kế hoạch trả ("trả dần khi chạm") — code mới
# phải đặt transaction/bulk_create ở application/services ngay từ đầu.
WHITELIST: dict[tuple[str, str], str] = {}


def _is_serializer_module(rel: str) -> bool:
    parts = rel.split("/")
    if "tests" in parts or "migrations" in parts or parts[-1].startswith("test_"):
        return False
    if "serializers" in parts[:-1]:  # nằm trong thư mục serializers (dạng package)
        return True
    name = parts[-1]
    return name == "serializers.py" or "_serializers" in name


def _iter_serializer_files():
    for path in APPS_DIR.rglob("*.py"):
        rel = path.relative_to(APPS_DIR).as_posix()
        if _is_serializer_module(rel):
            yield path, rel


def _imports_atomic_directly(tree: ast.Module) -> bool:
    """``from django.db.transaction import atomic`` — biến thể ít gặp nhưng phải bắt."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("transaction"):
            if any(alias.name == "atomic" for alias in node.names):
                return True
    return False


def _find_markers(tree: ast.Module) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    atomic_imported = _imports_atomic_directly(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr == "atomic":
                base = node.value
                base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
                if base_name == "transaction":
                    hits.append((node.lineno, MARKER_ATOMIC))
            elif node.attr == "bulk_create":
                hits.append((node.lineno, MARKER_BULK_CREATE))
        elif atomic_imported and isinstance(node, ast.Name) and node.id == "atomic":
            hits.append((node.lineno, MARKER_ATOMIC))
    return hits


def test_serializers_contain_no_transaction_or_bulk_writes():
    offenders: list[str] = []
    seen_whitelist: set[tuple[str, str]] = set()

    for path, rel in _iter_serializer_files():
        source = path.read_text(encoding="utf-8")
        if "atomic" not in source and "bulk_create" not in source:
            continue
        tree = ast.parse(source)
        for lineno, marker in _find_markers(tree):
            key = (rel, marker)
            if key in WHITELIST:
                seen_whitelist.add(key)
                continue
            offenders.append(f"{rel}:{lineno} {marker}")

    assert not offenders, (
        "Serializer chứa transaction.atomic/bulk_create — business write phải nằm "
        "ở application/services (mẫu: catalog_write_service), serializer chỉ "
        "validate + shape. Hoặc thêm whitelist KÈM LÝ DO nếu là nợ có kế hoạch "
        "trả:\n  " + "\n  ".join(sorted(offenders))
    )

    stale = set(WHITELIST) - seen_whitelist
    assert not stale, (
        "Entry whitelist không còn khớp marker nào (đã trả nợ hoặc file đổi) — "
        f"xoá đi để danh sách không tích xác chết: {sorted(stale)}"
    )
