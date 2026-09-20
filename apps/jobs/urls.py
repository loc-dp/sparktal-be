from django.urls import path

from apps.jobs.api import (
    JobCloseView,
    JobCountsView,
    JobDetailView,
    JobListCreateView,
    JobOverviewView,
    JobReopenView,
    PublicJobView,
)

urlpatterns = [
    path("jobs", JobListCreateView.as_view(), name="jobs"),
    path("jobs/counts", JobCountsView.as_view(), name="job-counts"),
    path("jobs/<uuid:pk>", JobDetailView.as_view(), name="job-detail"),
    path("jobs/<uuid:pk>/close", JobCloseView.as_view(), name="job-close"),
    path("jobs/<uuid:pk>/reopen", JobReopenView.as_view(), name="job-reopen"),
    path("jobs/<uuid:pk>/overview", JobOverviewView.as_view(), name="job-overview"),
    path("public/jobs/<str:share_token>", PublicJobView.as_view(), name="public-job"),
]
