# Quyết định: KHÔNG squash thêm migration app `orders` — và vì sao

- Ngày: 2026-07-26
- Kết luận: **đóng backlog "squash 108 migration của `orders`"**. Không có việc gì để làm,
  không có gì để deploy.
- Bối cảnh: mục T4 của `docs/audit-2026-07-23-full-stack.md`, nhắc lại ở T6 của
  `docs/audit-2026-07-26-full-stack.md`.

Tài liệu này giữ lại vì nó bác bỏ một tiền đề đã đi qua hai đợt audit. Không có nó thì
đợt audit sau sẽ lại đề xuất đúng việc này lần thứ ba.

---

## 1. Tiền đề của audit đã sai: `orders` được squash từ 2026-07-03

Commit `7361843d` (đã merge `master`) tạo
`backend/apps/orders/migrations/0001_squashed_0089_order_is_export_vat_vat_link.py`,
gộp 0001..0089 thành **một** node.

Con số "108 migration phải replay mỗi lần dựng môi trường mới" đếm **số file**, không
phải số node thực sự được replay. Đo thật bằng `manage.py migrate` trên DB rỗng:

```
orders: 20 node   (= 0001_squashed_0089 + 0090..0108)
toàn hệ thống: 255 node
```

20, không phải 108.

## 2. Dư địa còn lại không mang lợi ích đo được

Đã thử squash thật `0090→0098` (9 file → 1 node) và đo A/B xen kẽ 4 lượt:

| | Thời gian `migrate` trên DB rỗng |
|---|---|
| Trước squash | **47,0 s** |
| Sau squash | **48,0 s** |

Chênh lệch nằm trong sai số và **dấu không nhất quán giữa các lượt**. Lý do:
`squashmigrations` báo **"No optimizations possible"** cho mọi dải đã thử — DDL sinh ra
y hệt, chỉ bớt overhead mỗi node. Phương án gộp tối đa (20 → 3 node) đo được ~3,5 s (6 %),
vẫn dưới mức đáng đánh đổi.

Đặt cạnh nhau cho đúng tỉ lệ: `migrate` mất ~47 s, còn `pytest apps/orders` mất **11–15
phút**. **Migration không phải nút thắt của CI.**

## 3. Hai rào chắn kỹ thuật thật, nếu về sau ai muốn thử lại

**a. `0090→0108` bất khả thi — phụ thuộc vòng giữa hai app:**

```
orders.0099 → customers.0028 → customers.0027 → orders.0092
```

`CircularDependencyError`. Đây **không** phải vấn đề `RunPython` không `elidable` như
audit dự đoán — nó là phụ thuộc chéo app, không gỡ được bằng cách sửa migration.

**b. `0099→0108` squash được về mặt kỹ thuật nhưng phá một test đáng giữ:**
`apps/orders/tests/test_return_slip_migration.py` ghim node 0100/0101 để chạy data
migration thật trên dữ liệu hình dạng prod — nó bảo vệ một phép đổi kiểu cột. Đổi test
đó lấy 9 node không có lợi ích đo được là một giao dịch lỗ.

**c. Bẫy nếu vẫn làm:** `squashmigrations` **không** mang `atomic` từ migration gốc sang
file squash. `0091/0092/0095` dùng `CREATE INDEX CONCURRENTLY`; thiếu `atomic = False`
thì `migrate` từ số 0 **fail**. Phải thêm tay.

## 4. Bằng chứng thu được (dùng lại nếu về sau cần squash app khác)

Trong lúc thử nghiệm đã kiểm chứng được rằng squash là **an toàn** với môi trường đã
chạy — chỉ là không đáng làm:

- `pg_dump --schema-only` của hai DB dựng từ số 0 (một bằng migration lẻ, một bằng file
  squash) **giống nhau từng byte** — 9355 dòng, chỉ khác token `\restrict` ngẫu nhiên.
- Trên DB đã migrate **trước khi** file squash tồn tại (đúng trạng thái prod), chạy lại
  `migrate` cho `No migrations to apply` → cơ chế `replaces` hoạt động, deploy không cần
  thao tác gì.
- `makemigrations --check --dry-run` → `No changes detected`.

Đây là khuôn kiểm chứng nên dùng cho bất kỳ lần squash nào về sau: dump schema hai chiều
rồi so byte, chứ không chỉ dựa vào test xanh.

## 5. Việc cho người vận hành

**Không có.** Không file nào được thêm, không migration nào được xoá, không cần thao tác
khi deploy.

Nếu về sau muốn **xoá** các file migration cũ đã được `0001_squashed_0089` thay thế
(việc dọn dẹp thuần, cũng không mang lợi ích hiệu năng), điều kiện là **mọi** môi trường
đã qua mốc squash — kiểm bằng:

```bash
python manage.py showmigrations orders | head -5
```

Trên **từng** môi trường (prod, staging, máy mỗi dev): dòng `0001_squashed_0089...` phải
có `[X]`. Chỉ khi tất cả đều thoả mới được xoá file cũ.
