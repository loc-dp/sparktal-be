# mypy: disable-error-code="no-untyped-def,type-arg"

from datetime import timedelta

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.accounts.models import User
from apps.chat.models import ChannelRead, Message, MessageKind
from apps.chat.tests.factories import ChannelFactory, MessageFactory
from apps.companies.models import Company
from apps.core.testing.pdf import make_pdf
from apps.jobs.access import grant_creator_perms
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db
CHANNELS = "/api/v1/chat/channels"


def pdf_upload(name: str = "CV_Mai_Gia_Bao.pdf") -> ContentFile:
    return ContentFile(make_pdf("CV"), name=name)


def test_channel_list_shows_nickname_preview_and_unread(
    client_for,
    company: Company,
    company_ta: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    channel = ChannelFactory(
        job=JobFactory(company=company, title="Senior Backend Engineer"),
        recruiter=recruiter_profile,
    )
    MessageFactory(
        channel=channel,
        author=recruiter_profile.user,
        body="Sharing a new profile",
    )
    body = client_for(company_ta).get(CHANNELS).json()
    assert body[0]["recruiter"]["nickname"] == recruiter_profile.nickname
    assert body[0]["unread_count"] == 1
    assert body[0]["last_message"]["body"] == "Sharing a new profile"
    assert recruiter_profile.user.email not in str(body)


def test_hiring_manager_sees_only_granted_job_channels(
    client_for, company: Company, company_hm: User
) -> None:
    mine = JobFactory(company=company, created_by=company_hm)
    grant_creator_perms(mine, company_hm)
    granted = ChannelFactory(job=mine)
    ChannelFactory(job=JobFactory(company=company))
    body = client_for(company_hm).get(CHANNELS).json()
    assert [row["id"] for row in body] == [str(granted.id)]


def test_recruiter_sees_only_their_channels(
    client_for,
    company: Company,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    mine = ChannelFactory(job=JobFactory(company=company), recruiter=recruiter_profile)
    ChannelFactory(job=JobFactory(company=company))
    body = client_for(recruiter_user).get(CHANNELS).json()
    assert [row["id"] for row in body] == [str(mine.id)]
    assert body[0]["company"]["name"] == company.name


def test_messages_page_backwards_oldest_first(
    client_for, company_ta: User, company: Company
) -> None:
    channel = ChannelFactory(job=JobFactory(company=company))
    made = [MessageFactory(channel=channel, body=f"m{i}") for i in range(8)]
    page = client_for(company_ta).get(f"{CHANNELS}/{channel.id}/messages?limit=3").json()
    assert [row["body"] for row in page["results"]] == ["m5", "m6", "m7"]
    older = (
        client_for(company_ta)
        .get(
            f"{CHANNELS}/{channel.id}/messages",
            {"limit": 3, "before": made[5].created_at.isoformat()},
        )
        .json()
    )
    assert [row["body"] for row in older["results"]] == ["m2", "m3", "m4"]


def test_message_limit_is_capped(client_for, company_ta: User, company: Company) -> None:
    channel = ChannelFactory(job=JobFactory(company=company))
    response = client_for(company_ta).get(f"{CHANNELS}/{channel.id}/messages?limit=500")
    assert response.status_code == 400


def test_foreign_channel_is_hidden(client_for, other_company_admin: User, company: Company) -> None:
    channel = ChannelFactory(job=JobFactory(company=company))
    assert (
        client_for(other_company_admin).get(f"{CHANNELS}/{channel.id}/messages").status_code == 404
    )


def test_posting_text_sanitises_html_and_sets_author(
    client_for, company_ta: User, company: Company
) -> None:
    channel = ChannelFactory(job=JobFactory(company=company))
    response = client_for(company_ta).post(
        f"{CHANNELS}/{channel.id}/messages",
        {"body": "<script>alert(1)</script>Call me <b>today</b>"},
        format="multipart",
    )
    assert response.status_code == 201
    assert response.json()["body"] == "Call me today"
    assert response.json()["author_name"] == company_ta.full_name


def test_message_needs_body_or_attachment(client_for, company_ta: User, company: Company) -> None:
    channel = ChannelFactory(job=JobFactory(company=company))
    response = client_for(company_ta).post(
        f"{CHANNELS}/{channel.id}/messages", {"body": "   "}, format="multipart"
    )
    assert response.status_code == 400


def test_attachment_and_quote_are_serialized(
    client_for, company_ta: User, company: Company
) -> None:
    channel = ChannelFactory(job=JobFactory(company=company))
    response = client_for(company_ta).post(
        f"{CHANNELS}/{channel.id}/messages",
        {
            "body": "See CV",
            "attachment": pdf_upload(),
            "quote_source": "Candidate note",
            "quote_text": "Strong Kafka experience",
        },
        format="multipart",
    )
    assert response.status_code == 201
    assert response.json()["attachment_name"] == "CV_Mai_Gia_Bao.pdf"
    assert response.json()["quote_source"] == "Candidate note"


def test_recruiter_author_is_nickname_only(
    client_for,
    company: Company,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    channel = ChannelFactory(job=JobFactory(company=company), recruiter=recruiter_profile)
    response = client_for(recruiter_user).post(
        f"{CHANNELS}/{channel.id}/messages", {"body": "On it."}, format="multipart"
    )
    assert response.json()["author_name"] == recruiter_profile.nickname
    assert recruiter_user.email not in response.content.decode()


def test_marking_read_clears_unread_and_never_moves_backwards(
    client_for, company: Company, company_ta: User, recruiter_profile: RecruiterProfile
) -> None:
    channel = ChannelFactory(job=JobFactory(company=company), recruiter=recruiter_profile)
    MessageFactory(channel=channel, author=recruiter_profile.user)
    client = client_for(company_ta)
    response = client.post(f"{CHANNELS}/{channel.id}/read", {}, format="json")
    assert response.json()["unread_count"] == 0
    marker = ChannelRead.objects.get(channel=channel, user=company_ta)
    stale = (marker.last_read_at - timedelta(hours=1)).isoformat()
    client.post(
        f"{CHANNELS}/{channel.id}/read",
        {"last_read_at": stale},
        format="json",
    )
    marker.refresh_from_db()
    assert marker.last_read_at > timezone.now() - timedelta(minutes=1)


def test_attachment_download_is_authorized_and_streamed(
    client_for, company_ta: User, other_company_admin: User, company: Company
) -> None:
    message = MessageFactory(
        channel=ChannelFactory(job=JobFactory(company=company)),
        body="",
        attachment=pdf_upload(),
        attachment_name="CV_Mai_Gia_Bao.pdf",
        attachment_size=120,
    )
    url = f"/api/v1/chat/messages/{message.id}/attachment"
    response = client_for(company_ta).get(url)
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    assert b"".join(response.streaming_content).startswith(b"%PDF-")
    assert client_for(other_company_admin).get(url).status_code == 404


def test_system_message_has_no_author(client_for, company_ta: User, company: Company) -> None:
    channel = ChannelFactory(job=JobFactory(company=company))
    MessageFactory(channel=channel, author=None, kind=MessageKind.SYSTEM, body="submitted")
    row = client_for(company_ta).get(f"{CHANNELS}/{channel.id}/messages").json()["results"][0]
    assert row["author_id"] is None
    assert row["author_name"] == "System"


def test_foreign_post_creates_nothing(
    client_for, other_company_admin: User, company: Company
) -> None:
    channel = ChannelFactory(job=JobFactory(company=company))
    response = client_for(other_company_admin).post(
        f"{CHANNELS}/{channel.id}/messages", {"body": "hi"}, format="multipart"
    )
    assert response.status_code == 404
    assert Message.objects.count() == 0
