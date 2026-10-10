from django.urls import path

from apps.jobs.api import (
    JdParseAvailabilityView,
    JdParseCreateView,
    JdParseDetailView,
    JobCloseView,
    JobCountsView,
    JobDetailView,
    JobListCreateView,
    JobOverviewView,
    JobReopenView,
    MarketplaceJobDetailView,
    MarketplaceJobListView,
    MarketplaceJoinView,
    MarketplaceLeaveView,
    PublicJobView,
)

urlpatterns = [
    path("jobs", JobListCreateView.as_view(), name="jobs"),
    path("jobs/counts", JobCountsView.as_view(), name="job-counts"),
    path("jd-parses", JdParseCreateView.as_view(), name="jd-parses"),
    path(
        "jd-parses/availability",
        JdParseAvailabilityView.as_view(),
        name="jd-parse-availability",
    ),
    path("jd-parses/<uuid:pk>", JdParseDetailView.as_view(), name="jd-parse-detail"),
    path("jobs/<uuid:pk>", JobDetailView.as_view(), name="job-detail"),
    path("jobs/<uuid:pk>/close", JobCloseView.as_view(), name="job-close"),
    path("jobs/<uuid:pk>/reopen", JobReopenView.as_view(), name="job-reopen"),
    path("jobs/<uuid:pk>/overview", JobOverviewView.as_view(), name="job-overview"),
    path("public/jobs/<str:share_token>", PublicJobView.as_view(), name="public-job"),
    path("marketplace/jobs", MarketplaceJobListView.as_view(), name="marketplace-jobs"),
    path(
        "marketplace/jobs/<uuid:pk>",
        MarketplaceJobDetailView.as_view(),
        name="marketplace-job-detail",
    ),
    path(
        "marketplace/jobs/<uuid:pk>/join",
        MarketplaceJoinView.as_view(),
        name="marketplace-job-join",
    ),
    path(
        "marketplace/jobs/<uuid:pk>/leave",
        MarketplaceLeaveView.as_view(),
        name="marketplace-job-leave",
    ),
]
