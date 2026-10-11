"""The company Talent pool: everyone recruiters have submitted to the jobs a member can see.

There is no table behind it. A person is keyed by the candidate first submitted to the
company, so the same person sent by two recruiters (a duplicate) is one entry.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from django.db.models import F, Max, Q, QuerySet
from django.db.models.functions import Coalesce
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.serializers import CompanyPoolEntrySerializer
from apps.companies.access import membership_of
from apps.core.pagination import DefaultPagination
from apps.jobs.access import jobs_visible_to


@dataclass
class PoolEntry:
    id: uuid.UUID
    full_name: str
    current_title: str
    location: str
    years_experience: int | None
    skills: list[str]
    last_submitted_at: datetime
    applications: list[Application] = field(default_factory=list)


def person() -> Coalesce:
    """Who an application is about: a duplicate counts as the person first submitted."""
    return Coalesce(F("duplicate_of__candidate_id"), F("candidate_id"))


def visible_applications(user: User) -> QuerySet[Application]:
    return Application.objects.filter(job__in=jobs_visible_to(user))


def matching(applications: QuerySet[Application], params: Any) -> QuerySet[Application]:
    stage = params.get("stage")
    if stage:
        if stage not in Stage.values:
            raise ValidationError({"stage": [f"Unknown stage '{stage}'."]})
        applications = applications.filter(stage=stage)

    job = params.get("job")
    if job:
        try:
            applications = applications.filter(job_id=uuid.UUID(job))
        except ValueError as exc:
            raise ValidationError({"job": ["Not a valid job id."]}) from exc

    search = (params.get("search") or "").strip()
    if search:
        applications = applications.filter(
            Q(candidate__full_name__icontains=search)
            | Q(candidate__current_title__icontains=search)
        )
    return applications


def entries_for(people: list[uuid.UUID], applications: QuerySet[Application]) -> list[PoolEntry]:
    """Every visible application of these people, newest first, under their latest profile."""
    rows = (
        applications.filter(
            Q(duplicate_of__isnull=False, duplicate_of__candidate_id__in=people)
            | Q(duplicate_of__isnull=True, candidate_id__in=people)
        )
        .select_related("job", "recruiter", "candidate", "duplicate_of")
        .order_by("-submitted_at", "-id")
    )
    by_person: dict[uuid.UUID, PoolEntry] = {}
    for application in rows:
        root = application.duplicate_of
        key = root.candidate_id if root is not None else application.candidate_id
        entry = by_person.get(key)
        if entry is None:
            candidate = application.candidate
            entry = by_person[key] = PoolEntry(
                id=key,
                full_name=candidate.full_name,
                current_title=candidate.current_title,
                location=candidate.location,
                years_experience=candidate.years_experience,
                skills=list(candidate.skills),
                last_submitted_at=application.submitted_at,
            )
        entry.applications.append(application)
    return [by_person[key] for key in people if key in by_person]


@extend_schema_view(
    get=extend_schema(
        tags=["companies"],
        operation_id="company_talent_pool_list",
        parameters=[
            OpenApiParameter(
                "search", OpenApiTypes.STR, description="Candidate name or current title."
            ),
            OpenApiParameter("stage", OpenApiTypes.STR, enum=list(Stage.values)),
            OpenApiParameter("job", OpenApiTypes.UUID),
        ],
    )
)
class CompanyTalentPoolView(ListAPIView[Any]):
    serializer_class = CompanyPoolEntrySerializer
    pagination_class = DefaultPagination

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        user = cast(User, request.user)
        if membership_of(user) is None:
            raise NotFound("You are not a member of a company.")
        applications = visible_applications(user)
        people = (
            matching(applications, request.query_params)
            .values(person=person())
            .annotate(last_submitted_at=Max("submitted_at"))
            .order_by("-last_submitted_at", "person")
        )
        page = self.paginate_queryset(people)
        keys = [row["person"] for row in page or []]
        serializer = self.get_serializer(entries_for(keys, applications), many=True)
        return self.get_paginated_response(serializer.data)
