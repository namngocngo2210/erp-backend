# KiotViet Channel Encryption Setup

This document explains how to set up encryption for sensitive data in the KiotVietChannel model.

## Overview

The following fields in `KiotVietChannel` are now encrypted:
- `client_secret`
- `password`

These fields are automatically encrypted when saved to the database and decrypted when retrieved.

## Setup Instructions

### 1. Generate Encryption Key

Run the following command to generate a new encryption key:

```bash
cd backend
python manage.py generate_encryption_key
```

This will output a key like:
```
Generated encryption key:
your-32-byte-encryption-key-here

Add this key to your .env file:
ENCRYPTION_KEY=your-32-byte-encryption-key-here
```

### 2. Add Key to Environment

Add the generated key to your `.env` file:

```env
ENCRYPTION_KEY=your-32-byte-encryption-key-here
```

**⚠️ IMPORTANT:**
- Keep this key safe and secure
- Never commit it to version control
- Back it up in a secure location
- If you lose this key, you will not be able to decrypt existing data

### 3. Run Migration

After adding the key to your `.env` file, run the migration to encrypt existing data:

```bash
cd backend
python manage.py migrate kiotviet
```

This will:
- Encrypt all existing `client_secret` and `password` values
- Update the database schema to use encrypted fields

**Note:** The migration number is `0009_encrypt_sensitive_fields`. If you need to rollback, you can use:

```bash
python manage.py migrate kiotviet 0008
```

### 4. Verify Encryption

You can verify that encryption is working by checking the database:

```bash
# Access Django shell
python manage.py shell

# Check encrypted data in database
from apps.kiotviet.models import KiotVietChannel

# This will show encrypted values in the database
channel = KiotVietChannel.objects.first()
print(f"Encrypted in DB: {channel.client_secret}")

# This will show decrypted values (automatically decrypted by model)
print(f"Decrypted value: {channel.client_secret}")
```

## How It Works

### Encryption Flow

1. **Saving Data**: When you save a `KiotVietChannel` instance, the `client_secret` and `password` fields are automatically encrypted before being stored in the database.

2. **Retrieving Data**: When you retrieve a `KiotVietChannel` instance, the `client_secret` and `password` fields are automatically decrypted.

3. **Database Storage**: The database stores encrypted values (base64 encoded strings).

### Custom Field Type

The `EncryptedCharField` custom field type handles:
- Automatic encryption on save
- Automatic decryption on retrieve
- Detection of already-encrypted values
- Graceful fallback for decryption errors

## Security Considerations

### Key Management

- **Storage**: Store the encryption key in environment variables, not in code
- **Rotation**: If you need to rotate the key, you'll need to decrypt all data with the old key and re-encrypt with the new key
- **Backup**: Always backup your encryption key along with your database backups

### Database Security

- The encryption protects against unauthorized database access
- It does not protect against application-level attacks
- Ensure your application server is properly secured
- Use SSL/TLS for database connections

### Development vs Production

- **Development**: You can use a generated key for development
- **Production**: Use a strong, randomly generated key
- **Different Environments**: Use different keys for different environments

## Troubleshooting

### Migration Fails

If the migration fails with "ENCRYPTION_KEY environment variable is not set":

1. Make sure you've added `ENCRYPTION_KEY` to your `.env` file
2. Restart your application to load the new environment variable
3. Try running the migration again

### Decryption Errors

If you see decryption errors:

1. Verify your `ENCRYPTION_KEY` is correct
2. Check that the key is exactly 32 bytes long
3. Ensure you're using the same key that was used to encrypt the data

### Data Appears Encrypted in Application

If data appears encrypted in your application:

1. Check that the `ENCRYPTION_KEY` is set correctly
2. Verify the key is being loaded from environment variables
3. Check for any middleware or caching that might be interfering

## Testing

To test encryption/decryption:

```python
from apps.kiotviet.models import KiotVietChannel
from apps.common.encryption import get_encryption

# Test encryption utility
encryption = get_encryption()
plaintext = "test_secret"
encrypted = encryption.encrypt(plaintext)
decrypted = encryption.decrypt(encrypted)

print(f"Original: {plaintext}")
print(f"Encrypted: {encrypted}")
print(f"Decrypted: {decrypted}")
assert plaintext == decrypted

# Test model encryption
channel = KiotVietChannel.objects.create(
    name="Test Channel",
    retailer="test_retailer",
    client_secret="my_secret_key",
    password="my_password"
)

# The values are automatically encrypted in the database
# and automatically decrypted when accessed
print(f"Client Secret: {channel.client_secret}")  # Shows decrypted value
```

## Rollback

If you need to rollback the encryption:

```bash
python manage.py migrate kiotviet 0008
```

This will decrypt all data back to plaintext. **Note:** You'll need the original encryption key to decrypt the data.

## Additional Resources

- [Cryptography Library Documentation](https://cryptography.io/)
- [Django Field Documentation](https://docs.djangoproject.com/en/stable/howto/custom-model-fields/)
- [Environment Variables Best Practices](https://12factor.net/config)
