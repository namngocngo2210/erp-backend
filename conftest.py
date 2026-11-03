"""Cấu hình pytest dùng chung cho toàn backend."""

import itertools

import pytest


@pytest.fixture(autouse=True)
def _isolate_event_bus():
    """Snapshot/restore subscriber của `event_bus` quanh MỖI test.

    Bus là singleton toàn process; subscriber production đăng ký lúc `ready()`.
    Test nào gọi `event_bus.clear()` (để bắt event bằng lambda riêng) sẽ xoá luôn
    handler production và test chạy SAU nó — ở app khác — mất subscriber mà không
    hay (phụ thuộc thứ tự chạy, chỉ lộ khi đổi cách chọn test). Trước đây vá bằng
    3 autouse chép nhau ở hr/cnv_loyalty/warranty gọi lại `register()`; app chưa
    có bản chép (orders, inventory, kiotviet…) vẫn hở.

    Fixture này chụp lại danh sách handler trước test và trả về y nguyên sau test,
    nên `event_bus.clear()`/`subscribe()` trong test chỉ có hiệu lực trong test đó.
    Đọc thẳng `_handlers` vì bus không có API snapshot; nếu bus đổi cấu trúc thì
    test `apps/common/tests/test_events.py` sẽ đỏ trước.
    """
    from apps.common.events import event_bus

    snapshot = {event_type: list(handlers) for event_type, handlers in event_bus._handlers.items()}
    yield
    event_bus._handlers.clear()
    for event_type, handlers in snapshot.items():
        event_bus._handlers[event_type] = list(handlers)


@pytest.fixture
def isolated_cache(settings, request):
    """LocMemCache riêng cho từng test, thay cho `cache.clear()`.

    `CACHES["default"]` mặc định là Redis THẬT dùng chung giữa mọi test, giữa các
    worktree chạy song song và với dev server — `RedisCache.clear()` là FLUSHDB,
    xoá lock/counter/throttle của test khác đang chạy. Test nào cần cache sạch
    (endpoint có cache TTL, counter quota, token cache…) thì xin fixture này:
    nó trỏ `default` sang LocMemCache với LOCATION duy nhất theo nodeid (LocMem
    cùng tên dùng chung một dict toàn cục), nên không cần dọn gì cả.

    Dùng: `@pytest.mark.usefixtures("isolated_cache")` trên class/module, hoặc nhận
    làm tham số. Với `TestCase` (unittest) fixture còn gắn `self.isolated_cache`
    để test cần xoá cache GIỮA chừng gọi `self.isolated_cache.clear()` — đây là
    LocMem cục bộ, an toàn. Guard `apps/common/tests/test_no_global_cache_clear_in_tests.py`
    cấm `cache.clear()` mới trong tests.
    """
    from django.core.cache import cache

    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"isolated-{request.node.nodeid}",
        }
    }
    if request.instance is not None:
        request.instance.isolated_cache = cache
    yield cache


@pytest.fixture(autouse=True)
def _disable_default_throttles(settings):
    """Tắt TRẦN MẶC ĐỊNH (user/anon) trong test — không tắt throttle nghiệp vụ.

    Vì sao bắt buộc: bộ đếm nằm trong Redis THẬT dùng chung giữa mọi test (và giữa
    các worktree chạy song song), còn key thì chỉ theo `user.pk` — mà pk cứ lặp
    lại 1, 2, 3... ở từng test. Cả suite cộng dồn vào chung một bucket sẽ chạm trần
    3000/hour và đẻ ra lỗi 429 ngẫu nhiên ở những test chẳng liên quan gì tới
    throttle. Đây đúng là loại flaky tốn cả ngày để truy.

    Trần theo scope nghiệp vụ (login/warranty_register/order_print) KHÔNG bị đụng
    tới: chúng là hành vi bảo mật đang được test tử tế.

    Test nào cần chính trần mặc định thì tự bật lại bằng cách ghi đè
    `settings.REST_FRAMEWORK` (xem apps/common/tests/test_throttling.py).
    """
    rates = dict(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    # Chỉ ghi đè scope ĐANG CÓ, không tự thêm key: nếu ai đó xoá scope khỏi
    # settings thì test kiểm tra cấu hình phải đỏ, chớ không được fixture này
    # âm thầm dựng lại hộ.
    for scope in ("user", "anon"):
        if scope in rates:
            rates[scope] = ""
    settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": rates}


@pytest.fixture(autouse=True)
def _isolate_hardcoded_channel_ids(settings, monkeypatch):
    """Đẩy các id kênh hard-code ra ngoài tầm với của pk tự tăng trong test.

    Vì sao bắt buộc: pk của `Channel` là serial tự tăng, còn sequence Postgres
    KHÔNG rollback theo transaction test — nó chỉ tăng lên, kể cả khi
    transaction test bị huỷ. Suite càng chạy nhiều test tạo `Channel` thì
    sequence càng tiến gần tới các hằng số id kênh đang bị hard-code trong
    code production. Có HAI hằng số như vậy, cùng một lớp lỗi, khác điểm rơi:

    - `KOC_CHANNEL_ID` (mặc định 15, `config/settings_parts/base.py`) — dùng ở
      `Channel.is_koc` và `_ads_excluded_channel_ids`
      (`apps/orders/reporting/shared.py`).
    - `SALE_FB_CHANNEL_IDS = (24,)` — hằng số Python cứng ngay trong module
      `apps/orders/reporting/shared.py`, cũng được `_ads_excluded_channel_ids`
      đọc để loại kênh sale-fb khỏi báo cáo ads.

    Hễ có một lần pk `Channel` chạm đúng 1 trong 2 giá trị trên, một `Channel`
    hoàn toàn không liên quan gì tới KOC hay sale-fb sẽ bị
    `_ads_excluded_channel_ids` âm thầm loại khỏi báo cáo ads — sai ở một chỗ
    chẳng liên quan gì tới điều đang được test, cộng dồn theo số lượng test
    trong suite chứ không theo test nào cụ thể (đúng kiểu flaky đã từng ăn ở
    throttle, xem fixture `_disable_default_throttles` phía trên). Đã bắt tận
    tay: trong 1 lần chạy gộp `facebook_ads + google_ads + test_ads_reports.py`,
    kênh `fb-staffed` biến mất khỏi báo cáo vì pk của nó rơi đúng 24
    (`SALE_FB_CHANNEL_IDS`) — không phải do `KOC_CHANNEL_ID` như nghi ban đầu;
    hai bẫy độc lập, chỉ trùng triệu chứng.

    Fixture này CHỈ cách ly cho test, không sửa bản chất vấn đề: bản thân việc
    hard-code id kênh trong code production vẫn là rủi ro thật ở môi trường
    khác (staging mới, restore DB khác thứ tự...) — kênh nào đó nhận đúng id
    hard-code sẽ âm thầm biến mất khỏi báo cáo ads mà không ai hay. Đây là nợ
    kỹ thuật cần một MR riêng (chuyển sang nhận diện thuần theo `code`, hoặc
    early-warning nếu id trùng), không xử ở đây.

    Cách xử — đặt mỗi hằng số ra một mốc pk tự tăng trong test không bao giờ
    chạm tới, và CỐ TÌNH khác nhau giữa hai hằng số (để nếu sau này có test
    nào lẫn lộn hai khái niệm KOC / sale-fb thì lộ ra ngay thay vì tình cờ
    khớp):

    - `KOC_CHANNEL_ID` → `2_000_000_000`, qua fixture `settings` của
      pytest-django (đọc qua Django settings nên override kiểu này ăn thẳng).
    - `SALE_FB_CHANNEL_IDS` → `(9_999_000_000,)`, qua `monkeypatch.setattr`
      trực tiếp lên module `apps.orders.reporting.shared` — hằng số này
      KHÔNG phải Django setting, chỉ là biến module-level, nên không thể ghi
      đè qua fixture `settings`. (Hằng số còn được re-export ở
      `apps/orders/repositories.py` và facade `apps/orders/_repositories_reports.py`,
      nhưng đã grep xác nhận không có chỗ nào trong code production đọc nó qua
      đường re-export đó lúc chạy — `_ads_excluded_channel_ids` đọc thẳng biến
      global của module `reporting.shared` nơi nó được ĐỊNH NGHĨA, nên phải patch
      đúng module đó; patch lên facade chỉ đổi binding re-export, vô tác dụng.
      Refactor 2026-08 tách `_repositories_reports.py` thành package
      `apps/orders/reporting/` — điểm patch dời theo sang `reporting.shared`.)

    Sau khi override, việc nhận diện KOC trong test chỉ còn dựa vào
    `code == "koc"` (đúng như chính comment trong `_ads_excluded_channel_ids`
    đã nói: match cả code vì id kênh sinh tự động nên có thể khác nhau giữa
    các môi trường), còn kênh sale-fb trong test sẽ không còn kênh nào vô tình
    bị loại theo `SALE_FB_CHANNEL_IDS` nữa (không có cơ chế nhận diện thay thế
    theo code cho sale-fb — nếu test nào thật sự cần mô phỏng kênh sale-fb thì
    phải tự ghi đè lại `SALE_FB_CHANNEL_IDS` bằng đúng cách patch ở trên).

    Test nào cố tình tạo `Channel` với `id=settings.KOC_CHANNEL_ID` (ví dụ
    `apps/kiotviet/tests/test_access_token.py`,
    `apps/orders/tests/test_order_create_api.py`,
    `apps/orders/tests/test_order_list_filters_api.py`) không cần đổi gì: các
    test đó đọc lại `settings.KOC_CHANNEL_ID` tại thời điểm chạy nên tự nhận
    giá trị đã override ở đây, và channel tạo ra vẫn được nhận diện qua
    `code="koc"` sẵn có.
    """
    settings.KOC_CHANNEL_ID = 2_000_000_000

    from apps.orders.reporting import shared as reporting_shared

    monkeypatch.setattr(reporting_shared, "SALE_FB_CHANNEL_IDS", (9_999_000_000,))


@pytest.fixture
def read_streaming_response():
    """Đọc hết body của một streaming response, kể cả khi nó stream đường async.

    `response.getvalue()` và `b"".join(response.streaming_content)` chỉ đi đường
    đồng bộ; gặp response async chúng rơi vào nhánh dự phòng của Django và che
    mất đúng thứ cần kiểm chứng.
    """
    from asgiref.sync import async_to_sync

    async def _collect(response) -> bytes:
        return b"".join([part async for part in response])

    def _read(response) -> bytes:
        if getattr(response, "is_async", False):
            return async_to_sync(_collect)(response)
        return b"".join(response.streaming_content)

    return _read


@pytest.fixture(autouse=True)
def _sysconfig_test_mode(settings):
    """Toàn suite đọc cài đặt hệ thống ở TEST MODE: override_sysconfig → override_settings
    → default; không chạm DB/Redis. 155 chỗ `override_settings(GEMINI_API_KEY=…)` cũ
    nhờ vậy vẫn chạy. Test cần bảng thật: đặt `settings.SYSCONFIG_TEST_MODE = False`
    rồi `sysconfig.reset()` (xem apps/sysconfig/tests/test_store.py::sysconfig_db)."""
    settings.SYSCONFIG_TEST_MODE = True
    from apps.sysconfig.service import sysconfig

    sysconfig.reset()
    yield
    sysconfig.reset()


@pytest.fixture
def order_factory(db):
    """Dựng một `Order` hợp lệ tối thiểu — nguồn sự thật DUY NHẤT cho mọi app.

    `Order.channel` và `Order.customer` là FK PROTECT không null nên không tạo Order
    trơn được: mỗi lần gọi tự dựng Channel RIÊNG (mã theo counter, không get_or_create
    theo mã cố định — test scope/parity không được vô tình dùng chung kênh giữa các
    đơn không liên quan) và Customer theo `phone` (get_or_create theo
    `phone_normalized` vì Customer có unique partial trên cột này: cùng số điện
    thoại → cùng khách, đúng nghiệp vụ; số mặc định là duy nhất theo counter nên
    mặc định mỗi đơn một khách).

    Tham số:
    - `prefix`: tiền tố mã kênh/khách (`{prefix}-ch-{n}`), mặc định "test".
    - `items`: số dòng hàng tạo kèm (Product mới + OrderItem 2 × 100.000).
    - `channel=`, `customer=`: dùng lại đối tượng có sẵn thay vì tạo mới.
    - `deleted=True`: đánh dấu `deleted_at` (soft delete) sau khi tạo.
    - `**kwargs`: field bất kỳ của `Order` (`name`, `phone`, `status`, `staff`,
      `shipping_code`, `total_amount`…), đè lên mặc định.

    App cần mặc định riêng (vd cnv_loyalty luôn có line item) override fixture
    cùng tên và gọi lại fixture này — không chép lại logic dựng Order.
    """
    from django.utils import timezone

    from apps.catalog.models import Product
    from apps.channels_core.models import Channel
    from apps.customers.models import Customer
    from apps.orders.models import Order, OrderItem

    counter = itertools.count(1)

    def _make(
        *,
        prefix: str = "test",
        items: int = 0,
        channel=None,
        customer=None,
        deleted: bool = False,
        **kwargs,
    ):
        seq = next(counter)
        name = kwargs.pop("name", "Khach test")
        phone = kwargs.pop("phone", f"09{seq:08d}")
        if channel is None:
            code = f"{prefix}-ch-{seq}"
            while Channel.objects.filter(code=code).exists():
                seq = next(counter)
                code = f"{prefix}-ch-{seq}"
            channel = Channel.objects.create(code=code, name=f"Test Channel {prefix} {seq}")
        if customer is None:
            if phone:
                customer, _ = Customer.objects.get_or_create(
                    phone_normalized=phone,
                    deleted_at__isnull=True,
                    defaults={"full_name": name or "Khach test", "phone_raw": phone},
                )
            else:
                customer = Customer.objects.create(full_name=name or "Khach test")
        data = {
            "channel": channel,
            "customer": customer,
            "name": name,
            "phone": phone,
            "quantity": 1,
        }
        data.update(kwargs)
        order = Order.objects.create(**data)
        for index in range(items):
            product = Product.objects.create(
                name=f"Sản phẩm {prefix} {seq}-{index}",
                sku=f"{prefix.upper()}-SKU-{seq}-{index}",
                price=100_000,
            )
            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=product.name,
                sku=product.sku,
                quantity=2,
                unit_price=100_000,
                line_total=200_000,
            )
        if deleted:
            order.deleted_at = timezone.now()
            order.save(update_fields=["deleted_at"])
        return order

    return _make
