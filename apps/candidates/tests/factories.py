import factory
from django.core.files.base import ContentFile
from factory.django import DjangoModelFactory

from apps.candidates.models import Application, Candidate, Stage
from apps.core.testing.pdf import make_pdf
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.tests.factories import RecruiterProfileFactory


class CandidateFactory(DjangoModelFactory[Candidate]):
    class Meta:
        model = Candidate

    owner = factory.SubFactory(RecruiterProfileFactory)
    full_name = factory.Sequence(lambda n: f"Ứng viên {n}")
    current_title = "Backend Engineer · Tiki"
    email = factory.Sequence(lambda n: f"candidate{n}@example.com")


class ApplicationFactory(DjangoModelFactory[Application]):
    class Meta:
        model = Application

    job = factory.SubFactory(JobFactory)
    candidate = factory.SubFactory(CandidateFactory)
    recruiter = factory.LazyAttribute(lambda application: application.candidate.owner)
    stage = Stage.NEW
    cv_filename = factory.LazyAttribute(lambda a: f"{a.candidate.full_name} - CV.pdf")
    cv_file = factory.LazyAttribute(
        lambda a: ContentFile(
            make_pdf(f"{a.candidate.full_name}\n{a.candidate.current_title}"), name="cv.pdf"
        )
    )
