from django.core.cache import cache

CHANNELS_CACHE_VERSION_KEY = "channels:cache:version"


def get_channels_cache_version() -> int:
    version = cache.get(CHANNELS_CACHE_VERSION_KEY)
    if version is None:
        cache.set(CHANNELS_CACHE_VERSION_KEY, 1, timeout=None)
        return 1
    return int(version)


def bump_channels_cache_version() -> int:
    try:
        return int(cache.incr(CHANNELS_CACHE_VERSION_KEY))
    except ValueError:
        cache.set(CHANNELS_CACHE_VERSION_KEY, 2, timeout=None)
        return 2
