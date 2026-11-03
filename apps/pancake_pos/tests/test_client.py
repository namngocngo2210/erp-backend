"""Client Pancake POS — mock tầng HTTP: phân trang, điều tốc, retry 429/5xx/timeout,
không lộ api_key, `success:false`, parse phòng thủ. KHÔNG gọi API thật."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
import requests

from apps.pancake_pos.infrastructure.pancake_pos_client import (
    PancakePosApiError,
    PancakePosClient,
    PancakePosError,
    PancakePosRateLimited,
    PancakePosTransportError,
    parse_product,
    parse_variation,
)
from apps.pancake_pos.infrastructure.rate_limiter import TokenBucket

KEY = "sk-live-SECRET-123"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _resp(payload=None, status=200, headers=None, text=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers or {}
    if isinstance(payload, Exception):
        resp.json.side_effect = payload
    else:
        resp.json.return_value = payload
    resp.text = text if text is not None else str(payload)
    return resp


def _client(responses, *, rps=1000.0, max_retries=3):
    http = MagicMock()
    http.request.side_effect = responses
    clock = FakeClock()
    client = PancakePosClient(
        KEY, 42, rps=rps, max_retries=max_retries, http=http, sleep=clock.sleep, clock=clock, jitter=lambda: 1.0
    )
    return client, http, clock


def _variation(i, product="p1"):
    return {"id": f"v{i}", "product_id": product, "custom_id": f"SKU{i}", "barcode": None,
            "product": {"custom_id": "GRP", "name": "X"}}


def test_iter_variations_phan_trang_toi_het():
    pages = [
        _resp({"success": True, "data": [_variation(i) for i in range(100)], "total_pages": 3, "page_number": 1}),
        _resp({"success": True, "data": [_variation(i) for i in range(100, 200)], "total_pages": 3}),
        _resp({"success": True, "data": [_variation(200)], "total_pages": 3}),
    ]
    client, http, _ = _client(pages)
    items = list(client.iter_variations())
    assert len(items) == 201
    assert items[0].id == "v0" and items[0].custom_id == "SKU0" and items[0].product_custom_id == "GRP"
    calls = http.request.call_args_list
    assert [c.kwargs["params"]["page_number"] for c in calls] == [1, 2, 3]
    assert all(c.kwargs["params"]["page_size"] == 100 for c in calls)
    assert all(c.args[1] == "/shops/42/products/variations" for c in calls)
    assert calls[0].kwargs["params"]["api_key"] == KEY  # xác thực qua query string


def test_iter_variations_khong_co_total_pages_dung_khi_trang_thieu():
    client, http, _ = _client([
        _resp({"success": True, "data": [_variation(i) for i in range(100)]}),
        _resp({"success": True, "data": [_variation(1000)]}),
    ])
    assert len(list(client.iter_variations(search="SKU"))) == 101
    assert http.request.call_args_list[0].kwargs["params"]["search"] == "SKU"


def test_429_retry_voi_backoff_va_retry_after():
    client, http, clock = _client([
        _resp({"success": False, "message": "You have reached the limit of requests"}, status=429,
              headers={"Retry-After": "7"}),
        _resp({"success": False}, status=429),
        _resp({"success": True, "shops": [{"id": 42, "name": "Delax"}]}),
    ])
    assert client.list_shops() == [{"id": 42, "name": "Delax"}]
    assert http.request.call_count == 3
    # lần 1: max(1*2^0, Retry-After 7) = 7; lần 2: 1*2^1 = 2 (jitter=1.0 → 100%)
    assert clock.sleeps == [7.0, 2.0]


def test_429_het_luot_nem_rate_limited():
    client, http, _ = _client([_resp({"success": False}, status=429)] * 3, max_retries=2)
    with pytest.raises(PancakePosRateLimited):
        client.list_shops()
    assert http.request.call_count == 3


def test_get_retry_5xx_va_timeout():
    client, http, _ = _client([
        requests.ReadTimeout(f"https://pos.pages.fm/api/v1/shops?api_key={KEY}"),
        _resp(None, status=502, text="bad gateway"),
        _resp({"success": True, "shops": []}),
    ])
    assert client.list_shops() == []
    assert http.request.call_count == 3


def test_post_khong_retry_5xx_va_read_timeout():
    """Ghi có thể đã áp dụng → retry là tạo trùng."""
    client, http, _ = _client([_resp({"success": False}, status=500)])
    with pytest.raises(PancakePosApiError):
        client.create_product({"product": {}})
    assert http.request.call_count == 1

    client, http, _ = _client([requests.ReadTimeout("x")])
    with pytest.raises(PancakePosTransportError):
        client.update_product("p1", {"product": {}})
    assert http.request.call_count == 1


def test_post_retry_429_va_connect_timeout():
    client, http, _ = _client([
        requests.ConnectTimeout("x"),
        _resp({"success": False}, status=429),
        _resp({"success": True}),
    ])
    client.create_product({"product": {"name": "A"}})
    assert http.request.call_count == 3
    assert http.request.call_args.args[0] == "POST"
    assert http.request.call_args.kwargs["json"] == {"product": {"name": "A"}}


def test_success_false_http_200_la_loi_co_message():
    client, _, _ = _client([_resp({"success": False, "message": "Tên sản phẩm không được để trống"})])
    with pytest.raises(PancakePosApiError, match="Tên sản phẩm không được để trống"):
        client.create_product({"product": {}})


def test_khong_bao_gio_lo_api_key(caplog):
    caplog.set_level(logging.DEBUG)
    leak = f"https://pos.pages.fm/api/v1/shops?api_key={KEY}"
    client, _, _ = _client([requests.ConnectionError(leak)] * 4 + [
        _resp({"success": False, "message": f"invalid api_key={KEY}"}, status=401)
    ], max_retries=3)
    with pytest.raises(PancakePosTransportError) as exc_info:
        client.list_shops()
    assert KEY not in str(exc_info.value)
    assert exc_info.value.__cause__ is None and exc_info.value.__suppress_context__

    with pytest.raises(PancakePosApiError) as exc_info2:
        client.list_shops()
    assert KEY not in str(exc_info2.value)
    assert "***" in str(exc_info2.value)
    assert KEY not in caplog.text
    assert client.redact(f"a?api_key={KEY}&b=1") == "a?api_key=***&b=1"


def test_get_product_404_la_none_va_parse_phong_thu():
    client, _, _ = _client([_resp({"success": False, "message": "not found"}, status=404)])
    assert client.get_product("KHONG-CO") is None

    body = {"name": "A", "custom_id": "OHI01",
            "variations": [{"id": "v1", "product_id": "p9", "custom_id": "OHI01-DEN"}, "rác", {"id": None}]}
    client, http, _ = _client([_resp(body)])
    product = client.get_product("OHI01/ĐEN")
    assert product.id == "p9" and product.custom_id == "OHI01"
    assert [v.id for v in product.variations] == ["v1"]
    assert http.request.call_args.args[1] == "/shops/42/products/OHI01%2F%C4%90EN"


def test_parse_phong_thu():
    assert parse_variation("rác") is None
    assert parse_variation({"id": None}) is None
    v = parse_variation({"id": 123, "product": "rác", "barcode": 456})
    assert v.id == "123" and v.product_id == "" and v.barcode == "456"
    assert parse_product({"data": {"id": "p1", "variations": "rác"}}).id == "p1"
    assert parse_product({"name": "không id"}) is None
    assert parse_product(None) is None


def test_response_khong_phai_json():
    client, _, _ = _client([_resp(ValueError("no json"), text="<html>")])
    with pytest.raises(PancakePosApiError):
        client.list_shops()


def test_thieu_cau_hinh():
    with pytest.raises(PancakePosError):
        PancakePosClient("", 1)
    # Chưa chọn shop: vẫn liệt kê được shop (kiểm tra key), API theo shop thì chặn.
    http = MagicMock()
    http.request.return_value = _resp({"success": True, "shops": [{"id": 7, "name": "Desi"}]})
    client = PancakePosClient("k", 0, http=http)
    assert client.list_shops() == [{"id": 7, "name": "Desi"}]
    with pytest.raises(PancakePosError, match="SHOP_ID"):
        list(client.iter_variations())


def test_parse_giu_truong_gui_lai_nguyen_trang_khong_gui_ton_kho():
    raw = {
        "id": "v9", "product_id": "p1", "custom_id": "LA-01", "barcode": "B1", "is_hidden": True,
        "retail_price": 150000, "weight": 300, "images": ["https://x/1.jpg"],
        "fields": [{"id": "f1", "name": "Màu", "value": "Đỏ"}],
        "variations_warehouses": [{"warehouse_id": "w1", "remain_quantity": 5}],
        "display_id": "123", "inserted_at": "2026-09-01T00:00:00",
    }
    variation = parse_variation(raw)
    assert variation.is_hidden is True
    assert dict(variation.raw) == {
        "fields": [{"name": "Màu", "value": "Đỏ"}], "images": ["https://x/1.jpg"], "retail_price": 150000,
        "weight": 300, "barcode": "B1", "custom_id": "LA-01", "is_hidden": True,
    }
    product = parse_product({"id": "p1", "name": "Áo", "custom_id": "LA", "weight": 300,
                             "product_attributes": [{"name": "Màu", "values": ["Đỏ"]}], "variations": [raw]})
    assert dict(product.raw) == {"name": "Áo", "custom_id": "LA", "weight": 300,
                                 "product_attributes": [{"name": "Màu", "values": ["Đỏ"]}]}
    assert "variations_warehouses" not in product.variations[0].raw


def test_token_bucket_dieu_toc():
    clock = FakeClock()
    bucket = TokenBucket(2.0, clock=clock, sleep=clock.sleep)
    waits = [bucket.acquire() for _ in range(5)]
    assert waits[0] == 0
    assert clock.now == pytest.approx(2.0)  # 4 lần chờ × 0,5 s
    with pytest.raises(ValueError):
        TokenBucket(0)


def test_client_dieu_toc_theo_rps():
    client, _, clock = _client([_resp({"success": True, "shops": []})] * 3, rps=2.0)
    for _ in range(3):
        client.list_shops()
    assert clock.now == pytest.approx(1.0)


# ── A1: kho, danh mục, tồn ──────────────────────────────────────────────────
def test_list_warehouses_parse_phong_thu():
    client, http, _ = _client([_resp({"data": [
        {"id": "w-1", "name": "Đại Mỗ 1"}, {"id": None, "name": "hỏng"}, "rác", {"id": "w-2"},
    ]})])
    warehouses = client.list_warehouses()
    assert [(w.id, w.name) for w in warehouses] == [("w-1", "Đại Mỗ 1"), ("w-2", "")]
    assert http.request.call_args.args[:2] == ("GET", "/shops/42/warehouses")


def test_update_quantities_mot_dong_retry_5xx_vi_idempotent():
    entries = [{"variation_id": "v1", "warehouse_id": "w-1", "remain_quantity": 5}]
    client, http, _ = _client([_resp({"success": False}, status=502), requests.ReadTimeout(), _resp({"success": True})])
    assert client.update_quantities(entries) == []
    assert http.request.call_count == 3
    call = http.request.call_args
    assert call.args[:2] == ("POST", "/shops/42/variations/update_quantity")
    assert call.kwargs["json"] == {"is_actual_remain_quantity": True, "variations_warehouses": entries}


def test_update_quantities_lo_nhieu_dong_500_khong_retry():
    """API thật trả 500 ổn định cho lô lớn (30/09/2026) — retry vô ích, người gọi chia nhỏ."""
    entries = [{"variation_id": f"v{i}", "warehouse_id": "w-1", "remain_quantity": 1} for i in range(3)]
    client, http, _ = _client([_resp({"success": False}, status=500)])
    with pytest.raises(PancakePosApiError) as exc:
        client.update_quantities(entries)
    assert exc.value.status_code == 500 and http.request.call_count == 1


def test_update_quantities_tra_dong_loi_trong_results():
    entries = [{"variation_id": f"v{i}", "warehouse_id": "w-1", "remain_quantity": 1} for i in range(3)]
    body = {"success": True, "results": [
        {"success": True, "variation_id": "v0"},
        {"success": False, "variation_id": "v1", "message": f"sai api_key={KEY}"},
        {"success": False, "variation_id": "v2", "warehouse_id": "w-1"},
        "rác",
    ]}
    client, _, _ = _client([_resp(body)])
    assert client.update_quantities(entries) == [
        {"variation_id": "v1", "warehouse_id": "", "message": "sai api_key=***"},
        {"variation_id": "v2", "warehouse_id": "w-1", "message": "success=false"},
    ]


def test_iter_variations_chong_lap_trang():
    """API bỏ qua page_number → trang 2 trả y hệt trang 1: dừng, không trả trùng."""
    page = _resp({"success": True, "data": [_variation(i) for i in range(100)], "total_pages": 5})
    client, http, _ = _client([page, page, page])
    items = list(client.iter_variations())
    assert len(items) == 100 and len({v.id for v in items}) == 100
    assert http.request.call_count == 2


def test_danh_muc_lam_phang_cay_va_tao_tra_id():
    tree = {"success": True, "data": [
        {"id": 1, "text": "ÁO", "nodes": [{"id": 2, "text": "Áo thun", "nodes": []}]},
        {"id": 3, "text": "Linh kiện", "nodes": []},
    ]}
    client, http, _ = _client([_resp(tree), _resp({"success": True, "data": {"id": 3299, "name": "X"}})])
    assert client.list_categories() == [
        {"id": "1", "name": "ÁO", "parent_id": ""},
        {"id": "2", "name": "Áo thun", "parent_id": "1"},
        {"id": "3", "name": "Linh kiện", "parent_id": ""},
    ]
    assert client.create_category("X") == "3299"
    assert http.request.call_args.kwargs["json"] == {"name": "X"}


def test_tao_danh_muc_khong_co_id_la_loi():
    client, _, _ = _client([_resp({"success": True, "data": {}})])
    with pytest.raises(PancakePosApiError):
        client.create_category("X")


def test_parse_ton_thuc_theo_kho():
    variation = parse_variation({
        "id": "v1", "product_id": "p1",
        "variations_warehouses": [
            {"warehouse_id": "w-1", "actual_remain_quantity": 100, "remain_quantity": 20},
            {"warehouse_id": "w-2", "remain_quantity": 7},
            {"warehouse_id": "", "actual_remain_quantity": 1},
            {"warehouse_id": "w-3", "actual_remain_quantity": "x"},
        ],
    })
    assert variation is not None
    assert dict(variation.warehouse_quantities) == {"w-1": 100, "w-2": 7}
    assert "variations_warehouses" not in variation.raw  # không bao giờ gửi lại khi PUT
