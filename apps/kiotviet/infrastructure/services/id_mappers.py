"""
Shared mapping helpers: KiotViet saleChannelId / soldById giữa các KV channel.

Sale-channel mapping được lưu trong bảng `channel_kiotviet_mappings`
(`apps.channels_core.models.ChannelKiotvietMapping`). Mỗi `Channel` có 0..N
mapping, mỗi mapping ứng với 1 `KiotVietChannel` + `external_sale_channel_id`
sử dụng bên KV đó.
"""
import logging

logger = logging.getLogger(__name__)


def _parse_int(raw_value) -> int | None:
    if raw_value in (None, ""):
        return None
    try:
        return int(str(raw_value).strip())
    except (TypeError, ValueError):
        return None


def map_sale_channel_id(
    source_sale_channel_id,
    *,
    source_kv,
    target_kv,
) -> int | None:
    """
    Map saleChannelId từ `source_kv` sang `target_kv` qua bảng
    `ChannelKiotvietMapping`:

      Tìm Channel có mapping(kv=source_kv, ext=source_sale_channel_id),
      rồi trả mapping(kv=target_kv).external_sale_channel_id (đã parse int).

    Trả về int hoặc None nếu source rỗng / không match / không có mapping target /
    giá trị không parse được số.
    """
    if source_sale_channel_id in (None, ""):
        return None
    if source_kv is None or target_kv is None:
        return None
    try:
        from apps.channels_core.models import ChannelKiotvietMapping
    except Exception:  # noqa: BLE001
        return None

    source_str = str(source_sale_channel_id).strip()
    if not source_str:
        return None

    source_mapping = (
        ChannelKiotvietMapping.objects
        .filter(kiotviet_channel=source_kv, external_sale_channel_id=source_str)
        .select_related("channel")
        .first()
    )
    if source_mapping is None:
        logger.warning(
            "Cannot map saleChannelId %s on source_kv=%s: no mapping",
            source_sale_channel_id,
            getattr(source_kv, "id", None),
        )
        return None

    target_mapping = (
        ChannelKiotvietMapping.objects
        .filter(channel=source_mapping.channel, kiotviet_channel=target_kv)
        .only("external_sale_channel_id")
        .first()
    )
    if target_mapping is None or not target_mapping.external_sale_channel_id:
        logger.warning(
            "Cannot map saleChannelId %s on target_kv=%s: no mapping for channel=%s",
            source_sale_channel_id,
            getattr(target_kv, "id", None),
            source_mapping.channel_id,
        )
        return None

    return _parse_int(target_mapping.external_sale_channel_id)


def map_kiot_user_id(
    source_kiot_user_id,
    *,
    source_kv,
    target_kv,
) -> int | None:
    """
    Map soldById từ `source_kv` sang `target_kv` qua bảng
    `UserKiotvietMapping`:

      Tìm User có mapping(kv=source_kv, ext=source_kiot_user_id),
      rồi trả mapping(kv=target_kv).external_kiot_user_id (parse int).

    Trả về int hoặc None nếu source rỗng / không match / không có mapping target /
    giá trị không parse được số.
    """
    if source_kiot_user_id in (None, ""):
        return None
    if source_kv is None or target_kv is None:
        return None
    try:
        from apps.users.models import UserKiotvietMapping
    except Exception:  # noqa: BLE001
        return None

    source_str = str(source_kiot_user_id).strip()
    if not source_str:
        return None

    source_mapping = (
        UserKiotvietMapping.objects
        .filter(
            kiotviet_channel=source_kv,
            external_kiot_user_id=source_str,
            user__deleted_at__isnull=True,
        )
        .select_related("user")
        .first()
    )
    if source_mapping is None:
        logger.warning(
            "Cannot map soldById %s on source_kv=%s: no mapping",
            source_kiot_user_id,
            getattr(source_kv, "id", None),
        )
        return None

    target_mapping = (
        UserKiotvietMapping.objects
        .filter(user=source_mapping.user, kiotviet_channel=target_kv)
        .only("external_kiot_user_id")
        .first()
    )
    if target_mapping is None or not target_mapping.external_kiot_user_id:
        logger.warning(
            "Cannot map soldById %s on target_kv=%s: no mapping for user=%s",
            source_kiot_user_id,
            getattr(target_kv, "id", None),
            source_mapping.user_id,
        )
        return None

    return _parse_int(target_mapping.external_kiot_user_id)
