from django.apps import AppConfig


class ChatConfig(AppConfig):
    name = "apps.chat"
    label = "chat"

    def ready(self) -> None:
        from apps.chat import signals  # noqa: F401
        from apps.chat.models import Message
        from apps.core.files import delete_files_with_rows

        delete_files_with_rows(Message, "attachment")
