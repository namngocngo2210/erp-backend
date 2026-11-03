"""Fake dùng chung cho test facebook_ads.

`_FakeResponse` từng bị copy y hệt trong 3 file (batch errors, token
classification, token lifecycle) — gom về đây theo tái cấu trúc 2026-08-05.
"""

from __future__ import annotations

import json


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload
