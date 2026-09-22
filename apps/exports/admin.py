from django.contrib import admin

from apps.exports.models import CvExport


@admin.register(CvExport)
class CvExportAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "id",
        "job",
        "requested_by",
        "status",
        "file_count",
        "created_at",
        "expires_at",
    )
    list_filter = ("status",)
    search_fields = ("job__title", "requested_by__email")
    readonly_fields = tuple(field.name for field in CvExport._meta.fields)

    def has_add_permission(self, request: object) -> bool:
        return False
