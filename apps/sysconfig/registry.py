"""Registry cài đặt hệ thống — nguồn chân lý về KHÓA NÀO tồn tại.

Thêm một cài đặt mới = thêm một `Setting` vào `SETTINGS` (không migration). Giá trị
nằm ở bảng `system_settings` (apps.sysconfig.models); không có dòng → `default`.
Spec: docs/superpowers/specs/2026-09-19-system-settings-db-design.md §5.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from collections.abc import Iterable

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

SettingType = Literal["bool", "int", "float", "str", "url", "datetime"]

GROUPS: tuple[tuple[str, str], ...] = (
    ("lark_bots", "Lark bot"),
    ("lark_open", "Lark OpenAPI"),
    ("ai", "Gemini / AI"),
    ("call_qa", "Chấm chất lượng cuộc gọi"),
    ("zenify", "Zenify"),
    ("integrations", "Foxia / Pancake / CNV"),
    ("inbound_tokens", "Token API vào"),
    ("orders_portal", "Đơn hàng / Portal"),
)
GROUP_KEYS = frozenset(k for k, _ in GROUPS)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}
_PY_TYPE = {"bool": bool, "int": int, "float": float, "str": str, "url": str, "datetime": str}


@dataclass(frozen=True)
class Setting:
    key: str
    type: SettingType
    default: bool | int | float | str
    group: str
    label: str
    help: str = ""
    secret: bool = False
    staging_forbidden: bool = False


def coerce(spec: Setting, raw: str) -> bool | int | float | str:
    """Ép chuỗi thô (DB/env/form) về kiểu của khóa. Sai → ValueError."""
    text = (raw or "").strip()
    if spec.type == "bool":
        low = text.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f"{spec.key}: giá trị bool không hợp lệ: {raw!r}")
    if spec.type == "int":
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{spec.key}: phải là số nguyên, nhận {raw!r}") from exc
    if spec.type == "float":
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"{spec.key}: phải là số, nhận {raw!r}") from exc
    if spec.type == "url":
        if text and not text.startswith(("http://", "https://")):
            raise ValueError(f"{spec.key}: URL phải bắt đầu bằng http:// hoặc https://")
        return text
    if spec.type == "datetime":
        if text and parse_datetime_value(text) is None:
            raise ValueError(f"{spec.key}: thời điểm không hợp lệ (VD 2026-10-01 08:00), nhận {raw!r}")
        return text
    return text


def parse_datetime_value(raw: str) -> datetime | None:
    """Chuỗi khóa `datetime` → datetime có múi giờ. Thiếu múi giờ = giờ hệ thống
    (TIME_ZONE, UTC+7); chỉ ngày = 00:00. Rỗng/sai → None."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = parse_datetime(text)
        if value is None:
            day = parse_date(text)
            value = datetime(day.year, day.month, day.day, tzinfo=timezone.get_current_timezone()) if day else None
    except ValueError:
        return None
    if value is None:
        return None
    return value if timezone.is_aware(value) else timezone.make_aware(value)


def validate_registry(settings: Iterable[Setting]) -> None:
    seen: set[str] = set()
    for spec in settings:
        if spec.key in seen:
            raise ImproperlyConfigured(f"sysconfig registry: khóa trùng {spec.key}")
        seen.add(spec.key)
        if spec.group not in GROUP_KEYS:
            raise ImproperlyConfigured(f"sysconfig registry: {spec.key} có group lạ {spec.group!r}")
        expected = _PY_TYPE[spec.type]
        # bool là subclass của int — kiểm tra chặt để default=True không lọt vào khóa int.
        if type(spec.default) is not expected:
            raise ImproperlyConfigured(
                f"sysconfig registry: {spec.key} default {spec.default!r} không đúng kiểu {spec.type}"
            )
        if spec.secret and spec.type != "str":
            raise ImproperlyConfigured(f"sysconfig registry: {spec.key} secret phải là kiểu str")


_S = Setting

SETTINGS: tuple[Setting, ...] = (
    # staging_forbidden=True: staging chạy snapshot prod, đọc khóa này luôn ra `default`
    # (credential outbound → rỗng; cờ bật tích hợp gọi ra đối tác/Gemini → False) dù DB
    # restore từ prod có gì đi nữa. Review 2026-09-21 F2 thêm 7 cờ *_ENABLED outbound.
    # ── lark_bots ─────────────────────────────────────────────────────────────
    _S("LARK_ORDER_BOT_WEBHOOK_URL", "url", "", "lark_bots", "Bot đơn mới — Webhook URL",
       "Custom bot Lark trong group báo đơn. Trống = không gửi.", staging_forbidden=True),
    _S("LARK_ORDER_BOT_SECRET", "str", "", "lark_bots", "Bot đơn mới — Secret",
       "Sign verification secret của bot (tuỳ chọn).", secret=True, staging_forbidden=True),
    _S("LARK_ORDER_BOT_TIMEOUT_SECONDS", "float", 5.0, "lark_bots", "Bot đơn mới — Timeout (giây)"),
    _S("LARK_ORDER_BOT_MESSAGE", "str", "", "lark_bots", "Bot đơn mới — Mẫu tin",
       "Trống = mẫu mặc định trong code. Giữ đủ {channel_name} và {staff_name}."),
    _S("LARK_SALE_REMINDER_WEBHOOK_URL", "url", "", "lark_bots", "Bot nhắc CSKH — Webhook URL",
       staging_forbidden=True),
    _S("LARK_SALE_REMINDER_SECRET", "str", "", "lark_bots", "Bot nhắc CSKH — Secret",
       secret=True, staging_forbidden=True),
    _S("LARK_SALE_REMINDER_TIMEOUT_SECONDS", "float", 5.0, "lark_bots", "Bot nhắc CSKH — Timeout (giây)"),
    _S("LARK_VAT_BOT_WEBHOOK_URL", "url", "", "lark_bots", "Bot VAT — Webhook URL", staging_forbidden=True),
    _S("LARK_VAT_BOT_SECRET", "str", "", "lark_bots", "Bot VAT — Secret", secret=True, staging_forbidden=True),
    _S("LARK_VAT_BOT_TIMEOUT_SECONDS", "float", 5.0, "lark_bots", "Bot VAT — Timeout (giây)"),
    _S("LARK_CANCEL_DISPATCH_BOT_WEBHOOK_URL", "url", "", "lark_bots", "Bot huỷ xuất kho — Webhook URL",
       staging_forbidden=True),
    _S("LARK_CANCEL_DISPATCH_BOT_SECRET", "str", "", "lark_bots", "Bot huỷ xuất kho — Secret",
       secret=True, staging_forbidden=True),
    _S("LARK_CANCEL_DISPATCH_BOT_TIMEOUT_SECONDS", "float", 5.0, "lark_bots",
       "Bot huỷ xuất kho — Timeout (giây)"),
    _S("LARK_INVENTORY_BOT_WEBHOOK_URL", "url", "", "lark_bots", "Bot kho — Webhook URL", staging_forbidden=True),
    _S("LARK_INVENTORY_BOT_SECRET", "str", "", "lark_bots", "Bot kho — Secret", secret=True, staging_forbidden=True),
    _S("LARK_INVENTORY_BOT_TIMEOUT_SECONDS", "float", 5.0, "lark_bots", "Bot kho — Timeout (giây)"),
    _S("LARK_OPS_ALERT_WEBHOOK_URL", "url", "", "lark_bots", "Bot cảnh báo vận hành — Webhook URL",
       "Nhận cảnh báo token Facebook hỏng, báo cáo ads bị chặn… Trống = chỉ ghi log.",
       staging_forbidden=True),
    _S("LARK_OPS_ALERT_SECRET", "str", "", "lark_bots", "Bot cảnh báo vận hành — Secret",
       secret=True, staging_forbidden=True),
    # ── lark_open ─────────────────────────────────────────────────────────────
    _S("LARK_OPEN_BASE_URL", "url", "https://open.larksuite.com/open-apis", "lark_open", "Open API base URL"),
    _S("LARK_OPEN_API_TIMEOUT_SECONDS", "float", 15.0, "lark_open", "Open API timeout (giây)"),
    _S("LARK_APP_ID", "str", "", "lark_open", "App ID (Bitable)", staging_forbidden=True),
    _S("LARK_APP_SECRET", "str", "", "lark_open", "App Secret (Bitable)", secret=True, staging_forbidden=True),
    _S("LARK_BITABLE_APP_TOKEN", "str", "", "lark_open", "Bitable app token", secret=True, staging_forbidden=True),
    _S("LARK_BITABLE_REVENUE_TABLE_ID", "str", "", "lark_open", "Bitable — bảng doanh thu KV", staging_forbidden=True),
    _S("LARK_BITABLE_ADS_CHANNEL_TABLE_ID", "str", "", "lark_open", "Bitable — bảng ads theo kênh",
       staging_forbidden=True),
    _S("LARK_BITABLE_ADS_PRODUCT_TABLE_ID", "str", "", "lark_open", "Bitable — bảng ads theo sản phẩm",
       staging_forbidden=True),
    _S("LARK_CHECKIN_REMINDER_APP_ID", "str", "", "lark_open", "App nhắc chấm công — App ID", staging_forbidden=True),
    _S("LARK_CHECKIN_REMINDER_APP_SECRET", "str", "", "lark_open", "App nhắc chấm công — App Secret",
       secret=True, staging_forbidden=True),
    _S("LARK_CHECKIN_REMINDER_CHAT_ID", "str", "", "lark_open", "App nhắc chấm công — chat_id nhóm",
       "Lấy bằng manage.py lark_list_bot_chats.", staging_forbidden=True),
    _S("LARK_CHECKIN_REMINDER_LINK", "url", "", "lark_open", "App nhắc chấm công — link chấm công"),
    # ── ai ────────────────────────────────────────────────────────────────────
    _S("GEMINI_API_KEY", "str", "", "ai", "Gemini API key", secret=True, staging_forbidden=True),
    _S("GEMINI_TRANSCRIBE_MODEL", "str", "gemini-flash-latest", "ai", "Model transcribe"),
    _S("GEMINI_MAX_RPM", "int", 8, "ai", "Trần request/phút",
       "Trần phía mình (Google chỉ trả 429). Đã bật billing thì nâng."),
    _S("GEMINI_MAX_RPD", "int", 200, "ai", "Trần request/ngày", "Chốt chặn chi phí."),
    _S("GEMINI_EXTRACT_ENABLED", "bool", False, "ai", "Bật trích xuất từ transcript cuộc gọi",
       "Xem docs/superpowers/specs/2026-07-24-call-transcript-assessment-autofill-design.md. "
       "Kill switch riêng với ZENIFY_CALL_SYNC_ENABLED — transcribe có thể chạy mà chưa ghi assessment.",
       staging_forbidden=True),
    _S("GEMINI_EXTRACT_MODEL", "str", "gemini-flash-latest", "ai", "Model trích xuất"),
    _S("AI_CHAT_EXTRACT_ENABLED", "bool", False, "ai", "Bật trích xuất hội thoại chat (ai_mart)",
       staging_forbidden=True),
    _S("CHAT_CRAWLER_DB_PASSWORD", "str", "", "ai", "Mật khẩu DB crawler chat (chat_ro)",
       secret=True, staging_forbidden=True),
    # ── call_qa ───────────────────────────────────────────────────────────────
    _S("QA_SCORING_ENABLED", "bool", False, "call_qa", "Bật chấm chất lượng cuộc gọi",
       "Xem docs/superpowers/specs/2026-07-30-call-quality-scoring-design.md. "
       "Kill switch riêng với transcribe/extract — hai việc đó có thể chạy mà chưa muốn chấm điểm ai.",
       staging_forbidden=True),
    _S("QA_SCORING_MODEL", "str", "gemini-flash-latest", "call_qa", "Model chấm"),
    _S("QA_MIN_DURATION_SECONDS", "int", 60, "call_qa", "Thời lượng tối thiểu (giây)",
       "Audit prod 2026-08-29: ngưỡng cũ 120s cắt quá tay, loại 4.613/6.352 cuộc CÓ "
       "TRANSCRIPT (72,6%) chỉ vì dưới 2 phút — trong đó 3.755 cuộc vẫn ≥400 ký tự (có nội "
       "dung tư vấn thật). Hạ về 60s: mọi cuộc ≥60s trong tập đo đều đạt luôn ngưỡng ký tự, "
       "số cuộc chấm được ×2,4. Cặp đôi với QA_MIN_TRANSCRIPT_CHARS."),
    _S("QA_MIN_TRANSCRIPT_CHARS", "int", 400, "call_qa", "Transcript tối thiểu (ký tự)",
       "Audit prod 2026-08-29 (cùng đợt hạ QA_MIN_DURATION_SECONDS 120→60s): ở mốc 120s "
       "luật ký tự không loại thêm cuộc nào, nhưng khi hạ ngưỡng thời lượng thì đây mới là "
       "thứ chặn rác — nhóm dưới 30s chỉ 22% đạt 400 ký tự."),
    _S("QA_PASS_SCORE", "int", 80, "call_qa", "Điểm đạt"),
    # ── zenify ────────────────────────────────────────────────────────────────
    _S("ZENIFY_DOMAIN", "str", "", "zenify", "Domain Zenify"),
    _S("ZENIFY_API_KEY", "str", "", "zenify", "API key", secret=True, staging_forbidden=True),
    _S("ZENIFY_API_SECRET", "str", "", "zenify", "API secret", secret=True, staging_forbidden=True),
    _S("ZENIFY_CALL_SYNC_ENABLED", "bool", False, "zenify", "Bật đồng bộ lịch sử cuộc gọi",
       "Pipeline ghi âm + transcript cuộc gọi, xem docs/zenify-call-transcripts.md.",
       staging_forbidden=True),
    # ── integrations ──────────────────────────────────────────────────────────
    _S("FOXIA_PUSH_ENABLED", "bool", False, "integrations", "Bật đẩy đơn/cơ hội sang Foxia",
       staging_forbidden=True),
    _S("FOXIA_PUSH_BASE_URL", "url", "https://desiglobal.foxia.vn/duongdankhachhang", "integrations",
       "Foxia push base URL"),
    _S("FOXIA_PUSH_TIMEOUT", "float", 10.0, "integrations", "Foxia push timeout (giây)"),
    # Cơ hội resale từ đơn giao thành công (docs/foxia-resale-delivered.md). Cần CẢ
    # FOXIA_PUSH_ENABLED: cờ kia là công tắc tổng của mọi chiều ghi sang Foxia.
    _S("FOXIA_RESALE_PUSH_ENABLED", "bool", False, "integrations",
       "Bật tạo cơ hội resale Foxia từ đơn giao thành công",
       "Job quét mỗi phút, đẩy đơn giao thành công sang cột Resale của bảng liên kết.",
       staging_forbidden=True),
    _S("FOXIA_RESALE_PUSH_SINCE", "str", "2026-10-01", "integrations",
       "Resale Foxia — quét đơn TẠO từ ngày (YYYY-MM-DD) đã giao thành công",
       "Đơn tạo trước ngày này không bao giờ được đẩy, kể cả khi giao thành công sau đó."),
    _S("PANCAKE_POLL_ENABLED", "bool", False, "integrations", "Bật poll hội thoại Pancake",
       staging_forbidden=True),
    _S("PANCAKE_HTTP_TIMEOUT", "float", 15.0, "integrations", "Pancake HTTP timeout (giây)"),
    # Pancake POS (Open API, đồng bộ sản phẩm — docs/pancake-pos-product-sync.md). Khác
    # PANCAKE_* ở trên (Pages/chat). ENABLED chỉ chặn lượt GHI; dry-run chỉ cần key + shop.
    _S("PANCAKE_POS_ENABLED", "bool", False, "integrations", "Bật đồng bộ sản phẩm lên Pancake POS",
       staging_forbidden=True),
    _S("PANCAKE_POS_API_KEY", "str", "", "integrations", "Pancake POS — API key",
       "Cấu hình → Ứng dụng → API KEY trên Pancake POS.", secret=True, staging_forbidden=True),
    _S("PANCAKE_POS_SHOP_ID", "int", 0, "integrations", "Pancake POS — Shop ID",
       "0 = chưa cấu hình (không gọi API)."),
    _S("PANCAKE_POS_DEFAULT_WEIGHT_G", "int", 500, "integrations", "Pancake POS — cân nặng mặc định (gram)",
       "Dùng khi sản phẩm CRM có weight = 0."),
    _S("PANCAKE_POS_RPS", "float", 2.0, "integrations", "Pancake POS — số request mỗi giây"),
    _S("PANCAKE_POS_HTTP_TIMEOUT", "float", 15.0, "integrations", "Pancake POS — HTTP timeout (giây)"),
    _S("PANCAKE_POS_STOCK_BATCH_SIZE", "int", 20, "integrations", "Pancake POS — số dòng mỗi lô đặt tồn",
       "POST variations/update_quantity. API thật trả 500 với lô 100 (30/09/2026); lô 20 ổn. Tối đa 100."),
    _S("PANCAKE_POS_INCLUDE_COMPONENTS", "bool", True, "integrations", "Pancake POS — đẩy cả linh kiện",
       "Chỉ có tác dụng khi DB có products.product_type. Linh kiện lên Pancake ở trạng thái ẩn, "
       "danh mục «Linh kiện» (docs/pancake-pos-two-way-sync.md §6b)."),
    # Đẩy đơn CRM → Pancake (two-way-sync §4.1, A2). Chạy khi CẢ PANCAKE_POS_ENABLED và cờ
    # này bật — bật tay lúc cutover (khi KiotViet thôi nhận đơn), mặc định tắt.
    _S("PANCAKE_POS_PUSH_ORDERS_ENABLED", "bool", False, "integrations", "Pancake POS — đẩy đơn CRM sang Pancake",
       "Bật khi cutover. Cần PANCAKE_POS_ENABLED và đã ghép kho.", staging_forbidden=True),
    _S("PANCAKE_POS_PUSH_ORDER_SOURCES", "str", "landing,messenger,zalo,hotline,foxia", "integrations",
       "Pancake POS — nguồn đơn được đẩy", "Giá trị order_source CRM, cách nhau dấu phẩy."),
    _S("PANCAKE_POS_ORDER_SOURCE_MAP", "str", "", "integrations", "Pancake POS — ghép nguồn đơn",
       "nguồn CRM=order_sources Pancake[:account], cách nhau dấu phẩy. VD: landing=-7:123,messenger=-1. "
       "Trống = không gửi nguồn (xem GET /order_source trên Pancake)."),
    _S("PANCAKE_POS_PUSH_LOOKBACK_HOURS", "int", 72, "integrations", "Pancake POS — cửa sổ quét đơn chốt (giờ)",
       "Chỉ đẩy đơn chốt trong N giờ gần nhất — chặn đẩy dồn đơn cũ lúc mới bật."),
    _S("PANCAKE_POS_PUSH_ORDERS_SINCE", "datetime", "", "integrations", "Pancake POS — đẩy đơn chốt từ thời điểm",
       "Chỉ đẩy đơn chốt SAU mốc này (VD 2026-10-01 08:00, giờ VN). Trống = lần đầu bật "
       "PANCAKE_POS_PUSH_ORDERS_ENABLED (lịch sử cài đặt). Chặn đẩy đơn cũ đã xuất qua kho KiotViet."),
    # Webhook Pancake → CRM (two-way-sync §5.1, B1). Nhận/lưu thô luôn chạy khi secret đã đặt;
    # các cờ dưới chỉ đổi cách xử lý sự kiện đơn.
    _S("PANCAKE_POS_WEBHOOK_URL", "url", "", "integrations", "Pancake POS — URL nhận webhook",
       "URL công khai HTTPS khai với Pancake, vd https://<domain>/api/pancake-pos/webhook."),
    _S("PANCAKE_POS_INBOUND_CREATE_ORDERS", "bool", False, "integrations",
       "Pancake POS — tạo đơn CRM từ đơn sàn/page chưa khớp",
       "Tắt khi đơn sàn còn về CRM qua KiotViet (tạo sẽ ra đơn trùng). Tắt = chỉ khớp, không tạo."),
    # MR D: Pancake không ghi orders.status (KiotViet quyết định). "Đã gửi hàng" → xuất kho CRM
    # (trừ tồn) chỉ khi bật cờ dưới — sau cutover, khi kho quét trên Pancake thay vì CRM.
    _S("PANCAKE_POS_DISPATCH_ENABLED", "bool", False, "integrations",
       "Pancake POS — Đã gửi hàng → xuất kho CRM (trừ tồn)",
       "Bật SAU cutover (kho quét trên Pancake). Tắt = chỉ ghi Kho đang xử lý / trạng thái thô, "
       "đơn Pancake đã gửi mà CRM chưa xuất kho vào báo cáo lệch.", staging_forbidden=True),
    _S("PANCAKE_POS_STUCK_NOT_SENT_HOURS", "int", 24, "integrations",
       "Pancake POS — đơn chưa gửi quá N giờ là treo",
       "Mới / Đã xác nhận / Chờ in / Đã in / Đang đóng hàng / Chờ chuyển hàng quá N giờ từ lúc tạo."),
    _S("PANCAKE_POS_STUCK_NOT_DELIVERED_DAYS", "int", 5, "integrations",
       "Pancake POS — đơn đã gửi quá M ngày chưa giao là treo", "Tính từ mốc gửi hàng."),
    _S("PANCAKE_POS_STUCK_ALERT_HOUR", "int", 9, "integrations",
       "Pancake POS — giờ gửi Lark báo cáo đơn treo (0–23, giờ VN)", "Mỗi ngày một lần; -1 = tắt."),
    _S("PANCAKE_POS_INBOUND_UNMATCHED_HOURS", "int", 6, "integrations",
       "Pancake POS — số giờ chờ khớp trước khi báo", "Đơn Pancake chưa khớp đơn CRM quá N giờ → Chờ khớp + Lark."),
    _S("PANCAKE_POS_INBOUND_SOURCE_MAP", "str", "-3=shopee,-9=tiktok,-1=messenger", "integrations",
       "Pancake POS — nguồn đơn Pancake → order_source CRM (tạo đơn)",
       "order_sources Pancake=order_source CRM, cách nhau dấu phẩy. -3 Shopee, -9 TikTok, -1 Facebook."),
    _S("PANCAKE_POS_INBOUND_CHANNEL_MAP", "str", "", "integrations",
       "Pancake POS — shop/page Pancake → kênh CRM (tạo đơn)",
       "account hoặc page_id Pancake=id kênh CRM, cách nhau dấu phẩy. Trống = khớp theo tên/mã kênh."),
    _S("CNV_SYNC_ENABLED", "bool", False, "integrations", "Bật đồng bộ CNV Loyalty",
       staging_forbidden=True),
    _S("CNV_SSO_SERVER_URI", "url", "https://id.cnv.vn", "integrations", "CNV SSO server"),
    _S("CNV_API_BASE_URL", "url", "https://apis.cnvloyalty.com", "integrations", "CNV API base"),
    _S("CNV_SSO_REDIRECT_URI", "url", "", "integrations", "CNV SSO redirect URI",
       "Callback công khai đã đăng ký với CNV: https://<domain>/api/cnv/callback"),
    _S("CNV_ACCOUNT_NAME", "str", "", "integrations", "CNV account name (đối soát)"),
    _S("CNV_ACCOUNT_ID", "str", "", "integrations", "CNV account id (đối soát)"),
    # ── inbound_tokens ────────────────────────────────────────────────────────
    _S("HERMES_API_TOKEN", "str", "", "inbound_tokens", "Token Hermes",
       "Trống = endpoint trả 503 (fail-closed).", secret=True),
    _S("DAILY_REPORT_API_TOKEN", "str", "", "inbound_tokens", "Token Daily Report",
       "Trống = endpoint trả 503 (fail-closed).", secret=True),
    _S("CONVERSION_FEED_GOOGLE_ADS_WEB_CHANNEL_ID", "int", 7, "inbound_tokens",
       "Feed conversion — id kênh Google Ads Web"),
    _S("ORDERS_INGEST_API_TOKEN", "str", "", "inbound_tokens", "Token ingest đơn landing",
       "Dùng chung cho /api/conversion-feed.", secret=True),
    _S("KIOTVIET_WEBHOOK_SECRET", "str", "", "inbound_tokens", "Secret webhook KiotViet",
       "Bản base64 tự suy ra khi đăng ký/xác thực — không cần nhập riêng.", secret=True),
    _S("PANCAKE_POS_WEBHOOK_SECRET", "str", "", "inbound_tokens", "Secret webhook Pancake POS",
       "Header X-Desi-Webhook-Secret khai với Pancake (≥ 16 ký tự). Trống = endpoint trả 503.", secret=True),
    _S("WARRANTY_PORTAL_ZALO_TOKEN", "str", "", "inbound_tokens", "Token kênh Zalo OA kích hoạt BH",
       "Trống = chặn kênh zalo_oa.", secret=True),
    # ── orders_portal ─────────────────────────────────────────────────────────
    _S("ORDERS_LANDING_INGEST_DRY_RUN", "bool", False, "orders_portal", "Ingest landing chạy thử (không tạo đơn)"),
    _S("RECONCILE_DELIVERY_EXACT_MIRROR", "bool", False, "orders_portal", "Reconcile giao hàng: mirror chính xác"),
    _S("SALE_REMINDER_FOLLOWUP_HOUR", "int", 9, "orders_portal", "Giờ nhắc follow-up D+2/D+4/D+7"),
    _S("FB_TOKEN_EXPIRY_WARN_DAYS", "int", 7, "orders_portal", "Cảnh báo token Facebook trước (ngày)"),
    _S("WARRANTY_PORTAL_MONTHS", "int", 24, "orders_portal", "Thời hạn bảo hành (tháng)"),
    _S("WARRANTY_PORTAL_SCAN_LOG_RETENTION_DAYS", "int", 180, "orders_portal", "Giữ log quét portal (ngày)"),
    _S("WARRANTY_QR_BASE_URL", "url", "https://delax.com.vn/sp/", "orders_portal", "URL in lên tem QR",
       "Sai giá trị hỏng cả lô tem đã in."),
    _S("PORTAL_COMPANY_NAME", "str", "", "orders_portal", "Footer portal — Tên công ty"),
    _S("PORTAL_COMPANY_ADDRESS", "str", "", "orders_portal", "Footer portal — Địa chỉ"),
    _S("PORTAL_COMPANY_TAX_CODE", "str", "", "orders_portal", "Footer portal — Mã số thuế"),
    _S("PORTAL_COMPANY_HOTLINE", "str", "", "orders_portal", "Footer portal — Hotline"),
    _S("PORTAL_COMPANY_ZALO_URL", "url", "", "orders_portal", "Footer portal — Link Zalo"),
    _S("PORTAL_COMPANY_FACEBOOK_URL", "url", "", "orders_portal", "Footer portal — Link Facebook"),
)
BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}
