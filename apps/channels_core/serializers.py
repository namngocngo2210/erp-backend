"""Facade module to preserve public imports while delegating to layered implementation."""

from .transport.serializers.channel_serializers_impl import *  # noqa: F401,F403
