from datetime import UTC

import factory
from django.core.files.base import ContentFile
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.companies.models import Team
from apps.companies.tests.factories import CompanyFactory, OfficeFactory
from apps.core.testing.pdf import make_pdf
from apps.jobs.models import (
    PRIVATE_STATUSES,
    BountyType,
    JdParse,
    Job,
    JobAssignment,
    JobInvite,
    JobQuestion,
    JobStatus,
    PayBasis,
    WorkModel,
)
from apps.recruiters.tests.factories import RecruiterProfileFactory


def team_of(user: User | None) -> Team | None:
    membership = getattr(user, "membership", None)
    return membership.team if membership is not None else None


class JobFactory(DjangoModelFactory[Job]):
    class Meta:
        model = Job

    company = factory.SubFactory(CompanyFactory)
    created_by = None
    # Like the API: a job lands in its creator's team; with no creator it has no team.
    team = factory.LazyAttribute(lambda job: team_of(job.created_by))
    title = factory.Sequence(lambda n: f"Senior Engineer {n}")
    function = "engineering"
    why_we_need_you = "The payments team needs a fourth backend engineer to keep up with volume."
    roles = "Own the payout service\nReview designs"
    qualifications = "5+ years with Go or Python\nExperience with Postgres"
    salary_min = 45_000_000
    salary_max = 60_000_000
    pay_basis = PayBasis.GROSS
    work_model = WorkModel.HYBRID
    bounty_type = BountyType.PCT
    bounty_pct = 18
    skills = factory.List(["Go", "PostgreSQL"])
    status = JobStatus.ACTIVE
    on_marketplace = True
    published_at = factory.Faker("date_time_this_year", tzinfo=UTC)
    # Posted when it went live, unless it is still with the company.
    posted_at = factory.LazyAttribute(
        lambda job: None if job.status in PRIVATE_STATUSES else job.published_at or timezone.now()
    )
    office = factory.LazyAttribute(
        lambda job: job.company.offices.first() or OfficeFactory(company=job.company)
    )


def join_job_team(member: User, job: Job) -> None:
    """Move `member` into the team that owns `job`, so they see it."""
    membership = member.membership
    membership.team = job.team
    membership.save(update_fields=["team"])


class JobAssignmentFactory(DjangoModelFactory[JobAssignment]):
    class Meta:
        model = JobAssignment

    job = factory.SubFactory(JobFactory)
    recruiter = factory.SubFactory(RecruiterProfileFactory)


class JobInviteFactory(DjangoModelFactory[JobInvite]):
    class Meta:
        model = JobInvite

    job = factory.SubFactory(JobFactory)
    recruiter = factory.SubFactory(RecruiterProfileFactory)


class JdParseFactory(DjangoModelFactory[JdParse]):
    class Meta:
        model = JdParse

    company = factory.SubFactory(CompanyFactory)
    uploaded_by = factory.SubFactory(UserFactory)
    filename = "JD_Senior_Accountant.pdf"
    file = factory.LazyFunction(
        lambda: ContentFile(make_pdf("Senior Accountant\nResponsibilities"), name="jd.pdf")
    )


class JobQuestionFactory(DjangoModelFactory[JobQuestion]):
    class Meta:
        model = JobQuestion

    job = factory.SubFactory(JobFactory)
    asked_by = factory.SubFactory(RecruiterProfileFactory)
    body = "Is the salary range negotiable for a candidate with 8 years of experience?"
