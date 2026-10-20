from datetime import timedelta
from typing import Any, cast

from django.utils import timezone

from apps.companies.models import Company
from apps.jobs.models import Job, JobStatus
from apps.jobs.tests.factories import JobFactory

# What `OpsJobListItemSerializer` returns, on the dashboard queue and in `GET /ops/jobs`.
ITEM_KEYS = {
    "id",
    "title",
    "company",
    "team_name",
    "status",
    "posted_at",
    "published_at",
    "bounty_type",
    "bounty_pct",
    "bounty_amount",
    "on_marketplace",
    "invites",
    "recruiters_working",
}


def queued_job(company: Company, *, posted_ago_days: int, **extra: Any) -> Job:
    """A job waiting for Sparktal: posted by the company, no bounty or channel yet."""
    defaults: dict[str, Any] = {
        "company": company,
        "status": JobStatus.PENDING,
        "posted_at": timezone.now() - timedelta(days=posted_ago_days),
        "published_at": None,
        "bounty_type": None,
        "bounty_pct": None,
        "on_marketplace": False,
    }
    return cast(Job, JobFactory(**{**defaults, **extra}))
