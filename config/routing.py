"""WebSocket routes. Plan 07 appends `path("ws/events/", EventsConsumer.as_asgi())`."""

from typing import Any

websocket_urlpatterns: list[Any] = []
