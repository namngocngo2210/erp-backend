"""Facade module to preserve public imports while delegating to layered implementation."""

from .transport.views.channel_views_impl import *  # noqa: F401,F403
