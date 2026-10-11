from io import BytesIO
from typing import Any

import pytest
from django.core.files.base import ContentFile
from docx import Document

from apps.core.documents import DOC_MAGIC
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db


def inline(client: Any, application: Any) -> Any:
    return client.get(f"/api/v1/applications/{application.pk}/cv/inline")


def text(client: Any, application: Any) -> Any:
    return client.get(f"/api/v1/applications/{application.pk}/cv/text")


def replace_cv(application: Any, filename: str, data: bytes) -> None:
    application.cv_file.save(filename, ContentFile(data), save=False)
    application.cv_filename = filename
    application.save(update_fields=["cv_file", "cv_filename"])


def docx_bytes(*lines: str) -> bytes:
    document = Document()
    for line in lines:
        document.add_paragraph(line)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_pdf_streams_inline_for_the_viewer(ta_client: Any, application: Any) -> None:
    response = inline(ta_client, application)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("inline;")
    assert response["Content-Security-Policy"] == "sandbox"
    assert b"".join(response.streaming_content).startswith(b"%PDF")


def test_docx_has_no_inline_view_but_has_text(ta_client: Any, application: Any) -> None:
    replace_cv(application, "Quan CV.docx", docx_bytes("Phạm Minh Quân", "Kafka, Go, Postgres"))
    assert inline(ta_client, application).status_code == 404
    extracted = text(ta_client, application).json()["text"]
    assert "Kafka, Go, Postgres" in extracted
    assert "Phạm Minh Quân" in extracted


def test_pdf_text_is_extracted(ta_client: Any, application: Any) -> None:
    assert "Pham Minh Quan" in text(ta_client, application).json()["text"]


def test_doc_has_no_text(ta_client: Any, application: Any) -> None:
    replace_cv(application, "cv.doc", DOC_MAGIC + b"\0" * 64)
    assert text(ta_client, application).json() == {"text": ""}


def test_the_owning_recruiter_can_preview(
    client_for: Any, recruiter_user: Any, application: Any
) -> None:
    client = client_for(recruiter_user)
    assert inline(client, application).status_code == 200
    assert text(client, application).status_code == 200


def test_outsiders_get_404(client_for: Any, application: Any, other_company_admin: Any) -> None:
    rival = RecruiterProfileFactory(nickname="Bluefin Search").user
    for user in (rival, other_company_admin):
        client = client_for(user)
        assert inline(client, application).status_code == 404
        assert text(client, application).status_code == 404


def test_missing_file_is_404(ta_client: Any, application: Any) -> None:
    application.cv_file.storage.delete(application.cv_file.name)
    assert inline(ta_client, application).status_code == 404
    assert text(ta_client, application).status_code == 404
