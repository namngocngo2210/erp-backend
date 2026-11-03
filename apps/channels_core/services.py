from apps.users.rbac import resolve_effective_permissions

from .repositories import ChannelRepository


class ChannelService:
    @staticmethod
    def can_manage(user) -> bool:
        return resolve_effective_permissions(user).is_superuser

    @staticmethod
    def list_channels(user, *, limit: int, offset: int):
        return ChannelRepository.list_for_user(user, limit=limit, offset=offset)
