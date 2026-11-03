"""Bộ đếm + báo cáo một lượt đồng bộ (lưu vào `pancake_pos_sync_runs`)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

MAX_SAMPLES = 20
MAX_ISSUES = 100


@dataclass
class SyncCounters:
    """Đếm theo MẪU MÃ (SKU), không theo nhóm."""

    created: int = 0
    updated: int = 0
    linked: int = 0
    skipped: int = 0
    conflicts: int = 0
    errors: int = 0
    hidden: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "hidden": self.hidden,
            "created": self.created,
            "updated": self.updated,
            "linked": self.linked,
            "skipped": self.skipped,
            "conflicts": self.conflicts,
            "errors": self.errors,
        }


@dataclass
class SyncReport:
    run_id: int
    mode: str
    dry_run: bool
    counters: SyncCounters = field(default_factory=SyncCounters)
    candidates: int = 0
    eligible: int = 0
    groups: int = 0
    pancake_variations: int = 0
    ineligible: Counter[str] = field(default_factory=Counter)
    samples: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    fatal: str = ""
    requested: int = 0  # incremental: số id sản phẩm được yêu cầu
    # Nhánh linh kiện (two-way-sync §6b): status unsupported|excluded|included, eligible,
    # category_id / category (would_create|created) / category_error.
    components: dict[str, Any] = field(default_factory=dict)

    def add_sample(self, sample: dict[str, Any]) -> None:
        if len(self.samples) < MAX_SAMPLES:
            self.samples.append(sample)

    def add_conflict(self, group_key: str, sku: str, message: str) -> None:
        self.counters.conflicts += 1
        if len(self.conflicts) < MAX_ISSUES:
            self.conflicts.append({"group_key": group_key, "sku": sku, "message": message})

    def add_error(self, group_key: str, skus: list[str], message: str) -> None:
        self.counters.errors += len(skus)
        if len(self.errors) < MAX_ISSUES:
            self.errors.append({"group_key": group_key, "skus": skus, "message": message})

    def as_json(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "candidates": self.candidates,
            "eligible": self.eligible,
            "groups": self.groups,
            "pancake_variations": self.pancake_variations,
            **({"requested": self.requested} if self.requested else {}),
            "ineligible": dict(sorted(self.ineligible.items())),
            **({"components": self.components} if self.components else {}),
            "samples": self.samples,
            "conflicts": self.conflicts,
            "errors": self.errors,
            **({"fatal": self.fatal} if self.fatal else {}),
        }
