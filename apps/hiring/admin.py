from django.contrib import admin

from apps.hiring.models import ApprovalRequest, CandidateNote, Feedback, Interview


@admin.register(Interview)
class InterviewAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("application", "round", "starts_at", "format")
    list_filter = ("format",)
    raw_id_fields = ("application", "created_by")


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("interview", "author", "verdict", "updated_at")
    list_filter = ("verdict",)
    raw_id_fields = ("interview", "author")


@admin.register(CandidateNote)
class CandidateNoteAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("application", "author", "created_at")
    raw_id_fields = ("application", "author")


@admin.register(ApprovalRequest)
class ApprovalRequestAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("application", "requested_by", "approver", "stage", "status", "created_at")
    list_filter = ("status",)
    raw_id_fields = ("application", "requested_by", "approver")
