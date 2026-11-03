from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CredentialKind = Literal["personal", "shared"]


@dataclass(frozen=True)
class Credential:
    """Danh tính dùng để nói chuyện với ads-crm cho MỘT request (spec §4.4)."""

    kind: CredentialKind
    api_key: str
    role: str | None  # chỉ có ở kind="shared"
    base_url: str
