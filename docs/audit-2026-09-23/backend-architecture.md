# Audit kiến trúc backend — 2026-09-23 (đối chiếu audit 2026-08-19)

## 0. Đối chiếu audit trước
| Mục cũ | Trạng thái | Bằng chứng |
|---|---|---|
| N3 `.importlinter` delimiter | ĐÓNG | `.importlinter:26-49` containers/exhaustive/`|`; lint-imports KEPT 1882 file/4105 dep; 27/27 app khai tầng |
| N4 Engine sync KiotViet fork | MỞ MỘT NỬA | command → adapter 229 dòng, nhưng `_sync_invoices_legacy.py` 2835 dòng vẫn sống sau `--legacy`; commit 68380b98 (19/09) vẫn vá cả legacy; `SyncInvoicesService.run()` 868 dòng |
| C1 orders god module | MỘT PHẦN | catalog tách (`apps/catalog/models.py` 9 model); orders/models.py 47→39 class; nhưng LOC orders 33.0k→38.1k (+15%) |
| C2 django-ninja | ĐÓNG | không còn trong requirements |
| C3 logic trong serializer | ĐÓNG (serializer) | atomic trong serializers = 0; nợ dời sang view (P2-1) |
| T4 star-import | ĐÓNG | F403/F405=0, 9 facade có noqa |
| T4 `_repositories_reports.py` 1936 | ĐÓNG (đổi hình) | tách `orders/reporting/` 8 module 3296 dòng; `ads_reports.py` 1087 vẫn vượt |
| T4 requests trần / timeout | ĐÓNG | HttpClient; best_client có timeout |
| T4 mypy | ĐÓNG | pyproject `[tool.mypy]`, job backend:typecheck |
| T4 pytest-cov | CÒN | không có |
| F10 CI/hook | ĐÓNG TRÊN GIẤY, HỎNG THỰC TẾ | ruff master ĐỎ 5 lỗi; core.hooksPath unset; husky `_` không tồn tại |
| Lộ trình trả nợ B | Lô 0-5 đóng, Lô 6 chưa bắt đầu | Khối B 106 entry (104→102 từ 20/08) |

## 1. Số đo (cùng script AST, non-test, không migrations)
| Chỉ số | 08-18 | 09-23 |
|---|---|---|
| App | 21 | 27 |
| LOC non-test | 106 065 | 127 506 (+20%) |
| File test / LOC test | 319 / 73k | 456 / 99k |
| Hàm test | — | 5 312 |
| File >800 dòng | 14 | 17 |
| Hàm >150 dòng | 50 | 57 |
| Class >1000 dòng | 6 | 7 |
| Import `apps.X.models` chéo app | — | 242 |
| Ruff (rule đang ignore, trừ E501) | 405 | 626 (+55%) |

LOC theo app: orders 38 135 (29.9%, +15%), kiotviet 19 125, inventory 14 816, hr 5 034, callcenter 4 997, facebook_ads 4 182, users 4 168, foxia 3 671, customers 3 505 (−47%), cnv_loyalty 3 270 (+73%), ai_mart 2 967, channels_core 2 795, common 2 373, daily_report 1 833, media 1 727, pancake 1 658, analytics 829, catalog 680.

File >800: `_sync_invoices_legacy.py` 2835; `order_service_commands.py` 1475 (+259); `pending_scan_service.py` 1457 (+132); `orders/models.py` 1428; `orders/repositories.py` 1404; `gateway_kship_customer_webhook.py` 1290 (+327); `sync_invoices_service.py` 1261; `order_service_helpers.py` 1141; `reporting/ads_reports.py` 1087; `order_action_view.py` 1009 (+138); `_sync_service_check_mixin.py` 939; `import_excel.py` 878; `import_order_xlsx_standalone.py` 830; `webhook_impl.py` 820; `common_auth_customer.py` 817; `product_catalog_views.py` 805; `_import_service_handlers_mixin.py` 805.

Hàm >300: `sync_invoices_service.py:97 run` 868; legacy `handle` 814; `service_actions.py:122 execute_action` 639; `repositories.py:482 _apply_list_filters` 541; `reporting/overview.py:63 summary_overview` 478; `invoice_mutations_push.py:320` 433; `order_service_commands.py:79 create_order_from_landing` 364; `webhook_impl.py:490` 331; `ingest_job_tasks.py:403` 320; `order_service_commands.py:446 update_order_inline` 316.

## 2. Phát hiện
### P0-1. Hàng rào ruff ĐỎ trên master, hook local không chạy, CI ruff không ghim phiên bản
- `ruff check .` (0.16.1 theo requirements-dev.txt:6) → 5 lỗi: `ai_mart/management/commands/ai_mart_chat_fdw.py:27 RET503`; `foxia/tests/test_foxia_order_push.py:489 SIM300`; `users/tests/test_admin_api.py:324,355 SIM300`; `users/tests/test_rbac_sections.py:360 SIM300`. Vào master ở 63165276 (05/09), 14ff1c20 (16/09), 7c979fd2 (17/09).
- `.gitlab-ci.yml:57` `uvx ruff check .` không ghim phiên bản.
- `git config core.hooksPath` unset; `frontend/.husky/_` không tồn tại → pre-push không chạy.
- Sửa: fix 5 lỗi; `uvx ruff@0.16.1`; xác nhận GitLab Runner; `core.hooksPath .githooks` + pre-push gọi `check-backend-arch.sh`.

### P1-1. Monolith sync KiotViet 2835 dòng còn sống và vẫn được vá song song
- `_sync_invoices_legacy.py` docstring :16-22 "xoá sau 1 release" (20/08); commit 68380b98 (19/09) sửa cả 3 file. `run()` 868 dòng.
- Sửa: xoá legacy + cờ `--legacy` + `SyncCliEngineRoutingTests`; tách `run()` prescan→paginate→compare→mutate.

### P1-2. Nợ phân tầng Lô 6 đứng yên 5 tuần; "cấm import ORM chéo" không thực thi; header .importlinter sai số
- Khối B 106 entry: inventory→orders 25, orders↔customers cycle 39, channels_core↔users 18, orders→inventory 6, orders→contact_flow 5, audit↔dispatch 3.
- 242 điểm `from apps.X.models import` chéo app: inventory→orders 24, kiotviet→orders 23, orders→catalog 22, orders→channels_core 16, orders→users 16.
- Header sai: `.importlinter:52` "A 30" thực 61; `:147` "B 3" thực 106; `:185` "104" thực 102.
- Sửa: Lô 7 cycle customers↔orders qua `orders/contracts/customer_port.py`; test đếm khối A/B; contract forbidden `apps.*.models`.

### P1-3. God class/function tiếp tục phình, không gate "không tăng"
- Xem bảng §1. Sửa: baseline AST JSON commit vào repo, ratchet fail khi tăng.

### P1-4. Nợ ruff +55% dưới ignore; B008/E401 = 0 vẫn ignore; E501 chưa bao giờ bật
- 626 (388 prod, 238 test). SIM117 29→90, UP017 22→55, B905 14→27, SIM108 26, DTZ007 22, DTZ001 16, F401 119. E501 riêng: 418.
- Sửa: xoá B008/E401; --fix UP017/UP037/UP006/UP045/F541/C420 (~110); baseline `ruff --statistics` ratchet.

### P1-5. orders +15% LOC sau tách catalog; 5 context/39 class; 2 nơi "services"
- `orders/services/__init__.py:9` star-facade song song `orders/application/services/` 26 file. `Order.save()` :538-541 lazy import `customers.phone_utils`.
- Sửa: tách app `leads` (PhoneBlocklist, PhoneClaim, SaleReminder*, LeadAssignmentShare, ShiftWindow, FloatingPool*); move phone_utils → common; xoá `orders/services/`.

### P2-1. atomic + nghiệp vụ trong view (5 điểm)
`lead_assignment_view.py:184,254,485`; `price_book_views.py:104`; `foxia/transport/link_config_views.py:171`; `order_action_view.py:598,624,721`.

### P2-2. application→transport (3), domain import django (4)
`order_event_publisher.py:190`, `order_ingest_service.py:132` lazy-import serializers; `contact_list_operation.py:10`. Domain: `best_express/domain/status_map.py:3`, `hr/domain/workday.py:17`, `daily_report/domain/periods.py:13`, `warranty_codes/domain/serial.py:3`.

### P2-3. Repository chỉ là tên; không DI
`BaseRepository` chỉ hr + warehouses dùng. `OrderRepository` 1190 dòng 26 classmethod. Stub: inventory 23 dòng, customers 34, users 8.

### P2-4. Model-signal làm nghiệp vụ chéo app
`customers/signals.py:34-37` post_save Order → refresh_customer_type; `cnv_loyalty/subscribers.py:115-124` post_save Order/Customer/Product → enqueue task (bypass publish_on_commit).

### P2-5. Code trùng lặp
- `_parse_date` 6 bản (callcenter/exports_definitions.py:29; quality_views_impl.py:45; contact_list_view.py:23; reporting/shared.py:38; warranty_codes/exports_definitions.py:154; revenue_report_service.py:152).
- Pagination tự viết 3 chỗ ngoài `common.pagination` (contact_list_view.py:55-69; users/_view_helpers.py:76; daily_report/domain/paging.py:28).
- `CANCELLED_INVOICE_STATUS = 2` 4 bản; `lead_close_service.py:37` = (6,) — giá trị khác, cần xác nhận.
- Command gần giống: backfill_fb_campaign_products vs backfill_google_campaign_products; sync_invoice_amounts vs sync_invoice_item_prices.

### P2-6. 8/85 management command không ai gọi
`channels_core/run_pubsub_bridge` (kênh `desi:events:crm` không còn ai publish → chết), `kiotviet/repair_order_channel_branch`, `sync_kiotviet_daily_revenue`, `sync_invoice_amounts`, `sync_invoice_item_prices`, `orders/backfill_kiotviet_address`; công cụ không runbook: `gads_refresh_token`, `diagnose_lead_assignment`.

### P2-7. Settings base.py 1198 dòng, 8 import E402, id prod hard-code default
`KIOTVIET_STATUS_SYNC_TARGETS` default channel 4/5/6 + branch_ids prod (:549-551, chỉ warning); `KOC_CHANNEL_ID` 15 (:1146); `SALE_FB_CHANNEL_IDS` 24 (:1166). Sửa: guard fail-fast prod; tách base.py.

### P2-8. Migrations: orders 146; squash chưa dọn gốc
`orders/migrations/0001_squashed_0089` song song 90 file gốc + `_squashed_src/` 14 file; 0090→0146 = 57 migration/11 tuần. RunPython không reverse: 1 (inventory/0022, chủ đích).

### P2-9. Không pytest-cov; conftest cấp app 8/27.
### P2-10. Docs/config sai số (.importlinter header, pyproject "405", comment E501).

## Top 5
1. Dựng lại cổng 1 MR: fix 5 ruff, ghim ruff, hooksPath, xoá B008/E401, baseline ratchet ruff + AST, sửa header.
2. Xoá `_sync_invoices_legacy.py` + `--legacy`; tách `run()`.
3. Lô 7: cycle customers↔orders (39 entry) qua contracts; move phone_utils → common.
4. Tách app `leads` khỏi orders; xoá `orders/services/`; đẩy 5 khối atomic trong view xuống service.
5. Dọn nền: 6 command chết, gộp 2 cặp command, `common/dates.py`, gom pagination, CANCELLED_INVOICE_STATUS 1 nơi, dọn migration gốc, thêm pytest-cov.
