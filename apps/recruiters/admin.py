from django.contrib import admin

from apps.recruiters.models import RecruiterProfile


@admin.register(RecruiterProfile)
class RecruiterProfileAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("nickname", "user", "created_at")
    search_fields = ("nickname", "user__email")
    autocomplete_fields = ("user",)
