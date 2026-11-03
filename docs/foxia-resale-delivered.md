# Cơ hội resale Foxia từ đơn giao thành công

Ngày: 2026-10-02 · Nhánh: `feat/foxia-resale-delivered`

## 1. Mục tiêu

Đơn nội bộ **vừa chuyển sang giao thành công** (từ 01/10/2026) được tự động đẩy
sang Foxia thành **cơ hội resale** để sale resale chăm lại khách ngay trong ngày.

## 2. Vì sao lấy đơn nội bộ, không lấy đơn mirror Foxia

Đơn mirror (`order_source='foxia'`) không có tên/SĐT/địa chỉ khách vì API
`sale.order` của Foxia không trả các trường này (xem `sentinel_service.py`), nên
không thể tạo cơ hội từ đó. Đơn nội bộ có đủ, và mốc "vừa giao thành công" đã được
ghi sẵn ở journey `order_status_changed → 3` (index `ord_journey_type_created_idx`).

## 3. Luật nghiệp vụ

| Luật | Chi tiết |
|---|---|
| Đơn nào | **Tạo từ ngày `FOXIA_RESALE_PUSH_SINCE`** (giờ VN) và **hiện** `status=3`; hoá đơn không huỷ (`invoice_status≠2`); chưa xoá mềm. Chốt lại 2026-10-06: trước đó lọc theo ngày *chuyển* sang giao thành công — đơn tạo trước SINCE nhưng giao sau vẫn lọt vào, đơn đổi trạng thái không qua journey bị sót |
| Mốc giao | Lần gần nhất đơn vào trạng thái 3 theo journey (giờ trong payload, ngày chống trùng SĐT); không có journey → `updated_at` của đơn |
| Kênh nào | Kênh có ô **cột Resale** trong *Cài đặt → Cấu hình liên kết Foxia*. Kênh không có ô (shopee, tiktok…) là ngoài phạm vi, không ghi nhật ký |
| Id nguồn Foxia | `foxia_source_id` của ô (kênh, resale), hiện là 115 cho mọi kênh facebook/google |
| Cửa sổ | `created_at ∈ [SINCE, now)`, **không giới hạn số ngày lùi**. Lọc theo kênh + ngày tạo, phủ bởi index `order_channel_created_idx`; khi dữ liệu dày, nâng SINCE để cắt |
| Chống trùng | Mỗi đơn tối đa 1 cơ hội (`order` unique). Mỗi SĐT tối đa 1 cơ hội mỗi ngày giao (`skipped_duplicate`) |
| Không retry | POST không idempotent nên dòng được ghi `pending` **trước** khi gọi; dòng `failed` hay kẹt `pending` không bao giờ đẩy lại |
| Chia sale resale | Bộ chia của trang **Chia đơn** (cột **Resale** của ma trận ngày × ca × nhân viên × trọng số, largest-remainder như sale). **Luôn chia**, không giữ sale chốt đơn gốc. Chỉ người có `User.foxia_id` dạng số được nhận (trang Chia đơn chặn lưu) |
| Ngoài ca / chưa cấu hình | Không ghi, không đẩy: cơ hội chờ lượt có ca và có cột Resale (không hết hạn). Lý do: POST không đẩy lại được, đẩy thiếu người nhận là mất |
| Payload | `name, phone, address, chance_id=2, created_at` = giờ giao thành công, `staff_id` = `foxia_id` của sale resale được chia, `message` (ô "Khách hàng ghi chú") = SKU x SL các dòng + giá sau giảm của đơn (`total_after_discount`), ví dụ "DT11 x1, MSC06 x2 - Giá sau giảm: 2.450.000đ" (chốt 2026-10-07) |

## 4. Thành phần

| Thành phần | Vị trí |
|---|---|
| Model nhật ký `FoxiaResalePush` (`foxia_resale_pushes`) | `apps/foxia/models.py`, migration `foxia.0008` |
| Service | `apps/foxia/application/services/resale_push_service.py` |
| Task + beat | `apps.foxia.tasks.push_resale_delivered`, queue `foxia`, crontab mỗi phút (`minute="*"`, đổi từ :11/:41 ngày 2026-10-06) |
| Cờ | sysconfig `FOXIA_RESALE_PUSH_ENABLED` (mặc định tắt, staging cấm) **và** `FOXIA_PUSH_ENABLED` |
| Mốc sàn | sysconfig `FOXIA_RESALE_PUSH_SINCE` (`YYYY-MM-DD`) — ngày **tạo** đơn |
| Chạy tay | `manage.py foxia_resale_push [--dry-run]` |
| Admin | Foxia resale pushes (chỉ đọc, có cột sale resale) |
| Thống kê | `GET /api/foxia/resale-stats?date=` (quyền `lead-assignment:view`): `state` (lý do chờ hiện tại), `waiting` (đơn đủ điều kiện chưa chia), đếm dòng ghi trong ngày theo trạng thái và theo sale resale. FE: khối "Cơ hội resale" trên trang Chia đơn, tự làm mới mỗi phút |
| Cột chia Resale | `LeadAssignmentShare.sheet = "resale"` (`orders.0150`, chỉ đổi choices), `sheet_rules.RESALE_SHEET`/`ASSIGNMENT_SHEETS`; API `/api/orders/lead-assignment/shares` thêm cột, `/staff` thêm `has_foxia_id`. Workload/chuyển đơn KHÔNG có cột này (không có đơn để chuyển) |

Hiệu năng: mỗi lượt chạy 3 câu SQL (ô resale, đơn ứng viên, journey của các đơn đó,
kèm `select_related customer` và `prefetch items`), tối đa 40 đơn, POST **tuần tự**.
Trước 07/10 là 150 đơn × 4 luồng song song: lượt 00:11 ngày 07/10 có 8/40 request
ReadTimeout theo đúng hai đợt 4 cái cùng lúc — Foxia nghẽn khi nhận song song. Job
chạy mỗi phút nên lô 40 tuần tự vẫn đủ thông lượng, và 40 × timeout 10s = 400s vẫn
nằm trong soft_time_limit 540s. Kết quả của từng dòng được ghi ngay khi request trả về.

## 5. Bật trên prod

```bash
# 1) Deploy (migration foxia.0008 nhẹ: chỉ tạo bảng mới). Sau đó restart beat để nạp lịch mới.
# 2) Xem trước, không ghi và không gọi Foxia:
docker compose -f docker-compose.prod.yml exec web python manage.py foxia_resale_push --dry-run
# 3) Trang Chia đơn: điền cột Resale cho các ca (copy ngày gần nhất mang theo cả cột này).
#    Nhân viên resale phải có Foxia ID (form user) mới nhập được.
# 4) Bật cờ FOXIA_RESALE_PUSH_ENABLED ở Cài đặt hệ thống. Job tự chạy mỗi phút,
#    trả "outside_shift"/"no_resale_config" khi chưa chia được.
```

Đối chiếu sau khi chạy:

```sql
SELECT delivered_on, status, count(*) FROM foxia_resale_pushes GROUP BY 1, 2 ORDER BY 1, 2;
```

## 6. Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| S1 | Model, migration, service, task, beat, cờ sysconfig, admin, lệnh tay, test | ✅ Xong (2026-10-02) |
| S1b | Chia sale resale: cột Resale ở trang Chia đơn, job gọi bộ chia + gửi `staff_id` = `foxia_id`, ngoài ca chờ; test BE/FE | ✅ Xong (2026-10-03) |
| S1c | Thống kê cơ hội resale trên trang Chia đơn (API + khối FE + test) | ✅ Xong (2026-10-03) |
| S1d | Đổi tiêu chí quét: đơn **tạo** từ SINCE đã giao thành công (thay cho ngày chuyển trạng thái), fallback mốc giao `updated_at` | ✅ Xong (2026-10-06) |
| S1e | Nhịp job 30 phút → 1 phút | ✅ Xong (2026-10-06) |
| S1f | Đẩy tuần tự, lô 40 (Foxia timeout khi nhận 4 request song song) | ✅ Xong (2026-10-07) |
| S2 | Deploy, cấu hình cột Resale, chạy dry-run trên prod, bật cờ, theo dõi lượt đầu (khoảng 100 đơn từ 01/10) | ⏳ Chờ deploy |
| S3 (đề xuất) | Đối chiếu cơ hội resale ↔ đơn Foxia (`cohoi_id` ↔ `foxia_order_links`) để đo tỷ lệ chuyển đổi resale, tương tự `match_lead_orders` | ⬜ Chưa làm |

**Chưa làm / giới hạn đã biết**
- Đơn chuyển sang giao thành công qua đường không phát `OrderStatusChanged` sẽ không
  có journey nên job không thấy. Đây cũng là giới hạn của chính journey.
- Không có nút "đẩy lại" cho dòng `failed`. Muốn đẩy lại thì xoá dòng qua shell,
  và phải làm có chủ đích vì có nguy cơ tạo cơ hội trùng bên Foxia.
- Chưa có metric Prometheus riêng; hiện theo dõi qua log `worker-foxia` và bảng nhật ký.
- Hiện chỉ 8/17 nhân viên phòng Sale có Foxia ID dạng số — phải khai đủ cho đội resale trước khi bật.
