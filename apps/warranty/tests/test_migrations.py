import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_migrations_apply_clean():
    # Nếu tới đây DB đã migrate (fixture db), chỉ cần makemigrations --check không sinh mới.
    call_command("makemigrations", "warranty", check=True, dry_run=True)
