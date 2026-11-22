from django.contrib import admin
from django.http import HttpRequest

from apps.chat.models import Channel, ChannelRead, Message


class MessageInline(admin.TabularInline):  # type: ignore[type-arg]
    model = Message
    extra = 0
    fields = ("kind", "author", "body", "attachment_name", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("job", "recruiter", "application", "last_message_at", "created_at")
    list_filter = ("job__company",)
    list_select_related = (
        "job__company",
        "recruiter",
        "application__candidate",
        "application__job",
    )
    search_fields = ("job__title", "recruiter__nickname")
    # Nothing in the database ties a thread to its application's job and headhunter, and the
    # access rules trust them, so they are not editable here.
    readonly_fields = ("job", "recruiter", "application")
    inlines = (MessageInline,)

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Threads are made by the signals; an add page cannot fill the read-only fields."""
        return False


@admin.register(ChannelRead)
class ChannelReadAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("channel", "user", "last_read_at")
