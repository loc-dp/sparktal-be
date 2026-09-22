from typing import Any

from django.urls import path

from apps.chat.consumers import EventsConsumer

websocket_urlpatterns: list[Any] = [
    path("ws/events/", EventsConsumer.as_asgi()),
]
