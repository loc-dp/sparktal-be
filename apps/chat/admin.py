from django.contrib import admin

from apps.chat.models import Channel, ChannelRead, Message


class MessageInline(admin.TabularInline):  # type: ignore[type-arg]
    model = Message
    extra = 0
    fields = ("kind", "author", "body", "attachment_name", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("job", "recruiter", "last_message_at", "created_at")
    list_filter = ("job__company",)
    search_fields = ("job__title", "recruiter__nickname")
    autocomplete_fields = ("job", "recruiter")
    inlines = (MessageInline,)


@admin.register(ChannelRead)
class ChannelReadAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("channel", "user", "last_read_at")
