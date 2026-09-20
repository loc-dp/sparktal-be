from django.urls import path

from apps.jobs.api import JobDetailView, JobListCreateView, PublicJobView

urlpatterns = [
    path("jobs", JobListCreateView.as_view(), name="jobs"),
    path("jobs/<uuid:pk>", JobDetailView.as_view(), name="job-detail"),
    path("public/jobs/<str:share_token>", PublicJobView.as_view(), name="public-job"),
]
