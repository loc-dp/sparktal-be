from django.urls import path

from apps.hiring.api import (
    ApplicationDetailView,
    ApprovalDecisionView,
    ApprovalRequestCreateView,
    ApproversView,
    ArchiveView,
    CvInlineView,
    CvTextView,
    DiscussionView,
    FeedbackListView,
    HireView,
    InterviewCreateView,
    InterviewFeedbackView,
    MoveForwardView,
    NoteDetailView,
    NotesView,
    OfferView,
    WaitingApprovalsView,
)

urlpatterns = [
    path("applications/<uuid:pk>", ApplicationDetailView.as_view(), name="application-detail"),
    path(
        "applications/<uuid:pk>/move-forward",
        MoveForwardView.as_view(),
        name="application-move-forward",
    ),
    path(
        "applications/<uuid:pk>/interviews",
        InterviewCreateView.as_view(),
        name="application-interviews",
    ),
    path("applications/<uuid:pk>/offer", OfferView.as_view(), name="application-offer"),
    path("applications/<uuid:pk>/hire", HireView.as_view(), name="application-hire"),
    path("applications/<uuid:pk>/archive", ArchiveView.as_view(), name="application-archive"),
    path("applications/<uuid:pk>/approvers", ApproversView.as_view(), name="application-approvers"),
    path(
        "applications/<uuid:pk>/approval-requests",
        ApprovalRequestCreateView.as_view(),
        name="application-approval-requests",
    ),
    path(
        "approval-requests/waiting",
        WaitingApprovalsView.as_view(),
        name="approval-requests-waiting",
    ),
    path(
        "approval-requests/<uuid:pk>/decision",
        ApprovalDecisionView.as_view(),
        name="approval-request-decision",
    ),
    path("applications/<uuid:pk>/notes", NotesView.as_view(), name="application-notes"),
    path("notes/<uuid:pk>", NoteDetailView.as_view(), name="note-detail"),
    path(
        "applications/<uuid:pk>/feedback", FeedbackListView.as_view(), name="application-feedback"
    ),
    path(
        "interviews/<uuid:pk>/feedback", InterviewFeedbackView.as_view(), name="interview-feedback"
    ),
    path("applications/<uuid:pk>/messages", DiscussionView.as_view(), name="application-messages"),
    path("applications/<uuid:pk>/cv/inline", CvInlineView.as_view(), name="application-cv-inline"),
    path("applications/<uuid:pk>/cv/text", CvTextView.as_view(), name="application-cv-text"),
]
