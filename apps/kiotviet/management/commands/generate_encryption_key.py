"""
Management command to generate a new encryption key.
Run: python manage.py generate_encryption_key
"""

from django.core.management.base import BaseCommand
from cryptography.fernet import Fernet


class Command(BaseCommand):
    help = 'Generate a new encryption key for sensitive data encryption'

    def handle(self, *args, **options):
        """Generate and display a new encryption key."""
        key = Fernet.generate_key().decode()

        self.stdout.write(self.style.SUCCESS('Generated encryption key:'))
        self.stdout.write(key)
        self.stdout.write('')
        self.stdout.write('Add this key to your .env file:')
        self.stdout.write('ENCRYPTION_KEY=' + key)
        self.stdout.write('')
        self.stdout.write('⚠️  IMPORTANT: Keep this key safe!')
        self.stdout.write('   - Store it securely')
        self.stdout.write('   - Do not commit it to version control')
        self.stdout.write('   - Back it up in a secure location')
        self.stdout.write('   - If you lose this key, you will not be able to decrypt existing data')
