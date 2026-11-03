"""Store đọc/ghi cài đặt hệ thống. Spec §5.3–5.4.

Đọc: snapshot toàn bảng trong process; mỗi ≥2s so `sysconfig:version` trên Redis,
lệch thì nạp lại. Redis lỗi → giữ snapshot cũ (fail-soft). Test mode (conftest):
không chạm DB/Redis — override_sysconfig → django settings → default.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import ContextDecorator
from datetime import datetime
from typing import Any
from collections.abc import Iterable

from django.conf import settings as django_settings
from django.core.cache import cache

from apps.sysconfig import registry
from apps.sysconfig.registry import coerce

logger = logging.getLogger(__name__)

VERSION_KEY = "sysconfig:version"
CHECK_INTERVAL_SECONDS = 2.0
_monotonic = time.monotonic  # patch được trong test


def _is_test_mode() -> bool:
    return bool(getattr(django_settings, "SYSCONFIG_TEST_MODE", False))


def _is_staging() -> bool:
    return bool(getattr(django_settings, "IS_STAGING", False))


def _read_version() -> int | None:
    """None = Redis không trả lời (lỗi/timeout) — caller giữ snapshot cũ.

    Lưu ý: backend cache prod (InstrumentedRedisCache) KHÔNG raise khi Redis chết mà
    trả None như key chưa có → hàm này trả 0. Vì thế caller phải coi 0 cũng là
    "không có thông tin" khi đã có snapshot (xem _maybe_refresh).
    """
    try:
        raw = cache.get(VERSION_KEY)
    except Exception as exc:  # noqa: BLE001 — fail-soft có chủ đích
        logger.warning("sysconfig: không đọc được version từ Redis (%s)", exc)
        return None
    return int(raw or 0)


def bump_version() -> None:
    try:
        try:
            cache.incr(VERSION_KEY)
        except ValueError:  # key chưa tồn tại
            cache.set(VERSION_KEY, 1, None)
    except Exception as exc:  # noqa: BLE001
        logger.error("sysconfig: không tăng được version trên Redis (%s) — process khác sẽ lệch", exc)


def _load_from_db() -> dict[str, str]:
    from apps.sysconfig.models import SystemSetting  # tránh import model lúc nạp module

    rows: dict[str, str] = {}
    for key, value, secret_value in SystemSetting.objects.values_list("key", "value", "secret_value"):
        spec = registry.BY_KEY.get(key)
        if spec is None:
            continue
        rows[key] = secret_value if spec.secret else value
    return rows


class _Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._overrides: list[dict[str, Any]] = []
        self.reset()

    def reset(self) -> None:
        self._snapshot: dict[str, str] | None = None
        self._version = -1
        self._checked_at = -1e9

    # ── override (test + drain command) ──────────────────────────────────────
    def push_override(self, values: dict[str, Any]) -> None:
        self._overrides.append(values)

    def pop_override(self) -> None:
        self._overrides.pop()

    def _override_value(self, key: str):
        for layer in reversed(self._overrides):
            if key in layer:
                return True, layer[key]
        return False, None

    # ── đọc ──────────────────────────────────────────────────────────────────
    def _maybe_refresh(self) -> None:
        now = _monotonic()
        # abs(): nếu đồng hồ đi lùi (test patch _monotonic rồi trả lại giá trị thật, hoặc
        # clock không đơn điệu) thì hiệu âm lớn phải tính là "hết cửa sổ" chứ không phải
        # "mãi mãi trong cửa sổ" — không có abs, store sẽ không bao giờ so version nữa.
        if self._snapshot is not None and abs(now - self._checked_at) < CHECK_INTERVAL_SECONDS:
            return
        with self._lock:
            now = _monotonic()
            if self._snapshot is not None and abs(now - self._checked_at) < CHECK_INTERVAL_SECONDS:
                return
            self._checked_at = now
            version = _read_version()
            if self._snapshot is None:
                self._snapshot = _load_from_db()
                self._version = version if version is not None else 0
                return
            # 0 = key chưa tồn tại HOẶC Redis chết mà backend nuốt lỗi trả None (fail-soft):
            # cả hai đều không phải tín hiệu "có thay đổi" — một lần ghi thật luôn bump
            # version lên ≥1 (bump_version: incr, hoặc set 1 khi key chưa có). Không có
            # nhánh này thì Redis chết = nạp lại DB mỗi 2s trên mọi process.
            if version is None or version == 0 or version == self._version:
                return
            self._snapshot = _load_from_db()
            self._version = version

    def get(self, key: str) -> Any:
        spec = registry.BY_KEY[key]  # KeyError = lỗi lập trình
        found, value = self._override_value(key)
        if found:
            return value if not isinstance(value, str) else coerce(spec, value)
        if _is_test_mode():
            if hasattr(django_settings, key):
                raw = getattr(django_settings, key)
                return raw if not isinstance(raw, str) else coerce(spec, raw)
            return spec.default
        if spec.staging_forbidden and _is_staging():
            return spec.default
        self._maybe_refresh()
        raw = (self._snapshot or {}).get(key)
        if raw is None:
            return spec.default
        try:
            return coerce(spec, raw)
        except ValueError as exc:
            logger.warning("sysconfig: giá trị DB của %s không hợp lệ (%s) — dùng default", key, exc)
            return spec.default

    def get_datetime(self, key: str) -> datetime | None:
        """Khóa kiểu `datetime` → datetime có múi giờ, rỗng → None."""
        value = self.get(key)
        return value if isinstance(value, datetime) else registry.parse_datetime_value(str(value or ""))

    def get_many(self, keys: Iterable[str]) -> dict[str, Any]:
        return {k: self.get(k) for k in keys}


sysconfig = _Store()


class override_sysconfig(ContextDecorator):  # noqa: N801 — dùng như override_settings
    """Ghi đè giá trị trong process hiện tại (test, lệnh drain). Không chạm DB/Redis."""

    def __init__(self, **values: Any) -> None:
        unknown = [k for k in values if k not in registry.BY_KEY]
        if unknown:
            raise KeyError(f"override_sysconfig: khóa không có trong registry: {unknown}")
        self._values = values

    def __enter__(self):
        sysconfig.push_override(self._values)
        return self

    def __exit__(self, *exc):
        sysconfig.pop_override()
        return False


from django.db import transaction  # noqa: E402 — nhóm ghi, đặt dưới cho dễ đọc


class SysconfigValidationError(Exception):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def hint_for(raw: str) -> str:
    """4 ký tự cuối để nhận diện secret đã đặt (không đủ để dùng lại)."""
    raw = raw or ""
    return f"…{raw[-4:]}" if len(raw) >= 4 else "…"


def mask_for_log(spec: registry.Setting, raw: str) -> str:
    if not raw:
        return ""
    return "***" if spec.secret else raw


def canonical_raw(spec: registry.Setting, coerced, raw: str) -> str:
    """Chuỗi sẽ LƯU xuống DB: bool → "1"/"0", còn lại chuỗi đã strip."""
    if spec.type == "bool":
        return "1" if coerced else "0"
    return raw.strip()


def set_many(values: dict[str, str | None], *, user) -> None:
    """Ghi nhiều khóa trong MỘT transaction; lỗi bất kỳ → không ghi khóa nào.

    `None` = xoá dòng (secret: xoá; khóa thường: về default). Chuỗi = ép kiểu thử
    trước rồi lưu chuỗi đã strip; riêng bool lưu chuẩn hoá "1"/"0" (frontend render
    `checked={value === "1"}`, importer so sánh theo giá trị đã ép kiểu).
    """
    from apps.sysconfig.models import SystemSetting, SystemSettingChange

    errors: dict[str, str] = {}
    cleaned: dict[str, str | None] = {}
    for key, raw in values.items():
        spec = registry.BY_KEY.get(key)
        if spec is None:
            errors[key] = "Khóa không có trong registry."
            continue
        if raw is None:
            cleaned[key] = None
            continue
        try:
            coerced = coerce(spec, str(raw))
        except ValueError as exc:
            errors[key] = str(exc)
            continue
        cleaned[key] = canonical_raw(spec, coerced, str(raw))
    if errors:
        raise SysconfigValidationError(errors)

    with transaction.atomic():
        existing = {r.key: r for r in SystemSetting.objects.select_for_update().filter(key__in=cleaned)}
        changed = False
        for key, raw in cleaned.items():
            spec = registry.BY_KEY[key]
            row = existing.get(key)
            old_raw = (row.secret_value if spec.secret else row.value) if row else ""
            if raw is None:
                if row is None:
                    continue
                row.delete()
                new_raw = ""
            else:
                if row is not None and old_raw == raw:
                    continue
                if row is None:
                    row = SystemSetting(key=key)
                if spec.secret:
                    row.value, row.secret_value = "", raw
                else:
                    row.value, row.secret_value = raw, ""
                row.updated_by = user
                row.save()
                new_raw = raw
            SystemSettingChange.objects.create(
                key=key,
                old_value=mask_for_log(spec, old_raw),
                new_value=mask_for_log(spec, new_raw),
                changed_by=user,
            )
            changed = True
        if changed:
            transaction.on_commit(bump_version)


def first_enabled_at(key: str) -> datetime | None:
    """Lần ĐẦU khóa bool được bật ("1") theo lịch sử `system_setting_changes` (index `key`).
    Không có lịch sử (chưa bật, hoặc bật bằng env/settings) → None."""
    from apps.sysconfig.models import SystemSettingChange

    spec = registry.BY_KEY[key]  # KeyError = lỗi lập trình
    if spec.type != "bool":
        raise ValueError(f"first_enabled_at: {key} không phải khóa bool")
    return (
        SystemSettingChange.objects.filter(key=key, new_value="1")
        .order_by("changed_at", "id").values_list("changed_at", flat=True).first()
    )
