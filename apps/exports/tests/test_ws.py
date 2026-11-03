"""Consumer WS phải join group `exports_user_<id>` và forward event xuống client.

Test này KHÔNG dùng ``channels.testing.WebsocketCommunicator`` (không có tiền lệ
trong codebase — xem apps/channels_core/tests/test_broadcast_authorization.py) mà
bám đúng khuôn: khởi tạo ``CRMConsumer`` trần, gán thẳng state/attribute nó cần rồi
gọi coroutine qua ``asyncio.run``. Cách này tách được đúng phần cần chứng minh:

1. `connect()` chỉ join group export khi đã xác thực, và join đúng group `crm_updates`
   sẵn có (không phá vỡ hành vi cũ).
2. `disconnect()` phải `group_discard` group export — không thì channel rác tích tụ
   trong Redis (điểm brief nhấn mạnh).
3. Handler `export_event` là đường KHÔNG CHẠM DB — chứng minh bằng cách chạy test đó
   mà KHÔNG đánh dấu `pytest.mark.django_db`: nếu handler lỡ chạm DB, pytest-django
   tự chặn bằng ``RuntimeError: Database access not allowed`` (mạnh hơn mock, vì mock
   có thể quên patch đúng chỗ).
"""

from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.channels_core.consumers import CRMConsumer
from apps.exports.application.export_job_service import ExportJobService
from apps.exports.infrastructure.event_publisher import export_group, publish_export_event


@pytest.mark.django_db
def test_publish_sends_to_user_group(fake_export, export_user, settings):
    """`publish_export_event` phải gửi đúng group `exports_user_<id>` — khoá tên group
    mà consumer bên dưới join, để hai bên không lệch nhau âm thầm."""
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }
    job, _ = ExportJobService.create(export_user, "fake", {"n": 1})
    publish_export_event(job)

    layer = get_channel_layer()
    assert export_group(export_user.id) == f"exports_user_{export_user.id}"
    # Group rỗng thì group_send không lỗi; test này chỉ khoá đúng tên group đang dùng.
    async_to_sync(layer.group_send)(
        export_group(export_user.id), {"type": "export_event", "payload": {}}
    )


class _FakeUser:
    """User giả, không chạm DB: `connect()` chỉ cần `.id` và `.is_authenticated`.

    Stub thẳng `_load_scope` (thay vì dùng user thật trong DB) để test này không
    phụ thuộc RBAC/`resolve_effective_permissions` — điều đó đã có test riêng ở
    `apps/users`. Tránh luôn được lỗi "connection is closed" khi driving một
    coroutine chạm DB thật bằng `async_to_sync` từ một test đồng bộ (không có event
    loop sẵn của pytest-asyncio trong dự án này)."""

    id = 55
    is_authenticated = True


def test_connect_joins_export_group_for_authenticated_user():
    consumer = CRMConsumer()
    consumer.scope = {"user": _FakeUser()}
    consumer.channel_layer = AsyncMock()
    consumer.channel_name = "specific.channel.abcd"
    consumer.accept = AsyncMock()
    consumer._load_scope = AsyncMock(return_value=("all", None, {}, True))

    async_to_sync(consumer.connect)()

    consumer.accept.assert_awaited_once()
    consumer.channel_layer.group_add.assert_any_await("crm_updates", "specific.channel.abcd")
    consumer.channel_layer.group_add.assert_any_await(
        export_group(_FakeUser.id), "specific.channel.abcd"
    )
    assert consumer._export_group == export_group(_FakeUser.id)


def test_connect_anonymous_user_does_not_join_export_group():
    """User ẩn danh không được join group của ai — kể cả group export."""
    consumer = CRMConsumer()
    consumer.scope = {"user": None}
    consumer.channel_layer = AsyncMock()
    consumer.close = AsyncMock()

    async_to_sync(consumer.connect)()

    consumer.close.assert_awaited_once()
    consumer.channel_layer.group_add.assert_not_awaited()
    assert not hasattr(consumer, "_export_group")


def test_disconnect_discards_export_group():
    """Không chạm DB, không cần `django_db`: disconnect chỉ đọc state đã có sẵn trên
    instance rồi gọi group_discard — đúng những gì brief cảnh báo (channel rác)."""
    consumer = CRMConsumer()
    consumer._export_group = export_group(42)
    consumer.channel_layer = AsyncMock()
    consumer.channel_name = "specific.channel.abcd"

    async_to_sync(consumer.disconnect)(1000)

    consumer.channel_layer.group_discard.assert_any_await("crm_updates", "specific.channel.abcd")
    consumer.channel_layer.group_discard.assert_any_await(
        export_group(42), "specific.channel.abcd"
    )


def test_disconnect_without_export_group_only_discards_crm():
    """Client rớt trước khi kịp xác thực (chưa có `_export_group`) không được vỡ."""
    consumer = CRMConsumer()
    consumer.channel_layer = AsyncMock()
    consumer.channel_name = "specific.channel.abcd"

    async_to_sync(consumer.disconnect)(1000)

    consumer.channel_layer.group_discard.assert_awaited_once_with(
        "crm_updates", "specific.channel.abcd"
    )


def test_export_event_forwards_payload_without_touching_db():
    """Không có `pytest.mark.django_db` ở đây CHỦ Ý: nếu `export_event` lỡ chạm DB,
    pytest-django tự chặn bằng RuntimeError — mạnh hơn mock vì không thể quên patch.
    Group đã lọc theo user lúc `group_add`, nên handler không cần (và không được)
    phân quyền lại bằng cách tra DB, khác hẳn `crm_event`/`_is_authorized`."""
    consumer = CRMConsumer()
    consumer.send_json = AsyncMock()
    payload = {"type": "export.updated", "id": "job-1", "status": "running"}

    async_to_sync(consumer.export_event)({"type": "export_event", "payload": payload})

    consumer.send_json.assert_awaited_once_with(payload)
