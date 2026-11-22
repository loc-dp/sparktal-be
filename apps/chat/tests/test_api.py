# mypy: disable-error-code="no-untyped-def,type-arg"

from datetime import timedelta
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import User
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.chat.access import GENERAL_READ_ONLY, can_post_to_channel
from apps.chat.models import Channel, ChannelRead, Message, MessageKind
from apps.chat.tests.factories import ChannelFactory, MessageFactory
from apps.companies.models import Company
from apps.core.testing.pdf import make_pdf
from apps.jobs.models import JobAssignment
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory
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
        job=JobFactory(company=company, created_by=company_ta, title="Senior Backend Engineer"),
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


def test_hiring_manager_sees_only_their_teams_job_channels(
    client_for, company: Company, company_hm: User
) -> None:
    ours = ChannelFactory(job=JobFactory(company=company, team=company_hm.membership.team))
    ChannelFactory(job=JobFactory(company=company))
    body = client_for(company_hm).get(CHANNELS).json()
    assert [row["id"] for row in body] == [str(ours.id)]


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
    channel = ChannelFactory(job=JobFactory(company=company, created_by=company_ta))
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
    channel = ChannelFactory(job=JobFactory(company=company, created_by=company_ta))
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
    channel = ChannelFactory(job=JobFactory(company=company, created_by=company_ta))
    response = client_for(company_ta).post(
        f"{CHANNELS}/{channel.id}/messages",
        {"body": "<script>alert(1)</script>Call me <b>today</b>"},
        format="multipart",
    )
    assert response.status_code == 201
    assert response.json()["body"] == "Call me today"
    assert response.json()["author_name"] == company_ta.full_name


def test_message_needs_body_or_attachment(client_for, company_ta: User, company: Company) -> None:
    channel = ChannelFactory(job=JobFactory(company=company, created_by=company_ta))
    response = client_for(company_ta).post(
        f"{CHANNELS}/{channel.id}/messages", {"body": "   "}, format="multipart"
    )
    assert response.status_code == 400


def test_attachment_and_quote_are_serialized(
    client_for, company_ta: User, company: Company
) -> None:
    channel = ChannelFactory(job=JobFactory(company=company, created_by=company_ta))
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
    channel = ChannelFactory(
        job=JobFactory(company=company, created_by=company_ta), recruiter=recruiter_profile
    )
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
        channel=ChannelFactory(job=JobFactory(company=company, created_by=company_ta)),
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
    channel = ChannelFactory(job=JobFactory(company=company, created_by=company_ta))
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


# --- A headhunter removed from a job (assign-01 final review).


def _removed_channel(
    company: Company, profile: RecruiterProfile, *, with_cv: bool
) -> tuple[Any, Any]:
    job = JobFactory(company=company)
    JobAssignmentFactory(job=job, recruiter=profile)
    channel = Channel.objects.get(job=job, recruiter=profile)
    message = MessageFactory(channel=channel, author=None, kind=MessageKind.SYSTEM, body="hello")
    if with_cv:
        ApplicationFactory(job=job, candidate=CandidateFactory(owner=profile))
    JobAssignment.objects.filter(job=job, recruiter=profile).update(left_at=timezone.now())
    return channel, message


def test_removed_without_cv_gets_an_empty_list_and_404s(
    client_for, company: Company, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    channel, message = _removed_channel(company, recruiter_profile, with_cv=False)
    client = client_for(recruiter_user)
    assert client.get(CHANNELS).json() == []
    assert client.get(f"{CHANNELS}/{channel.id}/messages").status_code == 404
    posted = client.post(f"{CHANNELS}/{channel.id}/messages", {"body": "hi"}, format="multipart")
    assert posted.status_code == 404
    assert client.post(f"{CHANNELS}/{channel.id}/read").status_code == 404
    assert Message.objects.filter(channel=channel, author=recruiter_user).count() == 0
    assert message.pk


def test_removed_with_cv_still_reads_the_channel(
    client_for, company: Company, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    channel, _ = _removed_channel(company, recruiter_profile, with_cv=True)
    thread = Channel.objects.get(
        job=channel.job, recruiter=recruiter_profile, application__isnull=False
    )
    client = client_for(recruiter_user)
    rows = {row["id"]: row for row in client.get(CHANNELS).json()}
    assert set(rows) == {str(channel.id), str(thread.id)}
    assert rows[str(channel.id)]["can_post"] is False
    assert rows[str(thread.id)]["can_post"] is True
    page = client.get(f"{CHANNELS}/{channel.id}/messages")
    assert page.status_code == 200
    assert "hello" in str(page.json())


# --- One thread per submitted candidate, one general thread per job and headhunter.


def _candidate_thread(job: Any, recruiter: RecruiterProfile, name: str = "Lê Thu Trang") -> Any:
    """The thread the submission signal opens: `(application, channel)`."""
    application = ApplicationFactory(
        job=job, candidate=CandidateFactory(owner=recruiter, full_name=name)
    )
    return application, Channel.objects.get(application=application)


def test_channel_rows_carry_kind_and_candidate(
    client_for, company: Company, company_ta: User, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company, created_by=company_ta)
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    application, thread = _candidate_thread(job, recruiter_profile)
    general = Channel.objects.get(job=job, recruiter=recruiter_profile, application=None)
    rows = {row["id"]: row for row in client_for(company_ta).get(CHANNELS).json()}
    assert rows[str(thread.id)]["kind"] == "candidate"
    assert rows[str(thread.id)]["application"] == {
        "id": str(application.id),
        "candidate_name": "Lê Thu Trang",
    }
    assert rows[str(general.id)]["kind"] == "general"
    assert rows[str(general.id)]["application"] is None
    assert all(row["can_post"] is True for row in rows.values())


def test_other_recruiters_channel_is_404_everywhere(
    client_for, company: Company, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company)
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    rival = JobAssignmentFactory(job=job).recruiter
    _, theirs = _candidate_thread(job, rival)
    message = MessageFactory(
        channel=theirs,
        body="",
        attachment=pdf_upload(),
        attachment_name="CV.pdf",
        attachment_size=120,
    )
    client = client_for(recruiter_user)
    assert client.get(CHANNELS).json() != []
    assert str(theirs.id) not in str(client.get(CHANNELS).json())
    assert client.get(f"{CHANNELS}/{theirs.id}/messages").status_code == 404
    posted = client.post(f"{CHANNELS}/{theirs.id}/messages", {"body": "hi"}, format="multipart")
    assert posted.status_code == 404
    assert client.post(f"{CHANNELS}/{theirs.id}/read").status_code == 404
    assert client.get(f"/api/v1/chat/messages/{message.id}/attachment").status_code == 404
    assert not Message.objects.filter(channel=theirs, author=recruiter_user).exists()


def test_removed_recruiter_cannot_post_in_general(
    client_for, company: Company, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    general, _ = _removed_channel(company, recruiter_profile, with_cv=True)
    thread = Channel.objects.get(
        job=general.job, recruiter=recruiter_profile, application__isnull=False
    )
    client = client_for(recruiter_user)
    refused = client.post(f"{CHANNELS}/{general.id}/messages", {"body": "hi"}, format="multipart")
    assert refused.status_code == 403
    assert refused.json()["detail"] == GENERAL_READ_ONLY
    assert not Message.objects.filter(channel=general, author=recruiter_user).exists()
    posted = client.post(f"{CHANNELS}/{thread.id}/messages", {"body": "hi"}, format="multipart")
    assert posted.status_code == 201


def test_message_in_candidate_thread_is_tagged(
    client_for, company: Company, company_ta: User, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company, created_by=company_ta)
    application, thread = _candidate_thread(job, recruiter_profile)
    response = client_for(company_ta).post(
        f"{CHANNELS}/{thread.id}/messages", {"body": "Can he start in May?"}, format="multipart"
    )
    assert response.status_code == 201
    assert response.json()["application"]["id"] == str(application.id)
    assert Message.objects.get(pk=response.json()["id"]).application == application
    general = ChannelFactory(job=job)
    untagged = client_for(company_ta).post(
        f"{CHANNELS}/{general.id}/messages", {"body": "Hello"}, format="multipart"
    )
    assert untagged.json()["application"] is None


def test_unread_counts_are_per_thread(
    client_for, company: Company, company_ta: User, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company, created_by=company_ta)
    _, first = _candidate_thread(job, recruiter_profile, "Lê Thu Trang")
    _, second = _candidate_thread(job, recruiter_profile, "Phạm Minh Quân")
    for thread in (first, second):
        MessageFactory(channel=thread, author=recruiter_profile.user)
    client = client_for(company_ta)
    unread = {row["id"]: row["unread_count"] for row in client.get(CHANNELS).json()}
    assert unread[str(first.id)] == unread[str(second.id)] == 2  # the submission line and the text
    client.post(f"{CHANNELS}/{first.id}/read", {}, format="json")
    unread = {row["id"]: row["unread_count"] for row in client.get(CHANNELS).json()}
    assert unread[str(first.id)] == 0
    assert unread[str(second.id)] == 2


def test_discussion_is_the_candidate_thread(
    client_for,
    company: Company,
    company_ta: User,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    job = JobFactory(company=company, created_by=company_ta)
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    application, thread = _candidate_thread(job, recruiter_profile)
    discussion = f"/api/v1/applications/{application.id}/messages"
    company_side, recruiter_side = client_for(company_ta), client_for(recruiter_user)

    assert (
        company_side.post(discussion, {"body": "From discussion"}, format="json").status_code == 201
    )
    chat = company_side.get(f"{CHANNELS}/{thread.id}/messages").json()["results"]
    assert "From discussion" in [row["body"] for row in chat]

    posted = recruiter_side.post(
        f"{CHANNELS}/{thread.id}/messages", {"body": "From chat"}, format="multipart"
    )
    assert posted.status_code == 201
    page = company_side.get(discussion).json()
    assert page["channel_id"] == str(thread.id)
    assert page["has_more"] is False
    assert [row["body"] for row in page["results"]][-2:] == ["From discussion", "From chat"]


def test_discussion_makes_the_thread_on_first_use(
    client_for, company: Company, company_ta: User, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company, created_by=company_ta)
    application, thread = _candidate_thread(job, recruiter_profile)
    thread.delete()
    page = client_for(company_ta).get(f"/api/v1/applications/{application.id}/messages").json()
    assert page["channel_id"] == str(Channel.objects.get(application=application).id)
    assert page["results"] == []


def test_a_rejoining_headhunter_with_a_candidate_thread_gets_the_general_channel(
    client_for,
    settings: Any,
    company: Company,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    settings.MARKETPLACE_ENABLED = True
    job = JobFactory(company=company, status="active", on_marketplace=True)
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    _candidate_thread(job, recruiter_profile)
    JobAssignment.objects.filter(job=job, recruiter=recruiter_profile).update(
        left_at=timezone.now()
    )
    response = client_for(recruiter_user).post(f"/api/v1/marketplace/jobs/{job.id}/join")
    assert response.status_code in (200, 201), response.content
    general = Channel.objects.get(job=job, recruiter=recruiter_profile, application=None)
    assert response.json()["channel_id"] == str(general.id)


def test_channel_list_costs_the_same_however_many_threads(
    client_for,
    company: Company,
    company_ta: User,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    """`can_post` and the newest message come in bulk, so a longer list is not a slower one."""

    def thread_pair(title: str) -> None:
        job = JobFactory(company=company, created_by=company_ta, title=title)
        JobAssignmentFactory(job=job, recruiter=recruiter_profile)
        _, thread = _candidate_thread(job, recruiter_profile)
        MessageFactory(channel=thread, author=recruiter_user)

    thread_pair("Job 1")
    clients = {"headhunter": client_for(recruiter_user), "company": client_for(company_ta)}

    def cost(client: Any) -> int:
        with CaptureQueriesContext(connection) as captured:
            assert client.get(CHANNELS).status_code == 200
        return len(captured)

    before = {name: cost(client) for name, client in clients.items()}
    for number in range(2, 5):
        thread_pair(f"Job {number}")
    for name, client in clients.items():
        assert len(client.get(CHANNELS).json()) == 8
        assert cost(client) == before[name], name


def test_can_post_on_the_list_matches_the_access_rule(
    client_for, company: Company, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    _removed_channel(company, recruiter_profile, with_cv=True)
    held = JobFactory(company=company)
    JobAssignmentFactory(job=held, recruiter=recruiter_profile)
    _candidate_thread(held, recruiter_profile)
    rows = client_for(recruiter_user).get(CHANNELS).json()
    assert len(rows) == 4
    for row in rows:
        channel = Channel.objects.select_related("job", "recruiter").get(pk=row["id"])
        assert row["can_post"] is can_post_to_channel(recruiter_user, channel), row["kind"]
