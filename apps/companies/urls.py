from django.urls import path

from apps.companies.api import (
    CompanyBannerView,
    CompanyCompleteView,
    CompanyHomeView,
    CompanyLogoView,
    CompanyView,
)

urlpatterns = [
    path("company", CompanyView.as_view(), name="company"),
    path("company/home", CompanyHomeView.as_view(), name="company-home"),
    path("company/complete", CompanyCompleteView.as_view(), name="company-complete"),
    path("company/logo", CompanyLogoView.as_view(), name="company-logo"),
    path("company/banner", CompanyBannerView.as_view(), name="company-banner"),
]
