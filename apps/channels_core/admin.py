from django.contrib import admin

from .models import Channel, ChannelApiToken, ChannelType, UserChannel

admin.site.register(Channel)
admin.site.register(ChannelType)
admin.site.register(UserChannel)
admin.site.register(ChannelApiToken)
