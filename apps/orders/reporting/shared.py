"""Hằng số + helper dùng chung cho mọi báo cáo đơn hàng (package ``reporting``).

Tách nguyên trạng từ ``apps/orders/_repositories_reports.py`` (refactor 2026-08,
audit backend-architecture TOP15 #2) — KHÔNG đổi hành vi. Các module báo cáo
theo miền (``sale_reports`` / ``delivery_quality`` / ``ads_reports`` /
``overview``) đều import từ đây; đường import cũ vẫn sống qua facade
``apps.orders._repositories_reports``.

LƯU Ý monkeypatch: ``_ads_excluded_channel_ids`` đọc ``SALE_FB_CHANNEL_IDS``
qua global CỦA CHÍNH MODULE NÀY lúc chạy, nên test muốn cách ly hằng số đó phải
``monkeypatch.setattr`` vào ``apps.orders.reporting.shared`` (xem fixture
``_isolate_hardcoded_channel_ids`` ở backend/conftest.py) — patch lên facade cũ
chỉ đổi binding re-export, không ảnh hưởng hàm.
"""
from datetime import date as date_cls
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Q

from apps.channels_core.models import Channel, UserChannel
from apps.common.dates import parse_iso_date
# Re-export CLOSED_SALES_STATUS_CODES: định nghĩa gốc đã dời sang `crm_catalog`
# để tầng service (luật ghi `Order.sales_closed_at`) dùng chung mà không phải
# import ngược vào tầng báo cáo. Mọi đường import cũ qua module này vẫn sống.
from apps.orders.crm_catalog import CLOSED_SALES_STATUS_CODES  # noqa: F401  (re-export)
from apps.orders.models import (
    INVOICE_CODE_SOURCE_PREFIXES,
    OrderSource,
    OrderStatus,
)
from apps.users.constants import DepartmentID
from apps.users.department_membership import department_member_q
from apps.users.models import DataScope
from apps.users.rbac import resolve_effective_permissions


def _parse_report_date(value: str | None) -> date_cls | None:
    """Parse `YYYY-MM-DD` cho tham số lọc báo cáo; chuỗi hỏng/rỗng → None.

    Giữ NGUYÊN hành vi khoan dung của code cũ: tham số ngày không hợp lệ thì bỏ
    qua bộ lọc đó chứ không ném lỗi ra người dùng. Tách ra ở tầng orders để hai
    adapter quảng cáo (`facebook_ads`, `google_ads`) khỏi phải lặp lại logic parse.
    """
    return parse_iso_date(value)


# Doanh thu chốt (Nhóm 9 — Báo cáo Sale) KHÔNG tính "đã chốt, hẹn ngày": đơn hẹn
# ngày giao chưa đủ chắc chắn để ghi nhận là tiền đã chốt (user chốt 2026-07-23).
# CỐ Ý tách khỏi CLOSED_SALES_STATUS_CODES — Nhóm 1/2, doanh thu Sale FB, báo
# cáo Ads và báo cáo Khách hàng vẫn tính đủ 4 trạng thái. Sửa hằng số này KHÔNG
# ảnh hưởng các báo cáo đó, và ngược lại.
CLOSED_REVENUE_SALES_STATUS_CODES = ("closed", "closed_express", "created_order")
UNREACHABLE_SALES_STATUS_CODES = (
    "unreachable",
    "no_answer_1",
    "no_answer_2",
    "no_answer_3",
    "no_answer_4",
    "no_answer_5",
    "no_answer_6",
)
NURTURE_SALES_STATUS_CODES = (
    "need_more_consulting",
    "scheduled",
    "call_back_later",
    "wants_store_visit",
)
# "Khách muốn đến cửa hàng" — khách tự ra cửa hàng chốt, kết quả không nằm trong
# tay sale online. Loại khỏi MẪU SỐ của Tỷ lệ chốt (Nhóm 1) và Năng lực Sale
# (Nhóm 2) theo quyết định nghiệp vụ 2026-08-15, cùng cơ chế với
# `underlying_condition`. VẪN nằm trong NURTURE_SALES_STATUS_CODES ở trên vì
# đó là rổ nghiệp vụ dùng chung (segmentation khách hàng, sheet_rules, lịch sử
# follow-up) — chỉ hai nhóm báo cáo trên trừ nó ra.
WANTS_STORE_VISIT_SALES_STATUS_CODE = "wants_store_visit"
# Rổ nurture DÙNG RIÊNG cho Nhóm 2: bỏ "đến cửa hàng" để tử số "% chăm sóc lại"
# không chứa đơn đã bị trừ khỏi mẫu số (nếu không, ba thanh % của Nhóm 2 cộng
# lại vượt quá tập đơn thực sự được đo).
NURTURE_SALES_STATUS_CODES_FOR_CAPACITY = tuple(
    code for code in NURTURE_SALES_STATUS_CODES if code != WANTS_STORE_VISIT_SALES_STATUS_CODE
)
DIRTY_SALES_STATUS_CODES = (
    "spam",
    "duplicate",
    "wrong_number",
    "customer_bombed",
    "poor_quality_data",
)
# Group 3 ("không tiếp cận được"): chỉ mốc gọi cuối cùng (no_answer_6) và
# unreachable mới tính — no_answer_1..5 vẫn còn nhịp gọi tiếp theo. Mốc cuối dời
# từ lần 4 sang lần 6 khi mở rộng chuỗi gọi (2026-08-14): đơn đang đứng ở lần 4
# quay lại rổ "đang theo", nên số Nhóm 3 kỳ sau THẤP hơn kỳ trước — cố ý.
UNREACHED_SALES_STATUS_CODES = ("no_answer_6", "unreachable")
NOT_BUYING_SALES_STATUS_CODE = "not_buying"
WARRANTY_SALES_STATUS_CODE = "warranty"
# "Khách bị bệnh nền" — chống chỉ định, không thể bán. Loại khỏi MẪU SỐ của
# Tỷ lệ chốt (Nhóm 1) theo quyết định nghiệp vụ 2026-07-30; KHÔNG tính vào
# not_buying ("% từ chối") vì không phải lỗi tư vấn.
UNDERLYING_CONDITION_SALES_STATUS_CODE = "underlying_condition"

# Delivery-quality buckets — see OrderStatus enum in models.py.
SENT_OUT_ORDER_STATUSES = (2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13)
PENDING_DELIVERY_ORDER_STATUSES = (0, 1, 2, 4, 7, 8, 9, 10, 11, 12, 13)
DELIVERED_SUCCESS_ORDER_STATUS = 3
# "Đã chuyển hoàn" — phạm vi HẸP, chỉ đơn đã hoàn xong. Từ 2026-08-18 chỉ còn
# Nhóm 6 (Lý do bom) dùng: lý do bom chỉ được phân loại khi hàng đã về. Mọi chỗ
# đếm "đơn hoàn" khác đã chuyển sang RETURN_ORDER_STATUSES bên dưới.
RETURNED_ORDER_STATUS = 5
PENDING_DELIVERY_THRESHOLD_DAYS = 7

# Phân loại kết quả giao hàng theo vòng đời đơn (nhóm 5a). Đơn huỷ (6) đã bị
# _base_orders_queryset loại, nên 13 trạng thái còn lại chia đúng 3 rổ rời nhau:
#   IN_DELIVERY + BOMB + DELIVERED_SUCCESS = total.
# - IN_DELIVERY: chưa có kết quả cuối (còn trong kho hoặc đang di chuyển).
# - BOMB: khách không nhận -> đơn quay đầu về kho.
IN_DELIVERY_ORDER_STATUSES = (0, 1, 2, 7, 8, 9, 10, 11)
BOMB_ORDER_STATUSES = (4, 5, 12, 13)

SALE_FB_CHANNEL_CODE = "sale-fb"
# Đọc từ settings (env SALE_FB_CHANNEL_IDS) — giữ tên module-level vì test
# cách ly bằng monkeypatch.setattr vào chính module này (backend/conftest.py).
SALE_FB_CHANNEL_IDS = tuple(settings.SALE_FB_CHANNEL_IDS)

# Điều kiện "đơn đã hủy" mang cả hai dấu vết hủy trong dữ liệu: đơn hủy từ web
# KiotViet chỉ set invoice_status=2 (status giữ nguyên), đơn hủy POS set status=6
# nhưng invoice_status vẫn null/1. Dùng chung cho tab "Đã hủy" + chip đếm
# (repositories.py) và để LOẠI đơn hủy khỏi tổng của summary_overview (báo cáo
# tổng không còn bucket Hủy riêng) — sửa Ở ĐÂY nếu quy tắc đổi, để các nơi
# không lệch nhau.
CANCELLED_ORDER_Q = Q(status=6) | Q(invoice_status=2)

# Đơn LEAD — chưa từng lên hóa đơn KiotViet (đơn CRM/landing chờ chốt).
#
# Từ 2026-08-20 đây là RANH GIỚI DUY NHẤT của doanh thu Báo cáo tổng:
# `total_q = ~LEAD_ORDER_Q & ~CANCELLED_ORDER_Q`. Trước đó phạm vi gác bằng
# trạng thái sale (`OVERVIEW_CLOSED_SCOPE_Q`, đã gỡ) — bỏ vì doanh thu không nên
# phụ thuộc việc sale có gắn trạng thái hay chưa, và đơn có hóa đơn mang trạng
# thái ngoài 4 mã chốt bị loại oan. Đổi Ở ĐÂY là đổi mẫu số của MỌI ô % trên
# trang. (Lịch sử: bug phồng số 2026-07-20 là do ~Q(invoice_status=2) giữ lại
# hàng NULL nên lead lọt vào bucket net KHÔNG chủ đích.)
LEAD_ORDER_Q = Q(invoice_status__isnull=True)

# Trần của "lead time xử lý data" (thời gian từ lúc data đổ về tới lần ĐẦU sale
# nhập kết quả tác nghiệp). Trung bình cộng rất nhạy với đuôi dài: một data bị bỏ
# quên vài tuần đủ kéo lệch số của cả tháng, nên mỗi đơn bị cắt trần ở đây trước
# khi cộng. Đổi Ở ĐÂY là đổi số của cả 2 tab "Báo cáo theo sale" và "Báo cáo chi
# tiết theo sale" — nhớ sửa kèm ghi chú dưới bảng ở SaleMetricTable.tsx.
LEAD_TIME_CAP = timedelta(days=7)

# Mốc "đã nhập kết quả tác nghiệp": event đổi trạng thái sale. Lần LƯU đánh giá
# đầu tiên cũng bắn event này, và lần KiotViet tự lật sang "đã lên đơn" (bán trực
# tiếp / tạo vận đơn KShip thành công) cũng vậy — cả hai đều CỐ Ý được tính.
SALES_STATUS_EVENT_TYPE = "sales_status_changed"

# Mốc "data đến tay sale" — điểm BẮT ĐẦU của lead time (sửa 2026-08-24 sau khi số
# đo trên prod lộ ra vấn đề, xem docs/sale-report-lead-time-2026-08-24.md §7).
#
# KHÔNG dùng orders.created_at làm mốc gốc: LeadAssignmentService bỏ qua chia đơn
# khi data về NGOÀI GIỜ LÀM VIỆC, phải đợi sweep_unassigned() chạy trong giờ hành
# chính mới gán sale. Data về 23h, chia 8h sáng, sale gọi 8h10 → đo từ created_at
# ra 9h10p trong khi sale chỉ mất 10 phút. Đơn vào qua import/KiotViet cũng vậy
# (không đi webhook nên không chạy chia lúc tạo).
#
# Gồm cả staff_changed để đơn chuyển tay giữa chừng được tính từ lần bàn giao
# đang có hiệu lực, không phải từ lần chia đầu tiên cho người khác.
STAFF_HANDOVER_EVENT_TYPES = (
    "staff_assigned",           # bộ chia tự động
    "staff_ingest_designated",  # sale được chỉ định sẵn trong payload ingest
    "staff_changed",            # đổi sale phụ trách (sửa tay / chuyển hàng loạt)
)

# Nhận diện MỘT sàn TMĐT: theo order_source HOẶC prefix mã hóa đơn. Prefix là
# bằng chứng chắc chắn hơn vì đơn sàn đồng bộ về vẫn có thể bị sót order_source
# (2026-07: 3 đơn HDSPE_/HDTTS_ chưa gắn nguồn). Dựng từ chính
# ``INVOICE_CODE_SOURCE_PREFIXES`` để mở thêm sàn (Lazada...) chỉ sửa MỘT chỗ.
def _marketplace_order_q(source: str) -> Q:
    condition = Q(order_source=source)
    for prefix, prefix_source in INVOICE_CODE_SOURCE_PREFIXES.items():
        if prefix_source == source:
            condition |= Q(invoice_code__startswith=prefix)
    return condition


SHOPEE_ORDER_Q = _marketplace_order_q(OrderSource.SHOPEE)
TIKTOK_ORDER_Q = _marketplace_order_q(OrderSource.TIKTOK)

# NỘI SÀN = hợp của các sàn. Dùng cho BỘ LỌC "Kênh bán" và cho luật gác 2 ô chi
# phí Facebook/Google Ads (chỉ có số khi lọc ngoại sàn) — xem summary_overview.
MARKETPLACE_ORDER_Q = SHOPEE_ORDER_Q | TIKTOK_ORDER_Q

# Nhận diện Facebook / Web theo CODE KÊNH, KHÔNG theo id. Lý do đã ghi ở
# ``Channel.is_koc``: id channel sinh tự động nên khác nhau giữa các môi trường,
# còn code thì ổn định. Dùng id chính là cái bẫy đã cắn báo cáo Ads (id hard-code
# KOC=15, SALE_FB=24). Trên prod ``google-ads-web`` là channel id 7. Mở kênh
# ``facebook-<tên>`` mới thì kênh đó TỰ vào nhóm Facebook, không phải nhớ sửa
# danh sách nào.
FACEBOOK_CHANNEL_CODE_PREFIX = "facebook"
WEB_CHANNEL_CODE = "google-ads-web"
FACEBOOK_ORDER_Q = Q(channel__code__istartswith=FACEBOOK_CHANNEL_CODE_PREFIX)
WEB_ORDER_Q = Q(channel__code__iexact=WEB_CHANNEL_CODE)

# Giá trị của ô lọc "NGUỒN" trên trang (tên cũ là "Kênh bán", đổi 2026-08-24) —
# vẫn là 2 nhóm lớn nội/ngoại sàn. Ô "Kênh bán" MỚI đứng cạnh nó lọc sâu thêm
# một tầng, xem ``SALE_CHANNEL_GROUP_FILTERS``.
SALE_CHANNEL_MARKETPLACE = "marketplace"
SALE_CHANNEL_OFF_MARKETPLACE = "off_marketplace"

# NĂM nhóm của 3 khối biểu đồ (user chốt 2026-08-20, thay cho 2 nhóm nội/ngoại):
# nội sàn tách Shopee/TikTok, ngoại sàn tách Facebook/Web và phần còn lại.
#
# THỨ TỰ KHỚP LÀ BẮT BUỘC, không phải chi tiết cài đặt: sàn xét TRƯỚC kênh. Kênh
# ``delax-chinh-hang`` mang 786 đơn Shopee và prefix HDSPE_ xuất hiện trên 6 kênh
# khác nhau (đo prod 08/2026) — xét kênh trước là gán nhầm ~1,8 tỉ doanh thu
# Shopee sang Facebook.
#
# ``off_other`` (Resale, KOC, bán tại quầy KiotViet — 167 đơn ≈ 306 triệu trong
# tháng 8) BẮT BUỘC phải có: thiếu nó thì tổng 3 khối biểu đồ không còn bằng ô
# KPI, đúng cái bất biến đã vi phạm 2 lần và cả 2 lần đều lọt tới tay user.
SALE_CHANNEL_SHOPEE = "shopee"
SALE_CHANNEL_TIKTOK = "tiktok"
SALE_CHANNEL_FACEBOOK = "facebook"
SALE_CHANNEL_WEB = "web"
SALE_CHANNEL_OFF_OTHER = "off_other"
SALE_CHANNEL_GROUPS = (
    SALE_CHANNEL_SHOPEE,
    SALE_CHANNEL_TIKTOK,
    SALE_CHANNEL_FACEBOOK,
    SALE_CHANNEL_WEB,
    SALE_CHANNEL_OFF_OTHER,
)

# NGUỒN DUY NHẤT của phép gán nhóm. Hai chỗ tiêu thụ nó và cả hai BẮT BUỘC phải
# cùng thứ tự, nếu không thì lát donut và bộ đơn được lọc nói hai chuyện khác
# nhau:
#   1. ``Case`` annotate nhóm cho group-by (overview.py) — nhánh cuối là default
#      nên ``off_other`` KHÔNG có mặt ở đây.
#   2. ``SALE_CHANNEL_GROUP_FILTERS`` bên dưới — điều kiện lọc của ô "Kênh bán".
# Thêm nhóm lá mới thì sửa ĐÚNG một chỗ này.
SALE_CHANNEL_CASE_BRANCHES = (
    (SALE_CHANNEL_SHOPEE, SHOPEE_ORDER_Q),
    (SALE_CHANNEL_TIKTOK, TIKTOK_ORDER_Q),
    (SALE_CHANNEL_FACEBOOK, FACEBOOK_ORDER_Q),
    (SALE_CHANNEL_WEB, WEB_ORDER_Q),
)


def _leaf_group_q(group: str) -> Q:
    """Điều kiện lọc về đúng MỘT nhóm lá — PHẦN BÙ LUỸ TIẾN của các nhánh trước.

    ``Case`` xét nhánh theo thứ tự và dừng ở nhánh khớp đầu tiên, nên "là nhóm
    facebook" KHÔNG phải ``FACEBOOK_ORDER_Q`` mà là "không phải Shopee, không
    phải TikTok, VÀ code kênh bắt đầu bằng facebook". Đơn Shopee treo trên kênh
    ``facebook-*`` (786 đơn trên prod) là ca chứng minh: dùng thẳng Q của nhánh
    sẽ đếm nó hai lần và tổng donut hết bằng ô KPI.
    """
    condition = Q()
    for key, branch_q in SALE_CHANNEL_CASE_BRANCHES:
        if key == group:
            return condition & branch_q
        condition &= ~branch_q
    # Không khớp nhánh nào = nhánh default của ``Case`` (``off_other``).
    return condition


# Điều kiện lọc của ô "Kênh bán" — phủ mọi giá trị mà ``SALE_CHANNEL_GROUP_LEVELS``
# có thể đưa ra, gồm cả 2 nhóm lớn của tầng "Tất cả".
SALE_CHANNEL_GROUP_FILTERS = {
    SALE_CHANNEL_MARKETPLACE: MARKETPLACE_ORDER_Q,
    SALE_CHANNEL_OFF_MARKETPLACE: ~MARKETPLACE_ORDER_Q,
    **{group: _leaf_group_q(group) for group in SALE_CHANNEL_GROUPS},
}

# NHÓM THEO TẦNG (user chốt 2026-08-23, thay cho việc luôn hiện đủ 5 nhóm):
# biểu đồ chia theo đúng tầng dưới của thứ đang lọc, không sâu hơn.
#
#   Tất cả     → Nội sàn · Ngoại sàn
#   Nội sàn    → Shopee · TikTok
#   Ngoại sàn  → Facebook · Web · Ngoại sàn khác
#
# KHÔNG cần SQL điều kiện: annotate vẫn luôn là `Case` 5 nhánh, tầng nào cần
# gộp lên thì gộp ở Python (`SALE_CHANNEL_ROLLUP`). Khi đã lọc nội/ngoại sàn
# thì `base` đã loại sẵn phía kia nên chỉ còn đúng các nhóm con — không phải
# làm gì thêm. Một đường tính duy nhất, không nhánh đôi.
SALE_CHANNEL_GROUP_LEVELS = {
    "": (SALE_CHANNEL_MARKETPLACE, SALE_CHANNEL_OFF_MARKETPLACE),
    SALE_CHANNEL_MARKETPLACE: (SALE_CHANNEL_SHOPEE, SALE_CHANNEL_TIKTOK),
    SALE_CHANNEL_OFF_MARKETPLACE: (
        SALE_CHANNEL_FACEBOOK,
        SALE_CHANNEL_WEB,
        SALE_CHANNEL_OFF_OTHER,
    ),
}

# Gộp nhóm con lên nhóm cha, dùng cho tầng "Tất cả".
SALE_CHANNEL_ROLLUP = {
    SALE_CHANNEL_SHOPEE: SALE_CHANNEL_MARKETPLACE,
    SALE_CHANNEL_TIKTOK: SALE_CHANNEL_MARKETPLACE,
    SALE_CHANNEL_FACEBOOK: SALE_CHANNEL_OFF_MARKETPLACE,
    SALE_CHANNEL_WEB: SALE_CHANNEL_OFF_MARKETPLACE,
    SALE_CHANNEL_OFF_MARKETPLACE: SALE_CHANNEL_OFF_MARKETPLACE,
    SALE_CHANNEL_OFF_OTHER: SALE_CHANNEL_OFF_MARKETPLACE,
}


def sale_channel_groups_for(sale_channel: str | None) -> tuple[str, ...]:
    """Các nhóm (đã đúng thứ tự hiển thị) của tầng ứng với bộ lọc đang chọn.

    Giá trị lạ coi như không lọc — khớp cách `summary_overview` xử lý bộ lọc.
    """
    return SALE_CHANNEL_GROUP_LEVELS.get(sale_channel or "", SALE_CHANNEL_GROUP_LEVELS[""])


def sale_channel_group_filter(sale_channel: str | None, sale_channel_group: str | None) -> str:
    """Giá trị đã kiểm của ô "Kênh bán"; "" = không lọc sâu thêm.

    Chỉ chấp nhận nhóm THUỘC TẦNG đang chọn ở ô "Nguồn" — chọn "Facebook" trong
    khi Nguồn = Nội sàn là mâu thuẫn, coi như không lọc thay vì trả rỗng (khớp
    cách ``summary_overview`` xử lý mọi giá trị lạ của bộ lọc).
    """
    group = (sale_channel_group or "").strip()
    return group if group in sale_channel_groups_for(sale_channel) else ""


def rolled_sale_channel(group: str, sale_channel: str | None) -> str:
    """Khóa nhóm sau khi gộp lên đúng tầng của bộ lọc.

    Chỉ tầng "Tất cả" mới thật sự gộp; hai tầng con trả về chính nó.
    """
    if sale_channel in (SALE_CHANNEL_MARKETPLACE, SALE_CHANNEL_OFF_MARKETPLACE):
        return group
    return SALE_CHANNEL_ROLLUP.get(group, group)

# Đơn HOÀN: đang chuyển hoàn (4) + đã chuyển hoàn (5) + chờ lấy hàng hoàn (12)
# + chờ xác nhận hoàn (13). Dùng chung cho bucket Hoàn của Báo cáo tổng, cột
# "sau hoàn" của Nhóm 10 và cột "Đơn hoàn"/"% hoàn" của Nhóm 7 (Tỷ lệ hoàn theo
# sản phẩm) — sửa Ở ĐÂY nếu quy tắc đổi.
#
# 2026-08-18: Nhóm 7 trước đây chỉ đếm status=5 nên đơn đang trên đường hoàn bị
# bỏ sót — % hoàn theo sản phẩm thấp giả tạo và lệch với Báo cáo tổng. Nay dùng
# chung rổ này, số Nhóm 7 sẽ TĂNG so với trước.
RETURN_ORDER_STATUSES = (4, 5, 12, 13)

# BÁO CÁO TỔNG — 4 rổ kết quả của "tiền đã chốt" (user chốt 2026-08-11).
# "Chờ giao lại" (10) tách riêng thành RỦI RO HOÀN: đơn đã quay đầu một lần,
# nguy cơ thành hoàn cao hơn hẳn đơn đang giao bình thường. CỐ Ý không tái dùng
# IN_DELIVERY_ORDER_STATUSES (nhóm 5a) vì bộ đó gộp mã 10 vào "đang giao".
RETURN_RISK_ORDER_STATUSES = (10,)
# Rổ "chờ giao" định nghĩa bằng PHÉP TRỪ trên toàn bộ OrderStatus, không liệt kê
# tay: thêm trạng thái mới vào enum thì nó tự vào rổ này thay vì rơi khỏi doanh
# thu. Đơn hủy (6) đã bị loại khỏi tổng nên không thuộc rổ nào.
AWAITING_DELIVERY_ORDER_STATUSES = tuple(
    sorted(
        {status.value for status in OrderStatus}
        - {DELIVERED_SUCCESS_ORDER_STATUS, OrderStatus.CANCELLED.value}
        - set(RETURN_ORDER_STATUSES)
        - set(RETURN_RISK_ORDER_STATUSES)
    )
)

# Nguồn lead thuộc luồng sale nội bộ — Nhóm 10 của Báo cáo Sale chỉ nhìn các
# nguồn này: bỏ đơn sàn (tiktok/shopee) và đơn KOC vì không do sale chốt, bỏ
# luôn đơn không gắn nguồn vì không xếp được vào cột nào. KOL cũng bị loại: đây
# là tên cũ của KOC (form không cho chọn nữa, chỉ còn ở đơn cũ) nên giữ lại sẽ
# mâu thuẫn với việc đã loại KOC.
SALE_SOURCE_REPORT_ORDER_SOURCES = (
    OrderSource.LANDING,
    OrderSource.ZALO,
    OrderSource.MESSENGER,
    OrderSource.HOTLINE,
)

# Kho Đại Mỗ — user chốt 2026-07-20: KHÔNG tính vào doanh thu công ty, khớp
# báo cáo bán hàng KiotViet + số "doanh thu thuần" trên Lark (đều không gồm kho
# này). Chỉ loại ở Báo cáo tổng; EXCLUDED_BRANCH_IDS toàn cục giữ nguyên để
# không đổi số các báo cáo khác.
OVERVIEW_EXCLUDED_BRANCH_IDS = ("1000000477",)


def _ads_excluded_channel_ids() -> set[int]:
    """Channel ids không bao giờ tính vào báo cáo ads.

    - Sale FB (``SALE_FB_CHANNEL_IDS``): doanh thu sale, không phải ads.
    - KOC / hàng mẫu: đơn hàng mẫu không phản ánh hiệu quả ads. Nhận diện theo
      ``KOC_CHANNEL_ID`` hoặc mã ``koc`` — match cả code vì id channel sinh tự
      động nên có thể khác ``KOC_CHANNEL_ID`` giữa các môi trường (xem
      ``Channel.is_koc``).
    """
    excluded: set[int] = set(SALE_FB_CHANNEL_IDS)
    excluded.update(
        Channel.objects.filter(
            Q(id=settings.KOC_CHANNEL_ID) | Q(code__iexact=Channel.KOC_CODE),
            deleted_at__isnull=True,
        ).values_list("id", flat=True)
    )
    return excluded


def _user_sees_all_ads_channels(user) -> bool:
    """User có được xem TẤT CẢ kênh trong báo cáo ads không.

    True khi: không truyền user (caller nội bộ), HOẶC scope hiệu dụng của section
    ``ads-report`` = ALL. ``scope_for()`` đã trả ALL sẵn cho superuser nên điều
    kiện này bao gồm cả admin lẫn role "xem toàn bộ" (scope ALL) — nhờ đó tách
    được "xem rộng số liệu ads" khỏi "toàn quyền admin": chỉ cần cấp 1 role có
    scope ads-report = ALL là xem hết kênh, không cần role hệ thống.
    """
    if user is None:
        return True
    return resolve_effective_permissions(user).scope_for("ads-report") == DataScope.ALL

# Hệ số hiển thị chi phí Ads theo ngày — chi phí FB nhân hệ số phụ phí/VAT theo
# yêu cầu business; áp dụng cả ratio_pct để % chi phí/doanh thu nhất quán.
#
# Hệ số FB ĐỔI THEO THỜI GIAN nên KHÔNG sửa đè giá trị cũ: báo cáo vẫn xem được kỳ
# quá khứ, sửa đè sẽ viết lại lịch sử chi phí của những tháng đã chốt sổ. Thay vào
# đó mỗi lần đổi là THÊM MỘT MỐC vào bảng dưới đây — nguồn duy nhất của luật, cả
# màn hình báo cáo lẫn mart ``ai.chi_phi_quang_cao`` đều đọc từ đây.
#
# Lịch sử: 1.1 (gốc) → 1.08 từ 01/08/2026 (user chốt 2026-08-26) → 1.1 trở lại từ
# 01/09/2026 (user chốt 2026-09-11). Tháng 8/2026 vì vậy là một "hố" ×1.08 kẹp
# giữa hai giai đoạn ×1.1, KHÔNG phải dữ liệu lỗi.
ADS_DAILY_COST_MULTIPLIER = Decimal("1.1")

# (ngày hiệu lực, hệ số) — sắp xếp MỚI NHẤT TRƯỚC, tra bằng mốc đầu tiên <= ngày cần
# tìm. Thêm mốc mới thì chèn lên đầu; trước mốc cuối bảng dùng
# ``ADS_DAILY_COST_MULTIPLIER``.
FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE: tuple[tuple[date_cls, Decimal], ...] = (
    (date_cls(2026, 9, 1), Decimal("1.1")),
    (date_cls(2026, 8, 1), Decimal("1.08")),
)
# Google Ads áp VAT 8% tại VN → nhân 1.08 (tách riêng khỏi hệ số FB, KHÔNG gộp làm
# một dù hai con số hiện bằng nhau: hai nền tảng đổi hệ số theo hai lịch khác nhau).
GOOGLE_ADS_DAILY_COST_MULTIPLIER = Decimal("1.08")


def facebook_ads_cost_multiplier(date_from: date_cls | None) -> Decimal:
    """Hệ số chi phí Facebook cho MỘT kỳ báo cáo, chọn theo ngày BẮT ĐẦU kỳ.

    Chọn theo kỳ chứ không theo từng ngày chi tiêu (user chốt 2026-08-26): cả kỳ
    dùng chung một hệ số của mốc gần nhất TRƯỚC ngày bắt đầu kỳ, nên kỳ bắc qua
    một mốc vẫn ăn hệ số CŨ cho những ngày sau mốc. Hệ quả đã được báo trước: xem
    "tháng 8" và xem "30 ngày gần nhất" ra hai con số không cộng lại khớp nhau.

    Không lọc ngày (``None``) → kỳ bao trùm cả quá khứ nên giữ hệ số gốc.
    """
    if date_from is not None:
        for effective_date, multiplier in FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE:
            if date_from >= effective_date:
                return multiplier
    return ADS_DAILY_COST_MULTIPLIER


def resale_channel_ids() -> tuple[int, ...]:
    """Ids kênh Resale — hiện DÙNG CHUNG với ``SALE_FB_CHANNEL_IDS``.

    Kênh id 24 mang tên "Resale" nhưng ``code = 'sale-fb'``, và biến env
    ``SALE_FB_CHANNEL_IDS`` đang nuôi cả báo cáo "doanh thu Sale FB".

    ⚠️ THÊM KÊNH VÀO ``SALE_FB_CHANNEL_IDS`` SẼ ĐỔI LUÔN SỐ CỦA TAB RESALE.
    Hai báo cáo dùng chung một công tắc là quyết định có chủ ý (user chốt
    2026-08-26), không phải sơ suất. Cần tách thì thêm env ``RESALE_CHANNEL_IDS``
    riêng và chỉ sửa hàm này.

    Đọc biến module TẠI THỜI ĐIỂM GỌI chứ không bind lúc import: ``conftest.py``
    có fixture autouse patch hằng số này thành ``(9_999_000_000,)`` cho cả suite
    (để pk ``Channel`` tự tăng không tình cờ chạm 24). Module nào
    ``from .shared import SALE_FB_CHANNEL_IDS`` sẽ giữ giá trị cũ và test hoá ra
    XANH GIẢ.
    """
    return SALE_FB_CHANNEL_IDS


def sale_report_channel_ids() -> list[int]:
    """Ids các kênh thuộc phạm vi báo cáo sale = kênh CÓ gán nhân viên phòng Ads.

    Định nghĩa nghiệp vụ: kênh ad-driven (page Facebook / tài khoản Google Ads do
    một nhân viên phòng Ads phụ trách, gán qua ``UserChannel``). Kênh KHÔNG có NV
    Ads gán vào — Resale, KOC/hàng mẫu, WEB, KiotViet, sàn/store, và cả page
    Facebook chưa gán ads — KHÔNG tính vào báo cáo sale.

    Thay cho whitelist id cứng cũ (luôn lệch mỗi khi mở kênh mới): phạm vi giờ
    TỰ CẬP NHẬT — gán NV Ads vào một kênh là kênh đó tự vào báo cáo, gỡ ra thì tự
    rời. Nguồn sự thật là dữ liệu gán kênh, không phải hằng số trong code.

    "Đang gán" = UserChannel active + user còn active + user chưa bị xoá + user
    thuộc phòng Ads (theo phòng CHÍNH hoặc phòng ban NHIỀU — NV Sale kiêm chạy ads
    vẫn tính) + user KHÔNG phải admin (role hệ thống). Bỏ điều kiện user-active sẽ
    đếm nhầm cả NV Ads đã nghỉ.
    """
    return list(
        UserChannel.objects.filter(
            is_active=True,
            user__is_active=True,
            user__deleted_at__isnull=True,
            channel__deleted_at__isnull=True,
        )
        # "NV Ads" tính theo CẢ phòng chính lẫn phòng ban nhiều: NV Sale kiêm chạy
        # ads (phòng chính Sale + M2M có Ads) vẫn được coi là người phụ trách kênh.
        .filter(department_member_q(DepartmentID.ADS, prefix="user__"))
        .exclude(user__role_assignments__role__is_system=True)
        .values_list("channel_id", flat=True)
        .distinct()
    )


class SalesReportChannel:
    """DEPRECATED — whitelist id cứng gây lệch khi mở kênh mới (thiếu kênh âm thầm).

    Không còn dùng để scope báo cáo sale; đã thay bằng ``sale_report_channel_ids()``
    (kênh có gán NV phòng Ads). Giữ lại để không vỡ import cũ; sẽ gỡ sau.
    """

    ALL = (4, 5, 6, 7, 9, 28, 42)


class SalesReportBranch:
    """Scope branch_id cho báo cáo sale."""

    ALL = ("30965", "231174", "1000000477")


class KiotVietReportChannel:
    """Scope channel cho báo cáo sale: chỉ tính 2 KiotViet channel này."""

    NEW = 4
    OLD = 5
    ALL = (NEW, OLD)
