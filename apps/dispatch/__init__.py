"""Dispatch wrapper backed by the Go task queue."""
default_app_config = "apps.dispatch.apps.DispatchConfig"


def __getattr__(name):
    # Lazy import — tránh load models trước khi Django apps registry sẵn sàng.
    if name in ("dispatch", "write_audit"):
        from .api import dispatch, write_audit

        return {"dispatch": dispatch, "write_audit": write_audit}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
