"""
KiotViet integration app.
"""

__all__ = ["KiotVietService"]


def __getattr__(name):
    """Lazy load KiotVietService to avoid circular imports"""
    if name == "KiotVietService":
        from .services import KiotVietService
        return KiotVietService
    raise AttributeError(f"Module '{__name__}' has no attribute '{name}'")
