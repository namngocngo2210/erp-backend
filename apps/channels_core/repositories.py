"""Facade module to preserve public imports while delegating to layered implementation."""

from .infrastructure.repositories.channel_repositories_impl import *  # noqa: F401,F403
