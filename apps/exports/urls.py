from django.urls import path

from apps.exports.api import CvExportCreateView, CvExportDetailView, CvExportDownloadView

urlpatterns = [
    path("jobs/<uuid:job_id>/cv-exports", CvExportCreateView.as_view(), name="cv-exports"),
    path("cv-exports/<uuid:pk>", CvExportDetailView.as_view(), name="cv-export-detail"),
    path(
        "cv-exports/<uuid:pk>/download",
        CvExportDownloadView.as_view(),
        name="cv-export-download",
    ),
]
