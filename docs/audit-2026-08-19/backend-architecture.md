# Audit chi tiết — Kiến trúc & Cấu trúc Code backend (2026-08-19)

> Báo cáo gốc của luồng audit kiến trúc. Bản tổng hợp: `../audit-2026-08-19-full-stack.md`.
> **Phạm vi đo:** 796 file `.py` non-test, ~74.8k LOC non-test trong `backend/apps` (21 app);
> 297 file test; 1504 file / 3269 dependency theo `lint-imports`.

## 0. Tóm tắt điều hành

| # | Phát hiện | Mức |
|---|---|---|
| F1 | `.importlinter` dùng `:` = **non-independent** delimiter → contract KEPT nhưng **không chặn** bất kỳ import ngang hàng nào; comment trong file nói ngược lại với hành vi thực tế | **Critical** |
| F2 | 7/21 app **không nằm trong** contract phân tầng, không có `exhaustive` | **High** |
| F3 | SCC 17 app — đồ thị phụ thuộc gần như một khối; 16 cặp cycle 2 chiều | **Critical** |
| F4 | `orders` là god module: 33.8k LOC (45% backend), 47 class trong 1 file model, 82 điểm import từ 11 app khác | **Critical** |
| F5 | ~5.6k dòng engine đồng bộ KiotViet **nhân bản có chủ đích** thành 2 nhánh không dùng chung code | **Critical** |
| F6 | `EncryptedCharField` nằm trong `kiotviet/models.py`, 5 app khác import ở **tầng model + migration** | **High** |
| F7 | django-ninja chỉ là scaffold demo (3 file); 142 file thực chạy DRF — stack tuyên bố ≠ stack thật | **High** |
| F8 | Business logic + `transaction.atomic` + `bulk_create` nằm trong DRF serializer | **High** |
| F9 | Refactor "tách file bằng star-import" tạo facade 3 tầng, namespace vẫn phẳng (F403=27, F405=576 phải ignore) | **High** |
| F10 | ruff + lint-imports **không chạy trong pre-commit**, chỉ ở `frontend/.husky/pre-push` | **High** |
| F11 | Không có mypy/django-stubs; type hint 68% return / 69% param; `from __future__ import annotations` chỉ 38% file | **Medium** |
| F12 | Transaction/atomic **dùng đúng** — không tìm thấy HTTP/`.delay()` trong atomic block | **Low (điểm tốt)** |

## 1. Phân tầng & Module boundary

### 1.1 `.importlinter` xanh nhưng không bảo vệ gì — lỗi 1 ký tự

`backend/.importlinter:33-38` dùng `:` nối app cùng tầng; dòng 15 ghi *"các app cùng tầng
độc lập với nhau"*. Nhưng trong import-linter 2.12:
- `importlinter/contracts/layers.py:20` → `_INDEPENDENT_LAYER_DELIMITER = "|"`
- `importlinter/contracts/layers.py:21` → `_NON_INDEPENDENT_LAYER_DELIMITER = ":"`

**Hệ quả:** mọi import giữa các app *cùng dòng* được cho qua. Đếm được **~118 import
ngang hàng** đang vô hình với contract:

| Cặp | Số import |
|---|---|
| `inventory -> orders` | 35 |
| `customers -> orders` | 28 (**cycle** với dòng dưới) |
| `orders -> customers` | 20 |
| `channels_core -> users` | 15 (**cycle**) |
| `orders -> inventory` | 7 (**cycle**) |
| `users -> channels_core` | 5 |
| `facebook_ads/google_ads/foxia/best_express/cnv_loyalty -> kiotviet` | 6 |
| `foxia -> facebook_ads`, `foxia -> google_ads` | 2 |

Con số "18 khoản nợ thật" đang bị đánh giá thấp một bậc độ lớn. — **Critical**
*(→ ĐÃ SỬA trong MR fix/importlinter-independent-layers: đổi `|`, containers+exhaustive,
ghi 117 entry nợ vào B-Lô6.)*

### 1.2 Bảy app ngoài vùng phủ contract

Không có trong `.importlinter` và không có `exhaustive`: `audit`, `callcenter`,
`contact_flow`, `dispatch`, `warehouses`, `warranty`, `warranty_codes`.
→ `callcenter -> orders` (6), `contact_flow -> orders` (2) + `orders -> contact_flow`
(5, **cycle**), `warranty_codes -> orders` (6), `audit ↔ dispatch` (**cycle**) không
bao giờ bị soi. — **High**

### 1.3 Đồ thị phụ thuộc: một SCC 17 app

Tarjan trên đồ thị import non-test cho **một** SCC gồm: audit, best_express, callcenter,
channels_core, contact_flow, customers, dispatch, exports, facebook_ads, google_ads, hr,
inventory, kiotviet, orders, users, warehouses, warranty.

16 cặp cycle 2 chiều: `orders↔users`, `orders↔customers`, `orders↔inventory`,
`orders↔kiotviet`, `orders↔contact_flow`, `orders↔callcenter`, `kiotviet↔channels_core`,
`kiotviet↔users`, `customers↔users`, `hr↔users`, `channels_core↔users`,
`facebook_ads↔users`, `google_ads↔users`, `best_express↔users`, `audit↔users`,
`audit↔dispatch`.

Chỉ 4 app đứng ngoài: `cnv_loyalty`, `foxia`, `warranty_codes` (fan-in = 0) và `common`
(fan-out = 0). — **Critical**

### 1.4 God module: `orders`

| Chỉ số | Giá trị |
|---|---|
| LOC non-test | **33 796** (45.2% toàn backend; thứ 2 là `kiotviet` 17 927) |
| Số file | 228 (gấp đôi `inventory`) |
| Fan-out / Fan-in | 12 / 12 app |
| Điểm import `apps.orders.models` từ app khác | **82** (inventory 26, kiotviet 18, cnv_loyalty 10, customers 10, warranty_codes 5, foxia 4, callcenter 3, users 2, warranty 2, contact_flow 1, facebook_ads 1) |
| Route trong `apps/orders/urls.py` | 76 (217 dòng) |

`apps/orders/models.py` (1623 dòng) chứa **47 class**, trộn 6 bounded context:

| Context | Class | Dòng |
|---|---|---|
| **Product Catalog** | Product, ProductGroup, ProductCategory, ProductPackage, ProductComponent, PriceBook, ProductPackagePrice, ProductProfile, ProductProfileImage | `:124-343`, `:1572-1623` |
| **Order core** | Order, OrderItem, OrderJourneyEvent, OrderMilestoneSnapshot | `:366-710`, `:997-1053`, `:1118-1141` |
| **Assessment/CRM** | NeedGroup, NeedType, OrderAssessment, OrderNeed, AssessmentFieldDef | `:711-935` |
| **KiotViet job queue** | OrderKiotVietJob, OrderKiotVietJobStatus | `:1054-1117` |
| **Shipping (KShip)** | KshipNotePreset, KshipSenderAddress | `:1277-1337` |
| **Lead/Sales ops** | PhoneBlocklist, PhoneClaim, SaleReminder, LeadAssignmentShare, ShiftWindow, FloatingPoolConfig, FloatingPoolEntry | `:1208-1571` |
| **Ads reporting (Lark)** | AdsDailyChannelLarkRecord, AdsDailyProductLarkRecord | `:1395-1429` |

### 1.5 Coupling ở tầng DB (quan trọng nhất cho tách service)

Import có thể gỡ bằng port; **FK không**. Bản đồ FK chéo app:

```
inventory -> warehouses : 8      warranty      -> orders/users : 2/2
inventory -> orders     : 8      warranty_codes-> orders/users : 2/2
hr        -> users      : 3      best_express  -> orders/users : 1/1
customers -> orders     : 2      callcenter    -> orders/users : 1/1
orders    -> kiotviet   : 1      channels_core -> users/orders/kiotviet : 1/1/1
users     -> kiotviet   : 1      cnv_loyalty   -> orders : 1
foxia     -> channels_core/orders : 1/1   contact_flow -> customers : 1
```

Hai FK **đi ngược tầng** mà import-linter không thấy (string reference, không sinh import):
- `apps/channels_core/models.py:21-22` → `price_book = ForeignKey("orders.PriceBook")`
- `apps/users/models.py:86-87` → `UserKiotvietMapping.kiotviet_channel = ForeignKey("kiotviet.KiotVietChannel")`

**Fix:** contract `forbidden` thứ hai cho FK string-ref, hoặc test kiến trúc quét
`_meta.get_fields()` — đây là lớp coupling mà `.importlinter` về bản chất mù.

### 1.6 `EncryptedCharField` đặt sai chỗ

`apps/kiotviet/models.py:21` định nghĩa tiện ích hạ tầng thuần tuý, bị import ở tầng
model bởi 5 app (`best_express`, `cnv_loyalty`, `facebook_ads`, `foxia`, `google_ads`)
và **migration đã đóng băng đường dẫn** trong 5 file `0001_initial.py`.
→ Không app nào tách ra được nếu chưa move field sang `apps/common/` + `AlterField`
cho cả 6 app. — **High**

### 1.7 Mức độ sẵn sàng tách service — xếp hạng

| Hạng | Bounded context | Fan-in | FK ra ngoài | Đánh giá |
|---|---|---|---|---|
| 🟢 1 | **cnv_loyalty** | 0 | 1 (orders.Order) | Có contracts/dtos.py, infrastructure/cnv_client.py, subscribers dùng event bus. Thay 1 FK bằng order_id + gỡ EncryptedCharField là tách được. **Đầu tiên.** |
| 🟢 2 | **foxia** | 0 | 2 | Có contracts/dtos.py, foxia_client.py, dùng common.http_client. Fan-out 8 nhưng toàn read-only. |
| 🟡 3 | **warranty_codes + warranty** | 0/2 | 4 (orders.Product, users.User) | Cụm khép kín, 30 file test. Cần tách Product khỏi orders trước. |
| 🟡 4 | **hr** | 1 | 3 (users.Department) | Đã có contracts/hr_events.py + domain/ + application/. Vướng 1 nợ B. |
| 🟡 5 | **callcenter** | 1 | 2 | 4056 LOC, tự chứa, chỉ đọc orders. |
| 🟠 6 | **Product Catalog** (tách khỏi orders) | — | — | **Bước bắt buộc** mở khoá inventory/warranty*. 9 model, 5 FK từ inventory, 3 từ warranty*. |
| 🔴 7 | inventory | 2 | 8 FK orders + 8 warehouses | Chờ Catalog service. |
| 🔴 8 | kiotviet | 8 | FK 2 chiều orders/users/channels_core | ACL đã có (adapters, 4 port) nhưng schema dính chặt. |
| ⛔ | orders, users, common | 12/16/18 | — | Là lõi, không phải ứng viên tách. |

## 2. Cấu trúc bên trong app (4 app mẫu)

### 2.1 Ba "thế hệ" kiến trúc đang cùng tồn tại

| Thế hệ | Cấu trúc | App |
|---|---|---|
| G1 — Django phẳng | models + views + serializers + services top-level | `customers` (31 file top-level!), `warehouses`, `dispatch`, `audit` |
| G2 — Layered | transport/ + application/services + repositories | `orders`, `inventory`, `hr`, `contact_flow`, `channels_core` |
| G3 — Hexagonal | thêm domain/, contracts/, infrastructure/, adapters | `orders`, `hr`, `kiotviet` (một phần) |

Không app nào hoàn tất chuyển đổi. — **Medium**

### 2.2 `orders` — layered nhưng chưa "clean"

- `apps/orders/services/__init__.py` **và** `apps/orders/application/services/` cùng tồn tại — hai nơi cho một khái niệm. — Medium
- 37 module domain-logic nằm phẳng top-level (`import_excel.py` 877, `invoice.py`, `followup.py`, `address_parser.py`, `crm_catalog.py`, `dynamic_assessment.py`, …). — Medium
- **DTO/schema riêng: KHÔNG có.** DRF ModelSerializer kiêm validation + DTO + ORM write.
- Điểm tốt: `order_action_view.py` 905 dòng nhưng chỉ 8 lần `.objects.`, 0 atomic — view thực sự uỷ quyền; độ dài đến từ 7 khối `@extend_schema`.
- Ngoại lệ xấu: `order_action_view.py:501` `_expire_stale_jobs()` và `:527` `_active_job_order_ids()` — logic nghiệp vụ nằm trong transport. — Medium

### 2.3 `inventory` — cấu trúc tốt nhất trong các app lớn

application/services 14 file, transport/views 6 file, repositories, contracts (3 file),
subscribers. Vấn đề: `flow_history_views.py` 939 dòng gom 3 nhóm màn hình. — Medium

### 2.4 `kiotviet` — God Service + Mixin

`SyncInvoicesService` = SyncPurgeMixin + SyncCheckMixin + SyncCreateMixin (572+702+274
dòng); `run()` trải `:84-916` — **832 dòng một hàm**. — **Critical**
`ImportInvoicesService` = ImportHandlersMixin (689) + ImportUpsertMixin (455).
Mixin ở đây không phải composition — các mixin gọi `self._xxx` của nhau, không interface,
không test riêng được.

### 2.5 `contact_flow` — mẫu tham chiếu nên nhân rộng

993 LOC non-test / 6 test file; models.py 98 (4 model, 0 save() override); mọi file
production < 200 dòng. Nhược: không có repositories.py; type-hint return 41%.

### 2.6 Bảng so sánh

| App | LOC non-test | api/router | service | repository | DTO | File lớn nhất |
|---|---|---|---|---|---|---|
| orders | 33 796 | ✅ transport | ✅ (+ dir trùng services/) | ✅ | ❌ | 1936 |
| inventory | 12 917 | ✅ | ✅ 14 file | ✅ | ❌ | 1325 |
| kiotviet | 17 927 | ⚠️ views.py + webhook_impl 820 | ⚠️ God service + mixin | ❌ | ❌ | 2835 |
| contact_flow | 993 | ✅ | ✅ | ❌ | ⚠️ | 197 |
| customers | 6 602 | ❌ 31 file phẳng | ⚠️ 8 file *_service.py phẳng | ✅ | ❌ | ~600 |

## 3. TOP 15 file dài nhất (`backend/apps`, trừ migrations)

| # | Dòng | File | Nhận định & cách tách | Mức |
|---|---|---|---|---|
| 1 | 2835 | `kiotviet/management/commands/sync_invoices_between_channels.py` | `handle()` 822 dòng. Docstring `:18-21` tự thú: *"bản monolith tự chứa (revert về snapshot 12/05/2026)… hai luồng KHÔNG còn dùng chung engine"*. **Xoá command, gọi lại SyncInvoicesService với flag CLI.** | Critical |
| 2 | 1936 | `orders/_repositories_reports.py` | OrderReportsMixin 40+ classmethod, mix 4 miền: sale (:350-673), delivery quality (:699-933), quyền ads (:970-1059), báo cáo ads (:1060-1664), overview (:1665). Tách package `reporting/` 4 module; `SALE_FB_CHANNEL_IDS = (24,)` `:149` ra settings. | Critical |
| 3 | 1623 | `orders/models.py` | 47 class / 6 context (§1.4). Tách `catalog/` trước — tiên quyết cho inventory/warranty*. | Critical |
| 4 | 1325 | `inventory/application/services/pending_scan_service.py` | 26 static/classmethod, 3 use case: query (:146-472), matching engine (:473-801), manual ops (:803-1290). Tách 3 service + PendingScanRepository. | High |
| 5 | 1309 | `orders/repositories.py` | `_apply_list_filters` dài 471 dòng (:479-949) → filter registry / django-filter (đã có trong requirements). Tách cache logic (:950-991) ra decorator. | High |
| 6 | 1216 | `orders/application/services/order_service_commands.py` | Star-import `order_service_shared` (:4). Tách theo use case: create/update/transfer/restore. | High |
| 7 | 1205 | `kiotviet/infrastructure/services/sync_invoices_service.py` | `run()` 832 dòng → pipeline steps (prescan→paginate→compare→mutate), mỗi step một class có test. Bỏ mixin. | Critical |
| 8-10 | 1128/1050/1039 | test_ingest_api / test_inventory_export_definitions / test_invoice_webhook | test — chấp nhận, nên chia theo endpoint. | Low |
| 11 | 1012 | `orders/application/services/order_service_helpers.py` | Tên "helpers" = thùng rác kiến trúc. Phân loại lại theo domain rule/mapper/validator. | High |
| 12 | 1006 | `kiotviet/infrastructure/services/gateway_kship_customer_webhook.py` | Tên file gộp 3 trách nhiệm; dùng `requests` trần (:50, :230, :910) thay vì common/http_client. Tách 3 module. | High |
| 13 | 953 | test_contact_list | test | Low |
| 14 | 939 | `inventory/transport/views/flow_history_views.py` | 13 view + 5 helper → issue/return/lookup_views.py; đẩy `_common_filters` (:53), `_parse_replacement` (:704) xuống application. | Medium |
| 15 | 905 | `orders/.../order_action_view.py` | Tách 7 khối extend_schema sang schemas riêng + 2 helper job sang service → còn ~300 dòng. | Medium |

Kế tiếp (16-22): import_excel 877 · import_order_xlsx_standalone 829 · webhook_impl 820 ·
product_catalog_views 767 · service_actions 746 · common_auth_customer 741 ·
invoice_mutations_push 726.

## 4. Design pattern

### 4.1 ✅ Ports & Adapters — làm rất chuẩn (điểm mạnh nhất codebase)
4 port + 2 chiều: `orders/contracts/{kiotviet_invoice,kiotviet_order_flow,kiotviet_cancellation,ads_spend}_port.py`,
`inventory/contracts/kiotviet_delivery_port.py`, chiều ngược `channels_core/contracts.py`
(app tầng dưới khai port, orders cắm adapter). Adapter đăng ký trong `ready()`.

### 4.2 ✅ In-process Event Bus
`apps/common/events.py:44-79` — `publish_on_commit()` dùng `transaction.on_commit`,
handler isolation, "Events are plain immutable data".

### 4.3 ✅ Signal — tiết chế, đúng chỗ
Chỉ 13 điểm, toàn cache-invalidation/integration hook, có `dispatch_uid`, không
signal-chạy-signal.

### 4.4 ✅ Shared HTTP adapter — nhưng adoption dở dang
`common/http_client.py` (Retry 429/5xx, timeout 10s, pooling) được 10 module dùng.
**KiotViet — tích hợp lớn nhất — không dùng**: `common_auth_customer.py:146,585,696`,
`gateway_kship_customer_webhook.py:50,230,910`, `fetch_kiotviet_locations.py:69` gọi
`requests` trần → không retry/backoff ở đúng integration hay hỏng nhất. — High
`best_express/best_client.py:28-32` `_http_post()` không set default timeout. — Medium

### 4.5 ❌ Anti-pattern: "tách file bằng star-import"
58 dòng `from X import *` (non-test). `kiotviet`: 3 facade cho cùng tập tên, các module
star-import lẫn nhau → namespace vẫn là khối phẳng 4700 dòng; đổi lại F403 (27) +
F405 (**576**) phải ignore trong pyproject. Tương tự orders (`order_service_commands:4`,
`order_service_helpers:3`), domain/address (2 facade). — High

### 4.6 ❌ Business logic trong DRF serializer
`product_item_crm_serializers.py` (681 dòng): 3 cặp create/update `@transaction.atomic`,
query PriceBook, bulk_create ProductPackage/PackagePrice. 35 điểm `.objects.`/`atomic`
trong module serializer toàn repo → use case không có nơi trú ngụ, chỉ test được qua
HTTP. — High

### 4.7 ❌ Cross-app cross-layer import
`orders/application/services/contact_list_operation.py:10` import
`contact_flow.transport.serializers.runtime_serializers.bucket_of` — application của A
import transport của B. `order_ingest_service.py:133` vá cycle nội bộ bằng lazy import
thay vì đảo phụ thuộc. — High/Medium

### 4.8 ⚠️ "Fat model" — KHÔNG phải vấn đề
Model rất mỏng (orders 3 save() override, inventory/kiotviet/contact_flow 0). Vấn đề là
quá nhiều aggregate trong một file. Ngoại lệ: `Order.save()` (:685-709) làm 3 việc +
lazy import vô nghĩa (`:14` đã import Customer top-level). — Low

### 4.9 ⚠️ Service layer = namespace tĩnh, không DI
Toàn repo chỉ 9 `def __init__` trong application/services. Test phải monkeypatch
module-level; không thay được implementation khi tách service. — Medium

### 4.10 ⚠️ Contract phụ thuộc ngược models + validate tay
`orders/contracts/order_events.py:10` import OrderStatus từ models; `:74` biến
`statusES` (dấu vết find-replace hỏng); `validate_payload()` ~100 dòng validate tay
trong khi pydantic 2.13.4 có sẵn trong requirements. — Medium

### 4.11 ❌ Duplicate code

| Trùng lặp | Vị trí | Mức |
|---|---|---|
| Engine sync KiotViet ~2835 vs ~2753 dòng | command vs service + 3 mixin | Critical |
| `_web_list_invoices` | command:2703 và service:1112 | High |
| `_normalize_dt_arg` | command:2824 và service:1196 | High |
| `resolve_target_customer` | command:2550 vs import shared từ invoice_payload_builder — cả hai trong cùng file | High |
| `CANCELLED_INVOICE_STATUS` 3 nơi | orders/customers/kiotviet | Medium (có chủ đích, documented) |
| Channel ID 24 hard-code 2 nơi | `_repositories_reports.py:149` và `lark_ads_report_sync.py:42` | Medium |

### 4.12 ❌ django-ninja: stack tuyên bố ≠ stack thật
Chỉ 3 file chạm (scaffold demo `config/api_v2.py:29`, ninja_auth, ninja_demo); 142 file
non-test dùng DRF. Strangler chưa migrate module thật nào. — High

## 5. Chất lượng code

### 5.1 Type hints — 68/69%, không có type checker
Return annotation 2143/3130 = 68%; param 3361/4833 = 69%; `from __future__ import
annotations` 310/796 = 38%; **không có mypy/pyright/django-stubs**. Đuôi thấp: audit 25%,
contact_flow 41%, warehouses 40%. Đề xuất: mypy `--follow-imports=skip` cho
`apps/*/contracts/` + `apps/common/` trước, mở rộng dần.

### 5.2 Ruff — triết lý tốt, danh sách nợ khổng lồ
Ruleset E4,E7,E9,F,B,C4,DTZ,LOG,PIE,RET,SIM,T20,UP hợp lý. Nhưng ignore 62 rule; E501
664, F405 576, F403 27, F401 105. F403/F405 không bao giờ trả được chừng nào chuỗi
star-import (§4.5) còn — không phải "nợ", là hệ quả kiến trúc.

### 5.3 CI/hook — hàng rào KHÔNG chạy tự động
`.githooks/pre-commit` chỉ check encoding; `check-backend-arch.sh` đúng logic nhưng chỉ
được gọi từ `frontend/.husky/pre-push:9`; không có `.gitlab-ci.yml`. Sự cố đã xảy ra
thật: vi phạm lọt master ở MR !1166 (`docs/import-layering-debt-paydown-2026-08.md:425`).
**5.3b:** lint-imports crash Windows `'charmap' codec` vì box-drawing chars — cần
`PYTHONUTF8=1`. *(→ ĐÃ SỬA trong MR fix/restore-gitlab-ci.)*

### 5.4 Test — 297 file, coverage mất cân đối
django_db 119 (40%); API client 105 (35%) → integration test đội lốt unit; conftest cấp
app chỉ 6/21; **không có pytest-cov**. `kiotviet` 17.9k LOC chỉ 21 file test vs
`facebook_ads` 3.6k LOC 17 file. 23 entry miễn trừ "test dựng fixture app khác" là tín
hiệu chẩn đoán aggregate boundary sai, không chỉ là chuyện test.
Điểm sáng: `backend/conftest.py` (throttle disable + cách ly channel id cứng) là fixture
chất lượng cao, document đúng *tại sao*.

### 5.5 ✅ Transaction/atomic — đúng chỗ (điểm mạnh)
158 điểm atomic / 17 app; select_for_update 84; on_commit 10; **0** HTTP call trong
atomic; **0** `.delay()` trong atomic; **0** side-effect ngoài trong hàm @atomic.
Lưu ý còn lại (Low): rà chỗ nào dùng `publish()` (immediate) thay vì
`publish_on_commit()` trong atomic — bus có cả hai API.

## 6. Lộ trình đề xuất (ROI)

**Ngay:** (1) `.importlinter` `:`→`|` ✅ đã làm; (2) exhaustive + 7 app ✅ đã làm;
(3) CI + PYTHONUTF8 ✅ đã làm.
**Ngắn hạn:** (4) xoá command sync fork (−2835 dòng, cần diff staging trước);
(5) move EncryptedCharField → common; (6) atomic/bulk_create từ serializer xuống
service; (7) chốt số phận django-ninja.
**Trung hạn:** (8) tách Product Catalog; (9) tách `_repositories_reports.py` →
`reporting/`; (10) gỡ star-import kiotviet (trả 603 vi phạm ruff); (11) mypy contracts+common.
**Dài hạn:** tách theo thứ tự cnv_loyalty → foxia → warranty* → hr → callcenter; trước
mỗi lần tách thay FK chéo bằng id + event.
