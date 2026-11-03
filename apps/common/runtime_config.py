"""Cổng đọc cài đặt hệ thống cho tầng `apps.common`.

`apps.common` là tầng đáy (.importlinter) nên không được import `apps.sysconfig`.
`apps.sysconfig.apps.SysconfigConfig.ready()` đăng ký provider vào đây; các module
common (lark_open_api, ops_alerts) gọi `runtime_config.get(KEY)`.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from django.core.exceptions import ImproperlyConfigured

_provider: Callable[[str], Any] | None = None


def register_provider(fn: Callable[[str], Any]) -> None:
    global _provider
    _provider = fn


def get(key: str) -> Any:
    if _provider is None:
        raise ImproperlyConfigured("runtime_config: chưa có provider (apps.sysconfig chưa ready?)")
    return _provider(key)
