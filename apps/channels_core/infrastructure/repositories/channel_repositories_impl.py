from __future__ import annotations

from typing import TYPE_CHECKING

from django.apps import apps as django_apps
from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from apps.users.constants import DepartmentID
from apps.users.department_membership import department_member_q
from apps.users.models import User
from apps.users.rbac import resolve_effective_permissions

from apps.channels_core.models import (
    Channel,
    ChannelAdAccount,
    ChannelKiotvietMapping,
    ChannelType,
    UserChannel,
)

if TYPE_CHECKING:
    from apps.kiotviet.models import KiotVietChannel


def _kiotviet_channel_model():
    # Lazy get_model: cắt phụ thuộc import-time channels_core → kiotviet.
    # TODO(kiến trúc): thay bằng registry pattern ở sprint tách module sau.
    return django_apps.get_model("kiotviet", "KiotVietChannel")

_UNSET = object()


def _normalize_mappings(raw_mappings) -> list[dict]:
    if not raw_mappings:
        return []
    normalized: list[dict] = []
    seen: set[int] = set()
    for entry in raw_mappings:
        if not isinstance(entry, dict):
            continue
        try:
            kv_id = int(entry.get("kiotviet_channel_id"))
        except (TypeError, ValueError):
            continue
        if kv_id in seen:
            continue
        ext = str(entry.get("external_sale_channel_id") or "").strip()
        if not ext:
            continue
        seen.add(kv_id)
        normalized.append({"kiotviet_channel_id": kv_id, "external_sale_channel_id": ext})
    return normalized


class ChannelRepository:
    @staticmethod
    def list_for_user(user, *, limit: int, offset: int):
        queryset = Channel.objects.filter(deleted_at__isnull=True).select_related(
            "price_book", "channel_type"
        ).prefetch_related(
            "departments",
            "ad_account_links",
            Prefetch(
                "user_links",
                queryset=UserChannel.objects.select_related("user")
                .filter(
                    is_active=True,
                    user__is_active=True,
                    user__deleted_at__isnull=True,
                )
                # NOTE: bỏ filter role=STAFF cũ; UserChannel chỉ gán cho user phòng Ads
                # (xem channel_assignments view), nên không cần loại admin ở đây.
                .exclude(user__role_assignments__role__is_system=True)
                .order_by("user__username")
                .distinct(),
                to_attr="prefetched_user_links",
            ),
            Prefetch(
                "kiotviet_mappings",
                queryset=ChannelKiotvietMapping.objects.select_related("kiotviet_channel").order_by(
                    "kiotviet_channel__name", "kiotviet_channel_id"
                ),
                to_attr="prefetched_kiotviet_mappings",
            ),
        ).order_by("code")
        if resolve_effective_permissions(user).is_superuser:
            return queryset[offset : offset + limit]
        department_id = getattr(user, "department_id", None)
        # ADS giữ per-user qua UserChannel (không đổi).
        if department_id == DepartmentID.ADS:
            return queryset.filter(
                user_links__user=user, user_links__is_active=True
            ).distinct()[offset : offset + limit]
        # User chưa gán phòng ban: parity với hành vi cũ (thấy tất cả).
        if department_id is None:
            return queryset[offset : offset + limit]
        # Các phòng còn lại: kênh thuộc phòng ban mình + kênh dùng chung (M2M rỗng).
        return queryset.filter(
            Q(departments=department_id) | Q(departments__isnull=True)
        ).distinct()[offset : offset + limit]

    @staticmethod
    def create_channel(
        *,
        code: str,
        name: str,
        owner: str,
        landingpage_source: list[str],
        is_active: bool,
        fb_ads_accounts: list[int] | None = None,
        google_ads_accounts: list[int] | None = None,
        kiotviet_mappings: list[dict] | None = None,
        departments=None,
        channel_type: ChannelType | None = None,
    ) -> Channel:
        fb_ads_accounts = fb_ads_accounts or []
        google_ads_accounts = google_ads_accounts or []
        channel, created = Channel.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "owner": owner,
                "landingpage_source": landingpage_source,
                "is_active": is_active,
                "channel_type": channel_type,
            },
        )
        if not created:
            channel.name = name
            channel.owner = owner
            channel.landingpage_source = landingpage_source
            channel.is_active = is_active
            channel.channel_type = channel_type
            channel.deleted_at = None
            channel.save(
                update_fields=[
                    "name",
                    "owner",
                    "landingpage_source",
                    "is_active",
                    "channel_type",
                    "deleted_at",
                ]
            )
        ChannelRepository.replace_ad_accounts(
            channel, ChannelAdAccount.Provider.FACEBOOK, fb_ads_accounts
        )
        ChannelRepository.replace_ad_accounts(
            channel, ChannelAdAccount.Provider.GOOGLE, google_ads_accounts
        )
        if departments is not None:
            channel.departments.set(departments)
        if kiotviet_mappings is not None:
            ChannelRepository.replace_kiotviet_mappings(channel, kiotviet_mappings)
        return channel

    @staticmethod
    def update_channel(
        channel: Channel,
        *,
        name: str | None = None,
        owner: str | None = None,
        landingpage_source: list[str] | None = None,
        fb_ads_accounts: list[int] | None = None,
        google_ads_accounts: list[int] | None = None,
        is_active: bool | None = None,
        kiotviet_mappings: list[dict] | None = None,
        departments=_UNSET,
        price_book=_UNSET,
        channel_type=_UNSET,
    ) -> Channel:
        if name is not None:
            channel.name = name
        if owner is not None:
            channel.owner = owner
        if landingpage_source is not None:
            channel.landingpage_source = landingpage_source
        if is_active is not None:
            channel.is_active = is_active
        update_fields = [
            "name",
            "owner",
            "landingpage_source",
            "is_active",
        ]
        # price_book dùng sentinel _UNSET (giống departments) vì None là giá trị hợp
        # lệ (gỡ gán bảng giá) — khác với "field không được gửi lên".
        if price_book is not _UNSET:
            channel.price_book = price_book
            update_fields.append("price_book")
        # Cùng lý do sentinel như price_book: None = "bỏ phân loại" (giá trị hợp
        # lệ), khác hẳn với "client không gửi field này lên".
        if channel_type is not _UNSET:
            channel.channel_type = channel_type
            update_fields.append("channel_type")
        channel.save(update_fields=update_fields)
        if fb_ads_accounts is not None:
            ChannelRepository.replace_ad_accounts(
                channel, ChannelAdAccount.Provider.FACEBOOK, fb_ads_accounts
            )
        if google_ads_accounts is not None:
            ChannelRepository.replace_ad_accounts(
                channel, ChannelAdAccount.Provider.GOOGLE, google_ads_accounts
            )
        if departments is not _UNSET:
            channel.departments.set(departments)
        if kiotviet_mappings is not None:
            ChannelRepository.replace_kiotviet_mappings(channel, kiotviet_mappings)
        return channel

    @staticmethod
    def replace_ad_accounts(channel: Channel, provider: str, account_ids: list[int]) -> None:
        """Full-replace mapping ad account của 1 provider (semantics ghi đè như ArrayField cũ).

        ``position`` giữ đúng thứ tự client gửi lên để list id round-trip y hệt.
        """
        ChannelAdAccount.objects.filter(channel=channel, provider=provider).delete()
        ChannelAdAccount.objects.bulk_create(
            ChannelAdAccount(
                channel=channel, provider=provider, account_id=account_id, position=position
            )
            # dict.fromkeys: bỏ trùng nhưng giữ thứ tự (serializer đã dedup, đây là
            # lớp phòng thủ cho caller nội bộ để không vướng unique constraint).
            for position, account_id in enumerate(dict.fromkeys(account_ids or []))
        )

    @staticmethod
    def replace_kiotviet_mappings(channel: Channel, raw_mappings: list[dict]) -> None:
        normalized = _normalize_mappings(raw_mappings)
        desired_kv_ids = {entry["kiotviet_channel_id"] for entry in normalized}
        ChannelKiotvietMapping.objects.filter(channel=channel).exclude(
            kiotviet_channel_id__in=desired_kv_ids
        ).delete()
        for entry in normalized:
            ChannelKiotvietMapping.objects.update_or_create(
                channel=channel,
                kiotviet_channel_id=entry["kiotviet_channel_id"],
                defaults={"external_sale_channel_id": entry["external_sale_channel_id"]},
            )

    @staticmethod
    def list_kiotviet_mappings(channel: Channel):
        if hasattr(channel, "prefetched_kiotviet_mappings"):
            return channel.prefetched_kiotviet_mappings
        return list(
            ChannelKiotvietMapping.objects
            .filter(channel=channel)
            .select_related("kiotviet_channel")
            .order_by("kiotviet_channel__name", "kiotviet_channel_id")
        )

    @staticmethod
    def list_channel_types(*, include_inactive: bool = False):
        """Loại kênh theo thứ tự Meta.ordering (sort_order, id).

        ``include_inactive`` chỉ dành cho màn quản lý danh mục; dropdown chọn loại
        kênh vẫn chỉ nhận loại đang bật. ``channel_count`` annotate sẵn để
        serializer không bắn thêm một query mỗi dòng.
        """
        queryset = ChannelType.objects.annotate(
            channel_count_annotated=Count(
                "channels", filter=Q(channels__deleted_at__isnull=True), distinct=True
            )
        )
        if include_inactive:
            return queryset
        return queryset.filter(is_active=True)

    @staticmethod
    def get_channel(channel_id: int) -> Channel:
        return Channel.objects.get(id=channel_id, deleted_at__isnull=True)

    @staticmethod
    def assigned_user_count(channel: Channel) -> int:
        if hasattr(channel, "prefetched_user_links"):
            return len(channel.prefetched_user_links)
        return UserChannel.objects.filter(channel=channel, is_active=True).count()

    @staticmethod
    def list_staff_users(*, department=None):
        # NOTE: thay filter role=STAFF cũ bằng "loại user có role hệ thống" (admin).
        queryset = User.objects.filter(
            is_active=True, deleted_at__isnull=True
        ).exclude(role_assignments__role__is_system=True)
        if department is not None:
            # Thuộc phòng theo phòng chính HOẶC phòng ban nhiều (NV kiêm vai trò).
            queryset = queryset.filter(department_member_q(department))
        return queryset.order_by("username").distinct()

    @staticmethod
    def list_assigned_staff(channel: Channel):
        # NOTE: thay filter role=STAFF cũ bằng "loại user có role hệ thống" (admin).
        return (
            User.objects.filter(
                channel_links__channel=channel,
                channel_links__is_active=True,
                is_active=True,
                deleted_at__isnull=True,
            )
            .exclude(role_assignments__role__is_system=True)
            .order_by("username")
            .distinct()
        )

    @staticmethod
    def add_staff_to_channel(*, channel: Channel, user: User) -> None:
        link, created = UserChannel.objects.get_or_create(
            channel=channel,
            user=user,
            defaults={"is_active": True, "deleted_at": None},
        )
        if not created and (not link.is_active or link.deleted_at is not None):
            link.is_active = True
            link.deleted_at = None
            link.save(update_fields=["is_active", "deleted_at"])

    @staticmethod
    def remove_staff_from_channel(*, channel: Channel, user: User) -> None:
        UserChannel.objects.filter(channel=channel, user=user, is_active=True).update(
            is_active=False,
            deleted_at=timezone.now(),
        )

    @staticmethod
    def soft_delete_channel(channel: Channel) -> None:
        channel.deleted_at = timezone.now()
        channel.is_active = False
        channel.save(update_fields=["deleted_at", "is_active"])

    @staticmethod
    def soft_delete_channels_by_ids(channel_ids: list[int]) -> int:
        if not channel_ids:
            return 0
        return Channel.objects.filter(id__in=channel_ids, deleted_at__isnull=True).update(
            deleted_at=timezone.now(),
            is_active=False,
        )

    @staticmethod
    def list_kiotviet_channels(*, limit: int, offset: int):
        return _kiotviet_channel_model().objects.filter(deleted_at__isnull=True).order_by("created_at", "id")[offset : offset + limit]

    @staticmethod
    def create_kiotviet_channel(
        *,
        name: str,
        branch_id: str,
        branch_ids: list[str],
        old_sync_branch_id: str | None,
        client_id: str,
        client_secret: str,
        retailer: str,
        branch_names: list[str] | None = None,
        old_sync_branch_map: list[dict] | None = None,
        username: str = "",
        password: str = "",
        old: bool = False,
        vat_tax_id: int | None = None,
        paired_old_channel: KiotVietChannel | None = None,
    ) -> KiotVietChannel:
        return _kiotviet_channel_model().objects.create(
            name=name,
            branch_id=branch_id,
            branch_ids=branch_ids,
            branch_names=branch_names or [],
            old_sync_branch_id=old_sync_branch_id,
            old_sync_branch_map=old_sync_branch_map or [],
            client_id=client_id,
            client_secret=client_secret,
            retailer=retailer,
            username=username,
            password=password,
            old=old,
            vat_tax_id=vat_tax_id,
            paired_old_channel=paired_old_channel,
        )

    @staticmethod
    def update_kiotviet_channel(
        channel: KiotVietChannel,
        *,
        name: str | None = None,
        branch_id: str | None = None,
        branch_ids: list[str] | None = None,
        branch_names: list[str] | None = None,
        old_sync_branch_id: str | None = None,
        old_sync_branch_map: list[dict] | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        retailer: str | None = None,
        username: str | None = None,
        password: str | None = None,
        old: bool | None = None,
        vat_tax_id=_UNSET,
        paired_old_channel=_UNSET,
    ) -> KiotVietChannel:
        if name is not None:
            channel.name = name
        if branch_id is not None:
            channel.branch_id = branch_id
        if branch_ids is not None:
            channel.branch_ids = branch_ids
        if branch_names is not None:
            channel.branch_names = branch_names
        if old_sync_branch_id is not None:
            channel.old_sync_branch_id = old_sync_branch_id
        if old_sync_branch_map is not None:
            channel.old_sync_branch_map = old_sync_branch_map
        if client_id is not None:
            channel.client_id = client_id
        if client_secret is not None:
            channel.client_secret = client_secret
        if retailer is not None:
            channel.retailer = retailer
        if username is not None:
            channel.username = username
        if password is not None:
            channel.password = password
        if old is not None:
            channel.old = old
        if vat_tax_id is not _UNSET:
            channel.vat_tax_id = vat_tax_id
        if paired_old_channel is not _UNSET:
            channel.paired_old_channel = paired_old_channel
        channel.save(
            update_fields=[
                "name",
                "branch_id",
                "branch_ids",
                "branch_names",
                "old_sync_branch_id",
                "old_sync_branch_map",
                "client_id",
                "client_secret",
                "retailer",
                "username",
                "password",
                "old",
                "vat_tax_id",
                "paired_old_channel",
                "updated_at",
            ]
        )
        return channel

    @staticmethod
    def soft_delete_kiotviet_channel(channel: KiotVietChannel) -> None:
        channel.deleted_at = timezone.now()
        channel.save(update_fields=["deleted_at", "updated_at"])

    @staticmethod
    def soft_delete_kiotviet_channels_by_ids(channel_ids: list[int]) -> int:
        if not channel_ids:
            return 0
        now = timezone.now()
        return _kiotviet_channel_model().objects.filter(id__in=channel_ids, deleted_at__isnull=True).update(deleted_at=now, updated_at=now)
