"""Test các data migration của orders chạy THẬT trên database trống riêng
(scratch DB) — gộp từ test_price_book_migration.py (orders.0112) và
test_return_slip_migration.py (orders.0101), phần hạ tầng scratch-db dùng chung.

Về orders.0112_price_books:
    Chạy migration THẬT trên một database trống riêng (0111 → seed ProductPackage
    hình dạng prod → 0112) để xác nhận backfill giá hiện có sinh đúng
    ProductPackagePrice — đây là phần rủi ro nhất của migration vì chạy trên dữ
    liệu production thật, còn 4 test ở `test_price_book_service.py` chỉ chạy trên
    DB test tạo mới (`--create-db`) nên vòng lặp backfill không bao giờ có dữ liệu
    để lặp qua.

Về orders.0101_orderrecall_return_slip_code_array:
    Chạy migration THẬT trên một database trống riêng (0100 → seed dữ liệu hình
    dạng prod → 0101) để bắt các lỗi chỉ xuất hiện khi migration chạy trên dữ
    liệu thật, chứ không chỉ gọi SQL function `__split_return_slip_code_0101`
    tách biệt (unit-test kiểu đó không phát hiện được lỗi ở ALTER COLUMN ...
    USING, DROP DEFAULT/SET DEFAULT, hay việc cột có thực sự đổi type không).

Không dùng django-test-migrations (không có trong requirements) — dùng cùng
pattern scratch-db + MigrationExecutor như
`apps/inventory/tests/test_return_entry_migration.py`.
"""

from __future__ import annotations

import contextlib
import importlib
from decimal import Decimal

import psycopg
import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.models.signals import post_migrate

# ~20 s/test: migrate scratch DB thật. CI chạy đủ; local bỏ qua bằng -m "not slow".
pytestmark = pytest.mark.slow

ORDERS = "orders"

PRICE_BOOK_BEFORE = "0111_order_restored_from"
PRICE_BOOK_AFTER = "0112_price_books"

RETURN_SLIP_BEFORE = "0100_alter_order_sku"
RETURN_SLIP_AFTER = "0101_orderrecall_return_slip_code_array"

# Database trống riêng để chạy migration thật, tách khỏi DB test chính.
ALIAS = "default"
DB_NAME = "test_mig_orders_scratch"


def _admin_sql(sql: str) -> None:
    """Chạy lệnh cấp cluster (CREATE/DROP DATABASE) qua DB 'postgres'."""
    cfg = settings.DATABASES["default"]
    with psycopg.connect(
        host=cfg["HOST"], port=cfg["PORT"], user=cfg["USER"],
        password=cfg["PASSWORD"], dbname="postgres", autocommit=True,
    ) as conn:
        conn.execute(sql)


@pytest.fixture
def scratch_db(django_db_setup, django_db_blocker):
    """Trỏ alias `default` sang một database trống để migrate THẬT (có commit).

    Phải mượn chính alias `default` chứ không tạo alias phụ: hàm backfill của
    0112 query qua manager mặc định (`ProductPackage.objects....` không có
    `.using()`) → luôn đi vào connection `default`; migration 0101 cũng chạy
    RunSQL qua connection mặc định. Trả DB test cũ lại nguyên trạng ở teardown.
    """
    _admin_sql(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
    _admin_sql(f'CREATE DATABASE "{DB_NAME}"')

    default_cfg = settings.DATABASES[ALIAS]
    original_name = default_cfg["NAME"]
    with contextlib.suppress(Exception):
        connections[ALIAS].close()
        del connections[ALIAS]
    default_cfg["NAME"] = DB_NAME
    connections.__dict__.pop("settings", None)
    try:
        with django_db_blocker.unblock():
            yield ALIAS
    finally:
        with contextlib.suppress(Exception):
            connections[ALIAS].close()
            del connections[ALIAS]
        default_cfg["NAME"] = original_name
        connections.__dict__.pop("settings", None)
        _admin_sql(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')


def _migrate(alias: str, target: str) -> None:
    """Migrate tới `target`; tắt post_migrate vì DB tạm chỉ có schema của
    orders + dependency (contenttypes/permissions không cần thiết ở đây)."""
    receivers = post_migrate.receivers
    post_migrate.receivers = []
    post_migrate.sender_receivers_cache.clear()
    try:
        call_command("migrate", ORDERS, target, database=alias, verbosity=0)
    finally:
        post_migrate.receivers = receivers
        post_migrate.sender_receivers_cache.clear()


def _state_apps(alias: str, migration: str):
    executor = MigrationExecutor(connections[alias])
    return executor.loader.project_state([(ORDERS, migration)]).apps


def _seed_price_book(alias: str):
    """Seed Product + ProductPackage hình dạng prod ở state 0111 (chưa có PriceBook)."""
    apps = _state_apps(alias, PRICE_BOOK_BEFORE)
    Product = apps.get_model(ORDERS, "Product")
    ProductPackage = apps.get_model(ORDERS, "ProductPackage")

    product = Product.objects.using(alias).create(
        sku="MIG-PB-1", name="SP migration giá", price=Decimal("50")
    )
    pkg1 = ProductPackage.objects.using(alias).create(
        product=product, quantity=1, price=Decimal("120.50")
    )
    pkg2 = ProductPackage.objects.using(alias).create(
        product=product, quantity=2, price=Decimal("230.00")
    )
    return {"product": product, "packages": [pkg1, pkg2]}


def test_migration_0112_backfills_existing_package_prices(scratch_db):
    alias = scratch_db
    _migrate(alias, PRICE_BOOK_BEFORE)
    seeded = _seed_price_book(alias)

    # Chính nó: đây là bước rủi ro nhất — vòng lặp backfill chạy trên
    # ProductPackage đã tồn tại từ trước (hình dạng prod thật).
    _migrate(alias, PRICE_BOOK_AFTER)

    apps = _state_apps(alias, PRICE_BOOK_AFTER)
    PriceBook = apps.get_model(ORDERS, "PriceBook")
    ProductPackagePrice = apps.get_model(ORDERS, "ProductPackagePrice")

    # 1. Seed đúng 2 bảng giá, facebook là mặc định (sort_order=0), web sort_order=1.
    books = {b.code: b for b in PriceBook.objects.using(alias).all()}
    assert set(books) == {"facebook", "web"}
    assert books["facebook"].is_default is True
    assert books["facebook"].sort_order == 0
    assert books["web"].is_default is False
    assert books["web"].sort_order == 1

    # 2. Mỗi ProductPackage có sẵn được backfill đúng giá vào bảng facebook.
    prices = {pp.package_id: pp for pp in ProductPackagePrice.objects.using(alias).all()}
    assert len(prices) == len(seeded["packages"])
    for pkg in seeded["packages"]:
        pp = prices[pkg.id]
        assert pp.price_book_id == books["facebook"].id
        assert pp.price == pkg.price

    # 3. Bảng web KHÔNG được backfill giá — chưa khai giá ở nền tảng đó,
    #    resolve fallback về mặc định là việc của service ở task sau.
    assert not ProductPackagePrice.objects.using(alias).filter(price_book=books["web"]).exists()


def test_migration_0112_backfill_is_idempotent(scratch_db):
    """Gọi lại đúng hàm RunPython thật lần 2 (không xoá dữ liệu trước) không
    được sinh trùng bảng giá / dòng giá hay raise lỗi — bảo vệ trường hợp phải
    re-run migration thủ công trên prod (vd sau một lần chạy dở dang)."""
    alias = scratch_db
    _migrate(alias, PRICE_BOOK_BEFORE)
    seeded = _seed_price_book(alias)
    _migrate(alias, PRICE_BOOK_AFTER)

    apps = _state_apps(alias, PRICE_BOOK_AFTER)
    PriceBook = apps.get_model(ORDERS, "PriceBook")
    ProductPackagePrice = apps.get_model(ORDERS, "ProductPackagePrice")

    # Tên module bắt đầu bằng số nên không import được bằng cú pháp `from...import`
    # — nạp bằng importlib, đúng cách Django's MigrationLoader tự nạp migration.
    migration_module = importlib.import_module("apps.orders.migrations.0112_price_books")
    migration_module.seed_and_backfill(apps, None)

    assert PriceBook.objects.using(alias).count() == 2  # không sinh thêm bảng giá trùng code
    books = {b.code: b for b in PriceBook.objects.using(alias).all()}
    prices = list(ProductPackagePrice.objects.using(alias).all())
    assert len(prices) == len(seeded["packages"])  # không sinh thêm dòng giá trùng
    for pkg in seeded["packages"]:
        pp = next(p for p in prices if p.package_id == pkg.id)
        assert pp.price_book_id == books["facebook"].id
        assert pp.price == pkg.price


def _seed_return_slip(alias: str):
    """Seed OrderRecall hình dạng prod ở state 0100 (return_slip_code còn là
    varchar(64) đơn, không phải mảng).

    Mỗi OrderRecall là OneToOne với Order → cần 1 Order riêng/case, và Order
    bắt buộc FK channel + customer (PROTECT, không null) nên phải seed cả 2.
    Các field khác của Order/Channel/Customer đều có default nên không cần
    truyền.

    LƯU Ý: bỏ qua case NULL và case >64 ký tự — cột `return_slip_code` ở
    state 0100 là varchar(64) NOT NULL (model không set null=True, và chưa
    từng có migration nới max_length). Postgres tự chặn cả hai ở tầng INSERT
    (đã verify thủ công qua psql: INSERT chuỗi >64 ký tự vào cột varchar(64)
    ném lỗi "value too long for type character varying(64)" ngay lập tức,
    NULL vi phạm NOT NULL). Nghĩa là dữ liệu prod thật KHÔNG THỂ tồn tại ở 2
    dạng này trước khi migration chạy — nhánh `left(..., 64)` truncate trong
    SQL function là phòng thủ nhưng không thể reach được từ dữ liệu thật.
    Case biên được test thay bằng chuỗi đúng 64 ký tự (giá trị hợp lệ dài
    nhất có thể) để xác nhận không bị cắt/lệch off-by-one.
    """
    apps = _state_apps(alias, RETURN_SLIP_BEFORE)
    Channel = apps.get_model("channels_core", "Channel")
    Customer = apps.get_model("customers", "Customer")
    Order = apps.get_model(ORDERS, "Order")
    OrderRecall = apps.get_model(ORDERS, "OrderRecall")

    channel = Channel.objects.using(alias).create(code="MIG", name="Kênh Mig")
    customer = Customer.objects.using(alias).create(
        full_name="KH Mig", code="MIGCUST"
    )

    def mk_order(tag):
        return Order.objects.using(alias).create(
            channel=channel, customer=customer, order_code=f"ORD-MIG-{tag}"
        )

    long64 = "L" * 64  # đúng max_length hợp lệ, không bị cắt.

    single = OrderRecall.objects.using(alias).create(
        order=mk_order("single"), return_slip_code="PT-001"
    )
    multi = OrderRecall.objects.using(alias).create(
        order=mk_order("multi"), return_slip_code="PT-001, PT-002"
    )
    blank = OrderRecall.objects.using(alias).create(
        order=mk_order("blank"), return_slip_code=""
    )
    boundary = OrderRecall.objects.using(alias).create(
        order=mk_order("boundary"), return_slip_code=long64
    )
    return {
        "single": single, "multi": multi, "blank": blank, "boundary": boundary,
        "long64": long64,
    }


def test_migration_0101_converts_return_slip_code_to_array(scratch_db):
    alias = scratch_db
    _migrate(alias, RETURN_SLIP_BEFORE)
    seeded = _seed_return_slip(alias)

    # Chính nó: bước này chết nếu forwards() không split/trim/truncate đúng,
    # hoặc nếu USING không tự chạy được (subquery trong transform expression).
    _migrate(alias, RETURN_SLIP_AFTER)

    apps = _state_apps(alias, RETURN_SLIP_AFTER)
    OrderRecall = apps.get_model(ORDERS, "OrderRecall")

    single = OrderRecall.objects.using(alias).get(pk=seeded["single"].pk)
    assert single.return_slip_code == ["PT-001"]

    multi = OrderRecall.objects.using(alias).get(pk=seeded["multi"].pk)
    assert multi.return_slip_code == ["PT-001", "PT-002"]

    blank = OrderRecall.objects.using(alias).get(pk=seeded["blank"].pk)
    assert blank.return_slip_code == []

    boundary = OrderRecall.objects.using(alias).get(pk=seeded["boundary"].pk)
    assert boundary.return_slip_code == [seeded["long64"]]
    assert len(boundary.return_slip_code[0]) == 64
