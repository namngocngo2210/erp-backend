"""Serial + mã bảo hành. Pure — không Django: prefix/pad của serial do application
truyền vào (``apps.warranty_codes.application.services.serial_format`` đọc settings),
audit 2026-09-23 T3/P2-2."""

import secrets

CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
# 10 → 8 từ 18/09/2026 (quyết định chủ; mã cũ đã sinh lại bằng
# `manage.py regenerate_warranty_codes`). 36^8 ≈ 2,8 nghìn tỉ tổ hợp — đủ để
# retry-cả-chunk ở batch_service._insert_chunk không thành vòng lặp khi tổng mã
# lên hàng triệu (6 ký tự thì không).
CODE_LENGTH = 8


def format_serial(seq: int, *, prefix: str, pad: int) -> str:
    """``prefix`` + số đệm 0 tới ``pad`` chữ số (vượt pad thì giữ nguyên số)."""
    return f"{prefix}{seq:0{pad}d}"


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
