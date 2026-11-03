# Runbook — tích hợp Foxia

Thiết kế: [foxia-integration-design.md](../foxia-integration-design.md)

## 0. Tình trạng

| Phần | Trạng thái |
|------|-----------|
| Client JSON-RPC, danh mục, `foxia_probe` | Đã code, test xanh |
| Đơn + dòng + vận đơn + enrich nguồn | Đã code, test xanh |
| Hai cột trạng thái thô, `shipping_fee` | Đã code, test xanh |
| Hàng rào cô lập đơn mirror | Đã code, test xanh |
| Báo cáo CPO/CPS/ROAS + trang FE | Đã code, test xanh |
| Backfill, metrics, runbook | Đã code |
| Lưu cơ hội đẩy đi + đối chiếu ra đơn | Đã code, test xanh |
| **Chạy với dữ liệu Foxia thật** | **Chưa** — chờ bộ kết nối từ đối tác |
| Ánh xạ trạng thái sang `OrderStatus` | Chưa làm (cố ý — xem §5) |
| Đối soát kế toán, tính lương | Ngoài phạm vi |

Toàn bộ test hiện chạy trên client giả dựng theo đúng ví dụ trong tài liệu
Foxia. Chưa có lần gọi thật nào — bước đầu tiên khi có khoá là §1 bên dưới.

## 1. Bật kết nối lần đầu

Bốn mảnh Foxia cấp: endpoint, tên database, username, API key. **Không** đưa vào
`.env` — chúng là secret của đối tác và sống trong bảng `foxia_connections` với
`api_key` mã hoá.

```
1. Django admin → Foxia connections → Add
   endpoint_url  https://crm.<đối-tác>.vn/jsonrpc
   database      foxia_prod          ← KHÔNG suy ra từ API key, phải gõ đúng
   username      integration.marketing
   api_key       <khoá Foxia cấp>
   is_active     ✔

2. Dò kết nối (chạy được ngay cả khi FOXIA_SYNC_ENABLED=0):
   docker compose exec web python manage.py foxia_probe

   Kỳ vọng: "Xác thực OK — uid=<số>" rồi bảng số bản ghi từng tài nguyên.

3. Kéo danh mục TRƯỚC dữ liệu giao dịch:
   docker compose exec web python manage.py shell -c \
     "from apps.foxia.tasks import sync_catalog; print(sync_catalog())"

4. Bật cron:  FOXIA_SYNC_ENABLED=1  trong .env  →  docker compose up -d
```

## 2. Lệnh hay dùng

| Việc | Lệnh |
|------|------|
| Dò kết nối + đếm bản ghi | `manage.py foxia_probe` |
| Xem mã trạng thái Foxia đang có | `manage.py foxia_probe --fields donvanchuyen` |
| Backfill lịch sử | `manage.py foxia_backfill --from 2026-01-01 --to 2026-06-30` |
| Backfill đơn giao xong | `manage.py foxia_backfill --from … --to … --mode delivered` |
| Xem nhật ký đồng bộ | `GET /api/foxia/sync-status` hoặc admin → Foxia sync runs |

`foxia_probe --fields <resource>` in cả danh sách giá trị selection — đây chính
là thứ cần để dựng bảng ánh xạ trạng thái ở giai đoạn sau.

## 2b. Cơ hội đã đẩy đi (`foxia_lead_links`)

Phản hồi của lần POST đẩy đơn mang `data.cohoi_id` / `data.khachhang_id` — hai
id đó được ghi vào bảng `foxia_lead_links`, mỗi cơ hội một dòng, khoá chống
trùng là `cohoi_id` unique. Cột `order_id` trỏ về đơn landing của ta.

`foxia_order_id` để trống lúc tạo và được task `apps.foxia.tasks.match_lead_orders`
(queue `foxia`, nhịp 15 phút) điền khi cơ hội đã lên đơn. Job **thuần local**:
nó chỉ join `cohoi_id` sang `foxia_order_links` mà `sync_orders` đã kéo về,
không gọi Foxia. Chạy lại bao nhiêu lần cũng an toàn — dòng đã khớp không bị
đụng tới.

```sql
-- Cơ hội còn treo, cũ nhất trước
SELECT cohoi_id, order_id, created_at FROM foxia_lead_links
WHERE foxia_order_id IS NULL ORDER BY created_at LIMIT 20;
```

Treo lâu bất thường thì gần như luôn là **cửa sổ đồng bộ**, không phải job hỏng:
cron chỉ nhìn 3 ngày theo `ngayxacnhan`, cơ hội chốt muộn hơn thế không có
`foxia_order_links` để khớp. Cách xử lý là kéo lại lịch sử rồi để job tự chạy:

```
manage.py foxia_backfill --from <ngày> --to <ngày>
```

Bảng này **chỉ đọc** trong admin (Foxia lead links). Sửa tay `foxia_order_id` là
dựng số giả cho báo cáo đối soát sau này.

## 2c. Cơ hội resale từ đơn giao thành công (`foxia_resale_pushes`)

Job `apps.foxia.tasks.push_resale_delivered` (queue `foxia`, mỗi phút) đẩy đơn nội
bộ vừa chuyển sang giao thành công sang cột **Resale** của bảng liên kết. Cần bật CẢ
`FOXIA_PUSH_ENABLED` lẫn `FOXIA_RESALE_PUSH_ENABLED`. Thiết kế + luật:
[foxia-resale-delivered.md](../foxia-resale-delivered.md).

```
manage.py foxia_resale_push --dry-run   # xem trước ứng viên, không ghi/không gọi
```

Dòng `failed`/`pending` **không** được đẩy lại tự động (POST không idempotent).
Sale resale do cột **Resale** của trang Chia đơn chia; task trả `outside_shift` /
`no_resale_config` nghĩa là đang chờ ca/cấu hình, không phải lỗi.

## 3. Cảnh báo và cách xử lý

### `auth_status = revoked`

API key bị thu hồi hoặc tài khoản tích hợp ngừng hoạt động. **Không phải lỗi tạm
thời, không retry** — mọi job dừng cho tới khi có khoá mới.

```
1. Liên hệ Foxia xin khoá mới.
2. Admin → Foxia connections → cập nhật api_key.
3. manage.py foxia_probe   ← phải OK trước khi tin cron chạy lại.
```

### Không có lượt sync thành công nào trong 2 giờ

```
docker compose logs worker-foxia --since 2h | grep -i foxia
docker compose exec web python manage.py foxia_probe
```

Thường là một trong ba: khoá hỏng (xem trên), Foxia không phản hồi, hoặc worker
`worker-foxia` chết. Từ 2026-09-09, backfill (`foxia`) và đẩy đơn landing
(`foxia_push`) không còn nằm ở `worker-slow` nữa — cả hai queue chuyển sang
container RIÊNG `worker-foxia` (xem `docker-compose.prod.yml` và
`docs/runbooks/2026-09-09-add-foxia-worker.md`). Nhớ worker đó phải nghe **cả
hai** queue: `-Q foxia,foxia_push`.

### `FoxiaSyncRun` ghi "Trạng thái chưa có trong danh mục: …"

Không phải lỗi. Foxia có mã trạng thái mới mà danh mục `giaidoandonhang` chưa
kéo về. Chạy lại `sync_catalog`; nếu vẫn còn thì đó là giá trị Foxia không khai
trong danh mục — vẫn được lưu nguyên văn ở `orders.partner_delivery_status`.

### Đơn cũ sửa bên Foxia mà bên ta không thấy

Foxia **không cấp `write_date`** nên cron chỉ nhìn được cửa sổ vài ngày gần nhất
(3 ngày theo `ngayxacnhan`, 14 ngày theo `ngayhoanthanh`). Đơn sửa ngoài cửa sổ
phải vá tay:

```
manage.py foxia_backfill --from <ngày> --to <ngày>
```

An toàn chạy lại bao nhiêu lần cũng được — upsert idempotent theo `foxia_id`.

## 4. Kiểm tra hàng rào cô lập

Đơn Foxia nằm chung bảng `orders`. Nếu nghi ngờ chúng lọt vào số liệu nội bộ:

```sql
-- Phải bằng 0 ở MỌI báo cáo nội bộ
SELECT count(*) FROM orders WHERE order_source = 'foxia';   -- tổng đơn mirror

-- Đối chiếu: doanh thu nội bộ không được gồm đơn mirror
SELECT order_source, count(*), sum(total_after_discount)
FROM orders WHERE deleted_at IS NULL GROUP BY order_source;
```

Trong code, `Order.objects` đã loại đơn mirror sẵn; chỉ `Order.all_objects` mới
thấy chúng. Bộ test `apps/foxia/tests/test_foxia_order_isolation.py` phải xanh
trước mọi lần deploy đụng tới báo cáo.

## 5. Giới hạn đã biết

- **Tiền chưa qua đối soát.** `amount_collected` là tiền đã thu tại thời điểm
  đồng bộ, chưa trừ khấu trừ/tiền hoàn/phí ship thực tế. Đối soát kế toán
  (`ngaydoisoat`) ngoài phạm vi hiện tại.
- **Trạng thái chưa ánh xạ.** `Order.status` của đơn Foxia luôn là NEW và không
  mang ý nghĩa; trạng thái thật nằm ở hai cột `partner_*_delivery_status` dạng
  thô. Báo cáo lọc "giao thành công" bằng mốc `ngayhoanthanh`, không bằng mã.
- **Phí ship là phép cộng chưa kiểm chứng.** `shipping_fee` = tổng phí mọi vận
  đơn. Nếu Foxia cộng dồn phí giữa các vận đơn thì con số này tính trùng — phải
  đối chiếu tay vài đơn nhiều vận đơn trước khi dùng để ra quyết định.
- **Không có cơ chế báo xoá.** Đơn bị xoá bên Foxia vẫn nằm lại bên ta.
