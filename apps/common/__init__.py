"""Shared, framework-light utilities reused across apps.

Keep this package free of app-specific domain logic. It hosts cross-cutting
concerns (pagination parsing, HTTP client, base abstractions) so individual
apps don't re-implement the same plumbing.
"""
