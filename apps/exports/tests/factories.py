import factory
from factory.django import DjangoModelFactory

from apps.accounts.tests.factories import UserFactory
from apps.exports.models import CvExport, CvExportStatus
from apps.jobs.tests.factories import JobFactory


class CvExportFactory(DjangoModelFactory[CvExport]):
    class Meta:
        model = CvExport

    job = factory.SubFactory(JobFactory)
    requested_by = factory.SubFactory(UserFactory)
    application_ids = factory.List([])
    status = CvExportStatus.QUEUED
