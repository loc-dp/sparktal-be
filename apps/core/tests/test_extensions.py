import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


def test_unaccent_and_trigram_extensions_are_installed() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension")
        installed = {row[0] for row in cursor.fetchall()}

    assert {"unaccent", "pg_trgm"} <= installed
