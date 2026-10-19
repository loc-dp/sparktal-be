from typing import Any

from django import forms
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from rest_framework.exceptions import ValidationError
from simple_history.admin import SimpleHistoryAdmin

from apps.accounts.models import User
from apps.jobs.bounty import clean_bounty
from apps.jobs.distribution import DistributionError, distribute_job
from apps.jobs.lifecycle import restore_job
from apps.jobs.models import (
    PRIVATE_STATUSES,
    JdParse,
    Job,
    JobAssignment,
    JobChangeRequest,
    JobInvite,
)

BOUNTY_FIELDS = ("bounty_type", "bounty_pct", "bounty_amount")
# All Django Admin edits on an existing job (spec decision 15: the bounty, as a fallback; ops
# sees the JD read-only). Everything else the company wrote stays as the company left it.
EDITABLE_ON_CHANGE = (*BOUNTY_FIELDS, "on_marketplace")


class JobAdminForm(forms.ModelForm):  # type: ignore[type-arg]
    """Sparktal sets the bounty here (spec §8, decision 15); `clean_bounty` holds the rules.

    A job still with the company (draft, waiting for its admin) is not asked for a bounty:
    Sparktal prices it once it is posted. But whatever bounty is entered, on any status, is
    checked and saved as `clean_bounty` normalises it, so a value out of range cannot wait on a
    draft and go live later (nothing downstream re-checks the range).
    """

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        entered = any(cleaned.get(field) not in (None, "") for field in BOUNTY_FIELDS)
        if self.instance.status in PRIVATE_STATUSES and not entered:
            return cleaned
        if any(field in self.errors for field in BOUNTY_FIELDS):
            return cleaned  # a field already failed its own check; one message is enough
        try:
            cleaned.update(
                clean_bounty(
                    bounty_type=cleaned.get("bounty_type"),
                    bounty_pct=cleaned.get("bounty_pct"),
                    bounty_amount=cleaned.get("bounty_amount"),
                )
            )
        except ValidationError as error:
            detail = error.detail
            if not isinstance(detail, dict):  # `clean_bounty` always names the field
                raise
            for field, problems in detail.items():
                self.add_error(field, [str(problem) for problem in problems])
        return cleaned


class JobInviteInline(admin.TabularInline):  # type: ignore[type-arg]
    """What was offered to whom; invites are made and answered through `distribution.py`."""

    model = JobInvite
    fields = ("recruiter", "status", "created_at", "responded_at", "decline_reason")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: Job | None = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Job | None = None) -> bool:
        return False


@admin.register(Job)
class JobAdmin(SimpleHistoryAdmin):  # type: ignore[misc]
    form = JobAdminForm
    list_display = (
        "title",
        "company",
        "team",
        "function",
        "status",
        "on_marketplace",
        "created_at",
        "published_at",
        "deleted_at",
    )
    list_filter = ("status", "on_marketplace", "function", "company", "team", "deleted_at")
    search_fields = ("title", "company__name")
    autocomplete_fields = ("company", "team", "created_by", "office")
    # Status and deletion change only through the actions below (spec §5).
    readonly_fields = (
        "share_token",
        "status",
        "deleted_at",
        "created_at",
        "updated_at",
        "posted_at",
        "published_at",
        "closed_at",
    )
    inlines = (JobInviteInline,)
    actions = ("distribute_to_marketplace", "restore_jobs")

    def get_readonly_fields(self, request: HttpRequest, obj: Job | None = None) -> tuple[str, ...]:
        """On an existing job, only the bounty and the channel can be edited.

        The form saves the whole row, so a content field left editable lets a stale form revert
        what the company changed in between (the title, the brief). The add view keeps them all:
        a job made here has no company edit to protect and needs them to exist at all.
        """
        readonly = tuple(super().get_readonly_fields(request, obj))
        if obj is None:
            return readonly
        content = (
            field.name
            for field in self.model._meta.fields
            if field.editable and field.name not in (*EDITABLE_ON_CHANGE, *readonly)
        )
        return (*readonly, *content)

    @admin.action(description="Distribute selected jobs to the marketplace", permissions=["change"])
    def distribute_to_marketplace(self, request: HttpRequest, queryset: QuerySet[Job]) -> None:
        """Stop-gap until the Sparktal queue page: marketplace only, the bounty set on the job."""
        assert isinstance(request.user, User)
        distributed = 0
        problems: list[str] = []
        for job in queryset:
            try:
                distribute_job(job, actor=request.user, on_marketplace=True, recruiters=[])
            except DistributionError as error:  # not pending any more, or no bounty yet
                problems.append(f"{job.title}: {error.detail}")
            else:
                distributed += 1
        if distributed:
            self.message_user(
                request, f"Distributed {distributed} job(s) to the marketplace.", messages.SUCCESS
            )
        if problems:
            self.message_user(request, "Not distributed. " + " ".join(problems), messages.ERROR)

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


@admin.register(JobInvite)
class JobInviteAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("job", "recruiter", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("job__title", "recruiter__nickname")
    list_select_related = ("job", "recruiter")

    def get_readonly_fields(self, request: HttpRequest, obj: JobInvite | None = None) -> list[str]:
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: JobInvite | None = None) -> bool:
        return False

    # Delete stays at its default on purpose: Django checks it on every related object, so
    # refusing it here would block deleting a job that has invites.


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
