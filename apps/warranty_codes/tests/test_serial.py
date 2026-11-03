import re

from apps.warranty_codes.application.services import serial_format
from apps.warranty_codes.domain.serial import CODE_ALPHABET, CODE_LENGTH, format_serial, generate_code


def test_format_serial_pads_six_digits():
    """Domain nhận prefix/pad qua tham số (không đọc settings — audit 2026-09-23)."""
    assert format_serial(1, prefix="DX-", pad=6) == "DX-000001"
    assert format_serial(15152, prefix="DX-", pad=6) == "DX-015152"
    assert format_serial(1_234_567, prefix="DX-", pad=6) == "DX-1234567"  # vượt pad thì giữ nguyên số


def test_application_format_serial_reads_settings(settings):
    settings.WARRANTY_CODE_SERIAL_PREFIX = "DX-"
    settings.WARRANTY_CODE_SERIAL_PAD = 6
    assert serial_format.format_serial(1) == "DX-000001"
    settings.WARRANTY_CODE_SERIAL_PREFIX = "BH"
    settings.WARRANTY_CODE_SERIAL_PAD = 4
    assert serial_format.format_serial(7) == "BH0007"


def test_generate_code_charset_and_length():
    for _ in range(200):
        code = generate_code()
        assert len(code) == CODE_LENGTH
        assert re.fullmatch(rf"[A-Z0-9]{{{CODE_LENGTH}}}", code)


def test_code_length_is_eight():
    """Quyết định chủ 18/09/2026: rút mã dưới lớp cào từ 10 xuống 8 ký tự
    (36^8 ≈ 2,8 nghìn tỉ — vẫn đủ để retry-cả-chunk ở _insert_chunk không
    thành vòng lặp khi tổng mã lên hàng triệu; 6 ký tự thì không)."""
    assert CODE_LENGTH == 8


def test_code_alphabet_has_no_special_chars():
    assert set(CODE_ALPHABET) == set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
