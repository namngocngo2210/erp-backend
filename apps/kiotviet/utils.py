"""
DEPRECATED shim — Encryption/get_encryption đã chuyển về apps/common/encryption.py
(audit 2026-08-19 mục 1.6: tiện ích hạ tầng thuần, không có nghiệp vụ KiotViet).
Giữ re-export để đường import cũ `apps.kiotviet.utils` không gãy; code mới dùng
`from apps.common.encryption import ...`.
"""

from apps.common.encryption import Encryption, get_encryption

__all__ = ["Encryption", "get_encryption"]
