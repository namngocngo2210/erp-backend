"""Port đọc chi phí quảng cáo (trả nợ Lô 5, xem
``docs/import-layering-debt-paydown-2026-08.md``).

``_repositories_reports.py`` cần số tiền đã tiêu của từng tài khoản quảng cáo để
tính tỉ lệ chi phí / doanh thu. Trước đây nó import thẳng model của
``facebook_ads`` và ``google_ads`` — hai app nằm ở tầng TRÊN ``orders``.

Điểm đáng chú ý: orders vốn đã không quan tâm model là gì. Hai helper cũ nhận
``model`` làm THAM SỐ rồi query y hệt nhau cho cả hai nguồn. Nên port này không
bọc model — nó trả thẳng **số liệu**, và mỗi app quảng cáo tự đăng ký provider
của mình ở ``AppConfig.ready()``.

Ngày tháng đã được orders parse sẵn thành ``date`` (hoặc ``None`` nếu chuỗi hỏng)
trước khi truyền xuống, để logic parse không phải nhân đôi ở mỗi adapter.
"""

from __future__ import annotations

from datetime import date as date_cls
from decimal import Decimal
from enum import Enum
from typing import NamedTuple, Protocol


class AdsSpendSource(str, Enum):
    """Nguồn quảng cáo. Giá trị dùng làm khoá registry."""

    FACEBOOK = "facebook"
    GOOGLE = "google"


class AdAccountScope(NamedTuple):
    """Phạm vi tài khoản quảng cáo của một user.

    `ids`: PK dùng để lọc, đã trải sang MỌI bản sao của cùng một tài khoản thật.
    `None` = không lọc gì cả (user xem toàn bộ).

    `count`: số tài khoản THẬT, đếm theo id ngoài của nền tảng. Tách khỏi `len(ids)`
    vì `ids` có cả bản sao — hiển thị `len(ids)` sẽ báo "2 tài khoản" cho người chỉ
    được gán 1.
    """

    ids: list[int] | None
    # Field cố ý trùng tên method tuple.count — runtime hợp lệ (namedtuple cho phép
    # shadow count/index), đổi tên là đổi API của mọi caller báo cáo ads.
    count: int | None  # type: ignore[assignment]


class AdsSpendPort(Protocol):
    """Số liệu chi tiêu quảng cáo mà báo cáo của orders cần."""

    def account_scope(self, account_ids: list[int]) -> AdAccountScope:
        """Trải list PK sang mọi bản sao của cùng tài khoản thật, kèm số đếm thật.

        Cùng một tài khoản quảng cáo được lưu thành nhiều dòng: `FacebookAdAccount`
        unique theo `(app, account_id)`, `GoogleAdsAccount` theo `(config,
        customer_id)`. Mapping kênh chỉ trỏ được một dòng, còn dữ liệu chi tiêu có thể
        treo dưới dòng khác — nên lọc thẳng theo PK là cách làm báo cáo trắng bảng mà
        không để lại dấu vết.

        `account_ids` rỗng → `AdAccountScope([], 0)`.
        """
        ...

    def account_groups(self, account_ids: list[int]) -> list[list[int]]:
        """Gom PK thành nhóm theo tài khoản THẬT — mỗi nhóm là mọi bản sao của nó.

        Đầu vào là PK lấy từ mapping kênh (`ChannelAdAccount`), vốn chỉ trỏ được
        MỘT bản sao. Đầu ra gồm cả những bản sao chưa được map, nhờ đó caller vừa
        cộng được chi tiêu treo dưới bản khác, vừa biết hai PK khác nhau thực ra
        là cùng một tài khoản nên không cộng trùng.

        `account_ids` rỗng → `[]`, không chạm DB.
        """
        ...

    def spend_rows_by_account(
        self,
        *,
        account_ids: list[int],
        date_from: date_cls | None = None,
        date_to: date_cls | None = None,
    ) -> list[dict]:
        """Chi tiêu gộp theo (ngày, tài khoản).

        Trả list dict có khoá ``date``, ``ad_account_id``, ``spend``.
        ``account_ids`` rỗng → trả list rỗng, không chạm DB.
        """
        ...

    def spend_rows_by_account_product(
        self,
        *,
        account_ids: list[int],
        date_from: date_cls | None = None,
        date_to: date_cls | None = None,
    ) -> list[dict]:
        """Chi tiêu gộp theo (ngày, tài khoản, SẢN PHẨM đã gán cho chiến dịch).

        Trả list dict có khoá ``date``, ``ad_account_id``, ``product_id``, ``spend``.
        ``product_id`` = None nghĩa là chiến dịch chưa được gán sản phẩm nào — CỐ Ý
        vẫn trả về để caller dựng được rổ "chưa gán" thay vì làm hụt tổng.

        Rows trả về ĐÃ KHỬ BẢN SAO tài khoản: mỗi adapter tự biết grain của mình
        (Facebook lưu chi tiêu ở tầng ad — vốn unique toàn cục nên chỉ cần cộng;
        Google ở tầng campaign — cùng một campaign nằm dưới nhiều dòng tài khoản nên
        phải lấy ``Max`` trước). Caller CHỈ việc cộng, không được gộp thêm bằng
        ``Max`` như đường chi phí theo tài khoản.

        ``account_ids`` rỗng → trả list rỗng, không chạm DB.
        """
        ...

    def total_spend(
        self,
        *,
        date_from: date_cls | None = None,
        date_to: date_cls | None = None,
    ) -> Decimal:
        """Tổng chi tiêu toàn bộ tài khoản trong khoảng ngày. Không có số → 0.

        Mỗi tài khoản THẬT chỉ được tính một lần cho mỗi ngày, kể cả khi nó có
        nhiều dòng bản sao — xem docstring của từng adapter.
        """
        ...


_providers: dict[str, AdsSpendPort] = {}


def register_ads_spend_provider(source: AdsSpendSource, provider: AdsSpendPort) -> None:
    """App quảng cáo gọi hàm này ở ``AppConfig.ready()`` để cắm adapter."""
    _providers[AdsSpendSource(source).value] = provider


def ads_spend_provider(source: AdsSpendSource) -> AdsSpendPort:
    """Resolve provider đã đăng ký; lỗi rõ ràng nếu wiring chưa chạy."""
    key = AdsSpendSource(source).value
    provider = _providers.get(key)
    if provider is None:
        raise RuntimeError(
            f"AdsSpendPort cho nguồn '{key}' chưa được đăng ký — "
            "AppConfig.ready() của app quảng cáo tương ứng phải chạy trước "
            "(app có trong INSTALLED_APPS?)."
        )
    return provider
