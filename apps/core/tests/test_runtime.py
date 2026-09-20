import pytest
from channels.routing import ProtocolTypeRouter
from django.core.management import call_command

from apps.core.tasks import ping


def test_celery_app_is_configured() -> None:
    from config.celery import app

    assert app.main == "sparktal"
    assert app.conf.task_always_eager is True  # test settings


def test_ping_task_runs_eagerly() -> None:
    assert ping.delay().get() == "pong"


def test_asgi_application_handles_http_and_websocket() -> None:
    from config.asgi import application

    assert isinstance(application, ProtocolTypeRouter)
    assert {"http", "websocket"} <= set(application.application_mapping)


@pytest.mark.django_db
def test_spectacular_schema_generates_without_errors(capsys: pytest.CaptureFixture[str]) -> None:
    call_command("spectacular", "--validate", "--fail-on-warn")

    schema = capsys.readouterr().out
    assert "/api/v1/health" in schema
    assert "operationId: health_retrieve" in schema
