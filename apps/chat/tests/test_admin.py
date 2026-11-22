import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.candidates.tests.factories import ApplicationFactory
from apps.chat.models import Channel
from apps.chat.signals import ensure_candidate_channel
from apps.chat.tests.factories import ChannelFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(db: None) -> Client:
    User.objects.create_superuser(
        email="admin@sparktal.vn", password="pass-1234-word", full_name="Sparktal Admin"
    )
    client = Client()
    client.login(email="admin@sparktal.vn", password="pass-1234-word")
    return client


def _make_threads(count: int) -> None:
    for _ in range(count):
        ChannelFactory()
        ensure_candidate_channel(ApplicationFactory())


def _changelist_queries(client: Client) -> int:
    with CaptureQueriesContext(connection) as queries:
        assert client.get("/admin/chat/channel/").status_code == 200
    return len(queries)


def test_changelist_costs_the_same_for_few_and_many_threads(staff_client: Client) -> None:
    _make_threads(2)
    few = _changelist_queries(staff_client)
    _make_threads(3)
    assert _changelist_queries(staff_client) == few


def test_threads_are_not_added_by_hand(staff_client: Client) -> None:
    assert staff_client.get("/admin/chat/channel/add/").status_code == 403


def test_a_candidate_thread_opens_in_admin(staff_client: Client) -> None:
    thread = ensure_candidate_channel(ApplicationFactory())[0]
    assert Channel.objects.get(pk=thread.pk).application is not None
    assert staff_client.get(f"/admin/chat/channel/{thread.pk}/change/").status_code == 200
