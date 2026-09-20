from django.urls import path

from apps.accounts.api import MeView

urlpatterns = [
    path("me", MeView.as_view(), name="me"),
]
