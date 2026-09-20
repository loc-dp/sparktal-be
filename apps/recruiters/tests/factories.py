import factory
from factory.django import DjangoModelFactory

from apps.accounts.models import UserKind
from apps.accounts.tests.factories import UserFactory
from apps.recruiters.models import RecruiterProfile


class RecruiterProfileFactory(DjangoModelFactory[RecruiterProfile]):
    class Meta:
        model = RecruiterProfile

    user = factory.SubFactory(UserFactory, kind=UserKind.RECRUITER)
    nickname = factory.Sequence(lambda n: f"Recruiter {n}")
