from django.urls import path

from .views import (
    bulk_delete_channels,
    bulk_delete_kiotviet_channels,
    channel_assignment_detail,
    channel_assignments,
    channel_detail,
    channel_list_for_kiotviet,
    channel_type_detail,
    channel_types_collection,
    channels_collection,
    kiotviet_channel_detail,
    kiotviet_channels_collection,
    my_channel_assignments,
    staff_collection,
)

urlpatterns = [
    path("", channels_collection, name="channels-collection"),
    path("bulk-delete", bulk_delete_channels, name="channels-bulk-delete"),
    path("kiotviet/bulk-delete", bulk_delete_kiotviet_channels, name="kiotviet-channels-bulk-delete"),
    path("kiotviet", kiotviet_channels_collection, name="kiotviet-channels-collection"),
    path("kiotviet/<int:channel_id>", kiotviet_channel_detail, name="kiotviet-channels-detail"),
    path("staff", staff_collection, name="channels-staff"),
    path("types", channel_types_collection, name="channel-types"),
    path("types/<int:channel_type_id>", channel_type_detail, name="channel-types-detail"),
    path("<int:channel_id>/assignments", channel_assignments, name="channels-assignments"),
    path("<int:channel_id>/assignments/<int:user_id>", channel_assignment_detail, name="channels-assignment-detail"),
    path("me/assignments", my_channel_assignments, name="channels-my-assignments"),
    path("list-for-kiotviet", channel_list_for_kiotviet, name="channels-list-for-kiotviet"),
    path("<int:channel_id>", channel_detail, name="channels-detail"),
]
