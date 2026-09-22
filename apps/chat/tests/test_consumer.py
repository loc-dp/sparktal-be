import pytest
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from rest_framework.test import APIClient

from apps.chat.events import group_name
from config.routing import websocket_urlpatterns

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]
PATH = "/ws/events/"


def build_world(nickname: str = "NorthStar Talent") -> dict[str, object]:
    from apps.accounts.tests.factories import UserFactory
    from apps.chat.tests.factories import ChannelFactory
    from apps.companies.tests.factories import CompanyFactory, CompanyMembershipFactory
    from apps.jobs.tests.factories import JobFactory
    from apps.recruiters.tests.factories import RecruiterProfileFactory

    company = CompanyFactory()
    ta = UserFactory(kind="company", full_name="Trần Mai Anh")
    CompanyMembershipFactory(user=ta, company=company, role="talent_acquisition")
    recruiter = RecruiterProfileFactory(nickname=nickname)
    channel = ChannelFactory(job=JobFactory(company=company), recruiter=recruiter)
    return {"ta": ta, "recruiter": recruiter, "channel": channel}


async def connect_as(user: object) -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(URLRouter(websocket_urlpatterns), PATH)
    communicator.scope["user"] = user
    return communicator


async def test_anonymous_socket_is_closed_with_4401() -> None:
    communicator = await connect_as(AnonymousUser())
    connected, code = await communicator.connect()
    assert connected is False
    assert code == 4401


async def test_authenticated_socket_answers_ping_and_ignores_writes() -> None:
    world = await database_sync_to_async(build_world)()
    communicator = await connect_as(world["ta"])
    assert (await communicator.connect())[0] is True

    await communicator.send_json_to({"type": "ping"})
    assert await communicator.receive_json_from(timeout=2) == {"type": "pong"}
    await communicator.send_json_to({"type": "chat.message", "body": "injected"})
    assert await communicator.receive_nothing(timeout=0.25) is True
    await communicator.disconnect()


async def test_posted_message_reaches_both_sides_but_not_an_outsider() -> None:
    world = await database_sync_to_async(build_world)()
    outsider = await database_sync_to_async(build_world)("Outside Search")
    ta, recruiter, channel = world["ta"], world["recruiter"], world["channel"]

    ta_socket = await connect_as(ta)
    recruiter_socket = await connect_as(recruiter.user)
    outsider_socket = await connect_as(outsider["ta"])
    await ta_socket.connect()
    await recruiter_socket.connect()
    await outsider_socket.connect()

    def post() -> int:
        client = APIClient()
        client.force_login(recruiter.user)
        response = client.post(
            f"/api/v1/chat/channels/{channel.id}/messages",
            {"body": "Round 2 on 22/09 in the morning?"},
            format="multipart",
        )
        return response.status_code

    assert await database_sync_to_async(post)() == 201
    event = await ta_socket.receive_json_from(timeout=3)
    assert event["type"] == "chat.message"
    assert event["channel_id"] == str(channel.id)
    assert event["message"]["author_name"] == "NorthStar Talent"
    assert (await recruiter_socket.receive_json_from(timeout=3))["message"]["id"] == event[
        "message"
    ]["id"]
    assert await outsider_socket.receive_nothing(timeout=0.5) is True

    await ta_socket.disconnect()
    await recruiter_socket.disconnect()
    await outsider_socket.disconnect()


async def test_group_name_uses_uuid_hex() -> None:
    world = await database_sync_to_async(build_world)()
    assert group_name(world["ta"]) == f"user_{world['ta'].pk.hex}"
