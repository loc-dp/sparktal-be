from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from apps.candidates.models import Application, Candidate, CvParse


@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("full_name", "current_title", "owner", "is_expat", "updated_at")
    list_filter = ("is_expat", "location", "owner")
    search_fields = ("full_name", "email", "current_title")
    autocomplete_fields = ("owner",)
    readonly_fields = ("email_hash", "phone_hash", "name_dob_hash", "search_vector")


@admin.register(Application)
class ApplicationAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    """Until recruiters can submit CVs themselves, staff move stages here."""

    list_display = ("candidate", "job", "recruiter", "stage", "submitted_at", "is_duplicate")
    list_filter = ("stage", "is_duplicate", "job__company")
    list_editable = ("stage",)
    search_fields = ("candidate__full_name", "job__title")
    autocomplete_fields = ("job", "candidate", "recruiter")
    readonly_fields = ("submitted_at", "stage_changed_at")


@admin.register(CvParse)
class CvParseAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("filename", "owner", "status", "created_at", "finished_at")
    list_filter = ("status",)
    readonly_fields = ("fields", "error", "created_at", "finished_at")
