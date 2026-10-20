from django.urls import path

from apps.ops.api import (
    OpsCompanyDetailView,
    OpsCompanyListCreateView,
    OpsJobBountyView,
    OpsJobChannelsView,
    OpsJobDetailView,
    OpsJobDistributeView,
    OpsJobListView,
    OpsMemberDetailView,
    OpsMemberListCreateView,
    OpsMemberLockView,
    OpsMemberResendInviteView,
    OpsMemberUnlockView,
    OpsOverviewView,
    OpsRecruiterApproveView,
    OpsRecruiterListView,
    OpsRecruiterRejectView,
    OpsTeamDetailView,
    OpsTeamListCreateView,
)

COMPANY = "ops/companies/<uuid:company_id>"
MEMBER = f"{COMPANY}/members/<uuid:id>"

urlpatterns = [
    path("ops/overview", OpsOverviewView.as_view(), name="ops-overview"),
    path("ops/jobs", OpsJobListView.as_view(), name="ops-jobs"),
    path("ops/jobs/<uuid:id>", OpsJobDetailView.as_view(), name="ops-job-detail"),
    path("ops/jobs/<uuid:id>/bounty", OpsJobBountyView.as_view(), name="ops-job-bounty"),
    path(
        "ops/jobs/<uuid:id>/distribute", OpsJobDistributeView.as_view(), name="ops-job-distribute"
    ),
    path("ops/jobs/<uuid:id>/channels", OpsJobChannelsView.as_view(), name="ops-job-channels"),
    path("ops/companies", OpsCompanyListCreateView.as_view(), name="ops-companies"),
    path(COMPANY, OpsCompanyDetailView.as_view(), name="ops-company"),
    path(f"{COMPANY}/members", OpsMemberListCreateView.as_view(), name="ops-company-members"),
    path(MEMBER, OpsMemberDetailView.as_view(), name="ops-company-member"),
    path(
        f"{MEMBER}/resend-invite",
        OpsMemberResendInviteView.as_view(),
        name="ops-company-member-resend-invite",
    ),
    path(f"{MEMBER}/lock", OpsMemberLockView.as_view(), name="ops-company-member-lock"),
    path(f"{MEMBER}/unlock", OpsMemberUnlockView.as_view(), name="ops-company-member-unlock"),
    path("ops/recruiters", OpsRecruiterListView.as_view(), name="ops-recruiters"),
    path(
        "ops/recruiters/<uuid:id>/approve",
        OpsRecruiterApproveView.as_view(),
        name="ops-recruiter-approve",
    ),
    path(
        "ops/recruiters/<uuid:id>/reject",
        OpsRecruiterRejectView.as_view(),
        name="ops-recruiter-reject",
    ),
    path(f"{COMPANY}/teams", OpsTeamListCreateView.as_view(), name="ops-company-teams"),
    path(f"{COMPANY}/teams/<uuid:id>", OpsTeamDetailView.as_view(), name="ops-company-team"),
]
