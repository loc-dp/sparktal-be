from django.urls import path

from apps.candidates.api import (
    ApplicationCvView,
    CandidateCvView,
    CandidateDetailView,
    CandidateListCreateView,
    CandidateMatchView,
    CvParseCreateView,
    CvParseDetailView,
    JobApplicationListView,
    RecruiterSubmissionCvView,
    RecruiterSubmissionDetailView,
    RecruiterSubmissionListView,
    SubmissionCheckView,
    SubmissionCreateView,
)

urlpatterns = [
    path(
        "jobs/<uuid:job_id>/submissions/check",
        SubmissionCheckView.as_view(),
        name="job-submissions-check",
    ),
    path("jobs/<uuid:job_id>/submissions", SubmissionCreateView.as_view(), name="job-submissions"),
    path(
        "recruiters/me/submissions",
        RecruiterSubmissionListView.as_view(),
        name="recruiter-submissions",
    ),
    path(
        "recruiters/me/submissions/<uuid:pk>",
        RecruiterSubmissionDetailView.as_view(),
        name="recruiter-submission-detail",
    ),
    path(
        "recruiters/me/submissions/<uuid:pk>/cv",
        RecruiterSubmissionCvView.as_view(),
        name="recruiter-submission-cv",
    ),
    path(
        "jobs/<uuid:job_id>/applications",
        JobApplicationListView.as_view(),
        name="job-applications",
    ),
    path("applications/<uuid:pk>/cv", ApplicationCvView.as_view(), name="application-cv"),
    path("cv-parses", CvParseCreateView.as_view(), name="cv-parses"),
    path("cv-parses/<uuid:pk>", CvParseDetailView.as_view(), name="cv-parse-detail"),
    path("candidates", CandidateListCreateView.as_view(), name="candidates"),
    path("candidates/matches", CandidateMatchView.as_view(), name="candidate-matches"),
    path("candidates/<uuid:pk>", CandidateDetailView.as_view(), name="candidate-detail"),
    path("candidates/<uuid:pk>/cv", CandidateCvView.as_view(), name="candidate-cv"),
]
