"""Helper resolve kho đích trên Kiot cũ theo map cặp kho new→old.

Thứ tự resolve cho 1 kho nguồn:
  1. old_sync_branch_map trên kênh mới (cặp source→target).
  2. old_sync_branch_id trên kênh mới (fallback legacy, áp cho mọi kho chưa map).
  3. branch_id của kênh Kiot cũ đã ghép.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.kiotviet.models import KiotVietChannel


def normalize_branch_map(raw) -> list[dict]:
    """Chuẩn hoá old_sync_branch_map về list[{source_branch_id, target_branch_id}] đã strip.

    Bỏ entry thiếu source/target; entry trùng source giữ entry đầu tiên.
    """
    if not isinstance(raw, list):
        return []
    normalized: list[dict] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source_branch_id") or "").strip()
        target = str(entry.get("target_branch_id") or "").strip()
        if not source or not target or source in seen:
            continue
        seen.add(source)
        normalized.append({"source_branch_id": source, "target_branch_id": target})
    return normalized


def branch_map_lookup(new_channel: KiotVietChannel, source_branch_id: str | int | None) -> str:
    """Tra target theo map; trả "" nếu kho nguồn không có trong map."""
    source = str(source_branch_id or "").strip()
    if not source:
        return ""
    for entry in normalize_branch_map(getattr(new_channel, "old_sync_branch_map", None)):
        if entry["source_branch_id"] == source:
            return entry["target_branch_id"]
    return ""


def resolve_old_sync_target_branch_id(
    new_channel: KiotVietChannel,
    paired_old_channel: KiotVietChannel,
    source_branch_id: str | int | None,
) -> str:
    """Resolve kho đích trên Kiot cũ cho 1 kho nguồn của kênh mới (chuỗi fallback ở docstring module)."""
    mapped = branch_map_lookup(new_channel, source_branch_id)
    if mapped:
        return mapped
    legacy = str(getattr(new_channel, "old_sync_branch_id", None) or "").strip()
    if legacy:
        return legacy
    return str(getattr(paired_old_channel, "branch_id", None) or "").strip()
