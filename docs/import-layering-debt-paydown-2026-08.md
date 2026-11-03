---
title: Trả nợ phân tầng import giữa các app backend
version: 1.0
date: 2026-08-06
related: backend/.importlinter, docs/architecture-standard.md
---

# Trả nợ phân tầng import (import-linter)

## 1. Vấn đề

`backend/.importlinter` khai 45 entry `ignore_imports` — vi phạm hợp đồng phân tầng
được miễn trừ tạm thời. Con số này được dùng như thước đo "nợ kiến trúc", nhưng nó
đang **đo sai**: phần lớn không phải nợ, và thứ nợ thật nhất lại bị lẫn trong đám đông.

Hệ quả thực tế mà người dùng cảm nhận: sửa một app kéo theo rủi ro ở app khác, không
ai biết ranh giới module nằm ở đâu.

## 2. Hiện trạng đo được (2026-08-06)

Chạy `lint-imports --no-cache` trên master `c0cb2ed4`:

```
Analyzed 1269 files, 2698 dependencies.
App layering KEPT
Contracts: 1 kept, 0 broken.
```

Contract **đang PASS**. Không có nợ mới phát sinh từ 24/07. Phân loại 45 entry:

| Nhóm | Số entry | Bản chất | Xử lý |
|---|---|---|---|
| Test dựng fixture cross-app | 19 | Không phải coupling runtime | Ngoại lệ vĩnh viễn |
| `staging_neutralize`, `seed_initial_data` | 7 | Command quét mọi app theo bản chất | Ngoại lệ vĩnh viễn |
| **Code prod vượt tầng** | **19** | Nợ kiến trúc thật | Refactor theo lô |

Nợ thật phân bố theo cặp app:

| Cặp | Entry | Ghi chú |
|---|---|---|
| `orders` → `kiotviet` | 12 | 63% toàn bộ nợ. 1 trong số đó là import chết |
| `channels_core` → `orders` | 2 | Consumer realtime đọc thẳng model đơn |
| `orders` → ads apps | 2 | Báo cáo tổng hợp đọc bảng spend |
| `inventory` → `kiotviet` | 1 | `return_order_sync` gọi thẳng infrastructure |
| `users.serializers` → `kiotviet.models` | 1 | |
| `users.subscribers` → `hr.contracts` | 1 | Đã đi qua contracts, chỉ sai chiều tầng |

### 2.1. Ba phát hiện quyết định cách làm

**(a) Entry được ghi là "nợ lớn nhất" là một import chết.**

`backend/.importlinter` chú thích `apps.orders.models -> apps.kiotviet.models` là
*"nợ lớn nhất — FK model + invoice sync trực tiếp"*. Thực tế:

```python
# apps/orders/models.py:13
from apps.kiotviet.models import KiotVietChannel   # không dùng ở bất kỳ đâu

# apps/orders/models.py:287
kiotviet_channel = models.ForeignKey("kiotviet.KiotVietChannel", ...)   # lazy string
```

FK đã dùng lazy reference chuẩn Django. `ruff check --select F401` xác nhận
*"imported but unused"*. Xoá một dòng là trả xong entry này.

Lưu ý: `ruff check .` với cấu hình dự án **không** bắt lỗi này, vì `F401` nằm trong
danh sách `ignore` của `pyproject.toml` (105 vi phạm, khai là nợ cần trả dần). Phải
chỉ định `--select F401` mới thấy. Nói cách khác, lớp import chết này hiện không có
công cụ nào soi tới.

**(b) Không có gì đang thực thi contract.**

- Không có CI trong repo (branch `chore/remove-cicd` cho thấy đã gỡ chủ động).
- `core.hooksPath` trỏ `frontend/.husky/_`; husky chỉ có `pre-commit` chạy
  `lint-staged` cho frontend. `.githooks/pre-commit` **không bao giờ được gọi**.
- Do đó `lint-imports` lẫn `ruff` đều không chạy tự động ở bất kỳ đâu trên backend.

Điểm thuận lợi: `ruff check .` trên master hiện thoát mã 0. Cắm nó vào hook không
làm gãy đường của ai.

**(c) Nợ đã từng được trả và bị cuốn trôi.**

Commit `8766b9a1` *"route kiotviet invoice calls through contract port"* (30/07)
đã dựng đúng pattern Port/Adapter:

```
apps/orders/contracts/kiotviet_invoice_port.py   (Port — orders định nghĩa)
apps/kiotviet/adapters.py                        (Adapter — kiotviet cài đặt)
apps/kiotviet/apps.py                            (đăng ký lúc ready())
apps/orders/tests/test_kiotviet_invoice_port_wiring.py
docs/architecture-orders.md
```

Nó bị `26c1fa82` *"revert: roll back audit hardening and follow-up merges"* revert
cùng ngày, trong một cú revert diện rộng. Không ai phát hiện, vì không có gì gác.

**Kết luận:** dựng hàng rào phải đi trước trả nợ, nếu không kịch bản 30/07 lặp lại.

## 3. Nguyên tắc

1. **Chặn trước, trả sau.** Lô 0 dựng enforcement, không đụng một dòng logic nào.
2. **Phân biệt ngoại lệ với nợ.** `ignore_imports` tách thành 2 khối có tiêu đề rõ
   ràng. Khối "NGOẠI LỆ VĨNH VIỄN" không bao giờ phải trả. Khối "NỢ PHẢI TRẢ" chỉ
   được giảm, không được tăng.
3. **Mỗi lô một MR độc lập.** Không gộp. MR đụng `orders` (55k LOC) mà to thì
   không ai review nổi, và nếu phải revert thì mất trắng như lần trước.
4. **Dùng lại pattern đã có.** Port đặt ở app tầng dưới (`orders/contracts/`),
   Adapter đặt ở app tầng trên (`kiotviet/adapters.py`), đăng ký ở `AppConfig.ready()`.
   Không phát minh pattern mới giữa chừng.
5. **Test không nằm trong phạm vi refactor.** Xem §6.

## 4. Cơ chế enforcement (Lô 0)

Ràng buộc: máy dev không chắc có Python env cho backend (stack chạy bằng Docker).
Cài `pip install` trong hook mỗi lần push là không chấp nhận được về tốc độ.

Thiết kế `scripts/check-backend-arch.sh`:

1. Nếu `lint-imports` có sẵn trên PATH → chạy trực tiếp (nhanh nhất).
2. Nếu không → chạy qua image lint local `desi-lint:local`. Image build **một lần**
   (~30s), các lần sau chỉ `docker run` (~5s).
3. Nếu không có cả Docker → in cảnh báo và thoát 0. Hook không được chặn đường
   người không dựng được môi trường; nó là gờ giảm tốc, không phải cổng.

Script chạy hai thứ: `lint-imports` và `ruff check`.

Wire vào `frontend/.husky/pre-push` (vị trí duy nhất `core.hooksPath` thực sự đọc).

**Giới hạn phải nói rõ:** hook bypass được bằng `--no-verify`, và chỉ hoạt động khi
dev đã chạy `npm install` trong `frontend/`. Đây **không phải** cổng thật. Cổng thật
là CI — nằm ngoài phạm vi tài liệu này, xem hạng mục CI/registry đã bàn riêng.

## 5. Phân lô

Đếm chính xác 12 entry `orders → kiotviet` để chia lô theo cụm nghiệp vụ, không theo
từng dòng:

| # | Entry | Lô |
|---|---|---|
| 1 | `orders.models -> kiotviet.models` (import chết) | 0 |
| 2-4 | `orders.invoice ->` `kiotviet.models`, `gateway_kship_customer_webhook`, `invoice_sync_queries` | 1 |
| 5 | `orders.invoice -> invoice_mutations_push` | 1 |
| 6-7 | `order_crud_view ->` `kiotviet.models`, `kiotviet.services` | 2 |
| 8 | `product_item_crm_serializers -> kiotviet.models` | 2 |
| 9 | `ingest_job_tasks -> kiotviet.services` | 2 |
| 10 | `auto_sync_invoice_full -> kiotviet.models` | 2 |
| 11 | `order_service_commands -> cancellation_verifier` | 3 |
| 12 | `shipping_lookup_view -> cancellation_verifier` | 3 |

| Lô | Nội dung | Nợ còn lại | Rủi ro |
|---|---|---|---|
| **0** | Enforcement + phân loại lại config + xoá import chết | 45 entry → **18** nợ thật | Gần 0 |
| **1** | Khôi phục Port/Adapter hoá đơn đã bị revert, mở rộng cho `orders/invoice.py` (4 entry) | 18 → **14** | Thấp |
| **2** | Luồng tạo/sửa đơn đi qua port: `order_crud_view`, serializer CRM, `ingest_job_tasks`, `auto_sync_invoice_full` (5 entry) | 14 → **9** | Trung bình — nghiệp vụ nóng nhất |
| **3** | Port kiểm tra huỷ đơn (`cancellation_verifier`) cho `orders` (2 entry) | 9 → **7** | Trung bình |
| **4** | `channels_core.consumers` đọc đơn qua contract thay vì model (2 entry) | 7 → **5** | Trung bình — chạm WebSocket |
| **5** | Read-model báo cáo ads (2 entry) + `inventory.return_order_sync` qua port riêng của `inventory` (1 entry) | 5 → **2** | Thấp |

Hai entry dư (`users.serializers -> kiotviet.models`,
`users.subscribers -> hr.contracts.hr_events`) đánh giá lại sau Lô 5 — entry thứ hai
đã đi qua `contracts` đúng chuẩn, nhiều khả năng chỉ cần sửa định nghĩa tầng chứ
không phải sửa code.

### Định nghĩa hoàn thành từng lô

- `lint-imports` PASS.
- Số entry ở khối "NỢ PHẢI TRẢ" giảm đúng bằng số đã khai trong MR.
- Test backend liên quan tới app bị đụng chạy xanh.
- Không thêm entry mới vào bất kỳ khối nào.

## 6. Quyết định: không refactor 19 entry test

Test dựng fixture bằng model của app khác là chuyện bình thường và không tạo coupling
lúc chạy production. Chuyển 19 entry này sang khối "NGOẠI LỆ VĨNH VIỄN".

**Đánh đổi:** nếu sau này tách service thật, đống coupling ở tầng test sẽ lộ ra và
phải xử lý lúc đó. Chấp nhận, vì đổi lại danh sách 45 dòng nhiễu trở thành 18 dòng
tín hiệu thật — và một thước đo sai còn tệ hơn không có thước đo.

## 7. Rủi ro

| Rủi ro | Giảm thiểu |
|---|---|
| Revert diện rộng cuốn trôi lô đã xong (như 30/07) | Mỗi lô một MR nhỏ, độc lập; enforcement ở Lô 0 làm vi phạm tái phát lộ ra ngay lần push kế |
| Lô 2 chạm luồng tạo đơn — nghiệp vụ nóng nhất | Tách MR riêng, test luồng tạo đơn phải xanh trước khi gửi |
| Lô 4 chạm WebSocket, dễ tái hiện bão reconnect | Kiểm tra thủ công realtime trên local trước khi gửi MR |
| Hook bị bypass `--no-verify` | Chấp nhận ở giai đoạn này; cổng thật là CI, theo dõi ở hạng mục riêng |

## 8. Tiến độ

| Lô | Trạng thái | MR | Ghi chú |
|---|---|---|---|
| 0 | ✅ Xong 2026-08-06 | !1163 (đã merge) | Nợ 19 → 18 |
| 1 | ✅ Xong 2026-08-06 | !1164 (đã merge) | Nợ 18 → 14 |
| 2 | ✅ Xong 2026-08-06 | !1165 (đã merge) | Nợ 14 → 10 (trả 4/5, xem ghi chú) |
| 3 | ✅ Xong 2026-08-06 | !1166 (đã merge) | Nợ 10 → 8 — **làm master đỏ**, vá ở Lô 4 |
| 4 | ✅ Xong 2026-08-06 | !1168 | Nợ 8 → 6, kèm vá master đỏ |
| 5 | ✅ Xong 2026-08-06 | `refactor/ads-spend-and-inventory-ports` | Nợ 6 → **3** |

Sau mỗi lô, cập nhật bảng này: đã làm gì, còn nợ gì, entry giảm từ bao nhiêu xuống
bao nhiêu.

### Lô 0 — đã làm (2026-08-06)

- Xoá import chết `apps/orders/models.py:13` → nợ thật 19 → 18.
- `backend/.importlinter` chia hai khối **A — NGOẠI LỆ VĨNH VIỄN** (26) và
  **B — NỢ PHẢI TRẢ** (18), khối B gắn nhãn từng lô. Entry sắp xếp theo thứ tự
  chữ cái trong mỗi nhóm để diff về sau đọc được.
- Bật `unmatched_ignore_imports_alerting = error`: entry miễn trừ không còn khớp
  vi phạm nào sẽ làm lint FAIL. Đã kiểm chứng bằng cách thêm một entry rác —
  lint báo *"No matches for ignored import"*. Đây là chốt chống lặp lại đúng lớp
  lỗi đã che import chết suốt nhiều tuần.
- `scripts/check-backend-arch.sh`: chạy `lint-imports` + `ruff check`. Tự dò công
  cụ trên PATH → Docker → bỏ qua kèm cảnh báo.
- `frontend/.husky/pre-push` gọi script trên.
- Ghim `ruff==0.16.1` vào `requirements-dev.txt` (trước đó ruff không được ghim ở
  bất kỳ đâu, dù `pyproject.toml` có cấu hình đầy đủ cho nó).

**Đã kiểm chứng:**

| Kịch bản | Kết quả |
|---|---|
| Cây sạch | `Contracts: 1 kept, 0 broken` + `ruff: All checks passed!` → exit 0 |
| Thêm `apps.common.db_guards -> apps.orders.models` | `App layering BROKEN` → exit 1 |
| Thêm entry miễn trừ không khớp gì | `No matches for ignored import` → fail |

**Chưa làm / hạn chế còn lại:**

- Hook không chạy trong git worktree, vì `frontend/.husky/_` do `npm install` sinh
  ra và bị gitignore. Ai làm trong worktree phải chạy tay
  `sh scripts/check-backend-arch.sh`.
- Hook bypass được bằng `git push --no-verify`.
- Chưa có cổng thật. Việc này chỉ đóng lại khi CI được dựng lại — hạng mục riêng,
  không thuộc lộ trình này.

### Lô 1 — đã làm (2026-08-06)

Khôi phục nguyên vẹn commit `8766b9a1` bằng `git cherry-pick -n`. Chỉ
`backend/.importlinter` xung đột (do Lô 0 viết lại toàn bộ file); toàn bộ code áp
sạch, không phải viết lại dòng nào — bằng chứng cho thấy cú revert 30/07 là tai nạn
diện rộng chứ không phải do bản refactor có vấn đề.

Khôi phục về:

| File | Vai trò |
|---|---|
| `apps/orders/contracts/kiotviet_invoice_port.py` | Port: `Protocol` orders tự khai + registry |
| `apps/kiotviet/adapters.py` | Adapter: import service LƯỜI (lúc gọi, không lúc import) |
| `apps/kiotviet/apps.py` | Đăng ký adapter ở `KiotVietConfig.ready()` |
| `apps/orders/invoice.py` | Gọi qua port; `KiotVietChannel` chuyển vào `TYPE_CHECKING` |
| `apps/orders/tests/test_kiotviet_invoice_port_wiring.py` | 5 test wiring |
| `docs/architecture-orders.md` | Kiến trúc đích của app `orders` |

Hai chi tiết thiết kế đáng giữ lại từ bản gốc:

- **Adapter import service lười.** Nhờ vậy `ready()` không kéo cả cây service của
  kiotviet lúc khởi động, và mọi test đang patch
  `apps.kiotviet.infrastructure.services.*` vẫn ăn vào call qua port — không phải
  sửa test nào.
- **`orders/invoice.py` giữ nguyên tên hàm module-level** (`get_invoice_by_code`,
  `is_koc_non_cod_order`, `_kiotviet_web_request`) làm lớp mỏng gọi port, nên test
  patch `apps.orders.invoice.<tên>` cũng không phải sửa.

Sửa thêm ngoài bản gốc: thêm một dòng trống trước `_TEMPLATES_DIR` cho đúng PEP8
(ruff không bắt vì nhóm `E3` không nằm trong `select`).

**Đã kiểm chứng:** `lint-imports` KEPT với 14 entry; `ruff check .` sạch;
**101 test pass** (`test_kiotviet_invoice_port_wiring`, `test_order_print`,
`test_gateway_kship_invoice_lookup`, `test_invoice_payload_builder`,
`test_invoice_webhook`, `test_return_order_sync`).

### Lô 2 — đã làm (2026-08-06), trả 4/5 entry

Thêm port thứ hai `apps/orders/contracts/kiotviet_order_flow_port.py` với 4 method:
`list_channel_options`, `iter_active_channels`, `get_kship_options`,
`execute_order_action`. Adapter `KiotVietOrderFlowAdapter` ở `kiotviet/adapters.py`,
đăng ký cùng chỗ với adapter hoá đơn.

| Entry | Cách trả |
|---|---|
| `order_crud_view -> kiotviet.models` | `list_channel_options(offset, limit)` |
| `order_crud_view -> kiotviet.services` | `get_kship_options(channel)`, giữ tên hàm module-level cũ |
| `ingest_job_tasks -> kiotviet.services` | `execute_order_action(...)`; 6/7 tên import ở đây thật sự chết |
| `auto_sync_invoice_full -> kiotviet.models` | `iter_active_channels()` |
| `product_item_crm_serializers -> kiotviet.models` | **CHƯA trả** — xem dưới |

**Entry thứ 5 không trả được, và lý do quan trọng hơn con số.**

`ruff --select F401` báo `KiotVietChannel` trong `product_item_crm_serializers` là
import không dùng. Xoá theo → **8 test ERROR ngay lúc collect**. Nguyên nhân: tên đó
chảy sang `kiotviet_landing_serializers` qua chuỗi star-import
(`kiotviet_landing → order_core → product_item_crm`) và được dùng ở `queryset=` của
một DRF `PrimaryKeyRelatedField` — tức là cần ngay lúc import module, trước khi
port có thể resolve. Gỡ nó đòi hỏi đổi cách serializer dựng field (defer queryset),
việc đó có rủi ro riêng và không thuộc phạm vi lô này.

Đã trả lại import kèm comment cảnh báo tại chỗ, để lần sau ai chạy `ruff --fix`
không xoá lại. Entry giữ nguyên trong khối B với ghi chú đầy đủ.

**Bài học:** `ruff F401` **không** nhìn xuyên star re-export. Trong lô này có 2 ca
"import chết" — một ca thật (6/7 tên ở `ingest_job_tasks`) và một ca giả. Từ giờ
mọi trường hợp nghi import chết phải kiểm tra chuỗi star-import trước khi xoá, chứ
không tin ruff.

Ca thứ hai cùng loại đã bắt được trước khi gãy: `auto_sync_invoice_full` import
`get_invoice_by_code` **từ** `ingest_job_tasks` (re-export), nên tên đó không chết —
đã chuyển sang gọi `kiotviet_invoice_port()` của Lô 1.

**Sửa test:** `test_kship_sender_address` patch
`ingest_job_tasks.KiotVietService.execute_action`; đổi sang patch tại nơi class thật
sự sống (`apps.kiotviet.infrastructure.services.service_actions`). Patch thuộc tính
trên class có hiệu lực toàn cục nên vẫn chặn đúng call qua adapter.

Gộp `test_kiotviet_invoice_port_wiring.py` → `test_kiotviet_port_wiring.py` phủ cả
hai port, thay vì thêm file test rời cho mỗi port.

**Đã kiểm chứng:** `lint-imports` KEPT với 10 entry; `ruff check .` sạch; toàn bộ
`apps/orders` + `apps/kiotviet` — **1269 passed, 16 subtests passed** (15'13"),
không một failure. Vì lô này chạm luồng tạo đơn nên chạy cả hai suite chứ không
chỉ vài file liên quan.

### Lô 3 — đã làm (2026-08-06)

Port thứ ba `apps/orders/contracts/kiotviet_cancellation_port.py` cho guard chặn
xuất kho đơn đã huỷ (`order_service_commands`) và tra cứu vận đơn
(`shipping_lookup_view`).

**Lô này khác hai lô trước về bản chất.** Phụ thuộc không chỉ là lời gọi hàm mà
gồm cả **kiểu dữ liệu**: một enum kết quả (`CancelCheck`), một exception
(`VerificationUnavailable`), và một hằng số (`CANCELLED_INVOICE_STATUS`). Nếu để
orders tiếp tục dùng enum/exception của kiotviet thì ranh giới chỉ là hình thức.
Nên orders khai kiểu của mình, adapter dịch ở biên:

| Phía kiotviet | Phía orders | Ai dịch |
|---|---|---|
| `CancelCheck` | `CancelVerdict` | Adapter, theo `.value` |
| `VerificationUnavailable` | `CancellationVerificationUnavailable` | Adapter, `raise ... from exc` |
| `CANCELLED_INVOICE_STATUS` | `apps/orders/order_status_constants.py` | Orders tự khai |

**Cái bẫy phải cẩn thận: guard so sánh verdict bằng `is`, không phải `==`.**
`CancelCheck` là `str` enum nên `CancelVerdict.OK == CancelCheck.OK` vẫn `True`,
nhưng `is` thì `False`. Nếu port trả chuỗi trần hay enum của app khác, guard sẽ
âm thầm đi nhánh sai — mà đây là guard chặn xuất kho đơn đã huỷ, đi sai nhánh là
trừ tồn kho cho đơn không tồn tại. Đã ghi cảnh báo này ngay đầu file port, và có
test `assertIs` cho cả ba giá trị.

**Hằng số `CANCELLED_INVOICE_STATUS = 2` đã tồn tại độc lập ở 3 nơi** trước lô này
(`kiotviet/.../cancellation_verifier`, `customers/order_status_constants`,
`orders/.../floating_pool_service`). Thêm bản của orders là bản thứ 4 nhưng đúng
chỗ: `invoice_status` là cột của chính `orders.Order`, orders phải sở hữu hằng số
đọc cột của mình. Đã tạo `apps/orders/order_status_constants.py` theo đúng khuôn
mẫu `apps/customers/order_status_constants.py` sẵn có.

*Việc chưa làm:* `floating_pool_service.py` vẫn giữ bản sao cục bộ của hằng số này.
Gộp nó vào module mới là dọn dẹp hợp lý nhưng nằm ngoài phạm vi phân tầng — để lại
làm sau, không nhét vào MR này.

**Giá trị lạ → `UNVERIFIABLE` (fail-closed).** Trước đây một giá trị không hợp lệ
không khớp `CANCELLED` cũng không khớp `UNVERIFIABLE` nên lọt qua. Nay adapter quy
về `UNVERIFIABLE`, khiến `shipping_lookup_view` tra thêm DB trước khi cho qua. Đây
là thay đổi hành vi duy nhất của lô, chỉ chạm tới đường dữ liệu hỏng, và nghiêng
về phía an toàn.

**Một file test phải sửa — và tôi đã đoán sai chỗ này lúc rà.**

`test_dispatch_cancelled_guard` patch phía kiotviet
(`cancellation_verifier.InvoiceCancellationVerifier.verify_not_cancelled`) nên
không cần đụng. Nhưng `test_shipping_lookup` patch phía **orders**
(`shipping_lookup_view.InvoiceCancellationVerifier...`) — lúc grep tôi chỉ thấy
dòng đuôi `"InvoiceCancellationVerifier.verify_not_cancelled"` của một chuỗi nối
nhiều dòng nên tưởng cả hai file cùng patch phía kiotviet. Kết quả: **13 test đỏ**
với `AttributeError: module ... has no attribute 'InvoiceCancellationVerifier'`.

Đã đổi `_VERIFY` của file đó sang module nguồn phía kiotviet, đồng nhất với file
kia. Bài học lặp lại nguyên xi bài học Lô 2: grep một mảnh chuỗi rồi suy ra đích
patch là không đủ — phải đọc cả biểu thức.

**Đã kiểm chứng:** `lint-imports` KEPT với 8 entry; `ruff check .` sạch.
`apps/orders` + `apps/kiotviet` + `apps/inventory`: **1847 passed / 13 failed**
(19'10") ở lần đầu — 13 lỗi đều là ca patch-target nói trên. Sau khi sửa, chạy lại
5 file liên quan (`test_shipping_lookup`, `test_kiotviet_port_wiring`,
`test_dispatch_cancelled_guard`, `test_dispatch_stock`, `test_pending_scan_match`):
**111 passed, 3 subtests passed**. Thay đổi sau lần chạy đầy đủ chỉ nằm trong file
test, không đụng code prod.

> ⚠️ **Lô 3 lọt một vi phạm lên master.** Xem "Sự cố quy trình" ở Lô 4.

### Lô 4 — đã làm (2026-08-06)

**Chiều ngược với Lô 1-3.** `channels_core` nằm **dưới** `orders` trong contract
phân tầng, nên lần này **app tầng dưới khai port** (`channels_core/contracts.py`)
và **orders đăng ký adapter** (`apps/orders/adapters.py`, gọi ở
`OrdersConfig.ready()`). Cùng cơ chế registry, đảo vai.

| Entry | Cách trả |
|---|---|
| `channels_core.consumers -> orders.contracts` | `validate_event_payload(payload) -> dict \| None` |
| `channels_core.consumers -> orders.models` | `order_belongs_to_user`, `resolve_order_channel_code` |

Hai điểm giữ nguyên có chủ đích:

- **Tên hai method `_order_belongs_to_user` / `_resolve_order_channel_code` không
  đổi.** `test_broadcast_authorization` subclass `CRMConsumer` và override đúng hai
  method này để đếm số lần chạm DB (sự cố 2026-07-29). Đổi tên là test mất tác dụng
  mà vẫn xanh — kiểu hỏng nguy hiểm nhất.
- **Exception không vượt biên.** `OrderEventContractError` được nuốt ngay trong
  adapter, port trả `None`. Consumer chỉ cần biết "bỏ qua event này".

Port không thêm lần chạm DB nào: hai method vẫn nằm sau `broadcast_db_gate` như cũ,
chỉ dời chỗ định nghĩa.

Test wiring `apps/channels_core/tests/test_order_broadcast_port_wiring.py` **cố ý
không import gì từ `apps.orders`** — patch bằng đường dẫn chuỗi — để chính nó không
tạo vi phạm mới.

#### Sự cố quy trình: Lô 3 làm master đỏ

Sau khi merge !1166, `lint-imports` trên master **BROKEN**:

```
apps.orders.tests.test_kiotviet_port_wiring ->
apps.kiotviet.application.services.cancellation_verifier
```

Diễn biến: ở Lô 3 tôi chạy `scripts/check-backend-arch.sh` **trước** khi viết test
wiring, rồi không chạy lại sau khi thêm test — mà test wiring buộc phải import kiểu
gốc phía kiotviet để chứng minh adapter dịch đúng.

Hàng rào đã không bắt được, đúng như hạn chế tự khai ở Lô 0: **hook không chạy
trong git worktree** vì `frontend/.husky/_` do `npm install` sinh ra và bị gitignore.
Mọi lô của lộ trình này đều làm trong worktree.

Đã vá trong MR này: khai entry đó vào **KHỐI A** (test wiring chạm kiểu gốc là ngoại
lệ hợp lý, cùng nhóm 19 entry test sẵn có) → khối A 26 → 27 entry.

**Rút ra:** `lint-imports` phải là bước CUỐI trước khi commit, không phải bước giữa.
Và chừng nào chưa có CI, mỗi MR của lộ trình này phải chạy lại
`sh scripts/check-backend-arch.sh` ngay trước `git commit`.

### Lô 5 — đã làm (2026-08-06)

Hai port cuối, cả hai đều cùng chiều với Lô 1-3 (app tầng dưới khai port, app tầng
trên đăng ký adapter).

**a) Chi tiêu quảng cáo — `orders/contracts/ads_spend_port.py`**

Điểm cốt lõi: orders **chưa bao giờ thực sự cần model quảng cáo**. Hai helper cũ
nhận `model` làm THAM SỐ rồi query y hệt nhau cho cả hai nguồn:

```python
def _spend_rows(model, account_ids): qs = model.objects.filter(...)
def _total_spend(model):             qs = model.objects.all()
```

Nên port không bọc model — nó trả thẳng **số liệu**. Registry theo nguồn
(`AdsSpendSource.FACEBOOK` / `.GOOGLE`); `facebook_ads` và `google_ads` mỗi app tự
đăng ký provider ở `AppConfig.ready()`.

*Quyết định về parse ngày:* logic `fromisoformat` + nuốt `ValueError` trước nằm
trong helper của orders. Đẩy xuống adapter thì hai adapter phải lặp lại y hệt, nên
tách thành `_parse_report_date()` ở tầng orders và port nhận thẳng `date | None`.
Hành vi khoan dung với chuỗi ngày hỏng (bỏ qua bộ lọc, không ném lỗi) giữ nguyên.

**b) Đẩy trạng thái giao hàng — `inventory/contracts/kiotviet_delivery_port.py`**

Ca đơn giản nhất lộ trình: một lời gọi, vốn đã là local import. Adapter thêm vào
`kiotviet/adapters.py` đã có sẵn.

*Port CỐ Ý không nuốt exception.* Caller (`return_order_sync`) tự bọc `try/except`
và chỉ ghi log, vì "lỗi KiotViet không được chặn nghiệp vụ kho". Nếu port nuốt lỗi
thì khối `except` bên caller thành code chết và người đọc sau sẽ hiểu sai luồng
lỗi. Có test ghim điều này.

**Không phải sửa test nào.** Lần này tôi đọc CẢ BIỂU THỨC chuỗi patch trước khi
sửa (bài học lặp ở Lô 2 và Lô 3): `test_return_order_sync` patch phía kiotviet nên
adapter bind lười vẫn chặn đúng.

Hai file test wiring mới đều **cố ý không import gì** từ app phía bên kia — chỉ
patch bằng đường dẫn chuỗi — để chính chúng không sinh vi phạm mới như MR !1166.

**Đã kiểm chứng:** `lint-imports` KEPT với **3 entry**; `ruff check .` sạch;
`apps/orders` + `apps/inventory` + `apps/facebook_ads` + `apps/google_ads`:
**1677 passed, 23 subtests passed** (19'07"), không một failure.

## 9. Sau Lô 5 — 3 entry còn lại

Nợ thật đi từ **19 → 3**. Ba entry còn lại KHÔNG refactor thẳng được, mỗi cái cần
một quyết định riêng:

| Entry | Bản chất | Hướng xử lý |
|---|---|---|
| `product_item_crm_serializers -> kiotviet.models` | Star re-export nuôi `queryset=` của DRF field, cần lúc import module | Đổi cách serializer dựng field (defer queryset) — bài toán serializer, không phải phân tầng |
| `users.serializers -> kiotviet.models` | Chưa khảo sát | Khảo sát trước khi quyết |
| `users.subscribers -> apps.hr.contracts.hr_events` | Đã đi qua `contracts/` đúng chuẩn | Nghi là **định nghĩa tầng sai**, không phải code sai |

⚠️ **Cảnh báo cho entry thứ ba.** Sửa `layers =` để hợp thức hoá `users → hr` nghe
như sửa một dòng, nhưng nó tính lại quan hệ của MỌI cặp app. Những vi phạm đang
được che bởi thứ tự tầng hiện tại sẽ lộ ra cùng lúc. Phải **đo trước khi đổi**:
sửa thử `layers`, chạy `lint-imports`, đếm vi phạm mới — rồi mới quyết.
