"""
Migration to encrypt existing client_secret and password fields in KiotVietChannel.
"""

from django.db import migrations
from cryptography.fernet import Fernet
import os


def generate_encryption_key():
    """Generate a new encryption key."""
    return Fernet.generate_key().decode()


def encrypt_existing_data(apps, schema_editor):
    """
    Encrypt existing client_secret and password fields.
    """
    KiotVietChannel = apps.get_model('kiotviet', 'KiotVietChannel')

    # Get encryption key from environment
    encryption_key = os.environ.get('ENCRYPTION_KEY')
    if not encryption_key:
        raise ValueError(
            'ENCRYPTION_KEY environment variable is not set. '
            'Please set it in your .env file before running this migration.'
        )

    # Initialize Fernet with the key
    fernet = Fernet(encryption_key.encode())

    # Encrypt all existing records
    for channel in KiotVietChannel.objects.all():
        if channel.client_secret:
            channel.client_secret = fernet.encrypt(
                channel.client_secret.encode()
            ).decode()
            channel.save(update_fields=['client_secret'])

        if channel.password:
            channel.password = fernet.encrypt(
                channel.password.encode()
            ).decode()
            channel.save(update_fields=['password'])


def decrypt_existing_data(apps, schema_editor):
    """
    Decrypt existing client_secret and password fields (for rollback).
    """
    KiotVietChannel = apps.get_model('kiotviet', 'KiotVietChannel')

    # Get encryption key from environment
    encryption_key = os.environ.get('ENCRYPTION_KEY')
    if not encryption_key:
        raise ValueError(
            'ENCRYPTION_KEY environment variable is not set. '
            'Please set it in your .env file before running this migration.'
        )

    # Initialize Fernet with the key
    fernet = Fernet(encryption_key.encode())

    # Decrypt all existing records
    for channel in KiotVietChannel.objects.all():
        if channel.client_secret:
            try:
                channel.client_secret = fernet.decrypt(
                    channel.client_secret.encode()
                ).decode()
                channel.save(update_fields=['client_secret'])
            except Exception:
                # If decryption fails, leave as is
                pass

        if channel.password:
            try:
                channel.password = fernet.decrypt(
                    channel.password.encode()
                ).decode()
                channel.save(update_fields=['password'])
            except Exception:
                # If decryption fails, leave as is
                pass


class Migration(migrations.Migration):

    dependencies = [
        ('kiotviet', '0008_remove_kiotvietchannel_channel_ids'),
    ]

    operations = [
        migrations.RunPython(
            encrypt_existing_data,
            reverse_code=decrypt_existing_data,
        ),
    ]
