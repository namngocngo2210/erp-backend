# Cron import KiotViet "create-only" mỗi 15 phút

- **Ngày:** 2026-06-26
- **Branch:** `feat/kv-import-create-only-cron`
- **Trạng thái:** Design approved

## 1. Bối cảnh & mục tiêu

Hiện invoice mới từ KiotViet được kéo về DB (tạo `Order`) bằng cách chạy tay lệnh
`import_kiotviet_invoices --create-only`. Ta muốn một tiến trình tự động chạy **mỗi 15 phút**,
kéo invoice mới của các kênh KiotViet và **chỉ tạo Order mới** (bỏ qua đơn đã tồn tại), để dữ liệu
đơn gần realtime mà không phải thao tác thủ công.

Đây là chiều **PULL inbound** (KiotViet API → Order DB của ta), **không phải** mirror new→old
(việc đó do cron `reconcile_paired_channels` đảm nhiệm — xem
`docs/.../kiotviet-sync` & memory `kiotviet-sync-single-flow`).

### Phi mục tiêu (out of scope)
- Không cập nhật đơn đã tồn tại (không update giá/trạng thái/sđt) — đã có cron khác lo
  (`update_invoice_status_targets` mỗi 30', `reconcile_paired_channels` 21h).
- Không đụng luồng webhook inbound, không đụng mirror new→old.

## 2. Quyết định thiết kế (đã chốt)

| Vấn đề | Quyết định |
|---|---|
| Phạm vi kênh | Tự quét **mọi `KiotVietChannel` có `old=False`, `deleted_at IS NULL`** — không cấu hình tay |
| branch | Dùng `channel.branch_id` của chính kênh; kênh thiếu `branch_id` → skip + log |
| Cửa sổ quét (window) | **Hôm nay**: `00:00 localtime → now`, không lookback |
| Chống chồng lấn | **Redis lock per-channel** qua `cache.add(key, 1, ttl)`; tick sau bỏ qua kênh đang chạy |
| Cách gọi logic | **Không `call_command`**: khởi tạo `Command()` rồi gọi thẳng method handler (theo tiền lệ `_update_status_for_target`) |
| Quan hệ với job cũ | Bổ sung độc lập; reconcile 21h, update-status 30', revenue 1h **giữ nguyên** |

## 3. Kiến trúc

Luồng dữ liệu:

```
celery beat (mỗi 15')
  └─ import_invoices_create_only_task(from_dt=None, to_dt=None)   [queue: kiotviet]
       ├─ window = _today_window()                # 00:00 localtime → now
       └─ for channel in iter_import_channels():  # old=False, branch_id non-empty
            ├─ acquire per-channel Redis lock (cache.add); nếu fail → skip ("đang chạy")
            ├─ _import_invoices_for_target(channel_id, [branch_id], create_only=True, window)
            │    └─ Command()._handle_import(..., create_only=True, list_missing=False)
            │         └─ paginate KiotViet /invoices → _upsert_invoice(create_only=True)
            └─ finally: release lock (cache.delete)
```

### 3.1. Refactor `import_kiotviet_invoices.py` — tách "hàm chức năng chính"

Vòng lặp phân trang + upsert (đường mặc định trong `handle()`, hiện ở
[`import_kiotviet_invoices.py:335-421`](../../../backend/apps/kiotviet/management/commands/import_kiotviet_invoices.py))
được trích ra một method để dùng chung:

```python
def _handle_import(
    self, *, kv_channel, branch_ids, dry_run, create_only,
    list_missing, from_dt, to_dt, target_sale_channel_value,
) -> dict:
    """Phân trang /invoices và upsert (hoặc liệt kê missing). Trả về summary dict."""
```

- Method tự dựng và trả `summary` (`read/created/updated/skipped/errors/pages`).
- `handle()` ở đường mặc định chỉ còn `summary = self._handle_import(...)` rồi in summary.
- **Hành vi CLI giữ nguyên 100%** — đây chỉ là rút gọn để tái dùng, đúng tinh thần
  "command chỉ dùng cho CLI, logic dùng chung".
- Method nằm trên `Command` (hoặc `ImportHandlersMixin`) nên dùng lại trọn bộ helper sẵn có
  (`_upsert_invoice`, `_find_existing_order`, `_extract_sale_channel_id`,
  `_resolve_invoice_own_status`, `_apply_date_params`, `_first_non_empty`, `_to_int`...),
  tránh nhân bản logic.

### 3.2. `apps/kiotviet/tasks.py` — task + helper

Mượn `Command()` instance, **không** `call_command` (y hệt `_update_status_for_target`):

```python
def _import_invoices_for_target(*, channel_id, branch_ids, create_only, from_dt, to_dt) -> dict:
    from apps.kiotviet.management.commands.import_kiotviet_invoices import Command
    kv_channel = KiotVietChannel.objects.get(id=channel_id, deleted_at__isnull=True)
    cmd = Command()
    return cmd._handle_import(
        kv_channel=kv_channel,
        branch_ids=[str(b).strip() for b in branch_ids if str(b).strip()],
        dry_run=False, create_only=create_only, list_missing=False,
        from_dt=from_dt, to_dt=to_dt, target_sale_channel_value="",
    )


def iter_import_channels() -> Iterator[dict]:
    """Yield kênh old=False, deleted_at NULL. skip=True + reason nếu thiếu branch_id."""
    # KiotVietChannel.objects.filter(old=False, deleted_at__isnull=True).order_by("id")
    ...


@shared_task(bind=True, name="apps.kiotviet.tasks.import_invoices_create_only",
             soft_time_limit=600, time_limit=660)
def import_invoices_create_only_task(self, from_dt=None, to_dt=None) -> dict:
    """Cron: kéo invoice của các kênh old=False, chỉ tạo Order mới. Window mặc định = hôm nay."""
    window_from, window_to = (from_dt, to_dt) if (from_dt and to_dt) else _today_window()
    # for channel in iter_import_channels(): lock → import → release; gom summary
```

- **Lock per-channel:** key `f"kv:import_create:lock:{channel_id}"`, `ttl = soft_time_limit`
  (lock tự hết hạn nếu worker chết, không kẹt vĩnh viễn). `cache.add` trả `False` → skip.
- **Cô lập lỗi:** 1 kênh raise → log + ghi vào `summary["errors"]`, **không chặn** kênh sau.
- **`_today_window()`:** `now = timezone.localtime(); start = now.replace(hour=0, minute=0, second=0, microsecond=0)`;
  format `"%Y-%m-%d %H:%M:%S"`.

### 3.3. `config/settings_parts/base.py`

- `CELERY_TASK_ROUTES`: thêm
  `"apps.kiotviet.tasks.import_invoices_create_only": {"queue": CELERY_KIOTVIET_QUEUE}`.
- `CELERY_BEAT_SCHEDULE`: thêm
  ```python
  "kv-import-create-only": {
      "task": "apps.kiotviet.tasks.import_invoices_create_only",
      "schedule": 15 * 60.0,
  },
  ```

## 4. Xử lý lỗi

- Mỗi kênh độc lập: lỗi resolve channel / lỗi `_handle_import` được bắt, log `logger.exception`,
  cộng vào `summary["errors"]`, tiếp tục kênh kế.
- Lock luôn `release` trong `finally`; TTL = `soft_time_limit` để tự giải phóng nếu task chết giữa chừng.
- `create_only=True` đảm bảo idempotent: chạy lại tick sau không tạo trùng (đơn đã có → `"skipped"`,
  xem [`_import_invoices_upsert_mixin.py:216`](../../../backend/apps/kiotviet/management/commands/_import_invoices_upsert_mixin.py)).

## 5. Kiểm thử (unit test, mock API)

| # | Test | Kỳ vọng |
|---|---|---|
| a | `_handle_import(create_only=True)` với mock `_kiotviet_request` trả 1 đơn mới + 1 đơn đã tồn tại | `created=1`, `skipped=1` |
| b | `_today_window()` | `from = 00:00:00 hôm nay (localtime)`, `to ≈ now`, đúng format |
| c | `import_invoices_create_only_task` khi lock đã bị giữ | kênh đó bị skip, không gọi `_import_invoices_for_target` |
| d | 1 kênh raise giữa chừng | kênh sau vẫn được xử lý; `summary["errors"]` ghi nhận kênh lỗi |

Mock `SyncInvoicesService`/`_kiotviet_request` để không gọi API thật; theo pattern test sẵn có
trong `apps/kiotviet/tests/`.

## 6. Rủi ro & lưu ý vận hành

- **Tải API:** 15'/lần × số kênh `old=False`, window hôm nay → nhẹ; phân trang dừng sớm khi
  `len(invoices) < page_size`. Lock tránh chồng tick.
- **Đơn về trễ qua nửa đêm:** window "hôm nay" có thể bỏ sót đơn purchaseDate hôm qua nhưng sync trễ;
  reconcile 21h (window cả tháng) sẽ vớt → chấp nhận được. Nếu sau này cần, thêm lookback nhỏ (env).
- Không đụng: webhook inbound, mirror new→old, Zalo noti.
