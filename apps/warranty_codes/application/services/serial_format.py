"""Định dạng serial theo settings — lớp mỏng trên ``domain.serial.format_serial``.

Domain không import django (audit 2026-09-23 T3/P2-2); prefix/pad đọc từ
``WARRANTY_CODE_SERIAL_PREFIX`` / ``WARRANTY_CODE_SERIAL_PAD`` ở đây rồi truyền vào.
Transport (serializers) và batch_service dùng hàm này.
"""

from __future__ import annotations

from django.conf import settings

from apps.warranty_codes.domain import serial


def format_serial(seq: int) -> str:
    return serial.format_serial(
        seq,
        prefix=settings.WARRANTY_CODE_SERIAL_PREFIX,
        pad=settings.WARRANTY_CODE_SERIAL_PAD,
    )
