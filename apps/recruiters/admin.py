from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.accounts.models import User
from apps.recruiters.emails import send_review_result
from apps.recruiters.models import RecruiterProfile, RecruiterStatus


@admin.register(RecruiterProfile)
class RecruiterProfileAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("nickname", "user", "status", "submitted_at", "reviewed_at")
    list_filter = ("status",)
    search_fields = ("nickname", "user__email")
    autocomplete_fields = ("user",)
    readonly_fields = (
        "submitted_at",
        "reviewed_at",
        "reviewed_by",
        "agreed_terms_at",
        "created_at",
    )
    actions = ("approve_profiles", "reject_profiles")

    @admin.action(description="Approve selected recruiters")
    def approve_profiles(self, request: HttpRequest, queryset: QuerySet[RecruiterProfile]) -> None:
        assert isinstance(request.user, User)
        waiting = list(queryset.exclude(status=RecruiterStatus.APPROVED))
        for profile in waiting:
            profile.approve(by=request.user)
            send_review_result(profile)
        self.message_user(request, f"Approved {len(waiting)} recruiter(s).", messages.SUCCESS)

    @admin.action(description="Reject selected recruiters (fill in the review note first)")
    def reject_profiles(self, request: HttpRequest, queryset: QuerySet[RecruiterProfile]) -> None:
        assert isinstance(request.user, User)
        missing = [profile for profile in queryset if not profile.review_note.strip()]
        if missing:
            self.message_user(
                request,
                "Write a review note on each profile before rejecting it: "
                + ", ".join(profile.nickname for profile in missing),
                messages.ERROR,
            )
            return
        for profile in queryset:
            profile.reject(by=request.user, note=profile.review_note)
            send_review_result(profile)
        self.message_user(request, f"Rejected {queryset.count()} recruiter(s).", messages.WARNING)
