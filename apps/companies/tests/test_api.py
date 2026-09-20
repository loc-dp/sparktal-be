from collections.abc import Callable
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company

pytestmark = pytest.mark.django_db

URL = "/api/v1/company"

ClientFor = Callable[[User], APIClient]


def png_bytes(size: tuple[int, int] = (64, 64)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, "navy").save(buffer, format="PNG")
    return buffer.getvalue()


def test_get_returns_the_users_company_with_offices(
    client_for: ClientFor, company_ta: User, company: Company
) -> None:
    response = client_for(company_ta).get(URL)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Vela Pay"
    assert body["can_edit"] is True
    assert len(body["offices"]) == 1


def test_hiring_manager_can_read_but_not_write(client_for: ClientFor, company_hm: User) -> None:
    client = client_for(company_hm)

    assert client.get(URL).json()["can_edit"] is False
    assert client.patch(URL, {"name": "Renamed"}, format="json").status_code == 403


def test_recruiter_has_no_company(client_for: ClientFor, recruiter_user: User) -> None:
    assert client_for(recruiter_user).get(URL).status_code == 404


def test_patch_updates_fields_and_replaces_offices(
    client_for: ClientFor, company_admin: User, company: Company
) -> None:
    response = client_for(company_admin).patch(
        URL,
        {
            "year_incorporated": 2018,
            "bd_headcount": 20,
            "offices": [
                {"address": "1 Lê Duẩn, District 1, Ho Chi Minh City", "city": "hcm"},
                {"address": "72 Lê Thánh Tôn, Ha Noi", "city": "hn"},
            ],
        },
        format="json",
    )

    assert response.status_code == 200
    company.refresh_from_db()
    assert company.year_incorporated == 2018
    assert [office.position for office in company.offices.all()] == [0, 1]
    assert company.offices.all()[1].city == "hn"


@pytest.mark.parametrize(
    ("payload", "field", "message"),
    [
        ({"year_incorporated": 2031}, "year_incorporated", "Enter a year between 1900 and 2026."),
        ({"year_incorporated": 1800}, "year_incorporated", "Enter a year between 1900 and 2026."),
        ({"website": "velapay.vn"}, "website", "Enter a full link, e.g. https://velapay.vn"),
        (
            {"linkedin": "https://example.com/velapay"},
            "linkedin",
            "Enter a LinkedIn page link, e.g. https://www.linkedin.com/company/velapay",
        ),
        (
            {"facebook": "https://twitter.com/velapay"},
            "facebook",
            "Enter a Facebook page link, e.g. https://www.facebook.com/velapay.vn",
        ),
        (
            {"offices": [{"address": "   ", "city": "hcm"}]},
            "offices",
            "Enter this address or remove the row.",
        ),
    ],
)
def test_patch_validation_messages_match_the_prototype(
    client_for: ClientFor, company_admin: User, payload: dict[str, object], field: str, message: str
) -> None:
    response = client_for(company_admin).patch(URL, payload, format="json")

    assert response.status_code == 400
    assert message in str(response.json()[field])


def test_complete_requires_the_mandatory_fields(
    client_for: ClientFor, company: Company, company_admin: User
) -> None:
    company.industry = ""
    company.about = ""
    company.profile_completed_at = None
    company.save()

    response = client_for(company_admin).post(f"{URL}/complete")

    assert response.status_code == 400
    body = response.json()
    assert body["industry"] == ["Select an industry."]
    assert body["about"] == ["Tell recruiters about your company."]


def test_complete_marks_the_profile_completed(
    client_for: ClientFor, company: Company, company_admin: User
) -> None:
    company.profile_completed_at = None
    company.about = "x" * 150
    company.save()

    response = client_for(company_admin).post(f"{URL}/complete")

    assert response.status_code == 200
    company.refresh_from_db()
    assert company.profile_completed is True
    assert response.json()["profile_completed"] is True


def test_about_shorter_than_100_chars_is_rejected_on_complete(
    client_for: ClientFor, company: Company, company_admin: User
) -> None:
    company.profile_completed_at = None
    company.about = "Too short."
    company.save()

    response = client_for(company_admin).post(f"{URL}/complete")

    assert response.status_code == 400
    assert "at least 100 characters" in str(response.json()["about"])


def test_logo_upload_and_delete(
    client_for: ClientFor, company_admin: User, company: Company
) -> None:
    client = client_for(company_admin)
    upload = SimpleUploadedFile("logo.png", png_bytes(), content_type="image/png")

    response = client.put(f"{URL}/logo", {"file": upload}, format="multipart")

    assert response.status_code == 200
    assert response.json()["logo_url"].endswith(".png")
    company.refresh_from_db()
    assert company.logo

    assert client.delete(f"{URL}/logo").status_code == 200
    company.refresh_from_db()
    assert not company.logo


def test_logo_rejects_oversized_files(client_for: ClientFor, company_admin: User) -> None:
    big = SimpleUploadedFile("logo.png", b"x" * (2 * 1024 * 1024 + 1), content_type="image/png")

    response = client_for(company_admin).put(f"{URL}/logo", {"file": big}, format="multipart")

    assert response.status_code == 400
    assert "The limit is 2 MB." in str(response.json())


def test_logo_rejects_unsupported_types(client_for: ClientFor, company_admin: User) -> None:
    svg = SimpleUploadedFile("logo.svg", b"<svg/>", content_type="image/svg+xml")

    response = client_for(company_admin).put(f"{URL}/logo", {"file": svg}, format="multipart")

    assert response.status_code == 400
    assert "Use PNG, JPG or WebP." in str(response.json())


def test_me_includes_the_company(client_for: ClientFor, company_ta: User) -> None:
    body = client_for(company_ta).get("/api/v1/me").json()

    assert body["company"]["name"] == "Vela Pay"
    assert body["company"]["role"] == "talent_acquisition"
    assert body["company"]["profile_completed"] is True
