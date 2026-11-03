"""Test API CRUD bảng giá."""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.validators import UniqueValidator

from apps.catalog.models import PriceBook, Product, ProductCategory
from apps.orders.transport.views.order_views_impl.price_book_views import (
    _CODE_CONFLICT_DETAIL,
    _DEFAULT_CONFLICT_DETAIL,
)
from apps.users.tests.helpers import make_admin


class PriceBookApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin("pricebook-admin"))

    def test_list_returns_seeded_books_sorted(self):
        response = self.client.get("/api/orders/price-books")
        self.assertEqual(response.status_code, 200)
        codes = [row["code"] for row in response.json()]
        self.assertEqual(codes[:2], ["facebook", "web"])

    def test_create_price_book(self):
        response = self.client.post(
            "/api/orders/price-books", {"code": "tiktok", "name": "TikTok", "sort_order": 2}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.json()["is_default"])

    def test_setting_default_demotes_previous_default(self):
        web = PriceBook.objects.get(code="web")
        response = self.client.patch(
            f"/api/orders/price-books/{web.id}", {"is_default": True}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PriceBook.objects.get(code="web").is_default)
        self.assertFalse(PriceBook.objects.get(code="facebook").is_default)

    def test_cannot_delete_default_book(self):
        facebook = PriceBook.objects.get(code="facebook")
        response = self.client.delete(f"/api/orders/price-books/{facebook.id}")
        self.assertEqual(response.status_code, 400)
        facebook.refresh_from_db()
        self.assertIsNone(facebook.deleted_at)

    def test_delete_non_default_soft_deletes(self):
        web = PriceBook.objects.get(code="web")
        response = self.client.delete(f"/api/orders/price-books/{web.id}")
        self.assertEqual(response.status_code, 204)
        web.refresh_from_db()
        self.assertIsNotNone(web.deleted_at)

    def test_delete_after_promoting_to_default_returns_400(self):
        """Khoá logic re-check is_default: nhánh DELETE phải đọc lại trạng thái is_default
        NGAY TRƯỚC khi save (qua select_for_update trong cùng transaction), không dựa vào
        giá trị đã fetch trước đó — bảo vệ trước race với một PATCH set-default xen giữa."""
        web = PriceBook.objects.get(code="web")
        promote = self.client.patch(f"/api/orders/price-books/{web.id}", {"is_default": True}, format="json")
        self.assertEqual(promote.status_code, 200)

        response = self.client.delete(f"/api/orders/price-books/{web.id}")
        self.assertEqual(response.status_code, 400)
        web.refresh_from_db()
        self.assertIsNone(web.deleted_at)
        self.assertTrue(web.is_default)

    def test_concurrent_default_conflict_returns_400_not_500(self):
        """Giả lập race giữa hai request cùng đặt is_default=True: patch bước hạ cờ
        (_demote_other_default_price_books trong catalog_write_service — nơi ORM-write
        của serializer đã dời xuống) thành no-op để mô phỏng trường hợp demote-query của
        request khác không kịp thấy bảng vừa được đặt mặc định (READ COMMITTED). Khi đó
        save() đụng partial unique constraint pricebook_one_default và raise IntegrityError
        — view phải trả 400 (lỗi nghiệp vụ) với đúng message "xung đột default", không phải
        để lộ 500 và cũng không được lẫn sang message trùng code (finding 3: phải phân biệt
        theo tên constraint)."""
        tiktok = PriceBook.objects.create(code="tiktok-race", name="TikTok Race", sort_order=5)
        target = (
            "apps.orders.application.services"
            ".catalog_write_service._demote_other_default_price_books"
        )
        with patch(target, return_value=None):
            response = self.client.patch(
                f"/api/orders/price-books/{tiktok.id}", {"is_default": True}, format="json"
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], _DEFAULT_CONFLICT_DETAIL)
        tiktok.refresh_from_db()
        self.assertFalse(tiktok.is_default)

    def test_patch_cannot_unset_is_default_on_default_book(self):
        """Finding review CRITICAL 2: PATCH đi thẳng qua serializer với partial=True,
        không có guard tương đương DELETE — tắt cờ mặc định qua đây làm
        default_price_book() trả None, kéo theo catalog trả giá 0 cho mọi gói chưa
        khai giá riêng ở bảng đang chọn."""
        facebook = PriceBook.objects.get(code="facebook")
        response = self.client.patch(
            f"/api/orders/price-books/{facebook.id}", {"is_default": False}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("is_default", response.json())
        facebook.refresh_from_db()
        self.assertTrue(facebook.is_default)

    def test_patch_cannot_deactivate_default_book(self):
        """Cùng lỗ hổng, đường khác: is_active=False trên bảng mặc định làm
        price_book_service._active_books() lọc mất bảng mặc định — hậu quả y hệt xoá."""
        facebook = PriceBook.objects.get(code="facebook")
        response = self.client.patch(
            f"/api/orders/price-books/{facebook.id}", {"is_active": False}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("is_active", response.json())
        facebook.refresh_from_db()
        self.assertTrue(facebook.is_active)

    def test_concurrent_duplicate_code_returns_400_not_500(self):
        """Finding 3: `code` có HAI lớp check-rồi-mới-save không lock trước khi đụng DB:
        (1) DRF tự gắn UniqueValidator cho field `code` (suy ra từ model field — xác nhận
        bằng runtime introspection: ``PriceBookSerializer().fields['code'].validators``
        chứa ``UniqueValidator``, khác với `is_default` — field đó KHÔNG có validator nào
        vì được khai tường minh trên serializer, đúng như docstring giải thích), và (2)
        `validate_code` thủ công trong serializer. Cả hai đều exists()-rồi-mới-save, nên
        dưới race thật (hai request cùng gửi code trùng gần như đồng thời) cả hai có thể
        cùng bị vượt qua trước khi một request khác commit, và request còn lại đụng thẳng
        unique constraint pricebook_code_active_uniq ở tầng DB khi save.

        Test bypass CẢ HAI lớp check để mô phỏng đúng tình huống cực đoan đó, rồi xác nhận:
        IntegrityError phải được phân loại đúng theo tên constraint và trả 400 với message
        trùng code — không được lẫn sang message "xung đột default" của finding trước, và
        tuyệt đối không được nuốt-nhầm rồi báo sai nguyên nhân."""
        validate_code_target = (
            "apps.orders.transport.serializers.order_serializers_impl"
            ".product_item_crm_serializers.PriceBookSerializer.validate_code"
        )
        with (
            # UniqueValidator DRF tự gắn cho field `code` (không phải do ta khai báo) chạy
            # TRƯỚC validate_code trong pipeline validate của DRF — phải bypass cả hai mới
            # tới được save().
            patch.object(UniqueValidator, "__call__", return_value=None),
            # validate_code bị patch thành bypass (mock không nhận self do patch thay class
            # attribute bằng object không phải descriptor) để mô phỏng request cùng qua
            # được bước kiểm tra trùng code trước khi request khác save trước.
            patch(validate_code_target, side_effect=lambda value: value),
        ):
            response = self.client.post(
                "/api/orders/price-books", {"code": "web", "name": "Web Duplicate"}, format="json"
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], _CODE_CONFLICT_DETAIL)
        self.assertNotEqual(response.json()["detail"], _DEFAULT_CONFLICT_DETAIL)


class ProductPricesSerializerTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin("product-prices-admin"))

    def _create(self, payload):
        return self.client.post("/api/orders/products", payload, format="json")

    def test_create_product_writes_prices_for_both_books(self):
        response = self._create(
            {
                "name": "SP đa nền tảng",
                "sku": "SKU-MP-01",
                "packages": [
                    {"quantity": 1, "prices": {"facebook": "100000", "web": "130000"}, "weight": 0.25},
                    {"quantity": 2, "prices": {"facebook": "180000"}},
                ],
            }
        )
        self.assertEqual(response.status_code, 201, response.content)
        packages = {pkg["quantity"]: pkg for pkg in response.json()["packages"]}
        self.assertEqual(packages[1]["prices"], {"facebook": "100000.00", "web": "130000.00"})
        # Gói 2 chưa khai giá web → KHÔNG fallback ở contract quản trị: vắng key.
        self.assertEqual(packages[2]["prices"], {"facebook": "180000.00"})

    def test_default_book_price_mirrors_into_legacy_columns(self):
        from apps.catalog.models import Product, ProductPackage

        self._create(
            {
                "name": "SP mirror",
                "sku": "SKU-MP-02",
                "packages": [{"quantity": 1, "prices": {"facebook": "90000", "web": "120000"}}],
            }
        )
        product = Product.objects.get(sku="SKU-MP-02")
        # Product.price và ProductPackage.price là mirror của BẢNG MẶC ĐỊNH, không phải web.
        self.assertEqual(str(product.price), "90000.00")
        self.assertEqual(str(ProductPackage.objects.get(product=product, quantity=1).price), "90000.00")

    def test_price_required_only_for_default_book(self):
        response = self._create(
            {"name": "Thiếu giá mặc định", "sku": "SKU-MP-03",
             "packages": [{"quantity": 1, "prices": {"web": "120000"}}]}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("packages", response.json())

    def test_update_replaces_prices(self):
        created = self._create(
            {"name": "SP sửa", "sku": "SKU-MP-04",
             "packages": [{"quantity": 1, "prices": {"facebook": "100000", "web": "130000"}}]}
        ).json()
        response = self.client.patch(
            f"/api/orders/products/{created['id']}",
            {"packages": [{"quantity": 1, "prices": {"facebook": "110000"}}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        # Bỏ giá web đi thì row giá web phải biến mất, không được sót lại.
        self.assertEqual(response.json()["packages"][0]["prices"], {"facebook": "110000.00"})

    def test_create_without_price_or_packages_returns_400(self):
        """Finding 1 (review Task 4): trước fix, không gửi cả `price` lẫn `packages`
        vẫn tạo được sản phẩm vì Product.price có default=0 và nhánh seed gói mặc
        định không đi qua _validate_packages — phá bất biến "bảng mặc định luôn có
        giá > 0". Sau fix phải trả 400 và KHÔNG được để lại row Product mồ côi."""
        from apps.catalog.models import Product

        response = self._create({"name": "Thiếu giá hoàn toàn", "sku": "SKU-MP-08"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("packages", response.json())
        self.assertFalse(Product.objects.filter(sku="SKU-MP-08").exists())

    def test_patch_keeps_prices_for_inactive_book(self):
        """Finding 2 (review Task 4): to_representation chỉ lọc theo deleted_at, còn
        _replace_packages (trước fix) lọc thêm is_active=True khi tra bảng giá theo
        code — client fetch thấy đủ giá (kể cả bảng đã tắt), PATCH gửi lại nguyên
        payload đó thì giá của bảng đã tắt bị âm thầm biến mất khỏi DB."""
        from apps.catalog.models import PriceBook, ProductPackagePrice

        created = self._create(
            {
                "name": "SP bảng tắt",
                "sku": "SKU-MP-09",
                "packages": [{"quantity": 1, "prices": {"facebook": "100000", "web": "130000"}}],
            }
        ).json()
        PriceBook.objects.filter(code="web").update(is_active=False)
        fetched_prices = created["packages"][0]["prices"]
        self.assertEqual(fetched_prices, {"facebook": "100000.00", "web": "130000.00"})

        response = self.client.patch(
            f"/api/orders/products/{created['id']}",
            {"packages": [{"quantity": 1, "prices": fetched_prices}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["packages"][0]["prices"], fetched_prices)
        self.assertTrue(
            ProductPackagePrice.objects.filter(
                package__product_id=created["id"], price_book__code="web"
            ).exists()
        )

    def test_invalid_price_format_returns_400(self):
        """Finding 3 (review Task 4): `prices` chỉ khai CharField nên "abc" lọt qua
        validate kiểu, làm Decimal("abc") raise InvalidOperation không ai bắt → 500
        trước fix. Sau fix phải trả 400 với message tiếng Việt."""
        response = self._create(
            {
                "name": "Giá sai định dạng",
                "sku": "SKU-MP-10",
                "packages": [{"quantity": 1, "prices": {"facebook": "abc"}}],
            }
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("packages", response.json())


class ProductCatalogPriceBookTests(TestCase):
    def setUp(self):
        from decimal import Decimal

        from apps.catalog.models import PriceBook, Product, ProductPackage, ProductPackagePrice

        self.client = APIClient()
        self.client.force_authenticate(make_admin("catalog-pb-admin"))
        facebook = PriceBook.objects.get(code="facebook")
        web = PriceBook.objects.get(code="web")
        self.product = Product.objects.create(name="SP catalog", sku="SKU-CAT-01", price=Decimal("100"))
        pkg1 = ProductPackage.objects.create(product=self.product, quantity=1)
        pkg2 = ProductPackage.objects.create(product=self.product, quantity=2)
        ProductPackagePrice.objects.create(package=pkg1, price_book=facebook, price=Decimal("100"))
        ProductPackagePrice.objects.create(package=pkg2, price_book=facebook, price=Decimal("180"))
        ProductPackagePrice.objects.create(package=pkg1, price_book=web, price=Decimal("130"))
        self.web_price_row = ProductPackagePrice.objects.get(package=pkg1, price_book=web)

    def _packages(self, price_book: str | None):
        url = "/api/orders/products?limit=100&offset=0"
        if price_book:
            url += f"&price_book={price_book}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json() if item["sku"] == "SKU-CAT-01")
        return {pkg["quantity"]: pkg["price"] for pkg in row["packages"]}

    def test_default_book_used_when_param_missing(self):
        self.assertEqual(self._packages(None), {1: "100.00", 2: "180.00"})

    def test_web_book_prices_with_per_tier_fallback(self):
        self.assertEqual(self._packages("web"), {1: "130.00", 2: "180.00"})

    def test_unknown_code_falls_back_to_default(self):
        self.assertEqual(self._packages("khong-ton-tai"), {1: "100.00", 2: "180.00"})

    def test_package_without_any_price_book_row_falls_back_to_legacy_column(self):
        """CRITICAL 1 (review toàn nhánh): gói KHÔNG có ProductPackagePrice ở cả bảng
        đang chọn lẫn bảng mặc định phải rơi về cột legacy `product_packages.price`
        (vẫn dual-write ở pha 2 rollout), KHÔNG được trả "0" — 0đ tường minh sẽ đi
        thẳng vào đơn nếu sale chọn gói này."""
        from decimal import Decimal

        from apps.catalog.models import ProductPackage

        legacy_only_pkg = ProductPackage.objects.create(
            product=self.product, quantity=5, price=Decimal("999")
        )
        self.assertFalse(legacy_only_pkg.book_prices.exists())

        packages = self._packages(None)
        self.assertEqual(packages[5], "999.00")

        packages_web = self._packages("web")
        self.assertEqual(packages_web[5], "999.00")

    def test_etag_differs_between_books(self):
        fb = self.client.get("/api/orders/products?limit=100&offset=0&price_book=facebook")
        web = self.client.get("/api/orders/products?limit=100&offset=0&price_book=web")
        self.assertNotEqual(fb["ETag"], web["ETag"])

    def test_etag_changes_when_web_price_edited(self):
        from decimal import Decimal

        before = self.client.get("/api/orders/products?limit=100&offset=0&price_book=web")["ETag"]
        self.web_price_row.price = Decimal("140")
        self.web_price_row.save(update_fields=["price", "updated_at"])
        after = self.client.get("/api/orders/products?limit=100&offset=0&price_book=web")["ETag"]
        # Bỏ sót nhánh này = sale bán giá cũ sau khi đổi giá, không có log nào.
        self.assertNotEqual(before, after)

    def test_catalog_grouped_uses_price_book(self):
        response = self.client.get("/api/orders/catalog/products?limit=100&offset=0&price_book=web")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        entries = payload["ungrouped"] + [v for g in payload["groups"] for v in g["variants"]]
        entry = next(item for item in entries if item["sku"] == "SKU-CAT-01")
        self.assertEqual(entry["price"], "130.00")

    def test_include_prices_returns_raw_dict_without_fallback(self):
        response = self.client.get(
            "/api/orders/products?limit=100&offset=0&price_book=web&include_prices=1"
        )
        row = next(item for item in response.json() if item["sku"] == "SKU-CAT-01")
        packages = {pkg["quantity"]: pkg for pkg in row["packages"]}
        self.assertEqual(packages[1]["prices"], {"facebook": "100.00", "web": "130.00"})
        # Gói 2 chưa khai giá web → prices thô KHÔNG có key web (dù price đã fallback).
        self.assertEqual(packages[2]["prices"], {"facebook": "180.00"})
        self.assertEqual(packages[2]["price"], "180.00")

    def test_prices_absent_without_include_flag(self):
        response = self.client.get("/api/orders/products?limit=100&offset=0")
        row = next(item for item in response.json() if item["sku"] == "SKU-CAT-01")
        self.assertNotIn("prices", row["packages"][0])


class ProductCatalogQueryCountTests(TestCase):
    """N+1 guard cho catalog (review round 1, finding Important): `test_orders_list_
    query_count.py` đo endpoint danh sách ĐƠN HÀNG, không chạm `_serialize_products_fast`
    / `products_catalog_grouped` — cần lưới chắn riêng cho catalog vì nó có thể tới 10k dòng.

    Bằng chứng "không N+1" phải là số query KHÔNG đổi khi số sản phẩm tăng, không phải
    chỉ hardcode một con số với một tập dữ liệu (cách đó không bắt được N+1 khi nó phát
    sinh) — nên mỗi test so số query giữa 3 và 12 sản phẩm và khẳng định bằng nhau.
    """

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(make_admin("catalog-qc-admin"))
        self.facebook = PriceBook.objects.get(code="facebook")
        self.web = PriceBook.objects.get(code="web")
        # Mồi cache permission RBAC (apps.users.rbac.resolve_effective_permissions) trước
        # khi đo: cache-miss của request ĐẦU TIÊN trong TestCase tốn thêm 1 query không
        # liên quan gì tới catalog, sẽ làm sai lệch phép so sánh 3-vs-12 sản phẩm bên dưới.
        self.client.get("/api/orders/products?limit=1&offset=0")

    def _seed_products(self, count: int, start: int = 0) -> None:
        """Tạo `count` sản phẩm (mỗi sản phẩm 3 gói). Sản phẩm chỉ số chẵn có giá ở
        CẢ HAI bảng giá; số lẻ chỉ có giá facebook — buộc nhánh fallback per-tier chạy
        (đúng tình huống thật của `package_prices`)."""
        from decimal import Decimal

        from apps.catalog.models import Product, ProductPackage, ProductPackagePrice

        for i in range(start, start + count):
            product = Product.objects.create(
                name=f"QC Product {i}", sku=f"SKU-QC-{i:03d}", price=Decimal("100")
            )
            for qty in (1, 2, 3):
                pkg = ProductPackage.objects.create(product=product, quantity=qty)
                ProductPackagePrice.objects.create(
                    package=pkg, price_book=self.facebook, price=Decimal("100") + qty
                )
                if i % 2 == 0:
                    ProductPackagePrice.objects.create(
                        package=pkg, price_book=self.web, price=Decimal("130") + qty
                    )

    def test_list_products_query_count_independent_of_product_count(self):
        self._seed_products(3)
        with self.assertNumQueries(10):
            response = self.client.get("/api/orders/products?limit=100&offset=0&price_book=web")
        self.assertEqual(response.status_code, 200)

        self._seed_products(9, start=3)  # tổng 12 sản phẩm
        with self.assertNumQueries(10):
            response = self.client.get("/api/orders/products?limit=100&offset=0&price_book=web")
        self.assertEqual(response.status_code, 200)

    def test_include_prices_flag_adds_exactly_one_query(self):
        self._seed_products(12)
        with self.assertNumQueries(10):
            without = self.client.get("/api/orders/products?limit=100&offset=0&price_book=web")
        self.assertEqual(without.status_code, 200)

        with self.assertNumQueries(11):
            with_prices = self.client.get(
                "/api/orders/products?limit=100&offset=0&price_book=web&include_prices=1"
            )
        self.assertEqual(with_prices.status_code, 200)

    def test_catalog_grouped_query_count_independent_of_product_count(self):
        self._seed_products(3)
        with self.assertNumQueries(4):
            response = self.client.get("/api/orders/catalog/products?limit=100&offset=0&price_book=web")
        self.assertEqual(response.status_code, 200)

        self._seed_products(9, start=3)  # tổng 12 sản phẩm
        with self.assertNumQueries(4):
            response = self.client.get("/api/orders/catalog/products?limit=100&offset=0&price_book=web")
        self.assertEqual(response.status_code, 200)

    def _seed_grouped_catalog(self, count: int, start: int = 0) -> None:
        """Tạo `count` sản phẩm active: 2/3 rơi vào 2 NHÓM sản phẩm thật (group gán
        sẵn — kích hoạt nhánh `group_rows` aggregate), 1/3 còn lại là sản phẩm RỜI
        (kích hoạt nhánh hydrate qua `_serialize_products_fast`). Reviewer chỉ ra seed
        của `test_catalog_grouped_uses_price_book` không gán group nên nhánh group
        không hề chạy — test size-independence cho `products_grouped` cố ý có cả hai
        loại để lưới chắn thực sự căng."""
        from decimal import Decimal

        from apps.catalog.models import Product, ProductGroup

        group_a, _ = ProductGroup.objects.get_or_create(
            code="QC-GROUP-A", defaults={"name": "Nhom QC A"}
        )
        group_b, _ = ProductGroup.objects.get_or_create(
            code="QC-GROUP-B", defaults={"name": "Nhom QC B"}
        )
        groups = [group_a, group_b]
        for i in range(start, start + count):
            group = None if i % 3 == 0 else groups[i % len(groups)]
            Product.objects.create(
                name=f"QC Grouped Product {i}",
                sku=f"SKU-QCG-{i:03d}",
                price=Decimal("100"),
                group=group,
            )

    def test_products_grouped_query_count_independent_of_product_count(self):
        self._seed_grouped_catalog(3)
        with self.assertNumQueries(7):
            response = self.client.get("/api/orders/products-grouped?limit=100&offset=0")
        self.assertEqual(response.status_code, 200)

        self._seed_grouped_catalog(9, start=3)  # tổng 12 sản phẩm, vẫn 2 nhóm
        with self.assertNumQueries(7):
            response = self.client.get("/api/orders/products-grouped?limit=100&offset=0")
        self.assertEqual(response.status_code, 200)


class ProductCategoriesEnvelopeTests(TestCase):
    """GET /product-categories trả envelope {items, total} cho phân trang số."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=make_admin("admin-cat-envelope"))
        for i in range(3):
            ProductCategory.objects.create(code=f"CAT-{i}", name=f"Danh muc {i}", sort_order=i)

    def test_returns_items_and_total(self):
        response = self.client.get("/api/orders/product-categories?limit=2&offset=0")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(body["items"][0]["code"], "CAT-0")

    def test_total_respects_search_filter(self):
        response = self.client.get("/api/orders/product-categories?limit=10&offset=0&q=CAT-1")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(len(body["items"]), 1)


class ProductsGroupedTotalTests(TestCase):
    """GET /products-grouped trả thêm total (số entry sau filter)."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=make_admin("admin-grouped-total"))
        for i in range(4):
            Product.objects.create(name=f"San pham {i}", sku=f"SP-{i}", price="10000")

    def test_returns_total_alongside_has_next(self):
        response = self.client.get("/api/orders/products-grouped?limit=3&offset=0")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 4)
        self.assertTrue(body["has_next"])
        self.assertEqual(len(body["entries"]), 3)

    def test_total_respects_search_filter(self):
        response = self.client.get("/api/orders/products-grouped?limit=10&offset=0&q=San pham 2")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertFalse(body["has_next"])
