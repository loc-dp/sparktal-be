import factory
from factory.django import DjangoModelFactory

from apps.companies.tests.factories import CompanyFactory, OfficeFactory
from apps.jobs.models import BountyType, Job, JobStatus, PayBasis, WorkModel


class JobFactory(DjangoModelFactory[Job]):
    class Meta:
        model = Job

    company = factory.SubFactory(CompanyFactory)
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
    published_at = factory.Faker("date_time_this_year", tzinfo=None)
    # Non-remote jobs must have an office, so give every job one from its own
    # company unless the caller passes something else.
    office = factory.LazyAttribute(
        lambda job: job.company.offices.first() or OfficeFactory(company=job.company)
    )
