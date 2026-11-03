from django.urls import path

from apps.ai_mart.transport.hermes_chat_views import (
    hermes_conversation_messages,
    hermes_conversations,
)

# Kênh máy-tới-máy cho agent phân tích ngoài (Hermes) — token tĩnh, không JWT CRM.
# Chỉ đọc mart `ai` (đã che PII trừ nội dung thô); xem docs/hermes-chat-api.md.
urlpatterns = [
    path("hermes/conversations", hermes_conversations, name="ai-hermes-conversations"),
    path(
        "hermes/conversations/<int:conversation_id>/messages",
        hermes_conversation_messages,
        name="ai-hermes-conversation-messages",
    ),
]
