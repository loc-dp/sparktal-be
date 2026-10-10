from django.apps import AppConfig


class ExportsConfig(AppConfig):
    name = "apps.exports"
    label = "exports"

    def ready(self) -> None:
        from apps.core.files import delete_files_with_rows
        from apps.exports.models import CvExport

        delete_files_with_rows(CvExport, "file")
