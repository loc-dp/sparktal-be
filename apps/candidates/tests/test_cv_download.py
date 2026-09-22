from typing import Any, cast

import pytest

from apps.accounts.models import User
from apps.candidates.models import Application
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.jobs.models import Job

pytestmark = pytest.mark.django_db


@pytest.fixture
def application(job: Job, recruiter_profile: Any) -> Application:
    return cast(
        Application,
        ApplicationFactory(
            job=job,
            recruiter=recruiter_profile,
            candidate=CandidateFactory(owner=recruiter_profile, full_name="Nguyễn Thị Hằng"),
            cv_filename="Nguyễn Thị Hằng.pdf",
        ),
    )


def test_ta_downloads_the_cv_with_a_utf8_filename(
    client_for: Any, company_ta: User, application: Application
) -> None:
    response = client_for(company_ta).get(f"/api/v1/applications/{application.id}/cv")

    assert response.status_code == 200
    assert b"%PDF" in b"".join(response.streaming_content)[:8]
    assert "filename*=UTF-8''" in response["Content-Disposition"]
    assert response["X-Content-Type-Options"] == "nosniff"


def test_another_company_gets_404(
    client_for: Any, other_company_admin: User, application: Application
) -> None:
    response = client_for(other_company_admin).get(f"/api/v1/applications/{application.id}/cv")

    assert response.status_code == 404


def test_a_missing_blob_is_404_not_500(
    client_for: Any, company_ta: User, application: Application
) -> None:
    application.cv_file.storage.delete(application.cv_file.name)

    assert (
        client_for(company_ta).get(f"/api/v1/applications/{application.id}/cv").status_code == 404
    )
