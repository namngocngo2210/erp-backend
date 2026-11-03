"""Output plumbing cho các service được move từ management command (WP8.1).

Các mixin nghiệp vụ (import/sync invoices) được move NGUYÊN VĂN từ
``management/commands/`` sang đây; chúng vẫn gọi ``self.stdout.write(...)``,
``self.stderr.write(...)`` và ``self.style.SUCCESS(...)`` như khi còn là
BaseCommand. Module này cung cấp adapter tối thiểu để service chạy độc lập
(Celery task, webhook) mà không cần Django command machinery:

- Khi chạy qua CLI: command truyền thẳng ``self.stdout``/``self.stderr``
  (OutputWrapper) + ``self.style`` của BaseCommand vào service → output CLI
  giữ nguyên từng byte (kể cả màu).
- Khi chạy standalone (worker): mặc định ghi ra sys.stdout/sys.stderr với
  newline tự thêm (đúng hành vi OutputWrapper), style là passthrough.
"""

import sys


class StreamOutput:
    """Tối giản hoá django.core.management.base.OutputWrapper: tự thêm newline."""

    def __init__(self, stream=None):
        self._stream = stream if stream is not None else sys.stdout

    def write(self, msg="", style_func=None, ending="\n"):
        msg = str(msg)
        if ending and not msg.endswith(ending):
            msg += ending
        if style_func is not None:
            msg = style_func(msg)
        self._stream.write(msg)

    def flush(self):
        flush = getattr(self._stream, "flush", None)
        if callable(flush):
            flush()


class PlainStyle:
    """Passthrough thay cho BaseCommand.style: SUCCESS/WARNING/NOTICE/... = identity."""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda text: text
