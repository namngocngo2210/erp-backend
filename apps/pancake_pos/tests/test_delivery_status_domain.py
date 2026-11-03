"""Luật thuần trạng thái vận chuyển Pancake (MR D): nhãn trạng thái, ĐVVC mới nhất (mẫu thật
01/10/2026), kiện chậm nhất, quyết định mã "Trạng thái vận chuyển thực", luật lệch."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.pancake_pos.domain import delivery_status as d


def test_ten_trang_thai_uu_tien_status_map_roi_spec_roi_tho():
    assert d.status_label(2, {2: "Đã gửi (sửa)"}, "shipped") == "Đã gửi (sửa)"
    assert d.status_label(2, {}, "shipped") == "Đã gửi hàng"
    assert d.status_label(99, {}, "weird") == "weird"
    assert d.status_label(None, {}, "x") == "x"


def test_dvvc_uu_tien_partner_status_enum():
    got = d.dvvc_status({"partner_status": "delivered", "updated_at": "2026-09-30T01:00:00"})
    assert (got.label, got.at) == ("Đã giao hàng", datetime(2026, 9, 30, 1, tzinfo=UTC))
    # Giá trị thật "cancelled" (spec ghi "canceled").
    assert d.dvvc_status({"partner_status": "cancelled"}).label == "Đã hủy vận đơn"
    assert d.dvvc_status({"partner_status": "brand_new"}).label == "brand_new"


def test_dvvc_lay_muc_extend_update_moi_nhat_theo_millis():
    partner = {"partner_status": None, "extend_update": [
        # Thứ tự mảng không đáng tin — chọn theo update_time_millis.
        {"action_code": 30901, "status": "Your package has been collected", "update_time_millis": 1000},
        {"action_code": 50101, "status": "Your package has been delivered.", "update_time_millis": 3000},
        {"action_code": 40501, "status": "Out for delivery", "update_time_millis": 2000},
    ]}
    got = d.dvvc_status(partner)
    assert got.label == "Đã giao hàng" and got.at == datetime.fromtimestamp(3, tz=UTC)


@pytest.mark.parametrize(("code", "label"), [
    (20101, "Chờ xử lý"), (20201, "Đang lấy hàng"), (20301, "Trễ lấy hàng"), (30901, "Đã lấy hàng"),
    (36201, "Đã lấy hàng"), (31301, "Đang trung chuyển"), (40101, "Đang trung chuyển"),
    (41201, "Đang giao hàng"), (40801, "Giao không thành"), (41901, "Chờ chuyển hoàn"),
    (50102, "Đã giao hàng"), (70212, "Đang chuyển hoàn"), (80101, "Đã chuyển hoàn"),
])
def test_nhan_action_code_tiktok(code, label):
    assert d.action_label(code) == label


def test_ma_la_dung_cau_mo_ta_da_che_sdt():
    got = d.dvvc_status({"extend_update": [{
        "action_code": 99999,
        "status": "The shipping carrier is on the way. Driver phone number: +84342282002 Hotline 19001088",
    }]})
    assert "342282002" not in got.label and "***" in got.label and len(got.label) <= 64
    assert d.dvvc_status({}).label == "" and d.dvvc_status(None).label == ""


def _p(pid, status, name="", dvvc=""):
    return d.PartState(pid, status, name, dvvc)


def test_kien_cham_nhat():
    assert d.slowest_part([]) is None
    parts = [_p("A", 2, "Đã gửi hàng"), _p("B", 12, "Chờ in")]
    assert d.slowest_part(parts).pancake_order_id == "B"  # chỉ "Đã gửi" khi mọi kiện đã gửi
    assert d.slowest_part([_p("A", 3), _p("B", 2)]).pancake_order_id == "B"
    assert d.slowest_part([_p("A", 6), _p("B", 3)]).pancake_order_id == "B"  # kiện huỷ bỏ qua
    assert d.slowest_part([_p("A", 6), _p("B", 7)]).pancake_order_id == "A"  # mọi kiện huỷ
    assert d.slowest_part([_p("A", 99), _p("B", 0)]).pancake_order_id == "A"  # lạ = chậm nhất
    assert d.slowest_part([_p("A", 2), _p("B", 2)]).pancake_order_id == "A"  # hoà → kiện trước


def decide(cur, target, owned=False, enabled=False, allow=True):
    return d.decide_delivery_code(cur, target, owned_by_pancake=owned, dispatch_enabled=enabled,
                                  allow_dispatch=allow)


def test_quyet_dinh_ma_khi_co_xuat_kho_tat():
    assert decide("", "warehouse_processing") == d.DeliveryDecision(write="warehouse_processing")
    assert decide("warehouse_processing", "warehouse_processing") == d.DeliveryDecision()
    assert decide(None, None) == d.DeliveryDecision()
    # Pancake đã gửi mà CRM chưa xuất kho → không ghi, vào báo cáo.
    assert decide("", "dispatched").blocked == d.BLOCK_NEEDS_DISPATCH
    assert decide("", "dispatched", allow=False).blocked == d.BLOCK_NEEDS_DISPATCH
    # Mã kết thúc ghi thẳng, không xuất kho (CRM không giữ tồn khi cờ tắt) — kể cả backfill.
    assert decide("warehouse_processing", "received") == d.DeliveryDecision(write="received")
    assert decide("", "not_received", allow=False) == d.DeliveryDecision(write="not_received")
    assert decide("dispatched", "received") == d.DeliveryDecision(write="received")
    assert decide("received", "not_received", owned=True) == d.DeliveryDecision(write="not_received")
    # Mã kết thúc do người nhập vẫn giữ.
    assert decide("received", "not_received").blocked == d.BLOCK_MANUAL
    # Chỉ tiến.
    assert decide("dispatched", "warehouse_processing").blocked == d.BLOCK_REGRESS


def test_quyet_dinh_ma_khi_co_xuat_kho_bat():
    on = {"enabled": True}
    assert decide("", "dispatched", **on) == d.DeliveryDecision(dispatch=True)
    assert decide("warehouse_processing", "received", **on) == d.DeliveryDecision(write="received", dispatch=True)
    assert decide("dispatched", "received", **on) == d.DeliveryDecision(write="received")
    assert decide("dispatched", "not_received", **on) == d.DeliveryDecision(write="not_received")
    # Backfill: không bao giờ xuất kho.
    assert decide("", "dispatched", allow=False, **on).blocked == d.BLOCK_NEEDS_DISPATCH
    # Mã kết thúc do người nhập → giữ; do Pancake đặt → đổi ngang được.
    assert decide("received", "not_received", **on).blocked == d.BLOCK_MANUAL
    assert decide("received", "not_received", owned=True, **on) == d.DeliveryDecision(write="not_received")
    assert decide("received", "dispatched", owned=True, **on).blocked == d.BLOCK_REGRESS


def test_ma_nguoi_nhap_va_ma_la_khong_bi_ghi_de():
    for manual in ("stalled_complaint", "post_office_lost"):
        assert decide(manual, "received", owned=True, enabled=True).blocked == d.BLOCK_MANUAL
    assert decide("delivered_success", "received", enabled=True).blocked == d.BLOCK_UNKNOWN
    assert decide("", "bogus").blocked == d.BLOCK_UNKNOWN


def test_luat_lech_kiotviet_pancake():
    assert d.mismatch(6, 2).code == "crm_cancelled_pancake_shipped"
    assert d.mismatch(3, 4).code == "crm_delivered_pancake_returning"
    assert d.mismatch(5, 3).code == "crm_returned_pancake_delivered"
    assert d.mismatch(2, 6).code == "crm_shipping_pancake_cancelled"
    assert d.mismatch(1, 16).code == "crm_pending_pancake_delivered"
    assert d.mismatch(2, 2) is None and d.mismatch(None, 2) is None and d.mismatch(6, 6) is None


def test_tra_nguoc_ten_va_ma():
    names = {2: "Đã gửi (sửa)"}
    codes = d.codes_by_name(names)
    assert codes["Đã gửi (sửa)"] == 2 and codes["Đã gửi hàng"] == 2 and codes["Đã nhận"] == 3
    assert d.names_for([2], names) == ["Đã gửi (sửa)", "Đã gửi hàng"]
