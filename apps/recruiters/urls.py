from django.urls import path

from apps.recruiters.api import (
    NicknameCheckView,
    RecruiterJobsListView,
    RecruiterMeView,
    RecruiterOverviewView,
    SignupView,
)

urlpatterns = [
    path("recruiters/signup", SignupView.as_view(), name="recruiter-signup"),
    path("recruiters/nickname-check", NicknameCheckView.as_view(), name="recruiter-nickname-check"),
    path("recruiters/me/jobs", RecruiterJobsListView.as_view(), name="recruiter-jobs"),
    path("recruiters/me/overview", RecruiterOverviewView.as_view(), name="recruiter-overview"),
    path("recruiters/me", RecruiterMeView.as_view(), name="recruiter-me"),
]
