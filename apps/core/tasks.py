from celery import shared_task


@shared_task
def ping() -> str:
    """Smoke task used to prove the Celery wiring (eager in tests)."""
    return "pong"
