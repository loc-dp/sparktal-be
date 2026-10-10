from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Model
from django.db.models.signals import post_delete
from rest_framework.test import APIClient

from apps.candidates.models import Application, Candidate, CvParse
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory, CvParseFactory
from apps.chat.models import Message
from apps.core.testing.pdf import make_pdf
from apps.exports.models import CvExport
from apps.jobs.models import JdParse
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db

CANDIDATES = "/api/v1/candidates"

CaptureCommits = Callable[..., AbstractContextManager[list[Callable[[], Any]]]]


def with_cv(candidate: Candidate) -> Candidate:
    candidate.cv_file.save("cv.pdf", ContentFile(make_pdf(candidate.full_name)))
    return candidate


def test_deleting_a_candidate_removes_its_cv_blob(
    recruiter_client: APIClient,
    recruiter_profile: RecruiterProfile,
    django_capture_on_commit_callbacks: CaptureCommits,
) -> None:
    candidate = with_cv(CandidateFactory(owner=recruiter_profile))
    storage, name = candidate.cv_file.storage, candidate.cv_file.name

    with django_capture_on_commit_callbacks(execute=True):
        response = recruiter_client.delete(f"{CANDIDATES}/{candidate.pk}")

    assert response.status_code == 204
    assert not storage.exists(name)


def test_deleting_a_job_removes_the_cv_blobs_of_its_applications(
    django_capture_on_commit_callbacks: CaptureCommits,
) -> None:
    application = ApplicationFactory()
    storage, name = application.cv_file.storage, application.cv_file.name

    with django_capture_on_commit_callbacks(execute=True):
        application.job.delete()

    assert not Application.objects.filter(pk=application.pk).exists()
    assert not storage.exists(name)


def test_deleting_a_recruiter_removes_their_cv_parse_blobs(
    django_capture_on_commit_callbacks: CaptureCommits,
) -> None:
    parse = CvParseFactory()
    storage, name = parse.file.storage, parse.file.name

    with django_capture_on_commit_callbacks(execute=True):
        parse.owner.delete()

    assert not CvParse.objects.filter(pk=parse.pk).exists()
    assert not storage.exists(name)


def test_a_rolled_back_delete_keeps_the_blob(
    recruiter_profile: RecruiterProfile,
    django_capture_on_commit_callbacks: CaptureCommits,
) -> None:
    candidate = with_cv(CandidateFactory(owner=recruiter_profile))
    pk, storage, name = candidate.pk, candidate.cv_file.storage, candidate.cv_file.name

    def delete_then_fail() -> None:
        with transaction.atomic():
            candidate.delete()
            raise RuntimeError

    with django_capture_on_commit_callbacks(execute=True), pytest.raises(RuntimeError):
        delete_then_fail()

    assert Candidate.objects.filter(pk=pk).exists()
    assert storage.exists(name)


def test_a_candidate_without_a_cv_deletes_cleanly(
    recruiter_profile: RecruiterProfile,
    django_capture_on_commit_callbacks: CaptureCommits,
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        candidate.delete()

    assert callbacks == []


@pytest.mark.parametrize("model", [JdParse, CvExport, Message])
def test_every_other_uploaded_document_is_cleaned_up_with_its_row(model: type[Model]) -> None:
    assert post_delete.has_listeners(model)
