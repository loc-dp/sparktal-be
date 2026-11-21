import factory
from factory.django import DjangoModelFactory

from apps.accounts.tests.factories import UserFactory
from apps.chat.models import Channel, Message, MessageKind
from apps.jobs.models import JobAssignment
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.tests.factories import RecruiterProfileFactory


class ChannelFactory(DjangoModelFactory[Channel]):
    class Meta:
        model = Channel
        skip_postgeneration_save = True

    job = factory.SubFactory(JobFactory)
    recruiter = factory.SubFactory(RecruiterProfileFactory)

    @factory.post_generation
    def assigned(self, create: bool, extracted: bool | None, **kwargs: object) -> None:
        """A headhunter reaches a job's channel only through the job, so by default they hold an
        assignment on it. Written with `bulk_create`: the assignment signal would add a system
        message to the channel. Pass `assigned=False` for a headhunter who never had the job."""
        if create and extracted is not False:
            JobAssignment.objects.bulk_create(
                [JobAssignment(job=self.job, recruiter=self.recruiter)], ignore_conflicts=True
            )


class MessageFactory(DjangoModelFactory[Message]):
    class Meta:
        model = Message

    channel = factory.SubFactory(ChannelFactory)
    author = factory.SubFactory(UserFactory)
    kind = MessageKind.USER
    body = factory.Sequence(lambda n: f"Message {n}")
