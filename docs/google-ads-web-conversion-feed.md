# API feed chuyển đổi kênh Google Ads Web — Thiết kế

Ngày: 2026-09-28
Branch: `feat/google-ads-web-leads-api`

## 1. Mục tiêu

Mở một endpoint **máy-tới-máy, chỉ đọc** để hệ thống ngoài (upload offline
conversion Google Ads / Meta CAPI / TikTok Events, tool agency…) kéo các đơn
**đã chốt** của kênh Google Ads Web (`channels.id = 7`) theo định dạng phẳng,
đủ trường cho một "conversion event".

## 2. Quyết định đã chốt (2026-09-28)

| Vấn đề | Chốt |
|---|---|
| Kiểu lấy | **Pull** theo `created_at` của đơn (thời điểm CRM nhận lead). Không webhook. |
| Tập đơn | Kênh `CONVERSION_FEED_GOOGLE_ADS_WEB_CHANNEL_ID` (mặc định 7), chưa xoá, không phải đơn mirror, **trạng thái sale ∈ nhóm đã chốt**: `closed`, `closed_express`, `closed_scheduled_date`, `created_order` (= `CLOSED_SALES_STATUS_CODES`). |
| Trường tracking (ip, user_agent, gclid, gbraid, wbraid, fbclid, client_ip_address, fbp, madid, anon_id, ttclid, sc_id) | CRM chưa lưu → **trả chuỗi rỗng** (giữ key cố định trong contract để bên gọi không phải đổi khi bổ sung sau). |
| Auth | **Dùng chung token API ingest đơn** `ORDERS_INGEST_API_TOKEN` (chốt 2026-09-28 — cùng đối tác web đẩy lead vào và kéo conversion ra), header `X-Api-Token` hoặc `Authorization: Bearer`. Prod thiếu token → 503 (fail-closed). |
| Module | App mới `apps.conversion_feed`, tầng trên cùng cạnh `daily_report` trong `.importlinter`. Đọc bảng `orders` bằng SQL tham số hoá (không qua mart `ai.*` vì mart che SĐT và trễ 30 phút). |

## 3. Endpoint

```
GET /api/conversion-feed/google-ads-web
X-Api-Token: <ORDERS_INGEST_API_TOKEN>
```

| Param | Mặc định | Ý nghĩa |
|---|---|---|
| `from` | `now − 7 ngày` | Mốc dưới theo `created_at`. `YYYY-MM-DD` hoặc ISO datetime; không có múi giờ → hiểu là giờ VN. |
| `to` | `now` | Mốc trên. Dạng ngày trần **nở tới cuối ngày**. |
| `cursor` | — | `next_cursor` của trang trước (keyset theo `id` đơn). |
| `limit` | `200` | Trần cứng 1000, vượt trần bị kẹp, không lỗi. |

Khoảng `from..to` tối đa 92 ngày. Tham số sai → `400` kèm `detail`.

### Response

```json
{
  "items": [
    {
      "time": "2026-09-27T10:12:03+07:00",
      "first_name": "Văn An",
      "last_name": "Nguyễn",
      "phone": "0912345678",
      "province": "Hà Nội",
      "district": "Cầu Giấy",
      "sku": "MSC06;GOI01",
      "value": 12490000,
      "currency": "VND",
      "status": "Đã chốt",
      "status_code": "closed",
      "source": "googleads",
      "zip": "10000",
      "event_id": "482133",
      "order_id": "482133",
      "ip": "", "user_agent": "", "gclid": "", "gbraid": "", "wbraid": "",
      "fbclid": "", "client_ip_address": "", "fbp": "", "madid": "",
      "anon_id": "", "ttclid": "", "sc_id": ""
    }
  ],
  "next_cursor": 482133,
  "limit": 200,
  "meta": {"channel_id": 7, "from": "...", "to": "...", "status_codes": ["closed", "..."]}
}
```

### Quy tắc map

| Trường | Nguồn |
|---|---|
| `time` | `orders.created_at`, ISO giờ VN |
| `first_name` / `last_name` | Tách `orders.name` kiểu VN: từ đầu = họ (`last_name`), phần còn lại = tên (`first_name`). Một từ → chỉ `first_name`. |
| `phone` | `orders.phone_normalized`, hụt thì `orders.phone` |
| `province` | Tên tỉnh chuẩn hoá (`vietnam_regions.canonical_province`) từ `province` → `province_level2` → `kiotviet_location` |
| `district` | `orders.district` (đơn theo địa giới 2 cấp sau 07/2025 không còn huyện → rỗng) |
| `sku` | SKU các dòng `order_items` (theo `id`), nối bằng `;`; không có dòng → `orders.sku` |
| `value` | `orders.total_after_discount` (số nguyên VND) |
| `status` / `status_code` | Nhãn / mã `order_assessments.sales_status_code` |
| `source` | `order_source`: `landing`/rỗng → `googleads`, `hotline` → `hotline`, `zalo` → `zalo`, `messenger` → `facebook`, còn lại giữ nguyên mã |
| `zip` | Mã bưu chính 5 số **cấp tỉnh** (QĐ 2475/QĐ-BTTTT, 63 tỉnh cũ; tên tỉnh sau sáp nhập 2025 đều là tên tỉnh cũ nên vẫn tra được). Không nhận ra tỉnh → rỗng |
| `event_id` = `order_id` | `str(orders.id)` |

## 4. Kiến trúc

```
apps/conversion_feed/
  domain/lead.py          # pure: tách tên, map source, zip, nối sku, format tiền
  domain/params.py        # pure: parse from/to/cursor/limit, ParamError
  domain/postal_codes.py  # bảng tỉnh → mã bưu chính
  infrastructure/queries.py  # SQL keyset trên orders ⨝ order_assessments
  application/feed_service.py  # ghép query + domain → payload
  transport/auth.py       # token gate + throttle scope `conversion_feed`
  transport/views.py
```

- Không N+1: một câu SQL, SKU gom bằng subquery `string_agg` trên index `order_item_order_cr_idx`.
- Index mới `ord_channel_created_idx` = `orders(channel_id, created_at) WHERE deleted_at IS NULL`
  (`AddIndexConcurrently`, không khoá ghi) — các index hiện có đều chèn `status` giữa `channel` và
  `created_at` nên không quét range theo ngày được.
- Throttle scope riêng `conversion_feed` (`CONVERSION_FEED_THROTTLE_RATE`, mặc định `600/hour`).

## 5. Sprint

| Sprint | Nội dung | Trạng thái |
|---|---|---|
| S1 | App `conversion_feed`: domain + SQL + service + view + auth + throttle + sysconfig + index + unit test | ✅ Xong |
| S2 | Lưu tracking khi ingest landing: bảng `order_attribution` 1-1 (gclid, gbraid, wbraid, fbclid, fbp, ttclid, ip, user_agent…) + nối vào feed | ⏳ Chờ team web gửi các key này lên `/api/orders/ingest` |
| S3 | Zip cấp quận/huyện (bảng mã bưu cục VNPost) + SĐT dạng E.164 / SHA-256 nếu bên nhận yêu cầu | ⏳ Đề xuất |

## 6. Ghi chú tiến độ

### S1 — 2026-09-28
- Đã làm: toàn bộ mục 4; test domain/params/API (auth, lọc kênh/trạng thái/ngày, keyset, map trường).
- Đổi auth sang `ORDERS_INGEST_API_TOKEN` (không thêm token mới).
- Chưa làm: chạy migration index vào giờ thấp điểm trên prod.
