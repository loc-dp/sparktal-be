from typing import Any

import factory
from factory.django import DjangoModelFactory

from apps.accounts.models import User, UserKind

DEFAULT_TEST_PASSWORD = "pass-1234-word"


class UserFactory(DjangoModelFactory[User]):
    class Meta:
        model = User
        django_get_or_create = ("email",)
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    full_name = factory.Faker("name")
    kind = UserKind.COMPANY
    is_active = True
    password = factory.PostGenerationMethodCall("set_password", DEFAULT_TEST_PASSWORD)

    @classmethod
    def _after_postgeneration(cls, instance: User, create: bool, results: Any = None) -> None:
        if create:
            instance.save()
