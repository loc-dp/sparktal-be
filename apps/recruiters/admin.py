from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.accounts.models import User
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.review import ReviewError, approve_recruiter, reject_recruiter


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

    @admin.action(description="Approve selected recruiters", permissions=["change"])
    def approve_profiles(self, request: HttpRequest, queryset: QuerySet[RecruiterProfile]) -> None:
        assert isinstance(request.user, User)
        approved = 0
        for profile in queryset:
            try:
                approve_recruiter(profile, by=request.user)
            except ReviewError:
                continue  # Already approved: nothing to change, and nobody to tell.
            approved += 1
        self.message_user(request, f"Approved {approved} recruiter(s).", messages.SUCCESS)

    @admin.action(
        description="Reject selected recruiters (fill in the review note first)",
        permissions=["change"],
    )
    def reject_profiles(self, request: HttpRequest, queryset: QuerySet[RecruiterProfile]) -> None:
        assert isinstance(request.user, User)
        profiles = list(queryset)
        missing = [profile for profile in profiles if not profile.review_note.strip()]
        if missing:
            self.message_user(
                request,
                "Write a review note on each profile before rejecting it: "
                + ", ".join(profile.nickname for profile in missing),
                messages.ERROR,
            )
            return
        rejected = 0
        for profile in profiles:
            try:
                reject_recruiter(profile, by=request.user, note=profile.review_note)
            except ReviewError:
                continue  # Already rejected: nothing to change, and nobody to tell.
            rejected += 1
        self.message_user(request, f"Rejected {rejected} recruiter(s).", messages.WARNING)
