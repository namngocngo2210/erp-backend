"""Báo cáo doanh thu ngày KiotViet (`Sale.BigSaleProfitByInvoice`).

Phần parse là logic dễ vỡ nhất của module: KiotViet trả HTML với từng ô là một
`<div>` định vị tuyệt đối, cột nhận diện theo `left:NNNpx`. Test dựng HTML theo
đúng cấu trúc đó (helper `_cell`) — KHÔNG dùng HTML thật vì thứ cần khoá là
cách map cột, không phải một trang cụ thể.

Luồng gọi API (clients → instances → documents → info → pages) mock ở đúng ranh
giới `_kiotviet_web_request` để kiểm tra endpoint + payload gửi đi, và ranh giới
`push_daily_aggregates_to_lark` cho phần đẩy Lark.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import pytest

from apps.kiotviet.infrastructure.services import revenue_report_service as svc
from apps.kiotviet.infrastructure.services.revenue_report_service import (
    REPORT_NAME,
    RevenueReportError,
    RevenueRow,
    _extract_report_date,
    _parse_amount,
    fetch_daily_revenue_rows,
    parse_page_content,
    sync_daily_revenue_all_channels,
    sync_daily_revenue_for_channel,
    upsert_revenue_rows,
)
from apps.kiotviet.models import KiotVietChannel, KiotVietDailyRevenue

_MODULE = "apps.kiotviet.infrastructure.services.revenue_report_service"

# left offset của từng cột theo LEFT_COLUMN_MAP
L_DATE, L_TOTAL, L_DISCOUNT, L_REVENUE, L_COST, L_PROFIT = 10, 278, 419, 552, 705, 863


def _cell(left: int, top: int, value: str, *, action: str | None = None, box: int = 17) -> str:
    """Một ô báo cáo đúng cấu trúc KiotViet render (xem `_ROW_CELL_RE`)."""
    action_attr = f' data-reporting-action="{action}"' if action else ""
    return (
        f'<div title="" class="textBox{box} s3" '
        f'style="position:absolute;overflow:hidden;left:{left}px;top:{top}px;width:120px;height:18px;"'
        f"{action_attr}>"
        f'<div style="position:absolute;top:1px;white-space:pre;font-family:Arial;">{value}</div>'
        "</div>"
    )


def _row(top: int, values: dict[int, str]) -> str:
    return "".join(_cell(left, top, value) for left, value in values.items())


def _full_row(top: int, day: str, total, discount, revenue, cost, profit) -> str:
    return _row(
        top,
        {
            L_DATE: day,
            L_TOTAL: total,
            L_DISCOUNT: discount,
            L_REVENUE: revenue,
            L_COST: cost,
            L_PROFIT: profit,
        },
    )


def _page(*rows: str, banner: str = "") -> str:
    return f"<html><body><div>{banner}</div>{''.join(rows)}</body></html>"


# ── _parse_amount / _extract_report_date ─────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("204,279,296", Decimal("204279296")),
        ("-329,629", Decimal("-329629")),
        (" 1,000 ", Decimal("1000")),
        ("0", Decimal("0")),
        ("", Decimal("0")),
        (None, Decimal("0")),
        ("abc", Decimal("0")),
        ("1.5", Decimal("1.5")),
    ],
)
def test_parse_amount_bo_dau_phan_cach_va_fail_ve_0(raw, expected):
    assert _parse_amount(raw) == expected


def test_extract_report_date_doc_tu_banner():
    html = _page(banner="Từ ngày 21/09/2026 đến ngày 21/09/2026")
    assert _extract_report_date(html) == date(2026, 9, 21)


@pytest.mark.parametrize("html", ["", None, "<div>Không có banner</div>", "Từ ngày 99/99/2026 đến ngày 01/01/2026"])
def test_extract_report_date_khong_co_hoac_sai_dinh_dang_tra_none(html):
    assert _extract_report_date(html) is None


# ── parse_page_content ───────────────────────────────────────────────────────


def test_parse_dong_du_cot_map_dung_tung_cot():
    html = _page(_full_row(100, "21/09/2026", "204,279,296", "-329,629", "203,949,667", "150,000,000", "53,949,667"))

    daily, hourly = parse_page_content(html)

    assert hourly == {}
    assert daily == [
        RevenueRow(
            date=date(2026, 9, 21),
            total_amount=Decimal("204279296"),
            discount=Decimal("-329629"),
            revenue=Decimal("203949667"),
            total_cost=Decimal("150000000"),
            gross_profit=Decimal("53949667"),
        )
    ]


def test_parse_nhieu_dong_sap_xep_theo_top_khong_theo_thu_tu_html():
    # Dòng top=200 đứng TRƯỚC dòng top=100 trong HTML — kết quả phải theo top.
    html = _page(
        _full_row(200, "22/09/2026", "2", "0", "2", "1", "1"),
        _full_row(100, "21/09/2026", "1", "0", "1", "1", "0"),
    )

    daily, _ = parse_page_content(html)

    assert [row.date for row in daily] == [date(2026, 9, 21), date(2026, 9, 22)]
    assert [row.total_amount for row in daily] == [Decimal(1), Decimal(2)]


def test_parse_thieu_cot_thi_cot_thieu_bang_0():
    html = _page(_row(100, {L_DATE: "21/09/2026", L_REVENUE: "5,000"}))

    daily, _ = parse_page_content(html)

    assert len(daily) == 1
    row = daily[0]
    assert row.revenue == Decimal("5000")
    assert (row.total_amount, row.discount, row.total_cost, row.gross_profit) == (
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
    )


def test_parse_bo_qua_o_ngoai_ban_do_cot_va_dong_header_tong():
    html = _page(
        # header: ô "Thời gian" không phải ngày → bỏ
        _row(50, {L_DATE: "Thời gian", L_TOTAL: "Tổng tiền hàng"}),
        # cột lạ (left=999) không có trong LEFT_COLUMN_MAP → bỏ, không tạo dòng
        _cell(999, 100, "21/09/2026"),
        _full_row(120, "21/09/2026", "10", "0", "10", "5", "5"),
        # dòng tổng: ô đầu là chữ, không phải ngày → bỏ
        _row(140, {L_DATE: "Tổng cộng", L_TOTAL: "10"}),
    )

    daily, hourly = parse_page_content(html)

    assert [(row.date, row.total_amount) for row in daily] == [(date(2026, 9, 21), Decimal(10))]
    assert hourly == {}


def test_parse_dong_theo_gio_cong_don_vao_report_date():
    html = _page(
        _row(100, {L_DATE: "08:00", L_TOTAL: "1,000", L_DISCOUNT: "100", L_REVENUE: "900", L_COST: "500", L_PROFIT: "400"}),
        _row(120, {L_DATE: "09:30", L_TOTAL: "2,000", L_DISCOUNT: "0", L_REVENUE: "2,000", L_COST: "1,000", L_PROFIT: "1,000"}),
        _row(140, {L_DATE: "Tổng", L_TOTAL: "3,000"}),
    )

    daily, hourly = parse_page_content(html, report_date=date(2026, 9, 21))

    assert daily == []
    assert hourly == {
        date(2026, 9, 21): {
            "total_amount": Decimal("3000"),
            "discount": Decimal("100"),
            "revenue": Decimal("2900"),
            "total_cost": Decimal("1500"),
            "gross_profit": Decimal("1400"),
        }
    }


def test_parse_dong_theo_gio_khong_co_report_date_thi_bo_qua():
    html = _page(_row(100, {L_DATE: "08:00", L_TOTAL: "1,000"}))

    daily, hourly = parse_page_content(html, report_date=None)

    assert daily == [] and hourly == {}


def test_parse_nhan_o_co_data_reporting_action():
    html = _page(
        _cell(L_DATE, 100, "21/09/2026", action="drill")
        + _cell(L_REVENUE, 100, "7,500", action="drill")
    )

    daily, _ = parse_page_content(html)

    assert [(row.date, row.revenue) for row in daily] == [(date(2026, 9, 21), Decimal("7500"))]


@pytest.mark.parametrize("html", ["", None, "<html><body>không có ô nào</body></html>"])
def test_parse_trang_rong_tra_rong(html):
    assert parse_page_content(html) == ([], {})


# ── fetch_daily_revenue_rows: luồng API ──────────────────────────────────────


def _resp(payload: dict | None, status: int = 200, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    response.text = text or (str(payload) if payload is not None else "")
    return response


def _fake_channel(channel_id: int = 6) -> MagicMock:
    channel = MagicMock(spec=KiotVietChannel)
    channel.id = channel_id
    channel.name = "KV HKD"
    return channel


def _make_web_request(pages: dict[int, str], *, page_count: int | None = None):
    """Trả side_effect mô phỏng đủ 5 bước, ghi lại mọi lời gọi qua mock."""
    count = len(pages) if page_count is None else page_count

    def _side_effect(*, channel, method, endpoint, **kwargs):
        if endpoint.endswith("/clients"):
            return _resp({"clientId": "c1"})
        if endpoint.endswith("/instances"):
            return _resp({"instanceId": "i1"})
        if endpoint.endswith("/documents"):
            return _resp({"documentId": "d1"})
        if endpoint.endswith("/info"):
            return _resp({"documentReady": True, "pageCount": count})
        page_number = int(endpoint.rsplit("/pages/", 1)[1])
        return _resp({"pageContent": pages.get(page_number, "")})

    return MagicMock(side_effect=_side_effect)


def test_fetch_goi_du_5_buoc_dung_endpoint_va_payload():
    web = _make_web_request({1: _page(_full_row(100, "21/09/2026", "10", "0", "10", "4", "6"))})
    channel = _fake_channel()

    with patch(f"{_MODULE}._kiotviet_web_request", web):
        rows = fetch_daily_revenue_rows(channel, "89084", time_range="lastweek")

    assert rows == [
        RevenueRow(date(2026, 9, 21), Decimal(10), Decimal(0), Decimal(10), Decimal(4), Decimal(6))
    ]
    endpoints = [(c.kwargs["method"], c.kwargs["endpoint"]) for c in web.call_args_list]
    assert endpoints == [
        ("POST", "/reportapi/report/reports/clients"),
        ("POST", "/reportapi/report/reports/clients/c1/instances"),
        ("POST", "/reportapi/report/reports/clients/c1/instances/i1/documents"),
        ("GET", "/reportapi/report/reports/clients/c1/instances/i1/documents/d1/info"),
        ("GET", "/reportapi/report/reports/clients/c1/instances/i1/documents/d1/pages/1"),
    ]
    assert all(c.kwargs["channel"] is channel for c in web.call_args_list)

    instances_payload = web.call_args_list[1].kwargs["json"]
    assert instances_payload["report"] == REPORT_NAME
    assert instances_payload["parameterValues"]["time"] is None
    import json

    filter_payload = json.loads(instances_payload["parameterValues"]["filter"])
    assert filter_payload["BranchIds"] == [89084]
    # preset "lastweek" → TimeRange máy + nhãn người đọc
    assert filter_payload["TimeRange"] == "lastweek"
    assert filter_payload["dateLable"] == "Tuần trước"
    assert filter_payload["TimeZone"] == "Asia/Bangkok"

    documents_payload = web.call_args_list[2].kwargs["json"]
    assert documents_payload["format"] == "HTML5Interactive"


def test_fetch_time_range_la_fallback_ve_yesterday():
    web = _make_web_request({}, page_count=0)

    with patch(f"{_MODULE}._kiotviet_web_request", web):
        rows = fetch_daily_revenue_rows(_fake_channel(), "89084", time_range="khong-ton-tai")

    assert rows == []
    import json

    filter_payload = json.loads(web.call_args_list[1].kwargs["json"]["parameterValues"]["filter"])
    assert (filter_payload["TimeRange"], filter_payload["dateLable"]) == ("yesterday", "Hôm qua")
    # pageCount=0 → không gọi trang nào
    assert not any("/pages/" in c.kwargs["endpoint"] for c in web.call_args_list)


def test_fetch_gop_nhieu_trang_va_cong_don_dong_theo_gio_qua_cac_trang():
    banner = "Từ ngày 21/09/2026 đến ngày 21/09/2026"
    page1 = _page(_row(100, {L_DATE: "08:00", L_REVENUE: "1,000", L_PROFIT: "100"}), banner=banner)
    # Trang 2 KHÔNG có banner — report_date phải nhớ từ trang 1.
    page2 = _page(
        _row(100, {L_DATE: "15:00", L_REVENUE: "2,000", L_PROFIT: "200"}),
        _full_row(200, "20/09/2026", "9", "0", "9", "3", "6"),
    )
    web = _make_web_request({1: page1, 2: page2})

    with patch(f"{_MODULE}._kiotviet_web_request", web):
        rows = fetch_daily_revenue_rows(_fake_channel(), "89084", time_range="yesterday")

    # Dòng ngày đứng trước, dòng gộp theo giờ nối vào cuối.
    assert [(r.date, r.revenue, r.gross_profit) for r in rows] == [
        (date(2026, 9, 20), Decimal(9), Decimal(6)),
        (date(2026, 9, 21), Decimal("3000"), Decimal("300")),
    ]
    assert [c.kwargs["endpoint"].rsplit("/", 1)[1] for c in web.call_args_list[-2:]] == ["1", "2"]


@pytest.mark.parametrize(
    "failing_step, payload, status, message",
    [
        ("clients", {"clientId": "c1"}, 500, "clients call failed: status=500"),
        ("clients", {}, 200, "clients response missing clientId"),
        ("instances", {}, 200, "instances response missing instanceId"),
        ("documents", None, 502, "documents call failed: status=502"),
        ("info", {"documentReady": True}, 403, "document info failed: status=403"),
        ("pages/1", {}, 404, "page 1 failed: status=404"),
    ],
)
def test_fetch_loi_o_tung_buoc_raise_revenue_report_error(failing_step, payload, status, message):
    good = _make_web_request({1: _page()})

    def _side_effect(*, channel, method, endpoint, **kwargs):
        if endpoint.endswith(f"/{failing_step}"):
            return _resp(payload, status=status)
        return good.side_effect(channel=channel, method=method, endpoint=endpoint, **kwargs)

    with patch(f"{_MODULE}._kiotviet_web_request", side_effect=_side_effect):
        with pytest.raises(RevenueReportError, match=message):
            fetch_daily_revenue_rows(_fake_channel(), "89084")


def test_fetch_document_chua_san_sang_thi_poll_roi_timeout():
    info_calls = {"n": 0}

    def _side_effect(*, channel, method, endpoint, **kwargs):
        if endpoint.endswith("/info"):
            info_calls["n"] += 1
            return _resp({"documentReady": False})
        return _make_web_request({}).side_effect(channel=channel, method=method, endpoint=endpoint, **kwargs)

    # Đồng hồ giả: mỗi lần hỏi monotonic tiến 20s → poll 2 lần rồi vượt deadline 30s.
    clock = iter([0.0, 20.0, 40.0, 60.0, 80.0])
    fake_time = MagicMock()
    fake_time.monotonic.side_effect = lambda: next(clock)
    fake_time.time.return_value = 1_700_000_000.0

    with (
        patch(f"{_MODULE}._kiotviet_web_request", side_effect=_side_effect),
        patch(f"{_MODULE}.time", fake_time),
    ):
        with pytest.raises(RevenueReportError, match="document not ready within timeout"):
            fetch_daily_revenue_rows(_fake_channel(), "89084")

    assert info_calls["n"] >= 2
    assert fake_time.sleep.call_args_list == [call(0.5)] * (info_calls["n"] - 1)


# ── upsert + sync (DB) ───────────────────────────────────────────────────────


def _kv_channel(pk: int, *, branch_ids=None, branch_id=None, **extra) -> KiotVietChannel:
    return KiotVietChannel.objects.create(
        id=pk,
        name=f"KV {pk}",
        branch_id=branch_id,
        branch_ids=branch_ids or [],
        client_id="cid",
        client_secret="secret",
        retailer=f"retailer-{pk}",
        **extra,
    )


@pytest.mark.django_db
def test_upsert_tao_moi_roi_cap_nhat_theo_channel_branch_date():
    channel = _kv_channel(901)
    first = [RevenueRow(date(2026, 9, 21), Decimal(10), Decimal(1), Decimal(9), Decimal(4), Decimal(5))]

    created = upsert_revenue_rows(channel, "89084", first)
    again = upsert_revenue_rows(
        channel,
        "89084",
        [RevenueRow(date(2026, 9, 21), Decimal(20), Decimal(2), Decimal(18), Decimal(8), Decimal(10))],
    )

    assert len(created) == 1 and created[0].pk == again[0].pk
    assert KiotVietDailyRevenue.objects.count() == 1
    stored = KiotVietDailyRevenue.objects.get()
    assert (stored.channel_id, stored.branch_id, stored.date) == (901, "89084", date(2026, 9, 21))
    assert (stored.total_amount, stored.discount, stored.revenue, stored.total_cost, stored.gross_profit) == (
        Decimal(20),
        Decimal(2),
        Decimal(18),
        Decimal(8),
        Decimal(10),
    )


@pytest.mark.django_db
def test_sync_for_channel_duyet_moi_branch_va_gom_loi_tung_branch():
    channel = _kv_channel(902, branch_ids=["111", "", "222"])
    good_row = RevenueRow(date(2026, 9, 21), Decimal(1), Decimal(0), Decimal(1), Decimal(0), Decimal(1))

    def _fetch(ch, branch, time_range="yesterday", debug_dir=None):
        if branch == "222":
            raise RevenueReportError("boom 222")
        return [good_row]

    with patch(f"{_MODULE}.fetch_daily_revenue_rows", side_effect=_fetch) as fetch:
        summary = sync_daily_revenue_for_channel(channel, time_range="last7days")

    # branch rỗng bị bỏ qua, không gọi fetch cho nó
    assert [c.args[1] for c in fetch.call_args_list] == ["111", "222"]
    assert all(c.kwargs["time_range"] == "last7days" for c in fetch.call_args_list)
    assert summary["branches"] == [{"branch_id": "111", "rows": 1, "upserted": 1}]
    assert summary["rows_upserted"] == 1
    assert summary["errors"] == [{"branch_id": "222", "error": "boom 222"}]
    assert [o.branch_id for o in summary["_objects"]] == ["111"]
    assert KiotVietDailyRevenue.objects.filter(channel=channel, branch_id="111").count() == 1


@pytest.mark.django_db
def test_sync_for_channel_fallback_branch_id_don_khi_khong_co_branch_ids():
    channel = _kv_channel(903, branch_id=" 555 ")

    with patch(f"{_MODULE}.fetch_daily_revenue_rows", return_value=[]) as fetch:
        summary = sync_daily_revenue_for_channel(channel)

    fetch.assert_called_once()
    assert fetch.call_args.args[1] == "555"
    assert summary["branches"] == [{"branch_id": "555", "rows": 0, "upserted": 0}]


@pytest.mark.django_db
def test_sync_all_channels_theo_sync_targets_va_day_lark_dung_ngay():
    # SYNC_TARGETS cố định (6/89084, 4/231174, 5/30965): dựng đúng 2 kênh, kênh 5 thiếu.
    _kv_channel(6, branch_ids=["89084"])
    _kv_channel(4, branch_ids=["231174"])
    rows_by_branch = {
        "89084": [RevenueRow(date(2026, 9, 21), Decimal(1), Decimal(0), Decimal(1), Decimal(0), Decimal(1))],
        "231174": [
            RevenueRow(date(2026, 9, 21), Decimal(2), Decimal(0), Decimal(2), Decimal(0), Decimal(2)),
            RevenueRow(date(2026, 9, 20), Decimal(3), Decimal(0), Decimal(3), Decimal(0), Decimal(3)),
        ],
    }

    def _fetch(ch, branch, time_range="yesterday", debug_dir=None):
        return rows_by_branch[branch]

    with (
        patch(f"{_MODULE}.fetch_daily_revenue_rows", side_effect=_fetch) as fetch,
        patch(f"{_MODULE}.push_daily_aggregates_to_lark", return_value={"created": 2, "updated": 0, "skipped": 0}) as push,
    ):
        summary = sync_daily_revenue_all_channels(time_range="last7days")

    # Mỗi target chỉ sync ĐÚNG branch của nó dù kênh có nhiều branch.
    assert sorted((c.args[0].id, c.args[1]) for c in fetch.call_args_list) == [(4, "231174"), (6, "89084")]
    assert summary["channels"] == 2
    assert summary["rows_upserted"] == 3
    assert summary["errors"] == [
        {"channel_id": 5, "branch_id": "30965", "error": "KiotVietChannel not found or deleted"}
    ]
    assert [d["channel_id"] for d in summary["details"]] == [6, 4]
    assert all("_objects" not in d for d in summary["details"])
    push.assert_called_once()
    assert set(push.call_args.args[0]) == {date(2026, 9, 21), date(2026, 9, 20)}
    assert summary["lark"] == {"created": 2, "updated": 0, "skipped": 0}
    assert KiotVietDailyRevenue.objects.count() == 3


@pytest.mark.django_db
def test_sync_all_channels_khong_day_lark_khi_tat_hoac_khong_co_dong():
    _kv_channel(6, branch_ids=["89084"])

    with (
        patch(f"{_MODULE}.fetch_daily_revenue_rows", return_value=[]),
        patch(f"{_MODULE}.push_daily_aggregates_to_lark") as push,
    ):
        no_rows = sync_daily_revenue_all_channels()
    push.assert_not_called()
    assert "lark" not in no_rows

    row = RevenueRow(date(2026, 9, 21), Decimal(1), Decimal(0), Decimal(1), Decimal(0), Decimal(1))
    with (
        patch(f"{_MODULE}.fetch_daily_revenue_rows", return_value=[row]),
        patch(f"{_MODULE}.push_daily_aggregates_to_lark") as push,
    ):
        disabled = sync_daily_revenue_all_channels(push_to_lark=False)
    push.assert_not_called()
    assert disabled["rows_upserted"] == 1 and "lark" not in disabled


@pytest.mark.django_db
def test_sync_all_channels_loi_lark_khong_lam_hong_ket_qua_sync():
    _kv_channel(6, branch_ids=["89084"])
    row = RevenueRow(date(2026, 9, 21), Decimal(1), Decimal(0), Decimal(1), Decimal(0), Decimal(1))

    with (
        patch(f"{_MODULE}.fetch_daily_revenue_rows", return_value=[row]),
        patch(f"{_MODULE}.push_daily_aggregates_to_lark", side_effect=RuntimeError("lark down")),
    ):
        summary = sync_daily_revenue_all_channels()

    assert summary["rows_upserted"] == 1
    assert summary["lark"] == {"error": "lark down"}
    assert KiotVietDailyRevenue.objects.count() == 1


def test_module_giu_bang_cot_va_target_co_dinh():
    """Khoá hằng số layout: đổi LEFT_COLUMN_MAP/SYNC_TARGETS là đổi nghiệp vụ, phải sửa test."""
    assert svc.LEFT_COLUMN_MAP == {
        10: "date",
        278: "total_amount",
        419: "discount",
        552: "revenue",
        705: "total_cost",
        863: "gross_profit",
    }
    assert svc.SYNC_TARGETS == ((6, "89084"), (4, "231174"), (5, "30965"))
