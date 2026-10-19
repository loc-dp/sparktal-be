from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from simple_history.admin import SimpleHistoryAdmin

from apps.jobs.lifecycle import JobRuleError, activate_job, restore_job
from apps.jobs.models import JdParse, Job, JobAssignment, JobChangeRequest


@admin.register(Job)
class JobAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    list_display = (
        "title",
        "company",
        "team",
        "function",
        "status",
        "created_at",
        "published_at",
        "deleted_at",
    )
    list_filter = ("status", "function", "company", "team", "deleted_at")
    search_fields = ("title", "company__name")
    autocomplete_fields = ("company", "team", "created_by", "office")
    # Status and deletion change only through the actions below (spec §5).
    readonly_fields = (
        "share_token",
        "status",
        "deleted_at",
        "created_at",
        "updated_at",
        "published_at",
        "closed_at",
    )
    actions = ("approve_jobs", "restore_jobs")

    @admin.action(
        description="Approve selected jobs (publish to the marketplace)", permissions=["change"]
    )
    def approve_jobs(self, request: HttpRequest, queryset: QuerySet[Job]) -> None:
        approved = 0
        skipped = 0
        for job in queryset:
            try:
                activate_job(job)
            except JobRuleError:  # not pending, or it left pending since the list loaded
                skipped += 1
            else:
                approved += 1
        self.message_user(
            request,
            f"Published {approved} job(s)."
            + (f" Skipped {skipped} not pending." if skipped else ""),
            messages.SUCCESS,
        )

    @admin.action(description="Restore deleted jobs", permissions=["change"])
    def restore_jobs(self, request: HttpRequest, queryset: QuerySet[Job]) -> None:
        deleted = list(queryset.filter(deleted_at__isnull=False))
        for job in deleted:
            restore_job(job)
        self.message_user(request, f"Restored {len(deleted)} job(s).", messages.SUCCESS)


@admin.register(JobAssignment)
class JobAssignmentAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("job", "recruiter", "joined_at", "left_at")
    list_filter = ("job__company",)
    search_fields = ("job__title", "recruiter__nickname")
    autocomplete_fields = ("job", "recruiter")


@admin.register(JdParse)
class JdParseAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "filename",
        "company",
        "uploaded_by",
        "status",
        "llm_model",
        "input_tokens",
        "output_tokens",
        "created_at",
        "finished_at",
    )
    list_filter = ("status", "llm_model", "company")
    readonly_fields = (
        "fields",
        "error",
        "llm_model",
        "input_tokens",
        "output_tokens",
        "created_at",
        "started_at",
        "finished_at",
    )


@admin.register(JobChangeRequest)
class JobChangeRequestAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("job", "kind", "status", "requested_by", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("job__title", "requested_by__email")

    def get_readonly_fields(
        self, request: HttpRequest, obj: JobChangeRequest | None = None
    ) -> list[str]:
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
