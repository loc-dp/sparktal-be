from typing import Any

from channels.generic.websocket import AsyncJsonWebsocketConsumer

UNAUTHENTICATED_CLOSE_CODE = 4401


class EventsConsumer(AsyncJsonWebsocketConsumer):  # type: ignore[misc]
    group: str | None = None

    async def connect(self) -> None:
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=UNAUTHENTICATED_CLOSE_CODE)
            return
        self.group = f"user_{user.pk.hex}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code: int) -> None:
        if self.group is not None:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content: Any, **kwargs: Any) -> None:
        if isinstance(content, dict) and content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def event_push(self, event: dict[str, Any]) -> None:
        await self.send_json(event["payload"])
