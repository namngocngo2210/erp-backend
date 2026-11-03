# Purge duplicate synced invoices (`--purge-duplicates`)

Ngày: 2026-05-27
Command: `apps/kiotviet/management/commands/sync_invoices_between_channels.py`

## Bối cảnh

Khi sync hóa đơn từ kênh A sang B, mỗi đơn B mang description
`"SYNC từ kênh {branchId} - {source_code}"`. Do retry/chạy lại, một số `source_code`
bị tạo trùng nhiều đơn ở B (vd `A:HD008471 -> B:HD059121, B:HD059104`).

`--check-missing` đã **phát hiện** và in nhóm duplicate này, nhưng chưa có cách **xử lý**.
Mục tiêu: thêm mode `--purge-duplicates` để xóa các đơn trùng, giữ lại đúng 1 đơn / source.

## Phạm vi

- Thêm 1 mode mới vào command hiện có. Không tạo command riêng.
- Không refactor `_check_missing` (giữ nguyên output đang chạy ổn).
- Không đụng tới các mode khác.

## Hành vi

### Phát hiện duplicate (độc lập với A)

Quét toàn bộ đơn B trong `[from, to]` qua `_web_list_invoices` (phân trang, bỏ đơn đã
hủy: `Status == 5` hoặc `StatusValue ∈ {đã hủy, đã huỷ, cancelled}`). Với mỗi đơn có
description khớp `SYNC từ kênh \S+ - (\S+)`, gom theo `source_code`:

```
src_code -> [ {id, code, created_date}, ... ]
```

`source_code` nào có ≥2 entry là một nhóm duplicate. **Không cần** quét A — duplicate
được định nghĩa thuần trên B.

### Quy tắc giữ/xóa

Trong mỗi nhóm: sort theo `(created_date tăng dần, code tăng dần)`, **giữ phần tử đầu
(đơn cũ nhất)**, các phần tử còn lại đưa vào danh sách xóa.

Quyết định này tách thành staticmethod thuần để unit test:

```python
@staticmethod
def _select_duplicates_to_delete(
    entries_by_src: dict[str, list[dict]]
) -> tuple[list[dict], list[dict]]:
    """Trả về (to_keep, to_delete). Mỗi dict: {src, id, code, created_date}.
    Giữ đơn cũ nhất theo (created_date, code); chỉ nhóm có >1 entry mới sinh to_delete."""
```

- Entry thiếu `created_date` → coi như rất cũ (sort lên đầu) hoặc rất mới? → dùng chuỗi
  rỗng sort lên đầu nghĩa là "cũ nhất". Để an toàn, entry thiếu created_date sort
  **xuống cuối** (ưu tiên giữ đơn có timestamp rõ ràng). Tie-break luôn theo `code`.

### Thực thi

Method `_purge_duplicates(...)` theo đúng pattern `_purge_cancelled`:

1. Quét B, build `entries_by_src`.
2. `to_keep, to_delete = _select_duplicates_to_delete(entries_by_src)`.
3. In kế hoạch: mỗi nhóm `A:src -> KEEP B:{code} | DELETE B:{code}, ...`.
4. Nếu `to_delete` rỗng → in "không có duplicate" và return.
5. `--dry-run` → dừng, không xóa.
6. Xóa thật yêu cầu `--yes` (giống `--purge-target`/`--purge-cancelled`); nếu thiếu →
   `CommandError`. Gọi `delete_invoice_by_id(invoice_id, order=None, channel=target_channel)`,
   `sleep_between` giữa các lần xóa.
7. Summary: `groups, kept, deleted, errors`.

### Wiring

- `add_arguments`: thêm `--purge-duplicates` (action store_true).
- `handle`: nhánh dispatch early-return (đặt cạnh `--purge-cancelled`).

## Cách dùng

```bash
# xem trước (không xóa)
python manage.py sync_invoices_between_channels \
  --source-kiotviet-channel-id 1 --source-branch-id 231174 \
  --target-kiotviet-channel-id 2 --target-branch-id 415922 \
  --from "2026-05-01 00:00:00" --to "2026-05-27 23:59:59" \
  --purge-duplicates --dry-run

# xóa thật
... --purge-duplicates --yes
```

## Test

- Unit test `_select_duplicates_to_delete` (SimpleTestCase, không cần network/DB):
  - nhóm 1 phần tử → không xóa gì.
  - nhóm nhiều phần tử → giữ created_date nhỏ nhất, xóa phần còn lại.
  - tie created_date → giữ code nhỏ nhất.
  - entry thiếu created_date → bị đẩy xuống cuối (không được giữ nếu có entry khác có
    timestamp).

## Rủi ro

- Xóa là không hồi phục → mặc định an toàn: phải có `--yes`, có `--dry-run` để soát trước.
- Web list không khớp delete API về id → đã xác nhận `_web_list_invoices` trả `Id` thật
  (bỏ summary row `Id == -1`), `delete_invoice_by_id` nhận đúng id đó.
