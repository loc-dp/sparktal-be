from decimal import Decimal
from typing import Any, cast

import pytest
from django.test import Client
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.companies.models import Company
from apps.jobs.bounty import HAS_BOUNTY, clean_bounty, has_bounty
from apps.jobs.models import Job
from apps.jobs.tests.admin_helpers import job_change_form
from apps.jobs.tests.factories import JobFactory


def rejection(*args: Any) -> dict[str, list[str]]:
    """What `clean_bounty` says is wrong with `args`, by field."""
    with pytest.raises(ValidationError) as caught:
        clean_bounty(*args)
    detail = cast(dict[str, list[str]], caught.value.detail)
    return {field: [str(message) for message in messages] for field, messages in detail.items()}


@pytest.mark.parametrize(
    ("args", "error_key"),
    [
        (("pct", 4, None), "bounty_pct"),
        (("pct", 31, None), "bounty_pct"),
        (("pct", None, None), "bounty_pct"),
        (("pct", 18.5, None), "bounty_pct"),
        (("pct", Decimal("5.5"), None), "bounty_pct"),
        (("fixed", None, 0), "bounty_amount"),
        (("fixed", None, None), "bounty_amount"),
        (("fixed", None, Decimal("-5")), "bounty_amount"),
        ((None, None, None), "bounty_type"),
        (("", 18, None), "bounty_type"),
        (("percent", 18, None), "bounty_type"),
    ],
)
def test_clean_bounty_rejects(args: tuple[Any, ...], error_key: str) -> None:
    assert list(rejection(*args)) == [error_key]


def test_clean_bounty_keeps_the_sentences_the_company_form_used() -> None:
    assert rejection("pct", 40, None) == {"bounty_pct": ["Bounty must be between 5 and 30%"]}
    assert rejection("fixed", None, None) == {"bounty_amount": ["Enter the fixed bounty amount"]}
    assert rejection(None, None, None) == {"bounty_type": ["Pick a bounty type."]}


def test_clean_bounty_clears_the_other_field() -> None:
    assert clean_bounty("pct", 18, Decimal("5000000")) == {
        "bounty_type": "pct",
        "bounty_pct": 18,
        "bounty_amount": None,
    }
    assert clean_bounty("fixed", 18, Decimal("5000000")) == {
        "bounty_type": "fixed",
        "bounty_pct": None,
        "bounty_amount": Decimal("5000000"),
    }


@pytest.mark.parametrize("pct", [18, 18.0, Decimal("18")])
def test_clean_bounty_stores_a_whole_number_of_percent(pct: Any) -> None:
    stored = clean_bounty("pct", pct, None)["bounty_pct"]

    assert stored == 18
    assert type(stored) is int


def test_clean_bounty_says_the_pct_sentence_for_a_pct_that_is_not_whole() -> None:
    assert rejection("pct", 18.5, None) == {"bounty_pct": ["Bounty must be between 5 and 30%"]}


@pytest.mark.parametrize("pct", [5, 30])
def test_clean_bounty_accepts_the_edges_of_the_range(pct: int) -> None:
    assert clean_bounty("pct", pct, None)["bounty_pct"] == pct


@pytest.mark.django_db
@pytest.mark.parametrize(
    "values",
    [
        {"bounty_type": "pct", "bounty_pct": 18, "bounty_amount": None},
        {"bounty_type": "fixed", "bounty_pct": None, "bounty_amount": 90_000_000},
        {"bounty_type": "pct", "bounty_pct": None, "bounty_amount": 90_000_000},
        {"bounty_type": "fixed", "bounty_pct": 18, "bounty_amount": None},
        {"bounty_type": None, "bounty_pct": None, "bounty_amount": None},
        {"bounty_type": None, "bounty_pct": 18, "bounty_amount": 90_000_000},
    ],
    ids=["pct", "fixed", "pct-without-pct", "fixed-without-amount", "none", "no-type"],
)
def test_has_bounty_and_the_query_agree(company: Company, values: dict[str, Any]) -> None:
    job = JobFactory(company=company, **values)

    assert Job.objects.filter(pk=job.pk).filter(HAS_BOUNTY).exists() is has_bounty(job)


@pytest.mark.django_db
def test_a_bounty_needs_the_value_its_type_asks_for(company: Company) -> None:
    pct = JobFactory(company=company, bounty_type="pct", bounty_pct=18, bounty_amount=None)
    bare = JobFactory(company=company, bounty_type="pct", bounty_pct=None)
    fixed = JobFactory(company=company, bounty_type="fixed", bounty_pct=None, bounty_amount=5)

    assert has_bounty(pct)
    assert not has_bounty(bare)
    assert has_bounty(fixed)


@pytest.fixture
def staff_client(db: None) -> Client:
    User.objects.create_superuser(
        email="admin@sparktal.vn", password="pass-1234-word", full_name="Sparktal Admin"
    )
    client = Client()
    client.login(email="admin@sparktal.vn", password="pass-1234-word")
    return client


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("overrides", "field", "message"),
    [
        ({"bounty_type": "pct", "bounty_pct": 40}, "bounty_pct", "between 5 and 30"),
        ({"bounty_type": "pct", "bounty_pct": ""}, "bounty_pct", "between 5 and 30"),
        ({"bounty_type": "fixed", "bounty_amount": ""}, "bounty_amount", "fixed bounty amount"),
        ({"bounty_type": ""}, "bounty_type", "Pick a bounty type"),
    ],
)
def test_admin_form_validates_bounty(
    staff_client: Client, job: Job, overrides: dict[str, Any], field: str, message: str
) -> None:
    url, data = job_change_form(staff_client, job, **overrides)

    response = staff_client.post(url, data)

    assert response.status_code == 200
    errors = response.context["adminform"].form.errors
    assert message in str(errors[field])
    job.refresh_from_db()
    assert (job.bounty_type, job.bounty_pct) == ("pct", 18)


@pytest.mark.django_db
def test_admin_form_saves_one_bounty_and_clears_the_other(staff_client: Client, job: Job) -> None:
    url, data = job_change_form(
        staff_client, job, bounty_type="fixed", bounty_pct="18", bounty_amount="90000000"
    )

    response = staff_client.post(url, data)

    assert response.status_code == 302
    job.refresh_from_db()
    assert job.bounty_type == "fixed"
    assert job.bounty_amount == 90_000_000
    assert job.bounty_pct is None
