from typing import Any

import pytest
from django.utils import timezone

from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.chat.models import Channel, Message
from apps.chat.tests.factories import MessageFactory
from apps.hiring import discussion
from apps.jobs.models import JobAssignment
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db


def url(application: Any) -> str:
    return f"/api/v1/applications/{application.pk}/messages"


def channel_of(application: Any) -> Channel:
    """The candidate thread: the Discussion panel and the chat page show the same messages."""
    return Channel.objects.get(application=application)


def general_of(application: Any) -> Channel:
    return Channel.objects.get(
        job=application.job, recruiter=application.recruiter, application=None
    )


def bodies(response: Any) -> list[str]:
    return [message["body"] for message in response.json()["results"]]


def test_discussion_only_lists_messages_about_this_candidate(
    ta_client: Any, application: Any, recruiter_profile: Any, joined_job: Any
) -> None:
    other = ApplicationFactory(
        job=joined_job,
        recruiter=recruiter_profile,
        candidate=CandidateFactory(owner=recruiter_profile, full_name="Lê Thu Trang"),
    )
    MessageFactory(channel=general_of(application), body="General chat", application=None)
    MessageFactory(channel=channel_of(other), body="About Trang", application=other)
    MessageFactory(channel=channel_of(application), body="About Quân", application=application)
    listed = bodies(ta_client.get(url(application)))
    assert "About Quân" in listed
    assert "About Trang" not in listed
    assert "General chat" not in listed


def test_the_submission_message_belongs_to_the_candidate(application: Any) -> None:
    message = application.messages.get()
    assert message.body == "NorthStar Talent submitted Phạm Minh Quân"
    assert message.channel == channel_of(application)


def test_company_posts_into_the_candidate_thread(
    ta_client: Any,
    application: Any,
    company_ta: Any,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    sent: list[Any] = []
    monkeypatch.setattr(discussion, "broadcast_message", sent.append)
    with django_capture_on_commit_callbacks(execute=True):
        response = ta_client.post(url(application), {"body": "Can he start in May?"}, format="json")
    assert response.status_code == 201
    body = response.json()
    assert body["application"] == {
        "id": str(application.pk),
        "job_id": str(application.job_id),
        "candidate_name": "Phạm Minh Quân",
    }
    assert body["author_name"] == company_ta.full_name
    message = Message.objects.get(pk=body["id"])
    assert message.channel == channel_of(application)
    assert message.application == application
    assert [item.pk for item in sent] == [message.pk]


def test_the_owning_recruiter_reads_and_replies(
    client_for: Any, recruiter_user: Any, application: Any
) -> None:
    client = client_for(recruiter_user)
    response = client.post(url(application), {"body": "He can start on 1 May."}, format="json")
    assert response.status_code == 201
    assert response.json()["author_name"] == "NorthStar Talent"
    assert "He can start on 1 May." in bodies(client.get(url(application)))
    assert channel_of(application).messages.filter(body="He can start on 1 May.").exists()


def test_a_removed_headhunter_still_posts_about_their_candidate(
    client_for: Any, recruiter_user: Any, application: Any
) -> None:
    JobAssignment.objects.filter(job=application.job, recruiter=application.recruiter).update(
        left_at=timezone.now()
    )
    response = client_for(recruiter_user).post(
        url(application), {"body": "Still available."}, format="json"
    )
    assert response.status_code == 201
    message = Message.objects.get(pk=response.json()["id"])
    assert message.channel == channel_of(application)
    assert message.application == application
    assert not general_of(application).messages.filter(pk=message.pk).exists()


def test_other_recruiters_and_outsiders_get_404(
    client_for: Any, application: Any, other_company_admin: Any, company_hm: Any
) -> None:
    rival = RecruiterProfileFactory(nickname="Bluefin Search").user
    for user in (rival, other_company_admin, company_hm):
        client = client_for(user)
        assert client.get(url(application)).status_code == 404
        assert client.post(url(application), {"body": "hi"}, format="json").status_code == 404


def test_blank_message_is_400(ta_client: Any, application: Any) -> None:
    response = ta_client.post(url(application), {"body": " <b></b> "}, format="json")
    assert response.status_code == 400
    assert response.json()["body"] == ["Type a message."]


def test_discussion_pages_like_chat(ta_client: Any, application: Any) -> None:
    for number in range(3):
        MessageFactory(channel=channel_of(application), application=application, body=f"m{number}")
    page = ta_client.get(url(application), {"limit": 2}).json()
    assert page["has_more"] is True
    assert [message["body"] for message in page["results"]] == ["m1", "m2"]


def test_chat_shows_which_candidate_a_message_is_about(ta_client: Any, application: Any) -> None:
    MessageFactory(channel=channel_of(application), application=application, body="Tagged")
    MessageFactory(channel=general_of(application), application=None, body="Untagged")
    by_body = {}
    for channel in (channel_of(application), general_of(application)):
        results = ta_client.get(f"/api/v1/chat/channels/{channel.pk}/messages").json()["results"]
        by_body.update({message["body"]: message["application"] for message in results})
    assert by_body["Tagged"]["candidate_name"] == "Phạm Minh Quân"
    assert by_body["Untagged"] is None
