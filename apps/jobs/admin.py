from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from simple_history.admin import SimpleHistoryAdmin

from apps.jobs.models import Job, JobAssignment, JobStatus


@admin.register(Job)
class JobAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    list_display = ("title", "company", "function", "status", "created_at", "published_at")
    list_filter = ("status", "function", "company")
    search_fields = ("title", "company__name")
    autocomplete_fields = ("company", "created_by", "office")
    readonly_fields = ("share_token", "created_at", "updated_at", "published_at", "closed_at")
    actions = ("approve_jobs",)

    @admin.action(description="Approve selected jobs (publish to the marketplace)")
    def approve_jobs(self, request: HttpRequest, queryset: QuerySet[Job]) -> None:
        pending = list(queryset.filter(status=JobStatus.PENDING))
        for job in pending:
            job.publish()
        skipped = queryset.count() - len(pending)
        self.message_user(
            request,
            f"Published {len(pending)} job(s)."
            + (f" Skipped {skipped} not pending." if skipped else ""),
            messages.SUCCESS,
        )


@admin.register(JobAssignment)
class JobAssignmentAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("job", "recruiter", "joined_at", "left_at")
    list_filter = ("job__company",)
    search_fields = ("job__title", "recruiter__nickname")
    autocomplete_fields = ("job", "recruiter")
