# Cặp kho đồng bộ KiotViet new→old (`old_sync_branch_map`)

**Ngày:** 2026-07-15 · **Branch:** `feat/kiotviet-branch-sync-map`

## Bối cảnh

Kênh KiotViet mới (đã ghép `paired_old_channel`) trước đây chỉ có **1** field
`old_sync_branch_id`: mọi hóa đơn — bất kể thuộc kho nào trong `branch_ids` — đều đồng bộ
sang đúng 1 kho trên Kiot cũ. Nhu cầu mới: 1 kiot có nhiều kho, cần đồng bộ **từng cặp kho**
(kho kênh mới → kho Kiot cũ).

## Thiết kế

### Data model

- Thêm `KiotVietChannel.old_sync_branch_map` (JSONField, default `[]`):
  `[{"source_branch_id": "...", "target_branch_id": "..."}]`.
- `old_sync_branch_id` GIỮ LẠI làm **fallback** cho kho nguồn không có trong map
  (giữ hành vi cũ, không breaking).
- Migration `kiotviet.0016`: thêm field + **data seed** — kênh mới đã ghép Kiot cũ và có
  `old_sync_branch_id` được seed 1 cặp `(branch_id → old_sync_branch_id)` để UI hiển thị
  đúng cấu hình hiện hữu.

### Thứ tự resolve kho đích (helper `branch_sync_map.resolve_old_sync_target_branch_id`)

1. `old_sync_branch_map[source_branch_id]` (map trên kênh mới).
2. `old_sync_branch_id` trên kênh mới (legacy fallback).
3. `branch_id` của kênh Kiot cũ đã ghép.

### Các luồng bị ảnh hưởng

| Luồng | Trước | Sau |
|---|---|---|
| Push online new→old (`_ensure_old_channel_invoice`) | 1 target cứng | resolve theo `order.branch_id` qua chuỗi fallback trên |
| Reconcile hàng giờ (`iter_reconcile_pairs`) | 1 cặp/kênh (`branch_id` → target) | map có cặp nào → 1 item reconcile per cặp; map rỗng → legacy 1 cặp |
| Lock chống chạy chồng (`reconcile_one_pair_task`) | key theo `{src_kv}:{dst_kv}` | key thêm branch: các cặp kho khác nhau chạy song song, chỉ chặn chồng cùng cặp kho |

Ba mode reconcile (`purge_cancelled`, `purge_duplicates`, `reconcile_sync`) đều khớp theo
mã hóa đơn nguồn trong description (`SYNC từ kênh ...`) nên nhiều cặp kho — kể cả nhiều kho
nguồn về cùng 1 kho đích — không phá lẫn nhau.

### API

- `GET /api/channels/kiotviet` trả thêm `old_sync_branch_map`.
- Create/Update nhận `old_sync_branch_map`; validation:
  - `source_branch_id` phải thuộc `branch_ids` của kênh, không trùng nhau.
  - `target_branch_id` phải thuộc branch của kênh Kiot cũ đã ghép.
  - Kênh ghép Kiot cũ phải có ≥1 cặp **hoặc** `old_sync_branch_id` (tương thích client cũ).
  - Khi có map mà `old_sync_branch_id` rỗng → BE tự dẫn xuất fallback = target của kho
    chính (`branch_id`), không có thì target cặp đầu.
  - `old=True` hoặc bỏ ghép kênh cũ → map bị clear.

### Frontend (trang Kênh KiotViet)

- Bỏ select đơn "Mã kho đồng bộ sang Kiot cũ"; thay bằng editor **danh sách cặp kho**:
  mỗi dòng = select kho kênh mới (từ danh sách Mã kho đã nhập) → select kho Kiot cũ,
  nút thêm/xóa dòng.
- Edit kênh cũ chưa có map: seed hiển thị từ cặp legacy.
- FE gửi `old_sync_branch_id: ""` để BE tự dẫn xuất fallback từ map.

## Đã làm / chưa làm

- [x] Model + migration 0016 (schema + seed)
- [x] Helper resolve + wire vào push new→old
- [x] `iter_reconcile_pairs` multi-cặp + lock key per cặp kho
- [x] Serializers create/update + repository + tests (28 test mới)
- [x] FE pairs editor + i18n + types
- [x] Regen `backend/schema.yaml` + `frontend/src/api/schema.d.ts`
- [ ] Chạy migration 0016 trên prod (deploy note — migration tự chạy theo pipeline hiện tại,
      seed là idempotent)
- [ ] QA thị giác dialog kênh KiotViet (thêm/xóa cặp, đổi kênh cũ)

## Deploy notes

- Migration 0016 an toàn chạy nhiều lần (seed bỏ qua kênh đã có map).
- Sau deploy, kênh nào cần đồng bộ nhiều kho: vào trang Kênh KiotViet → sửa kênh → thêm cặp kho.
- Kho nguồn chưa có trong map vẫn đồng bộ về kho fallback như trước — không đổi hành vi
  cho tới khi user thêm cặp.
