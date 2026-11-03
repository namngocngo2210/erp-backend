# KiotViet Reconcile Delete-Recreate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile tạo đơn B ở trạng thái neo (delivery=Đang giao + mã VĐ + phí ship khớp A) rồi hội tụ về A; thay 3 mode PUT-sửa-tại-chỗ bằng 1 vòng "lệch là xoá + tạo lại".

**Architecture:** Tách quyết định thành 2 hàm THUẦN (`expected_b_state`, `decide_reconcile_action`) — unit-test không cần KiotViet. Routine tạo đơn (POST anchor + patch tới `expected_b_state`) và vòng `reconcile_sync` (fetch A + prescan B → decide → delete/create) bọc I/O quanh 2 hàm thuần đó. `RECONCILE_MODES` từ 6 còn 3.

**Tech Stack:** Python 3.12, Django, pytest. KiotViet REST qua `_kiotviet_request`. Không thêm dependency.

## Global Constraints

- Spec nguồn: `docs/superpowers/specs/2026-07-10-kiotviet-reconcile-delete-recreate-design.md`.
- Mã trạng thái: invoice `1`=Hoàn thành, `2`=Đã hủy, `3`=Đang xử lý. delivery `1`=Chờ xử lý, `2`=Đang giao, `3`=Giao thành công, `6`=Đã hủy.
- Cờ `RECONCILE_DELIVERY_EXACT_MIRROR` (settings, default `False`).
- Tolerance so `total`: **±1đ**. So `delivery_price`: bằng đúng.
- Map A↔B qua regex description `SYNC từ kênh \S+ - (\S+)` (giữ nguyên).
- List KiotViet luôn kèm `orderBy=id&orderDirection=Asc` (ổn định phân trang — không được bỏ).
- Test backend chạy trong container theo memory [[running-backend-tests]]: image prod thiếu pytest/deps; Windows dùng `desi-order-web-test`, luôn `-e POSTGRES_DB=desi_<unique>` cho full-suite. Các unit test thuần ở plan này không đụng DB nên chạy nhẹ.
- Không sửa interface `reconcile_one_pair` / `tasks.py` / `reconcile_channels.py`.

---

## Task 0: Sprint 0 — Verify LIVE 3 tiền đề (BẮT BUỘC, chạy trên prod trước khi merge write-path)

Không có test tự động — đây là kiểm chứng hành vi KiotViet trên đơn thật. Chuẩn bị script Django-shell read-mostly, chạy trên container prod `desi-web`.

**Files:**
- Create: `backend/apps/kiotviet/scripts/verify_reconcile_assumptions.py` (script chạy tay, không import vào app)

**Interfaces:**
- Produces: kết quả T1/T2/T3 (bằng chứng) để chốt giá trị cờ + xác nhận delete được.

- [ ] **Step 1: Viết script verify**

```python
# backend/apps/kiotviet/scripts/verify_reconcile_assumptions.py
"""Chạy TAY trên prod: python manage.py shell < apps/kiotviet/scripts/verify_reconcile_assumptions.py
Sửa SOURCE_KV/TARGET_KV/BRANCH + 1 mã đơn A test trước khi chạy. KHÔNG import vào app."""
from apps.kiotviet.models import KiotVietChannel
from apps.kiotviet.infrastructure.services.gateway_kship_customer_webhook import _kiotviet_request

TARGET_KV = 5   # kênh cũ B
TARGET_BRANCH = 30965
TEST_B_INVOICE_ID = None   # điền id 1 đơn B đã 'Hoàn thành' để test T3 (delete)

b = KiotVietChannel.objects.get(id=TARGET_KV)

# T2 + T1: tạo 1 đơn thử tay qua UI, rồi PUT delivery=2 (+code+price) đọc lại, rồi PUT delivery=3 đọc lại.
# In hướng dẫn thay vì tự tạo đơn (tránh rác data ngoài ý muốn):
print("T1/T2: tạo 1 đơn thử trên B, ghi id, rồi PUT deliveryDetail{status:2,deliveryCode,price} và GET đọc lại;")
print("       sau đó PUT deliveryDetail{status:3}, GET đọc lại xem invoice.status có bị ép 1 không.")

# T3: thử DELETE 1 đơn B đã Hoàn thành
if TEST_B_INVOICE_ID:
    resp = _kiotviet_request(order=None, channel=b, method="DELETE", endpoint=f"/invoices/{TEST_B_INVOICE_ID}")
    print(f"T3 DELETE completed invoice -> status={resp.status_code} body={(resp.text or '')[:300]}")
```

- [ ] **Step 2: Chạy trên prod & ghi kết quả**

Run (prod):
```bash
docker exec -i desi-web python manage.py shell < apps/kiotviet/scripts/verify_reconcile_assumptions.py
```
Ghi lại vào cuối file spec (mục "Kết quả Sprint 0"):
- T1: mã VĐ + phí ship có bám sau PUT delivery=2? (YES/NO)
- T2: set delivery=3 có ép invoice→1? (YES/NO) → chốt cờ default.
- T3: DELETE đơn Hoàn thành trả status nào? (2xx = xoá được → mô hình hợp lệ).

**GATE:** T1=NO hoặc T3 không xoá được → DỪNG, báo lại, không tiếp Task 2+.

- [ ] **Step 3: Commit script + kết quả**

```bash
git add backend/apps/kiotviet/scripts/verify_reconcile_assumptions.py docs/superpowers/specs/2026-07-10-kiotviet-reconcile-delete-recreate-design.md
git commit -m "chore(reconcile): sprint0 live-verify script + results"
```

---

## Task 1: Hàm thuần `expected_b_state` — trạng thái đích của B suy từ A

**Files:**
- Create: `backend/apps/kiotviet/infrastructure/services/_reconcile_decision.py`
- Test: `backend/apps/kiotviet/tests/test_reconcile_decision.py`

**Interfaces:**
- Produces:
  `expected_b_state(a_invoice_status: int | None, a_delivery_status: int | None, *, exact_mirror: bool) -> tuple[int | None, int | None]`
  trả `(exp_invoice_status, exp_delivery_status)` — đích mà cả routine tạo lẫn vòng so sánh dùng chung.

- [ ] **Step 1: Viết test thất bại**

```python
# backend/apps/kiotviet/tests/test_reconcile_decision.py
from apps.kiotviet.infrastructure.services._reconcile_decision import expected_b_state


def test_normal_case_mirrors_a_exactly():
    assert expected_b_state(3, 2, exact_mirror=False) == (3, 2)
    assert expected_b_state(1, 3, exact_mirror=False) == (1, 3)


def test_exception_delivered_but_processing_default_caps_delivery():
    # A(inv=3, del=3): default False -> giữ invoice 3, cap delivery về 2 (tránh auto-complete)
    assert expected_b_state(3, 3, exact_mirror=False) == (3, 2)


def test_exception_delivered_but_processing_exact_mirror_accepts_forced_complete():
    # exact_mirror True: del=3, chấp nhận KiotViet ép invoice->1
    assert expected_b_state(3, 3, exact_mirror=True) == (1, 3)


def test_none_statuses_passthrough():
    assert expected_b_state(None, None, exact_mirror=False) == (None, None)
```

- [ ] **Step 2: Chạy test — kỳ vọng FAIL**

Run: `pytest apps/kiotviet/tests/test_reconcile_decision.py -q`
Expected: FAIL — `ModuleNotFoundError: _reconcile_decision`.

- [ ] **Step 3: Viết implementation tối thiểu**

```python
# backend/apps/kiotviet/infrastructure/services/_reconcile_decision.py
"""Quyết định reconcile — hàm THUẦN (không I/O), unit-test được không cần KiotViet.

expected_b_state: đích B suy từ A, xử lý ca ngoại lệ A(giao thành công + đang xử lý) theo cờ.
decide_reconcile_action: so B hiện tại với đích → create / delete_recreate / skip.
"""
from __future__ import annotations

# Ca ngoại lệ DUY NHẤT: A đã "Giao thành công" (delivery=3) nhưng hóa đơn còn "Đang xử lý"
# (invoice=3). Set delivery=3 trên B khiến KiotViet auto-complete invoice->1 != A.
_DELIVERED = 3
_PROCESSING = 3
_COMPLETED = 1
_IN_DELIVERY = 2


def expected_b_state(
    a_invoice_status: int | None,
    a_delivery_status: int | None,
    *,
    exact_mirror: bool,
) -> tuple[int | None, int | None]:
    """(exp_invoice, exp_delivery) mà B nên đạt. Mặc định mirror A; riêng ca A(inv=3, del=3)
    xử lý theo cờ để tránh hoàn-thành-giả + churn."""
    if a_delivery_status == _DELIVERED and a_invoice_status == _PROCESSING:
        if exact_mirror:
            # Chấp nhận KiotViet ép invoice->1 khi set delivery=3.
            return (_COMPLETED, _DELIVERED)
        # An toàn sổ sách: giữ invoice=3, cap delivery về Đang giao(2).
        return (_PROCESSING, _IN_DELIVERY)
    return (a_invoice_status, a_delivery_status)
```

- [ ] **Step 4: Chạy test — kỳ vọng PASS**

Run: `pytest apps/kiotviet/tests/test_reconcile_decision.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/_reconcile_decision.py backend/apps/kiotviet/tests/test_reconcile_decision.py
git commit -m "feat(reconcile): expected_b_state pure helper for B target status"
```

---

## Task 2: Hàm thuần `decide_reconcile_action` — so 4 chiều → quyết định

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/_reconcile_decision.py`
- Test: `backend/apps/kiotviet/tests/test_reconcile_decision.py` (thêm)

**Interfaces:**
- Consumes: `expected_b_state` (Task 1).
- Produces:
  `decide_reconcile_action(*, a: dict, b: dict | None, exact_mirror: bool) -> str`
  trả một trong `"create" | "delete_recreate" | "skip"`.
  `a` keys: `invoice_status, delivery_status, total, delivery_price`.
  `b` keys (hoặc None nếu chưa có mirror): `invoice_status, delivery_status, total, delivery_price`.

- [ ] **Step 1: Viết test thất bại (thêm vào cuối file test)**

```python
from apps.kiotviet.infrastructure.services._reconcile_decision import decide_reconcile_action


def _a(inv=3, dele=2, total=1000, price=20):
    return {"invoice_status": inv, "delivery_status": dele, "total": total, "delivery_price": price}


def _b(inv=3, dele=2, total=1000, price=20):
    return {"invoice_status": inv, "delivery_status": dele, "total": total, "delivery_price": price}


def test_no_mirror_returns_create():
    assert decide_reconcile_action(a=_a(), b=None, exact_mirror=False) == "create"


def test_all_match_returns_skip():
    assert decide_reconcile_action(a=_a(), b=_b(), exact_mirror=False) == "skip"


def test_total_within_1d_is_skip():
    assert decide_reconcile_action(a=_a(total=1000), b=_b(total=1001), exact_mirror=False) == "skip"


def test_total_diff_over_1d_recreate():
    assert decide_reconcile_action(a=_a(total=1000), b=_b(total=1002), exact_mirror=False) == "delete_recreate"


def test_delivery_price_mismatch_recreate():
    assert decide_reconcile_action(a=_a(price=20), b=_b(price=25), exact_mirror=False) == "delete_recreate"


def test_invoice_status_mismatch_recreate():
    assert decide_reconcile_action(a=_a(inv=1, dele=3), b=_b(inv=3, dele=3), exact_mirror=False) == "delete_recreate"


def test_exception_default_b_capped_is_skip():
    # A(inv=3, del=3) default -> expected B (inv=3, del=2). B đúng vậy -> skip, KHÔNG churn.
    assert decide_reconcile_action(a=_a(inv=3, dele=3), b=_b(inv=3, dele=2), exact_mirror=False) == "skip"


def test_exception_exact_mirror_forced_complete_is_skip():
    # exact_mirror True -> expected B (inv=1, del=3). KiotViet ép B=(1,3) -> skip, KHÔNG churn.
    assert decide_reconcile_action(a=_a(inv=3, dele=3), b=_b(inv=1, dele=3), exact_mirror=True) == "skip"
```

- [ ] **Step 2: Chạy — kỳ vọng FAIL**

Run: `pytest apps/kiotviet/tests/test_reconcile_decision.py -q`
Expected: FAIL — `decide_reconcile_action` chưa tồn tại.

- [ ] **Step 3: Implement (thêm vào `_reconcile_decision.py`)**

```python
_TOTAL_TOLERANCE = 1


def _to_int(v) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def decide_reconcile_action(*, a: dict, b: dict | None, exact_mirror: bool) -> str:
    """So B với đích suy từ A. None mirror -> create; lệch bất kỳ chiều -> delete_recreate; khớp -> skip."""
    if b is None:
        return "create"

    exp_inv, exp_del = expected_b_state(
        a.get("invoice_status"), a.get("delivery_status"), exact_mirror=exact_mirror
    )

    total_ok = abs(_to_int(a.get("total")) - _to_int(b.get("total"))) <= _TOTAL_TOLERANCE
    price_ok = _to_int(a.get("delivery_price")) == _to_int(b.get("delivery_price"))
    inv_ok = b.get("invoice_status") == exp_inv
    del_ok = b.get("delivery_status") == exp_del

    if total_ok and price_ok and inv_ok and del_ok:
        return "skip"
    return "delete_recreate"
```

- [ ] **Step 4: Chạy — kỳ vọng PASS**

Run: `pytest apps/kiotviet/tests/test_reconcile_decision.py -q`
Expected: PASS (toàn bộ).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/_reconcile_decision.py backend/apps/kiotviet/tests/test_reconcile_decision.py
git commit -m "feat(reconcile): decide_reconcile_action 4-dim comparison"
```

---

## Task 3: Payload builder — tạo B với delivery=2 (Đang giao), neo mã VĐ + phí

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/invoice_payload_builder.py:479` (`order_delivery["status"] = 1` → `2`)
- Test: `backend/apps/kiotviet/tests/test_invoice_payload_builder.py` (thêm)

**Interfaces:**
- Consumes: `build_target_invoice_payload(...)` (chữ ký không đổi).
- Produces: payload có `deliveryDetail.status == 2`, `deliveryCode` (nếu A có), `price`, `codAmount`.

- [ ] **Step 1: Viết test thất bại (thêm vào cuối file test)**

```python
def test_delivery_created_in_shipping_state_with_code_and_fee():
    source_invoice = {
        "code": "HD000009",
        "total": 500000,
        "discount": 0,
        "status": 1,
        "invoiceDetails": [
            {
                "productCode": "P1", "productName": "P", "quantity": 1, "price": 462963.0,
                "invoiceDetailTaxs": [
                    {"priceAfterTax": 500000, "discountAfterTax": 0, "allocationDiscountAfterTax": 0, "subTotalAfterTax": 500000, "taxRate": 8}
                ],
            }
        ],
        "invoiceDelivery": {"deliveryCode": "BEST123", "price": 30000, "status": 3},
    }
    payload = _build(source_invoice)
    assert payload["deliveryDetail"]["status"] == 2, "phải tạo ở Đang giao(2) để neo mã VĐ + phí"
    assert payload["deliveryDetail"]["deliveryCode"] == "BEST123"
    assert payload["deliveryDetail"]["price"] == 30000
    assert payload["deliveryDetail"]["codAmount"] == 500000
```

- [ ] **Step 2: Chạy — kỳ vọng FAIL**

Run: `pytest apps/kiotviet/tests/test_invoice_payload_builder.py::test_delivery_created_in_shipping_state_with_code_and_fee -q`
Expected: FAIL — `assert 1 == 2`.

- [ ] **Step 3: Sửa builder**

Tại `invoice_payload_builder.py`, đổi dòng `order_delivery["status"] = 1` thành:

```python
    # Tạo ở Đang giao(2): KiotViet chỉ neo mã vận đơn + phí ship khi delivery >= Đang giao.
    # Tạo ở Chờ xử lý(1) khiến code/fee không bám (gốc lỗi lệch phí ship). Trạng thái thật
    # của A được hội tụ ở bước patch_invoice_status_after_create.
    order_delivery["status"] = 2
```

- [ ] **Step 4: Chạy test file — kỳ vọng PASS (cả test cũ)**

Run: `pytest apps/kiotviet/tests/test_invoice_payload_builder.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/invoice_payload_builder.py backend/apps/kiotviet/tests/test_invoice_payload_builder.py
git commit -m "feat(reconcile): create B in Đang giao(2) to anchor tracking code + fee"
```

---

## Task 4: `patch_invoice_status_after_create` — hội tụ về `expected_b_state`, bỏ CAP cũ

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/invoice_payload_builder.py:586-695` (`patch_invoice_status_after_create`)
- Test: `backend/apps/kiotviet/tests/test_patch_invoice_status.py` (create)

**Interfaces:**
- Consumes: `expected_b_state` (Task 1); `_kiotviet_request` (mock trong test).
- Produces: chữ ký thêm `exact_mirror: bool = False`. Patch delivery→`exp_del`, invoice→`exp_inv` (thay cho raw source + CAP nội bộ cũ).

- [ ] **Step 1: Viết test thất bại (mock `_kiotviet_request`)**

```python
# backend/apps/kiotviet/tests/test_patch_invoice_status.py
from unittest.mock import patch, MagicMock
from apps.kiotviet.infrastructure.services import invoice_payload_builder as B


def _resp(code=200, body=None):
    m = MagicMock()
    m.status_code = code
    m.content = b"{}"
    m.json.return_value = body or {}
    m.text = "{}"
    return m


def _calls_to_put_status(mock_req):
    """Trả list dict json của các call method=PUT."""
    out = []
    for c in mock_req.call_args_list:
        if c.kwargs.get("method") == "PUT":
            out.append(c.kwargs.get("json"))
    return out


def test_exception_default_caps_delivery_to_2_no_completion():
    # A(inv=3, del=3) default: expected (inv=3, del=2). B tạo sẵn del=2 -> KHÔNG cần PUT gì (đã đúng).
    with patch.object(B, "_kiotviet_request", return_value=_resp()) as mreq:
        # import cục bộ trong hàm nên patch ở module gateway; ở đây patch symbol dùng trong builder:
        pass
```

Lưu ý test: `patch_invoice_status_after_create` import `_kiotviet_request` cục bộ từ
`.gateway_kship_customer_webhook`. Patch tại nguồn đó:

```python
def _run_patch(*, exact_mirror, src_inv, src_del, created_del=2):
    from apps.kiotviet.infrastructure.services import gateway_kship_customer_webhook as GW
    with patch.object(GW, "_kiotviet_request", return_value=_resp(body={"status": src_inv, "invoiceDelivery": {"status": src_del}})) as mreq:
        B.patch_invoice_status_after_create(
            target_channel=MagicMock(),
            created_invoice_id=123,
            source_invoice_status=src_inv,
            source_delivery_status=src_del,
            source_delivery_code="BEST1",
            source_delivery_price=30000,
            exact_mirror=exact_mirror,
        )
    return mreq


def test_normal_delivered_completed_sets_del3_then_inv1():
    mreq = _run_patch(exact_mirror=False, src_inv=1, src_del=3)
    puts = _calls_to_put_status(mreq)
    # phải có PUT delivery status=3 rồi PUT invoice status=1
    assert any(p.get("deliveryDetail", {}).get("status") == 3 for p in puts)
    assert any(p.get("status") == 1 for p in puts)


def test_exception_default_no_forced_completion():
    # A(inv=3, del=3) default -> expected (inv=3, del=2). KHÔNG được PUT delivery=3, KHÔNG PUT invoice=1.
    mreq = _run_patch(exact_mirror=False, src_inv=3, src_del=3)
    puts = _calls_to_put_status(mreq)
    assert all(p.get("deliveryDetail", {}).get("status") != 3 for p in puts)
    assert all(p.get("status") != 1 for p in puts)
```

- [ ] **Step 2: Chạy — kỳ vọng FAIL**

Run: `pytest apps/kiotviet/tests/test_patch_invoice_status.py -q`
Expected: FAIL — chữ ký chưa có `exact_mirror` / hành vi CAP cũ khác.

- [ ] **Step 3: Sửa `patch_invoice_status_after_create`**

Thay khối tính `effective_delivery_status` (dòng 604-612) — bỏ CAP cứng cũ, dùng `expected_b_state`:

```python
def patch_invoice_status_after_create(
    *,
    target_channel,
    created_invoice_id: int,
    source_invoice_status: int | None,
    source_delivery_status: int | None,
    source_delivery_code: str = "",
    source_delivery_price: int | None = None,
    order=None,
    exact_mirror: bool = False,
) -> dict | None:
    from ._reconcile_decision import expected_b_state

    exp_invoice_status, exp_delivery_status = expected_b_state(
        source_invoice_status, source_delivery_status, exact_mirror=exact_mirror
    )
    # B được tạo sẵn ở delivery=2 (Đang giao). Chỉ PUT khi đích khác 2 / khác trạng thái hiện có.
    need_delivery = exp_delivery_status is not None and exp_delivery_status != 2
    need_invoice = exp_invoice_status is not None and exp_invoice_status != 3
    if (not need_delivery and not need_invoice) or not created_invoice_id:
        return None
    # ... phần PUT giữ nguyên nhưng thay source_delivery_status -> exp_delivery_status,
    #     source_invoice_status -> exp_invoice_status trong 2 payload + read-back.
```

Cập nhật thân hàm: mọi chỗ dùng `effective_delivery_status` → `exp_delivery_status`; payload invoice
dùng `exp_invoice_status`; read-back so với `(exp_invoice_status, exp_delivery_status)`.

- [ ] **Step 4: Chạy — kỳ vọng PASS**

Run: `pytest apps/kiotviet/tests/test_patch_invoice_status.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/invoice_payload_builder.py backend/apps/kiotviet/tests/test_patch_invoice_status.py
git commit -m "feat(reconcile): patch converges to expected_b_state, drop hard CAP"
```

---

## Task 5: Truyền `exact_mirror` từ create-path tới patcher

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/_sync_service_sync_mixin.py` (chỗ `_sync_invoice` gọi `patch_invoice_status_after_create`)
- Test: dựa Task 4 (không thêm test mới — thay đổi truyền tham số).

**Interfaces:**
- Consumes: `self._exact_mirror` (bool, set ở `run()` từ settings — Task 6).
- Produces: `_sync_invoice` truyền `exact_mirror=self._exact_mirror` vào patcher.

- [ ] **Step 1: Tìm call site**

Run: `grep -n "patch_invoice_status_after_create" backend/apps/kiotviet/infrastructure/services/_sync_service_sync_mixin.py`
Expected: 1 vị trí trong `_sync_invoice`.

- [ ] **Step 2: Thêm truyền tham số**

Tại call đó, thêm `exact_mirror=getattr(self, "_exact_mirror", False),` vào kwargs.

- [ ] **Step 3: Chạy test liên quan — kỳ vọng PASS**

Run: `pytest apps/kiotviet/tests/test_invoice_payload_builder.py apps/kiotviet/tests/test_reconcile_decision.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/_sync_service_sync_mixin.py
git commit -m "feat(reconcile): thread exact_mirror flag into create path"
```

---

## Task 6: Settings flag + prescan bổ sung `invoiceStatus` + mode `reconcile_sync`

> **PREREQUISITE:** Làm **Task 7 TRƯỚC** (tách `_prescan_target` + `_paginate_source`) — Step 5 test của task này mock 2 method đó. Thứ tự thực thi khuyến nghị: 1→2→3→4→5→**7→6**→8→9→10.

**Files:**
- Modify: `backend/config/settings_parts/base.py` (thêm cờ, sau khối `KIOTVIET_IMPORT_CREATE_ONLY_TARGETS` ~dòng 241)
- Modify: `backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py` (prescan map thêm `invoiceStatus`; thêm nhánh mode `reconcile_sync` trong `run()`)
- Test: `backend/apps/kiotviet/tests/test_reconcile_sync_mode.py` (create)

**Interfaces:**
- Consumes: `decide_reconcile_action`, `expected_b_state` (Task 1-2); `delete_invoice_by_id(*, invoice_id, order, channel)`; `_sync_invoice(...)`.
- Produces: `run(reconcile_sync=True, ...)` → summary keys `reconcile_created`, `reconcile_recreated`, `reconcile_skipped`, `reconcile_errors`. `self._exact_mirror` set từ `settings.RECONCILE_DELIVERY_EXACT_MIRROR`.

- [ ] **Step 1: Thêm cờ settings**

Trong `base.py`, sau `KIOTVIET_IMPORT_CREATE_ONLY_TARGETS`:

```python
# Reconcile: mirror delivery của A tuyệt đối cho ca A(giao thành công + đang xử lý)?
# False (mặc định, an toàn sổ sách): cap delivery về Đang giao(2), invoice giữ Đang xử lý(3) —
#   tránh KiotViet auto-complete B thành hoàn-thành-giả. True: set delivery=3, chấp nhận invoice bị ép=1.
RECONCILE_DELIVERY_EXACT_MIRROR = os.getenv("RECONCILE_DELIVERY_EXACT_MIRROR", "0") in {"1", "true", "True"}
```

- [ ] **Step 2: Prescan map thêm `invoiceStatus`**

Tại `sync_invoices_service.py` prescan (dòng ~466-481), thêm vào dict `synced_source_map[src_code_match]`:

```python
                            "invoiceStatus": inv_status,
```

- [ ] **Step 3: Viết test thất bại cho mode (mock KiotViet ở tầng service)**

```python
# backend/apps/kiotviet/tests/test_reconcile_sync_mode.py
from unittest.mock import patch, MagicMock
import apps.kiotviet.infrastructure.services.sync_invoices_service as S


def test_decide_wiring_recreate_then_create(monkeypatch):
    """reconcile_sync: đơn A1 lệch B -> delete+recreate; đơn A2 chưa có B -> create; A3 khớp -> skip."""
    svc = S.SyncInvoicesService()
    # đích: hàm build quyết định + gọi delete/create đúng số lần.
    decisions = []
    monkeypatch.setattr(S, "decide_reconcile_action", lambda **kw: decisions.pop(0))
    # ... dàn dựng source list + prescan qua monkeypatch các helper fetch (xem Step 5).
```

(Test đầy đủ dựng ở Step 5 sau khi có method.)

- [ ] **Step 4: Thêm nhánh mode `reconcile_sync` trong `run()`**

Đặt TRƯỚC khối `update_status` (dòng 246). Set `self._exact_mirror` đầu `run()`:

```python
        from django.conf import settings as _settings
        self._exact_mirror = bool(getattr(_settings, "RECONCILE_DELIVERY_EXACT_MIRROR", False))
```

Nhánh mode (dùng lại prescan + fetch A của luồng chính, nhưng thay `_process_one` bằng decide):

```python
        if bool(options.get("reconcile_sync")):
            from ._reconcile_decision import decide_reconcile_action
            for k in ("reconcile_created", "reconcile_recreated", "reconcile_skipped", "reconcile_errors"):
                summary[k] = 0
            # (a) prescan B -> synced_source_map (đã có, nay kèm invoiceStatus)
            # (b) fetch A theo trang (public, orderBy=id) — tái dùng vòng while ở cuối run()
            # (c) mỗi A: build dict a/b, decide, thực thi:
            def _reconcile_one(invoice_detail: dict) -> None:
                code = self._first_non_empty(invoice_detail.get("code"), invoice_detail.get("Code"))
                inv_del = invoice_detail.get("invoiceDelivery") or {}
                a = {
                    "invoice_status": invoice_detail.get("status") or invoice_detail.get("Status"),
                    "delivery_status": (inv_del.get("status") if isinstance(inv_del, dict) else None),
                    "total": self._first_non_empty(invoice_detail.get("total"), invoice_detail.get("Total")),
                    "delivery_price": (inv_del.get("price") if isinstance(inv_del, dict) else None),
                }
                b_info = synced_source_map.get(code)
                b = None if not b_info else {
                    "invoice_status": b_info.get("invoiceStatus"),
                    "delivery_status": b_info.get("deliveryStatus"),
                    "total": b_info.get("total"),
                    "delivery_price": b_info.get("deliveryPrice"),
                }
                action = decide_reconcile_action(a=a, b=b, exact_mirror=self._exact_mirror)
                if action == "skip":
                    summary["reconcile_skipped"] += 1
                    return
                if action == "delete_recreate":
                    if not dry_run:
                        delete_invoice_by_id(invoice_id=int(b_info["id"]), order=None, channel=target_channel)
                    # rơi xuống tạo lại
                if not dry_run:
                    self._sync_invoice(
                        source_invoice=invoice_detail, source_channel=source_channel,
                        target_channel=target_channel, target_branch_id=target_branch_id,
                        target_is_old=target_channel.old, dry_run=False,
                    )
                summary["reconcile_recreated" if action == "delete_recreate" else "reconcile_created"] += 1
            # dùng lại vòng fetch A ở cuối run() nhưng gọi _reconcile_one thay _process_one:
            self._paginate_source(  # helper trích xuất từ vòng while cuối run() — Task 7
                source_channel=source_channel, source_branch_id=source_branch_id,
                from_dt=from_dt, to_dt=to_dt, page_size=page_size,
                sleep_pages=sleep_pages, sleep_pushes=sleep_pushes, limit=limit,
                on_invoice=_reconcile_one, summary=summary,
            )
            self._stdout(
                f"reconcile_sync done: created={summary['reconcile_created']}, "
                f"recreated={summary['reconcile_recreated']}, skipped={summary['reconcile_skipped']}"
            )
            return summary
```

> Ghi chú: cần `delete_invoice_by_id` đã import ở đầu module (đã có, dùng ở update path cũ).
> `self._paginate_source(...)` là helper tách ở Task 7 từ vòng `while True` fetch A cuối `run()`
> để mode mới và luồng cũ dùng chung (DRY). Nếu Task 7 chưa tách, tạm inline vòng while tại đây.

- [ ] **Step 5: Hoàn thiện test mode (mock `_paginate_source` + prescan)**

```python
def test_reconcile_sync_actions(monkeypatch):
    svc = S.SyncInvoicesService()
    svc._summary = {}
    # ép decide theo thứ tự đơn: A1 recreate, A2 create, A3 skip
    seq = {"HDA1": "delete_recreate", "HDA2": "create", "HDA3": "skip"}
    monkeypatch.setattr(S, "decide_reconcile_action", lambda *, a, b, exact_mirror: seq[a["_code"]])
    deleted, created = [], []
    monkeypatch.setattr(S, "delete_invoice_by_id", lambda **kw: deleted.append(kw["invoice_id"]))
    monkeypatch.setattr(S.SyncInvoicesService, "_sync_invoice", lambda self, **kw: created.append(kw["source_invoice"]["code"]) or "created")
    # dàn source + prescan qua monkeypatch _paginate_source để bơm 3 đơn:
    def fake_paginate(self, *, on_invoice, summary, **kw):
        for code, has_b in [("HDA1", True), ("HDA2", False), ("HDA3", True)]:
            inv = {"code": code, "_code": code, "status": 3, "invoiceDelivery": {"status": 2, "price": 20}, "total": 1000}
            on_invoice(inv)
    monkeypatch.setattr(S.SyncInvoicesService, "_paginate_source", fake_paginate)
    # prescan map dựng sẵn cho HDA1/HDA3:
    monkeypatch.setattr(S.SyncInvoicesService, "_prescan_target", lambda self, **kw: {
        "HDA1": {"id": 11, "invoiceStatus": 3, "deliveryStatus": 2, "total": 999, "deliveryPrice": 20},
        "HDA3": {"id": 33, "invoiceStatus": 3, "deliveryStatus": 2, "total": 1000, "deliveryPrice": 20},
    }, raising=False)
    out = svc.run(
        source_kiotviet_channel_id=1, target_kiotviet_channel_id=2,
        source_branch_id="1", target_branch_id="2", from_dt="2026-06-01 00:00:00",
        reconcile_sync=True, yes=True,
    )
    assert deleted == [11]
    assert created == ["HDA1", "HDA2"]
    assert out["reconcile_recreated"] == 1 and out["reconcile_created"] == 1 and out["reconcile_skipped"] == 1
```

> Nếu prescan chưa tách thành `_prescan_target`, Task 7 sẽ tách; test này đòi hỏi có nó.
> Để test chạy độc lập I/O, Task 7 PHẢI tách prescan + pagination thành method mock được.

- [ ] **Step 6: Chạy — kỳ vọng PASS**

Run: `pytest apps/kiotviet/tests/test_reconcile_sync_mode.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/config/settings_parts/base.py backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py backend/apps/kiotviet/tests/test_reconcile_sync_mode.py
git commit -m "feat(reconcile): reconcile_sync unified mode + exact-mirror flag"
```

---

## Task 7: Tách `_prescan_target` + `_paginate_source` (DRY, để test được I/O)

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py` (trích prescan block → `_prescan_target`; vòng fetch A cuối → `_paginate_source`)

**Interfaces:**
- Produces:
  `_prescan_target(self, *, target_channel, target_branch_id, from_dt, to_dt, page_size, sleep_pages) -> dict[str, dict]`
  `_paginate_source(self, *, source_channel, source_branch_id, from_dt, to_dt, page_size, sleep_pages, sleep_pushes, limit, on_invoice, summary) -> None`

- [ ] **Step 1: Trích prescan block (dòng ~411-490) vào method `_prescan_target`**

Cắt nguyên khối while prescan, trả `synced_source_map`. Luồng cũ gọi
`synced_source_map = self._prescan_target(...)` và tự dựng `synced_source_codes = set(synced_source_map)`.

- [ ] **Step 2: Trích vòng fetch A (dòng ~851-921) vào `_paginate_source`**

Thân vòng `while True` gọi `on_invoice(invoice_detail)` thay vì `_process_one` cứng.
Luồng cũ truyền `on_invoice=_process_one`.

- [ ] **Step 3: Chạy toàn bộ test kiotviet — kỳ vọng PASS**

Run: `pytest apps/kiotviet/tests/ -q -k "reconcile or payload or patch"`
Expected: PASS (không đổi hành vi luồng cũ; chỉ refactor).

- [ ] **Step 4: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py
git commit -m "refactor(reconcile): extract _prescan_target + _paginate_source (DRY)"
```

---

## Task 8: Đổi `RECONCILE_MODES` 6 → 3

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/reconcile.py:21-28`
- Test: `backend/apps/kiotviet/tests/test_reconcile_modes.py` (create)

**Interfaces:**
- Consumes: mode `reconcile_sync` (Task 6).
- Produces: `RECONCILE_MODES` = `[purge_cancelled, purge_duplicates, reconcile_sync]`.

- [ ] **Step 1: Viết test thất bại**

```python
# backend/apps/kiotviet/tests/test_reconcile_modes.py
from apps.kiotviet.infrastructure.services.reconcile import RECONCILE_MODES


def test_modes_are_three_ending_in_reconcile_sync():
    names = [m[0] for m in RECONCILE_MODES]
    assert names == ["purge_cancelled", "purge_duplicates", "reconcile_sync"]
    assert RECONCILE_MODES[-1][1] == {"reconcile_sync": True}
```

- [ ] **Step 2: Chạy — kỳ vọng FAIL**

Run: `pytest apps/kiotviet/tests/test_reconcile_modes.py -q`
Expected: FAIL — còn 6 mode cũ.

- [ ] **Step 3: Sửa `RECONCILE_MODES`**

```python
RECONCILE_MODES: list[tuple[str, dict]] = [
    ("purge_cancelled", {"purge_cancelled": True}),
    ("purge_duplicates", {"purge_duplicates": True}),
    ("reconcile_sync", {"reconcile_sync": True}),
]
```

- [ ] **Step 4: Chạy — kỳ vọng PASS**

Run: `pytest apps/kiotviet/tests/test_reconcile_modes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/infrastructure/services/reconcile.py backend/apps/kiotviet/tests/test_reconcile_modes.py
git commit -m "feat(reconcile): RECONCILE_MODES 6->3 (purge x2 + reconcile_sync)"
```

---

## Task 9: Xoá code chết — `_update_status` + nhánh update trong `run()`

**Files:**
- Modify: `backend/apps/kiotviet/infrastructure/services/sync_invoices_service.py` (xoá nhánh `update_status`/`update_price`/`update_delivery_detail`/`update_sale_channel` trong `run()` + `_process_one`)
- Modify: `backend/apps/kiotviet/infrastructure/services/_sync_service_sync_mixin.py` (xoá `_update_status`)
- Test: xoá test cũ chỉ cho các mode này (nếu có); chạy full kiotviet suite.

**Interfaces:** không còn `run(update_status=...)`, `run(update_price=...)`, v.v.

- [ ] **Step 1: Tìm mọi tham chiếu 3 mode**

Run:
```bash
grep -rn "update_status\|update_price\|update_delivery_detail\|update_sale_channel\|_update_status" backend/apps/kiotviet/infrastructure/services backend/apps/kiotviet/management
```
Ghi lại các call site (đặc biệt trong `management/commands/sync_invoices_between_channels.py` — command CLI cũ có thể vẫn tham chiếu; nếu có, giữ command CLI độc lập KHÔNG đụng, chỉ xoá ở stack service mới dùng bởi reconcile).

- [ ] **Step 2: Xoá nhánh trong `run()`**

Bỏ các khối `if update_status:` (246-272), `if update_delivery_detail:` / `if update_price:` trong `_process_one` (593-753), và khối cấu hình `update_only_modes` (382-403) không còn dùng. Giữ `update_sale_channel` CHỈ nếu còn nơi khác gọi; nếu không, xoá luôn.

- [ ] **Step 3: Xoá `_update_status`**

Bỏ method `_update_status` (dòng 25-249) trong `_sync_service_sync_mixin.py`.

- [ ] **Step 4: Chạy full suite kiotviet — kỳ vọng PASS**

Run (container test):
```bash
docker exec -e POSTGRES_DB=desi_reconcile_$RANDOM desi-order-web-test pytest apps/kiotviet/tests/ -q
```
Expected: PASS (không còn test tham chiếu mode đã xoá; nếu FAIL do test cũ → xoá test cũ tương ứng).

- [ ] **Step 5: Commit**

```bash
git add backend/apps/kiotviet/
git commit -m "refactor(reconcile): remove dead update_status/price/delivery in-place PUT modes"
```

---

## Task 10: Dry-run e2e trên prod + đối chiếu (Sprint 4)

**Files:** none (vận hành).

- [ ] **Step 1: Dry-run window nhỏ trên prod**

```bash
docker exec -it desi-web python manage.py reconcile_channels \
  --date 2026-06-15 --dry-run
```
Đọc log: đếm `reconcile_recreated` (sẽ xoá+tạo) vs `reconcile_skipped`. Kỳ vọng phần lớn skip;
recreate chỉ ở đơn thật lệch.

- [ ] **Step 2: Chạy thật 1 ngày + đối chiếu**

```bash
docker exec -it desi-web python manage.py reconcile_channels --date 2026-06-15
```
Kiểm: phí ship B khớp A; không đơn nào bị hoàn-thành-giả (nếu cờ False); chạy LẠI cùng ngày →
`reconcile_recreated == 0` (idempotent, không churn).

- [ ] **Step 3: Bật lại cron 21h**

Xác nhận `kv-reconcile-paired-channels` chạy sạch 1 đêm: log `reconcile_sync done`, churn=0.

- [ ] **Step 4: Ghi kết quả vào spec + tạo MR**

Cập nhật mục tiến độ trong spec; push nhánh; gửi link MR (theo CLAUDE.md: kiểm conflict master mới nhất trước).

---

## Self-Review

**Spec coverage:**
- Neo delivery=2 + code + fee → Task 3. ✅
- Hội tụ về A / bỏ CAP → Task 4-5. ✅
- Vòng so 4 chiều + delete/recreate → Task 1-2 (thuần) + Task 6 (wiring). ✅
- `RECONCILE_MODES` 6→3 → Task 8. ✅
- Cờ `RECONCILE_DELIVERY_EXACT_MIRROR` default False → Task 6. ✅
- Convergence guard ca A(del=3,inv=3) → `expected_b_state` (Task 1) dùng chung create + compare → không churn. ✅
- Verify live T1/T2/T3 → Task 0; dry-run → Task 10. ✅
- Xoá code chết → Task 9. ✅
- Giữ purge_cancelled/purge_duplicates → Task 8 (không đụng). ✅

**Placeholder scan:** Task 6 Step 3 là skeleton dẫn nhập, bản đầy đủ ở Step 5 — chấp nhận (test thật ở Step 5). Không có TODO/TBD trong code giao.

**Type consistency:** `expected_b_state(a_inv, a_del, *, exact_mirror) -> (inv, del)` dùng đồng nhất ở Task 1/2/4. `decide_reconcile_action(*, a, b, exact_mirror) -> str` đồng nhất Task 2/6. `_prescan_target`/`_paginate_source` chữ ký khớp giữa Task 6 (consumer) và Task 7 (producer) — Task 7 phải xong TRƯỚC khi Task 6 Step 5 test chạy (thứ tự thực thi: 1→2→3→4→5→7→6→8→9→10, hoặc gộp 6+7).

> **Lưu ý thứ tự:** Task 6 phụ thuộc `_prescan_target`/`_paginate_source` của Task 7. Khi thực thi,
> làm Task 7 (refactor tách method, giữ hành vi cũ) TRƯỚC Task 6, hoặc gộp 2 task. Đã đánh dấu ở trên.
