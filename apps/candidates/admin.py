from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from apps.candidates.models import Application, Candidate


@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("full_name", "current_title", "owner", "created_at")
    search_fields = ("full_name", "email", "current_title")
    autocomplete_fields = ("owner",)


@admin.register(Application)
class ApplicationAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    """Until recruiters can submit CVs themselves, staff move stages here."""

    list_display = ("candidate", "job", "recruiter", "stage", "submitted_at", "is_duplicate")
    list_filter = ("stage", "is_duplicate", "job__company")
    list_editable = ("stage",)
    search_fields = ("candidate__full_name", "job__title")
    autocomplete_fields = ("job", "candidate", "recruiter")
    readonly_fields = ("submitted_at", "stage_changed_at")
