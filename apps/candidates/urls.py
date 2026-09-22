from django.urls import path

from apps.candidates.api import (
    ApplicationCvView,
    CandidateCvView,
    CandidateDetailView,
    CandidateListCreateView,
    CvParseCreateView,
    CvParseDetailView,
    JobApplicationListView,
)

urlpatterns = [
    path(
        "jobs/<uuid:job_id>/applications",
        JobApplicationListView.as_view(),
        name="job-applications",
    ),
    path("applications/<uuid:pk>/cv", ApplicationCvView.as_view(), name="application-cv"),
    path("cv-parses", CvParseCreateView.as_view(), name="cv-parses"),
    path("cv-parses/<uuid:pk>", CvParseDetailView.as_view(), name="cv-parse-detail"),
    path("candidates", CandidateListCreateView.as_view(), name="candidates"),
    path("candidates/<uuid:pk>", CandidateDetailView.as_view(), name="candidate-detail"),
    path("candidates/<uuid:pk>/cv", CandidateCvView.as_view(), name="candidate-cv"),
]
