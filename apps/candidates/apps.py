from django.apps import AppConfig


class CandidatesConfig(AppConfig):
    name = "apps.candidates"
    label = "candidates"

    def ready(self) -> None:
        from apps.candidates.models import Application, Candidate, CvParse
        from apps.core.files import delete_files_with_rows

        delete_files_with_rows(Candidate, "cv_file")
        delete_files_with_rows(Application, "cv_file")
        delete_files_with_rows(CvParse, "file")
