from collections import deque

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
import logging

from apps.channels_core.contracts import order_broadcast_port
from apps.channels_core.groups import CRM_BROAD_GROUP, CRM_GROUP, order_channel_group
from apps.channels_core.handshake import broadcast_db_gate, handshake_db_gate
from apps.channels_core.jwt_middleware import WS_JWT_SUBPROTOCOL
from apps.common.business_constants import MARKETPLACE_ORDER_SOURCE_CODES
from apps.common.masking import CUSTOMER_PHONE_PERMISSION, mask_phone_middle
from apps.users.field_policy import drop_hidden_fields

logger = logging.getLogger(__name__)

# Cửa sổ khử trùng lặp cho crm_event (xem groups.py — giai đoạn 1 publisher bắn
# cùng event vào group kênh + group chung). Hai bản sao được group_send liền nhau
# nên bản trùng tới gần như tức thì; 256 id đủ che cả lô transfer 50 đơn × 2 bản.
_DEDUP_WINDOW = 256


class CRMConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or not getattr(user, "is_authenticated", False):
            logger.warning("WS connect rejected: unauthenticated user")
            await self.close(code=4401)
            return

        try:
            self.user_id = user.id
            # scope_kind ∈ {"all", "own", "channels"}; allowed_channel_codes chỉ dùng
            # cho "channels" (set rỗng = không thấy gì).
            # Điểm chạm DB thứ hai của bắt tay — cũng phải qua gate, xem
            # apps/channels_core/handshake.py (sự cố 2026-07-23).
            async with handshake_db_gate():
                (
                    self.scope_kind,
                    self.allowed_channel_codes,
                    self.orders_field_policy,
                    self.can_view_full_phone,
                ) = await self._load_scope(user)
            await self.channel_layer.group_add(CRM_GROUP, self.channel_name)
            # Route theo kênh (audit 2026-08-19 — WS fanout O(clients)): ngoài group
            # chung (event không-theo-kênh: restore.completed, pubsub bridge, payload
            # thiếu channel_code — và là fallback cho publisher cũ), join thêm group
            # theo scope đã nạp ở handshake (KHÔNG thêm query nào — cùng dữ liệu
            # _is_authorized đang dùng):
            #   - "channels" → một group mỗi kênh được thấy. Giai đoạn 2 publisher
            #     ngừng bắn event-có-kênh vào group chung thì client này chỉ còn
            #     nhận đúng traffic kênh của mình.
            #   - "all"/"own" → CRM_BROAD_GROUP: hai scope này phải thấy mọi kênh
            #     ("own" lọc theo staff_id, kênh không biểu diễn được "của tôi").
            #     Join từ bây giờ để giai đoạn 2 chỉ cần đổi publisher, không cần
            #     một nhịp deploy consumer nữa.
            if self.scope_kind == "channels":
                self._crm_scope_groups = [
                    order_channel_group(code)
                    for code in sorted(self.allowed_channel_codes or ())
                ]
            else:
                self._crm_scope_groups = [CRM_BROAD_GROUP]
            for group in self._crm_scope_groups:
                await self.channel_layer.group_add(group, self.channel_name)
            # Group riêng theo user cho tiến trình xuất file — payload chỉ liên quan
            # một người, không fan-out ra cả công ty như crm_updates. Chỉ join khi đã
            # xác thực (nhánh này nằm sau check is_authenticated ở đầu connect()), nên
            # user ẩn danh không bao giờ join group của ai.
            # ĐỒNG BỘ THỦ CÔNG với apps.exports.infrastructure.event_publisher.export_group():
            # không import thẳng hàm đó được vì `.importlinter` cấm chiều
            # channels_core → apps.exports (contract phân tầng đặt apps.exports Ở TRÊN
            # channels_core). Vì vậy literal format bên dưới phải tự tay giữ khớp với
            # export_group() — đổi format ở một bên thì bắt buộc đổi luôn bên kia,
            # không có gì ép kiểu bắt lỗi lúc build nếu quên.
            self._export_group = f"exports_user_{user.id}"
            await self.channel_layer.group_add(self._export_group, self.channel_name)
            # Client gửi token qua Sec-WebSocket-Protocol (["desi.jwt.v1", token]) thì
            # server PHẢI chọn lại "desi.jwt.v1" — trình duyệt đóng kết nối nếu server
            # không chọn subprotocol nào trong danh sách offer. Client cũ (?token=)
            # không offer gì → accept() trần như trước.
            offered = self.scope.get("subprotocols") or []
            await self.accept(
                subprotocol=WS_JWT_SUBPROTOCOL if WS_JWT_SUBPROTOCOL in offered else None
            )
        except Exception:
            logger.exception("WS connect failed while joining group 'crm_updates'")
            await self.close(code=1011)

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(CRM_GROUP, self.channel_name)
        # Group theo scope chỉ tồn tại khi connect() đã qua bước join (guard getattr
        # cùng lý do với _export_group bên dưới).
        for group in getattr(self, "_crm_scope_groups", ()):
            await self.channel_layer.group_discard(group, self.channel_name)
        # Không có _export_group nếu client rớt trước khi vào được nhánh join ở trên
        # (vd lỗi giữa handshake) — discard group không tồn tại là vô hại, nhưng vẫn
        # guard bằng getattr cho rõ ý và tránh AttributeError.
        export_group = getattr(self, "_export_group", "")
        if export_group:
            await self.channel_layer.group_discard(export_group, self.channel_name)

    async def export_event(self, event):
        """Forward tiến trình export xuống đúng client của chủ job.

        ĐƯỜNG KHÔNG CHẠM DB (khác `crm_event`/`_is_authorized`): group
        `exports_user_<id>` đã tự phân quyền theo user ngay từ lúc `group_add`, và
        payload do `publish_export_event` phát ra tự chứa toàn bộ dữ liệu cần hiển thị
        — không có gì để tra thêm. Consumer này chạy trên mọi client trong group (ở
        đây group chỉ có 1 user, nhưng vẫn giữ nguyên tắc sau sự cố 2026-07-29: KHÔNG
        thêm truy vấn DB vào đường xử lý event).
        """
        await self.send_json(event["payload"])

    async def crm_event(self, event):
        # Khử trùng lặp giai đoạn chuyển tiếp (xem groups.py): publisher MỚI bắn
        # event có kênh vào CẢ group kênh lẫn group chung với cùng dedup_id — bắn
        # kép để consumer CŨ (chỉ ở group chung) không mất event khi web/worker
        # restart lệch nhịp. Consumer này có thể ở cả hai group nên nhận hai bản;
        # chỉ xử lý bản tới trước. Event KHÔNG có dedup_id (publisher cũ, pubsub
        # bridge, mọi sender khác) chỉ có một bản — đi thẳng như cũ, nên không có
        # tổ hợp lệch phiên bản nào bị mất hay bị nhân đôi event.
        dedup_id = event.get("dedup_id")
        if dedup_id is not None and not self._first_delivery(dedup_id):
            return
        payload = event.get("payload", {})
        validated = order_broadcast_port().validate_event_payload(payload)
        if validated is None:
            return
        if not await self._is_authorized(validated):
            return
        # `row` là bản list serialize chung cho mọi client — strip field ẩn theo
        # field_policy của TỪNG user (nạp một lần lúc handshake, không chạm DB ở
        # đây) để row qua WS bằng đúng row user đó nhận qua REST.
        row = validated.get("row")
        policy = getattr(self, "orders_field_policy", None)
        if row and policy:
            row = drop_hidden_fields(dict(row), policy)
            validated = {**validated, "row": row}
        # Che SĐT khách của đơn KHÔNG do user này phụ trách — cùng luật với REST
        # (apps/users/phone_visibility.py). Phải làm ở ĐÂY: row được serialize MỘT lần
        # cho mọi client nên publisher giữ số thật; bỏ qua bước này là realtime thành
        # đường vòng lấy đủ SĐT của đơn sale khác, chỉ cần mở tab và chờ.
        # Quyền + user_id nạp một lần lúc handshake → không chạm DB per event
        # (nguyên tắc sự cố 2026-07-29).
        if row and not getattr(self, "can_view_full_phone", False):
            validated = {
                **validated,
                "row": self._mask_row_phone(row, validated.get("staff_id")),
            }
        await self.send_json(validated)

    def _mask_row_phone(self, row: dict, event_staff_id=None) -> dict:
        """Bản sao `row` với SĐT đã che, trừ đơn của chính user đang kết nối.

        `staff_id` đọc từ row, fallback sang field cùng tên của EVENT: field_policy
        có thể đã bỏ `staff_id` khỏi row ngay phía trên, và event luôn mang sẵn giá
        trị này (publisher làm giàu — xem order_event_publisher.enrich_order_event).
        Thiếu cả hai → coi như không phải đơn của mình và che (fail-closed).
        """
        staff_id = row.get("staff_id", event_staff_id)
        if staff_id is None:
            staff_id = event_staff_id
        if staff_id is not None and staff_id == getattr(self, "user_id", None):
            return row
        # Đơn sàn TMĐT miễn che: sàn đã che sẵn SĐT (xem apps/users/phone_visibility.py).
        # Hằng đọc từ apps.common vì channels_core không được import apps.orders.
        if str(row.get("order_source") or "").strip().lower() in MARKETPLACE_ORDER_SOURCE_CODES:
            return row
        masked = dict(row)
        for field in ("phone", "customer_phone"):
            if field in masked:
                masked[field] = mask_phone_middle(masked[field])
        return masked

    def _first_delivery(self, dedup_id) -> bool:
        """True nếu dedup_id này chưa từng thấy trên connection này.

        State per-connection, thuần bộ nhớ (KHÔNG chạm DB — cùng nguyên tắc sự cố
        2026-07-29): set để tra O(1) + deque(maxlen) để tự đào thải id cũ. Khởi tạo
        lười vì consumer trong test được dựng trần không qua connect().
        """
        seen = getattr(self, "_dedup_seen", None)
        if seen is None:
            seen = self._dedup_seen = set()
            self._dedup_order = deque(maxlen=_DEDUP_WINDOW)
        if dedup_id in seen:
            return False
        if len(self._dedup_order) == _DEDUP_WINDOW:
            seen.discard(self._dedup_order[0])
        self._dedup_order.append(dedup_id)
        seen.add(dedup_id)
        return True

    async def restore_completed(self, event):
        """Batch event do task restore phát ra khi xử lý xong toàn bộ ids.
        FE chỉ hiện toast nếu user_id trùng (tránh spam tất cả user)."""
        payload = event.get("payload", {}) or {}
        await self.send_json({"type": "restore.completed", **payload})

    async def _is_authorized(self, payload: dict) -> bool:
        """Phân quyền một event broadcast — ĐƯỜNG NHANH KHÔNG CHẠM DB.

        Consumer này chạy trên MỌI client trong group ``crm_updates``, nên mỗi lần
        chạm DB ở đây là một connection Postgres nhân với số tab đang mở
        (``CONN_MAX_AGE=0`` ⇒ mỗi ``database_sync_to_async`` mở connection riêng).
        Đó là nguyên nhân sự cố 2026-07-29 — xem
        docs/ws-broadcast-db-fanout-2026-07-29.md.

        Payload đã được ``order_event_publisher`` làm giàu ``staff_id`` +
        ``channel_code`` tại điểm phát, nên nhánh fallback bên dưới chỉ chạy khi có
        call site còn sót field, và khi đó bị ``broadcast_db_gate`` chặn lại.
        """
        scope_kind = getattr(self, "scope_kind", "all")
        # all / superuser → không giới hạn.
        if scope_kind == "all":
            return True

        # own → chỉ đơn do chính user phụ trách (Order.staff). Channel code không
        # biểu diễn được "của tôi" nên phải so theo chủ đơn.
        if scope_kind == "own":
            user_id = getattr(self, "user_id", None)
            staff_id = payload.get("staff_id")
            if staff_id is not None:
                return staff_id == user_id
            order_id = payload.get("order_id")
            if not isinstance(order_id, int):
                return False
            self._warn_missing_field("staff_id", payload)
            async with broadcast_db_gate():
                return await self._order_belongs_to_user(order_id, user_id)

        # channels (channel/department scope) → lọc theo tập channel code được gán.
        scope = getattr(self, "allowed_channel_codes", None) or set()
        if not scope:
            return False
        channel_code = payload.get("channel_code")
        if not channel_code:
            order_id = payload.get("order_id")
            if not isinstance(order_id, int):
                return False
            self._warn_missing_field("channel_code", payload)
            async with broadcast_db_gate():
                channel_code = await self._resolve_order_channel_code(order_id)
            if not channel_code:
                return False
        return channel_code in scope

    @staticmethod
    def _warn_missing_field(field: str, payload: dict) -> None:
        """Kêu to khi payload thiếu field phân quyền.

        Thiếu field nghĩa là có call site chưa đi qua ``publish_order_event`` hoặc
        publisher không tra được đơn — phải vá call site đó, vì mỗi event thiếu
        field kéo theo một truy vấn DB cho MỖI client đang kết nối.
        """
        logger.warning(
            "broadcast payload thiếu %s — fallback query DB; source=%s order_id=%s",
            field,
            payload.get("source"),
            payload.get("order_id"),
        )

    @staticmethod
    @database_sync_to_async
    def _load_scope(user):
        """Trả (scope_kind, channel_codes, orders_field_policy, can_view_full_phone).

        Đọc scope hiệu dụng từ resolver RBAC.

        - "all"      → superuser hoặc scope all (channel_codes=None).
        - "own"      → lọc theo Order.staff (channel_codes=None).
        - "channels" → channel/department scope (channel_codes = set, rỗng = không thấy gì).

        orders_field_policy (dict field→rule của section "orders") nạp MỘT lần tại
        handshake — crm_event dùng để strip `row` per user mà không chạm DB per event
        (cùng nguyên tắc sự cố 2026-07-29). Resolver có cache nên chi phí nằm trong
        cùng lần tra RBAC của handshake.
        """
        from apps.users.rbac import resolve_effective_permissions
        from apps.users.scoping import OWN_SCOPE_SENTINEL, resolved_channel_codes

        eff = resolve_effective_permissions(user)
        orders_policy = (
            {} if eff.is_superuser else dict((eff.field_policy or {}).get("orders") or {})
        )
        # Xem đầy đủ SĐT khách trên đơn của người khác — cùng quyền với REST, nạp ở
        # handshake để `crm_event` không phải hỏi resolver mỗi event.
        can_view_full_phone = eff.can(CUSTOMER_PHONE_PERMISSION)

        result = resolved_channel_codes(user, "orders")
        if result is OWN_SCOPE_SENTINEL:
            return "own", None, orders_policy, can_view_full_phone
        if result is None:
            return "all", None, orders_policy, can_view_full_phone
        return "channels", result, orders_policy, can_view_full_phone

    # Hai method dưới giữ NGUYÊN tên: `test_broadcast_authorization` subclass
    # CRMConsumer và override chúng để đếm số lần chạm DB. Chỉ phần ruột đổi sang
    # gọi port — không còn import model của orders.
    @database_sync_to_async
    def _order_belongs_to_user(self, order_id: int, user_id) -> bool:
        return order_broadcast_port().order_belongs_to_user(order_id, user_id)

    @database_sync_to_async
    def _resolve_order_channel_code(self, order_id: int) -> str | None:
        return order_broadcast_port().resolve_order_channel_code(order_id)
