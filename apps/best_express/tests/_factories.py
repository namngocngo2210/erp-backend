"""Factory dùng chung cho suite best_express.

Module thường, import tường minh từ từng file test — best_express chủ đích
KHÔNG có conftest.py (suite standalone). Chỉ chứa helper mà mọi call site
dùng được với hành vi y hệt; bản factory lệch default (base_url, webhook_token,
username cố định) vẫn nằm cục bộ trong file test tương ứng.
"""

import uuid

from apps.best_express.models import BestExpressAccount


def make_account(**overrides) -> BestExpressAccount:
    defaults = {
        "name": "T",
        "username": f"u-{uuid.uuid4()}",
        "password": "pw",
    }
    defaults.update(overrides)
    return BestExpressAccount.objects.create(**defaults)
