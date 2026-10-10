from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from apps.candidates.models import CvParse, CvParseStatus
from apps.candidates.tasks import CV_PARSE_TTL, purge_stale_cv_parses
from apps.candidates.tests.factories import CvParseFactory
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db


def age(parse: CvParse, by: timedelta) -> None:
    CvParse.objects.filter(pk=parse.pk).update(created_at=timezone.now() - by)


def test_the_purge_runs_on_the_beat_schedule() -> None:
    tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}

    assert "candidates.purge_stale_cv_parses" in tasks


def test_a_parse_older_than_a_day_loses_its_row_and_its_file(
    recruiter_profile: RecruiterProfile,
) -> None:
    parse = CvParseFactory(owner=recruiter_profile, status=CvParseStatus.DONE)
    storage, name = parse.file.storage, parse.file.name
    age(parse, CV_PARSE_TTL + timedelta(minutes=1))

    assert purge_stale_cv_parses() == 1
    assert not CvParse.objects.filter(pk=parse.pk).exists()
    assert not storage.exists(name)


def test_a_parse_stuck_running_is_purged_too(recruiter_profile: RecruiterProfile) -> None:
    # A worker that died mid-parse leaves the row running forever; nothing else clears it.
    parse = CvParseFactory(owner=recruiter_profile, status=CvParseStatus.RUNNING)
    age(parse, CV_PARSE_TTL + timedelta(minutes=1))

    assert purge_stale_cv_parses() == 1
    assert not CvParse.objects.filter(pk=parse.pk).exists()


def test_a_recent_parse_is_kept(recruiter_profile: RecruiterProfile) -> None:
    parse = CvParseFactory(owner=recruiter_profile, status=CvParseStatus.DONE)
    age(parse, CV_PARSE_TTL - timedelta(minutes=1))

    assert purge_stale_cv_parses() == 0
    assert CvParse.objects.filter(pk=parse.pk).exists()
    assert parse.file.storage.exists(parse.file.name)


def test_a_parse_whose_file_is_already_gone_is_still_purged(
    recruiter_profile: RecruiterProfile,
) -> None:
    parse = CvParseFactory(owner=recruiter_profile)
    parse.file.delete(save=True)
    age(parse, CV_PARSE_TTL + timedelta(minutes=1))

    assert purge_stale_cv_parses() == 1
