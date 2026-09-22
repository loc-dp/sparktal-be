import factory
from factory.django import DjangoModelFactory

from apps.accounts.tests.factories import UserFactory
from apps.chat.models import Channel, Message, MessageKind
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.tests.factories import RecruiterProfileFactory


class ChannelFactory(DjangoModelFactory[Channel]):
    class Meta:
        model = Channel

    job = factory.SubFactory(JobFactory)
    recruiter = factory.SubFactory(RecruiterProfileFactory)


class MessageFactory(DjangoModelFactory[Message]):
    class Meta:
        model = Message

    channel = factory.SubFactory(ChannelFactory)
    author = factory.SubFactory(UserFactory)
    kind = MessageKind.USER
    body = factory.Sequence(lambda n: f"Message {n}")
