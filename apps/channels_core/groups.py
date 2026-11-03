"""Tên group WebSocket dùng chung giữa consumer (channels_core) và các publisher.

Đặt ở ``channels_core`` — tầng DƯỚI ``orders`` theo contract ``.importlinter`` —
để publisher phía orders import XUỐNG là đúng chiều phân tầng. Đây là điểm khác
với ``export_group()``: ``apps.exports`` nằm TRÊN channels_core nên
``consumers.py`` phải ghép literal bằng tay (xem comment trong ``connect()``);
group theo kênh thì cả hai phía đều import được hằng này, đổi format một chỗ là
mọi chỗ cùng đổi.

Lộ trình route theo kênh (audit performance 2026-08-19, mục WS fanout O(clients)):

- GIAI ĐOẠN 1 (hiện tại): consumer join ``CRM_GROUP`` + group theo scope
  (``crm_updates.ch.<code>`` cho scope "channels", ``CRM_BROAD_GROUP`` cho
  "all"/"own"). Publisher bắn event CÓ kênh vào group kênh VÀ ``CRM_GROUP``
  (cùng ``dedup_id``) — consumer CŨ chỉ ở ``CRM_GROUP`` nên không mất event khi
  web/worker restart lệch nhịp; consumer MỚI khử bản trùng bằng ``dedup_id``.
- GIAI ĐOẠN 2 (sau khi MỌI web đã chạy consumer mới): publisher đổi bản sao
  diện rộng từ ``CRM_GROUP`` sang ``CRM_BROAD_GROUP`` (một hằng trong
  ``order_event_publisher``). Từ đó sale scope "channels" không còn nhận
  traffic kênh ngoài phạm vi — fanout theo audience thay vì O(mọi client).
"""

from __future__ import annotations

import re

# Group chung: MỌI client join. Nhận event không gắn kênh (restore.completed,
# payload thiếu channel_code), event từ pubsub bridge (Go services), và — trong
# giai đoạn 1 — bản sao event có kênh để consumer cũ không mất event.
CRM_GROUP = "crm_updates"

# Group "diện rộng": consumer scope all/own join. Hai scope này phải thấy đơn của
# MỌI kênh ("own" lọc theo staff_id — channel không biểu diễn được "của tôi"),
# nên không join group kênh nào mà nhận qua group này ở giai đoạn 2.
CRM_BROAD_GROUP = "crm_updates.broad"

# Channels chỉ chấp nhận group name ASCII [a-zA-Z0-9._-] và giới hạn ~100 ký tự,
# trong khi Channel.code là CharField tự do (có thể chứa khoảng trắng, ký tự Việt).
_UNSAFE_GROUP_CHARS = re.compile(r"[^a-zA-Z0-9._-]")


def order_channel_group(channel_code: str) -> str:
    """Group theo kênh — chỉ client được thấy kênh đó join.

    Sanitize là ánh xạ một chiều nên hai code khác nhau CÓ THỂ trùng group
    (hiếm — code thực tế toàn ASCII). Vô hại: consumer vẫn giữ ``_is_authorized``
    so ``channel_code`` GỐC trong payload (defense in depth), client kênh khác
    chỉ tốn một lần nhận-rồi-bỏ chứ không thấy được dữ liệu ngoài phạm vi.
    """
    safe = _UNSAFE_GROUP_CHARS.sub("_", str(channel_code))[:80]
    return f"crm_updates.ch.{safe}"
