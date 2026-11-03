"""
Encryption utility for sensitive data.
Uses Fernet symmetric encryption from cryptography library.

Chuyển nguyên trạng từ apps/kiotviet/utils.py (audit 2026-08-19 mục 1.6):
đây là tiện ích hạ tầng thuần, không có nghiệp vụ KiotViet, nhưng bị
EncryptedCharField (dùng ở 6 app) kéo theo — đặt ở apps.common (tầng đáy)
để mọi app import mà không vượt tầng. apps/kiotviet/utils.py giữ re-export
tương thích ngược.
"""

import os

from cryptography.fernet import Fernet


class Encryption:
    """
    Utility class for encrypting and decrypting sensitive data.
    Uses Fernet symmetric encryption with a key from environment variables.
    """

    def __init__(self):
        """Initialize encryption with key from settings."""
        self.key = self._get_encryption_key()
        self.fernet = Fernet(self.key)

    def _get_encryption_key(self) -> bytes:
        """
        Get encryption key from environment variable.

        Returns:
            bytes: Encryption key (44 bytes base64-encoded string)

        Raises:
            ValueError: If ENCRYPTION_KEY is not set in environment
        """
        key = os.environ.get('ENCRYPTION_KEY')
        if not key:
            raise ValueError(
                'ENCRYPTION_KEY environment variable is not set. '
                'Please set it in your .env file.'
            )

        # Ensure key is properly formatted for Fernet
        if isinstance(key, str):
            key = key.strip().encode()

        # Validate key length (Fernet requires 44 bytes base64-encoded)
        if len(key) != 44:
            raise ValueError(
                f'ENCRYPTION_KEY must be 44 bytes (base64-encoded). Current length: {len(key)}'
            )

        return key

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt plaintext string.

        Args:
            plaintext: String to encrypt

        Returns:
            str: Encrypted string (base64 encoded)
        """
        if plaintext is None:
            return None

        if not isinstance(plaintext, str):
            plaintext = str(plaintext)

        encrypted = self.fernet.encrypt(plaintext.encode())
        return encrypted.decode()

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt ciphertext string.

        Args:
            ciphertext: Encrypted string (base64 encoded)

        Returns:
            str: Decrypted plaintext string
        """
        if ciphertext is None:
            return None

        if not isinstance(ciphertext, str):
            ciphertext = str(ciphertext)

        decrypted = self.fernet.decrypt(ciphertext.encode())
        return decrypted.decode()


# Singleton instance for reuse
_encryption_instance = None


def get_encryption() -> Encryption:
    """
    Get singleton encryption instance.

    Returns:
        Encryption: Encryption instance
    """
    global _encryption_instance
    if _encryption_instance is None:
        _encryption_instance = Encryption()
    return _encryption_instance
