from typing import Any

import pytest

from apps.hiring.models import CandidateNote
from apps.hiring.tests.factories import CandidateNoteFactory

pytestmark = pytest.mark.django_db


def notes_url(application: Any) -> str:
    return f"/api/v1/applications/{application.pk}/notes"


def test_no_notes_yet(ta_client: Any, application: Any) -> None:
    assert ta_client.get(notes_url(application)).json() == []


def test_write_a_note_and_read_it_back_newest_first(
    ta_client: Any, admin_client: Any, application: Any, company_ta: Any
) -> None:
    first = ta_client.post(notes_url(application), {"body": "Notice period 30 days"}, format="json")
    assert first.status_code == 201
    assert first.json()["author_name"] == company_ta.full_name
    assert first.json()["is_mine"] is True
    admin_client.post(notes_url(application), {"body": "Check the L4 band"}, format="json")
    rows = ta_client.get(notes_url(application)).json()
    assert [(row["body"], row["is_mine"]) for row in rows] == [
        ("Check the L4 band", False),
        ("Notice period 30 days", True),
    ]


def test_blank_note_is_400(ta_client: Any, application: Any) -> None:
    response = ta_client.post(notes_url(application), {"body": "  "}, format="json")
    assert response.status_code == 400
    assert response.json()["body"] == ["Write something before you save the note."]


def test_delete_only_your_own_note(
    ta_client: Any, admin_client: Any, application: Any, company_ta: Any, company_admin: Any
) -> None:
    mine = CandidateNoteFactory(application=application, author=company_ta)
    theirs = CandidateNoteFactory(application=application, author=company_admin)
    assert ta_client.delete(f"/api/v1/notes/{mine.pk}").status_code == 204
    refused = ta_client.delete(f"/api/v1/notes/{theirs.pk}")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "You can only delete your own notes."
    assert list(CandidateNote.objects.values_list("pk", flat=True)) == [theirs.pk]


@pytest.mark.parametrize("who", ["company_hm", "other_company_admin"])
def test_notes_are_404_outside_the_job(
    who: str, request: pytest.FixtureRequest, client_for: Any, application: Any, company_ta: Any
) -> None:
    note = CandidateNoteFactory(application=application, author=company_ta)
    client = client_for(request.getfixturevalue(who))
    assert client.get(notes_url(application)).status_code == 404
    assert client.post(notes_url(application), {"body": "x"}, format="json").status_code == 404
    assert client.delete(f"/api/v1/notes/{note.pk}").status_code == 404
