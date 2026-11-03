"""
Custom model field dùng chung cho mọi app.

EncryptedCharField chuyển nguyên trạng từ apps/kiotviet/models.py (audit
2026-08-19 mục 1.6): field là tiện ích hạ tầng thuần nhưng bị 5 app khác
(best_express, cnv_loyalty, facebook_ads, foxia, google_ads) import ở tầng
model, biến kiotviet thành phụ thuộc chung của các app cùng tầng — rào cản
tách service và 1 lô nợ trong backend/.importlinter (B-Lô6). apps.common là
tầng đáy nên mọi app import được mà không vượt tầng. Deconstruct path mới
(apps.common.fields.EncryptedCharField) được chốt lại bằng migration
AlterField no-op ở từng app.
"""

import logging

from django.db import models

from apps.common.encryption import get_encryption

logger = logging.getLogger(__name__)

# Chuẩn Decimal tiền VND cho FIELD MỚI (audit 2026-09-23 T2/P2-8). Codebase hiện có
# 4 chuẩn (12,2 / 14,2 / 18,2 / 20,2); KHÔNG đồng bộ cột cũ vì ALTER TYPE rewrite
# bảng lớn (orders 1,1GB, order_items 260MB) mà giá trị thấp. Field mới dùng:
#     models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
# 14,2 đủ tới 999 tỷ VND — vượt mọi số tiền một đơn/một dòng có thể có.
MONEY_MAX_DIGITS = 14
MONEY_DECIMAL_PLACES = 2

# Narrow exception set for decryption failures.
# Fernet raises InvalidToken on bad ciphertext; ValueError/TypeError cover
# encoding issues (bad base64, wrong type coercion, etc.).
_DECRYPT_ERRORS = (ValueError, TypeError)
try:
    from cryptography.fernet import InvalidToken as _InvalidToken
    _DECRYPT_ERRORS = (_InvalidToken, ValueError, TypeError)
except ImportError:
    pass


class EncryptedCharField(models.CharField):
    """
    Custom CharField that automatically encrypts data on save and decrypts on retrieve.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, expression, connection):
        """Decrypt value when retrieving from database."""
        if value is None:
            return value

        try:
            encryption = get_encryption()
            return encryption.decrypt(value)
        except _DECRYPT_ERRORS as exc:
            logger.warning("EncryptedCharField.from_db_value: decrypt failed (%s); returning raw", exc)
            return value

    def to_python(self, value):
        """Convert value to Python type."""
        if value is None:
            return value

        if isinstance(value, str):
            try:
                encryption = get_encryption()
                # Try to decrypt - if it fails, it's already plaintext
                return encryption.decrypt(value)
            except _DECRYPT_ERRORS as exc:
                logger.debug("EncryptedCharField.to_python: not encrypted (%s); using raw", exc)
                return value

        return value

    def get_prep_value(self, value):
        """Encrypt value before saving to database."""
        if value is None:
            return value

        if not isinstance(value, str):
            value = str(value)

        try:
            encryption = get_encryption()
            # Check if already encrypted by trying to decrypt
            encryption.decrypt(value)
            # If successful, it's already encrypted
            return value
        except _DECRYPT_ERRORS:
            # Decryption failed — value is plaintext, encrypt it now
            encryption = get_encryption()
            return encryption.encrypt(value)
