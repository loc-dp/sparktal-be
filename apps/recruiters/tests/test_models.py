import pytest
from django.db import IntegrityError

from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db


def test_nickname_is_unique() -> None:
    RecruiterProfileFactory(nickname="NorthStar Talent")

    with pytest.raises(IntegrityError):
        RecruiterProfileFactory(nickname="NorthStar Talent")


def test_str_is_the_nickname() -> None:
    profile = RecruiterProfileFactory(nickname="Bluefin Search")

    assert str(profile) == "Bluefin Search"
    assert profile.user.kind == "recruiter"
