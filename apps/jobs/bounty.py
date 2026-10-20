"""The bounty is Sparktal's to set (spec §8); no company screen or API shows or takes it.

A job has none until Sparktal enters one, so every reader asks `has_bounty` / `HAS_BOUNTY`
and every writer goes through `clean_bounty`.
"""

from decimal import Decimal
from typing import Any

from django.db.models import Q
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from apps.jobs.models import BountyType, Job

BOUNTY_PCT_MIN = 5
BOUNTY_PCT_MAX = 30
# The sentences of the bounty rule. The FE forms (zod) say the same ones.
BOUNTY_TYPE_MESSAGE = "Pick a bounty type."
BOUNTY_PCT_MESSAGE = f"Bounty must be between {BOUNTY_PCT_MIN} and {BOUNTY_PCT_MAX}%"
BOUNTY_AMOUNT_MESSAGE = "Enter the fixed bounty amount"

# A bounty is set when the value its type asks for is there.
HAS_BOUNTY = Q(bounty_type=BountyType.PCT, bounty_pct__isnull=False) | Q(
    bounty_type=BountyType.FIXED, bounty_amount__isnull=False
)


def has_bounty(job: Job) -> bool:
    """`HAS_BOUNTY`, for a job already in hand."""
    if job.bounty_type == BountyType.PCT:
        return job.bounty_pct is not None
    if job.bounty_type == BountyType.FIXED:
        return job.bounty_amount is not None
    return False


def clean_bounty(
    bounty_type: str | None,
    bounty_pct: int | float | Decimal | None,
    bounty_amount: Decimal | None,
) -> dict[str, Any]:
    """The three bounty fields as they are stored: the one the type does not use is None."""
    if bounty_type == BountyType.FIXED:
        if not bounty_amount or bounty_amount <= 0:
            raise ValidationError({"bounty_amount": [BOUNTY_AMOUNT_MESSAGE]})
        return {"bounty_type": bounty_type, "bounty_pct": None, "bounty_amount": bounty_amount}
    if bounty_type == BountyType.PCT:
        # A whole number only: 18.5 gets the same sentence as 40, and 18.0 is stored as 18.
        if (
            bounty_pct is None
            or bounty_pct != int(bounty_pct)
            or not BOUNTY_PCT_MIN <= bounty_pct <= BOUNTY_PCT_MAX
        ):
            raise ValidationError({"bounty_pct": [BOUNTY_PCT_MESSAGE]})
        return {"bounty_type": bounty_type, "bounty_pct": int(bounty_pct), "bounty_amount": None}
    raise ValidationError({"bounty_type": [BOUNTY_TYPE_MESSAGE]})


# The bounty as a headhunter reads it. Declared by hand, not taken from the model, so the OpenAPI
# contract states the rules: a pct of 5 to 30 (null for a fixed bounty) and an amount (null for
# a pct). These are comments, not docstrings, because drf-spectacular would print a docstring as
# the description of every component built on them.
class BountyReadFields(serializers.Serializer[Any]):
    # A job on the marketplace, in an invite, or being worked on is live, so it has a bounty
    # (`HAS_BOUNTY`) and the type is never empty.
    bounty_type = serializers.ChoiceField(choices=BountyType.choices, read_only=True)
    bounty_pct = serializers.IntegerField(
        read_only=True, allow_null=True, min_value=BOUNTY_PCT_MIN, max_value=BOUNTY_PCT_MAX
    )
    bounty_amount = serializers.DecimalField(
        max_digits=15, decimal_places=0, read_only=True, allow_null=True
    )


class JoinedBountyReadFields(BountyReadFields):
    # The same on a job the recruiter joined. It stays on their side after it closes or is
    # deleted (`jobs_joined_by`), and an assignment can be added in the Django Admin on a job
    # with no bounty yet, so here, and nowhere else, the type can be null.
    bounty_type = serializers.ChoiceField(
        choices=BountyType.choices, read_only=True, allow_null=True
    )
