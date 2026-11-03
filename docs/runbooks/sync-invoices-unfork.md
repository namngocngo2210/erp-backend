# Runbook: Unfork engine sync hóa đơn KiotViet (audit N4)

**Trạng thái (2026-09-23, audit C2):** ĐÃ GỠ LEGACY. CLI `sync_invoices_between_channels`
chỉ còn một engine `SyncInvoicesService`; module `_sync_invoices_legacy.py` (2 835 dòng),
cờ `--legacy` và `SyncCliEngineRoutingTests` đã xoá trong cùng MR. Lý do gỡ muộn hơn
kế hoạch "1 release" (20/08): monolith vẫn bị vá song song (`68380b98`, 19/09) — đúng
lớp lỗi unfork được lập ra để chặn. Phần "Chạy song song đối chiếu" bên dưới giữ lại
làm tư liệu lịch sử; lệnh `--legacy` trong đó KHÔNG còn chạy được.

## Kết quả điều tra lịch sử (vì sao từng revert)

| Mốc | Commit / MR | Chuyện gì xảy ra |
|---|---|---|
| 03/07 | `6087a921` + `15030e1c` | Hợp nhất lần 1: CLI chạy qua `SyncInvoicesCliService` → `SyncInvoicesService`. Kèm theo đổi hành vi CLI (orderBy=id pagination, skip cancelled mirror, ±1d price tolerance, single-PUT ship fee). |
| 06/07 | `bc2dd1e5`, MR !849 `revert/sync-invoices-cli-pre-golang` | **Revert CLI về snapshot 12/05** (bản "pre-golang"). Nguyên nhân vận hành: engine mới thiếu fallback field **PascalCase** khi resolve customer (web API trả `CustomerPhone`/`Customer.ContactNumber`/`CustomerName`… thay vì camelCase) và thiếu `create_missing=not dry_run`; cùng ngày phát hiện thêm bug mất giá ship khi patch status (`11148d1d`). Người vận hành chọn quay về bản đã tin cậy + port tay 3 fix nhỏ, thay vì debug stack mới. |
| 06/07→30/07 | — | Hệ quả revert: CLI mất luôn các fix 22/05–03/07 (mapping cancelled `==2` theo `e59650dc`, giữ discount theo dòng `a782572d`, PUT giá ship 1 lần `980e8602`…) → monolith mang **regression** `cancelled == 5`. |
| 30/07 | `c5723b3f` (trong MR !1095 audit-hardening) | Hợp nhất lần 2, làm đúng bài: 604 dòng characterization tests ghim hành vi CLI + 128 dòng test ghim 4 khác biệt CHỦ ĐÍCH (xem `test_sync_command_post_unification.py`). PascalCase fallback + delivery price đã có sẵn trong engine. |
| 30/07 | `26c1fa82`, MR !1099 | Audit-hardening bị **rollback nguyên khối** trong sự cố 30/07 — unification bị cuốn theo như tài sản thế chấp, KHÔNG phải vì engine sync lỗi. |
| Nay | MR này | Cherry-pick lại `c5723b3f` lên master hiện tại + thêm cờ `--legacy` giữ monolith 1 release. |

### 4 khác biệt hành vi monolith vs engine (đã chốt về phía engine, có test)

1. **Cancelled numeric = 2, không phải 5** — `==5` của monolith là regression do revert
   (5 = "Không giao được" và PHẢI được sync).
2. **`--update-delivery-detail` chỉ lệch giá ship → PUT giá 1 lần**, không
   delete+resync (delete+resync không sửa được giá, phá payments đơn B).
3. **`--check-price` chỉ so `total`** — các field discount của A/B cố tình lệch nhau
   sau `a782572d`, so `discountAfterTax` sinh lệch giả.
4. **`--purge-cancelled` bỏ qua mirror B đã hủy sẵn** — hủy lại trả 420, hết 53
   lỗi noise/đêm ở reconcile 21h.

Khác biệt được GIỮ NGUYÊN cho CLI: `exact_mirror=False` cứng (command lịch sử không
đọc `RECONCILE_DELIVERY_EXACT_MIRROR`).

## Chạy song song đối chiếu trên staging

Mọi mode read-only (`--check-missing`, `--check-price`, `--check-status`,
`--compare-invoice`) an toàn để chạy cả 2 engine trên cùng dữ liệu:

```bash
ARGS='--source-kiotviet-channel-id 1 --source-branch-id 231174 \
  --target-kiotviet-channel-id 2 --target-branch-id 415922 \
  --from "2026-08-01 00:00:00" --to "2026-08-07 23:59:59"'

docker compose -f docker-compose.staging.yml exec web \
  sh -c "python manage.py sync_invoices_between_channels $ARGS --check-missing" \
  > /tmp/new-check-missing.txt 2>&1

docker compose -f docker-compose.staging.yml exec web \
  sh -c "python manage.py sync_invoices_between_channels $ARGS --check-missing --legacy" \
  > /tmp/legacy-check-missing.txt 2>&1

diff /tmp/legacy-check-missing.txt /tmp/new-check-missing.txt
```

Lặp lại với `--check-price` và `--check-status`. Cách đọc diff:

- **Lệch mong đợi** (không phải bug): dòng lệch giá chỉ-`discountAfterTax` chỉ xuất
  hiện ở legacy (khác biệt #3); đơn status=5 bị legacy đếm là skipped (khác biệt #1);
  format log/màu có thể khác nhẹ.
- **Lệch phải điều tra**: khác nhau về TẬP invoice code (missing/mismatch), tổng
  `created/skipped/errors` ngoài các ca trên.

Mode ghi (`--dry-run` bắt buộc khi so): chạy `... --dry-run` cả 2 bên và diff payload
được log. KHÔNG chạy mode ghi thật 2 lần trên cùng khoảng thời gian (tạo đơn trùng).

## Kế hoạch gỡ legacy — ĐÃ XONG 2026-09-23

1. ~~Xác nhận reconcile 21h + CLI tay không có lệch "phải điều tra"~~ — 5 tuần chạy
   engine hợp nhất mặc định, không ghi nhận lệch.
2. ~~Xóa `_sync_invoices_legacy.py`, cờ `--legacy`, `SyncCliEngineRoutingTests`~~ — xong
   (nhánh `audit/arch`). Test wiring còn lại: `SyncCliEngineTests` khoá CLI → `run()`
   và khoá cờ `--legacy` phải lỗi.
3. Tách `run()` thành các bước prescan → paginate → compare → mutate/report — xong cùng
   nhánh (xem `SyncInvoicesService` docstring).
