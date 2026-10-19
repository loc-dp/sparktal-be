from django.urls import path

from apps.recruiters.api import (
    NicknameCheckView,
    RecruiterInviteAcceptView,
    RecruiterInviteDeclineView,
    RecruiterInviteListView,
    RecruiterJobsListView,
    RecruiterMeView,
    RecruiterOverviewView,
    SignupView,
)

urlpatterns = [
    path("recruiters/signup", SignupView.as_view(), name="recruiter-signup"),
    path("recruiters/nickname-check", NicknameCheckView.as_view(), name="recruiter-nickname-check"),
    path("recruiters/me/jobs", RecruiterJobsListView.as_view(), name="recruiter-jobs"),
    path("recruiters/me/invites", RecruiterInviteListView.as_view(), name="recruiter-invites"),
    path(
        "recruiters/me/invites/<uuid:id>/accept",
        RecruiterInviteAcceptView.as_view(),
        name="recruiter-invite-accept",
    ),
    path(
        "recruiters/me/invites/<uuid:id>/decline",
        RecruiterInviteDeclineView.as_view(),
        name="recruiter-invite-decline",
    ),
    path("recruiters/me/overview", RecruiterOverviewView.as_view(), name="recruiter-overview"),
    path("recruiters/me", RecruiterMeView.as_view(), name="recruiter-me"),
]
