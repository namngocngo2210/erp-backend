class BestExpressError(Exception):
    """Lỗi từ Best Express API — message giữ nguyên văn từ Best (passthrough)."""

    def __init__(self, message: str, payload: dict | None = None):
        super().__init__(message)
        self.message = message
        self.payload = payload or {}
