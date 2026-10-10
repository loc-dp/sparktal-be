from django.apps import AppConfig


class JobsConfig(AppConfig):
    name = "apps.jobs"
    label = "jobs"

    def ready(self) -> None:
        from apps.core.files import delete_files_with_rows
        from apps.jobs.models import JdParse

        delete_files_with_rows(JdParse, "file")
