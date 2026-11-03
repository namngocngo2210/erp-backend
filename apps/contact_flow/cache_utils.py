"""Versioned-bust cache cho danh mục cấu hình tác nghiệp (audit 2026-08-19 P2).

Stage/Result/Transition gần như tĩnh nhưng bị đọc mỗi lần mở trang contacts
(dropdown cột Tác nghiệp + dialog Ghi KQ). Cùng khuôn channels_core/cache_utils:
key mang version, ghi cấu hình thì bump — cache cũ tự mồ côi, không cần xoá.
"""
from django.core.cache import cache

CF_CONFIG_CACHE_VERSION_KEY = "contact_flow:config:cache:version"
CF_CONFIG_CACHE_TTL = 3600  # lưới an toàn; nguồn bust chính là bump version khi ghi


def get_config_cache_version() -> int:
    version = cache.get(CF_CONFIG_CACHE_VERSION_KEY)
    if version is None:
        cache.set(CF_CONFIG_CACHE_VERSION_KEY, 1, timeout=None)
        return 1
    return int(version)


def bump_config_cache_version() -> int:
    try:
        return int(cache.incr(CF_CONFIG_CACHE_VERSION_KEY))
    except ValueError:
        cache.set(CF_CONFIG_CACHE_VERSION_KEY, 2, timeout=None)
        return 2
