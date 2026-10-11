"""Who may open one application: company members who can view its job, or its recruiter."""

from uuid import UUID

from django.http import Http404

from apps.accounts.models import User, UserKind
from apps.candidates.models import Application
from apps.jobs.access import can_view_job
from apps.recruiters.access import approved_recruiter

DETAIL_HINTS = ("job", "job__company", "candidate", "recruiter", "duplicate_of")


def _application_or_404(pk: UUID) -> Application:
    application = Application.objects.select_related(*DETAIL_HINTS).filter(pk=pk).first()
    if application is None:
        raise Http404
    return application


def company_application_or_404(user: User, pk: UUID) -> Application:
    """404, not 403, so nobody can probe applications outside the jobs they see."""
    application = _application_or_404(pk)
    if not can_view_job(user, application.job):
        raise Http404
    return application


def shared_application_or_404(user: User, pk: UUID) -> Application:
    """The company side of the job, or the approved recruiter who submitted the candidate."""
    application = _application_or_404(pk)
    if user.kind == UserKind.RECRUITER:
        recruiter = approved_recruiter(user)
        if recruiter is None or application.recruiter_id != recruiter.pk:
            raise Http404
        return application
    if not can_view_job(user, application.job):
        raise Http404
    return application


def teammates_who_can_view(application: Application, *, excluding: User) -> list[User]:
    members = (
        User.objects.filter(membership__company_id=application.job.company_id, is_active=True)
        .exclude(pk=excluding.pk)
        .select_related("membership")
        .order_by("full_name")
    )
    return [member for member in members if can_view_job(member, application.job)]
