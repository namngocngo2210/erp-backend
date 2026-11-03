"""Lưới chặn FK vượt tầng (audit 2026-08-19, backend-architecture §1.5).

`.importlinter` gác import Python nhưng MÙ với ForeignKey khai bằng string
reference ("orders.PriceBook") — không sinh import nào. FK là lớp coupling nặng
nhất cho lộ trình tách service: import gỡ được bằng port, FK thì phải đổi schema.

Test này đọc CHÍNH `backend/.importlinter` làm nguồn sự thật về tầng (không
nhân bản bản đồ tầng — đổi contract là test tự theo), rồi duyệt mọi FK/O2O/M2M
khai tường minh giữa các app: quan hệ từ tầng DƯỚI trỏ lên tầng TRÊN, hoặc giữa
hai app cùng tầng (độc lập), là vi phạm — trừ khi nằm trong WHITELIST nợ.

WHITELIST chỉ được GIẢM. Entry hết khớp (đã trả nợ) làm test đỏ để xoá.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.apps import apps as django_apps

BACKEND_DIR = Path(__file__).resolve().parents[3]

# (app nguồn, "app_đích.Model") → lý do. Nợ đã biết từ audit 2026-08-19:
WHITELIST: dict[tuple[str, str], str] = {
    ("channels_core", "catalog.PriceBook"): (
        "2026-08-20: PriceBook đã move sang apps.catalog (docs/catalog-app-split.md) "
        "— FK không còn xuyên lên orders god-module nhưng VẪN ngược tầng "
        "(channels_core dưới catalog). Trả nốt bằng cách đảo quan hệ: catalog không "
        "nên biết channel; config bảng-giá-theo-kênh nên là bảng phía channels_core "
        "giữ price_book_id dạng giá trị, hoặc catalog tụt xuống dưới channels_core "
        "khi nó hết FK lên users."
    ),
    ("users", "kiotviet.KiotVietChannel"): (
        "users là tầng đáy nhưng UserKiotvietMapping FK lên tầng tích hợp — nợ "
        "schema audit §1.5; hướng trả: mapping dời sang app kiotviet (nó mới là "
        "bên sở hữu quan hệ này)."
    ),
    ("customers", "orders.Order"): (
        "customers cùng tầng orders (độc lập) nhưng resale/segment snapshot giữ "
        "FK sang Order — nợ cùng cụm với 44 import customers↔orders ở B-Lô6."
    ),
    ("channels_core", "users.Department"): (
        "Cùng tầng độc lập — nợ schema song hành với nợ import B-Lô6, trả theo lô."
    ),
    ("channels_core", "users.User"): (
        "Cùng tầng độc lập — nợ schema song hành với nợ import B-Lô6, trả theo lô."
    ),
    ("channels_core", "kiotviet.KiotVietChannel"): (
        "NGƯỢC tầng — schema coupling nặng nhất; hướng trả ghi ở audit §1.5."
    ),
    ("inventory", "orders.Order"): (
        "Nửa còn lại của cụm inventory→orders (audit §1.7 hạng 6): phần Product đã "
        "trả 2026-08-20 khi Catalog thành app riêng (FK giờ trỏ catalog.Product, hợp "
        "tầng); FK sang Order vẫn cùng-tầng — trả khi đảo sang id+event qua contracts."
    ),
    ("orders", "customers.Customer"): (
        "Cùng tầng độc lập — nợ schema song hành với nợ import B-Lô6, trả theo lô."
    ),
    ("orders", "kiotviet.KiotVietChannel"): (
        "orders (dưới) FK lên kiotviet (tầng tích hợp trên cùng) — audit §1.5 ghi nhận là khoá cứng schema orders↔kiotviet; trả khi có mapping bảng riêng."
    ),
}


def _layer_ranks() -> dict[str, int]:
    """Đọc block `layers =` của backend/.importlinter → {app: rank} (0 = trên cùng)."""
    text = (BACKEND_DIR / ".importlinter").read_text(encoding="utf-8")
    match = re.search(r"^layers =\n((?:    .+\n)+)", text, re.M)
    assert match, "không tìm thấy block layers trong .importlinter"
    ranks: dict[str, int] = {}
    for rank, line in enumerate(match.group(1).splitlines()):
        for name in line.strip().split("|"):
            ranks[name.strip()] = rank
    return ranks


def _explicit_relations():
    """Mọi quan hệ FK/O2O/M2M khai TƯỜNG MINH, chéo app trong `apps.*`."""
    for model in django_apps.get_models():
        src_app = model._meta.app_label
        for field in model._meta.get_fields():
            if not getattr(field, "is_relation", False) or field.auto_created:
                continue
            related = field.related_model
            if related is None:
                continue
            dst_app = related._meta.app_label
            if dst_app == src_app:
                continue
            yield src_app, dst_app, f"{dst_app}.{related.__name__}", (
                f"{src_app}.{model.__name__}.{field.name}"
            )


def test_no_new_fk_points_up_or_across_layers():
    ranks = _layer_ranks()
    offenders: list[str] = []
    seen_whitelist: set[tuple[str, str]] = set()

    for src_app, dst_app, dst_label, where in _explicit_relations():
        if src_app not in ranks or dst_app not in ranks:
            continue  # app ngoài contract (không còn — exhaustive) hoặc lib ngoài
        # rank nhỏ = tầng TRÊN. Hợp lệ khi nguồn ở TRÊN đích (import xuống dưới).
        if ranks[src_app] < ranks[dst_app]:
            continue
        key = (src_app, dst_label)
        if key in WHITELIST:
            seen_whitelist.add(key)
            continue
        direction = "cùng tầng" if ranks[src_app] == ranks[dst_app] else "NGƯỢC tầng"
        offenders.append(f"{where} -> {dst_label} ({direction})")

    assert not offenders, (
        "FK/M2M mới vượt tầng so với contract .importlinter — schema coupling là "
        "thứ đắt nhất phải gỡ khi tách service. Đảo chiều quan hệ, thay bằng "
        "id+event qua contracts/, hoặc thêm WHITELIST kèm lý do:\n  "
        + "\n  ".join(sorted(offenders))
    )

    stale = set(WHITELIST) - seen_whitelist
    assert not stale, f"Entry whitelist đã hết khớp (nợ đã trả?) — xoá đi: {sorted(stale)}"
