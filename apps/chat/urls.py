from django.urls import path

from apps.chat.api import (
    ChannelListView,
    ChannelMessagesView,
    ChannelReadView,
    MessageAttachmentView,
)

urlpatterns = [
    path("chat/channels", ChannelListView.as_view(), name="chat-channels"),
    path(
        "chat/channels/<uuid:pk>/messages",
        ChannelMessagesView.as_view(),
        name="chat-messages",
    ),
    path(
        "chat/channels/<uuid:pk>/read",
        ChannelReadView.as_view(),
        name="chat-read",
    ),
    path(
        "chat/messages/<uuid:pk>/attachment",
        MessageAttachmentView.as_view(),
        name="chat-attachment",
    ),
]
