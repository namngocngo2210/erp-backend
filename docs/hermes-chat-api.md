# API hội thoại chat cho agent Hermes

Kênh **máy-tới-máy** để agent phân tích ngoài (Hermes) đọc hội thoại chat với
khách (Facebook inbox/bình luận, Zalo, TikTok Shop, Shopee, YouTube) từ CRM.
Nguồn là hai bảng mart `ai.hoi_thoai` / `ai.tin_nhan` — ETL 30 phút từ crawler
Pancake qua FDW, mô tả ở [runbook chat mart](runbooks/2026-09-04-ai-chat-mart-deploy.md)
và [brief schema `ai`](ai-schema-hermes-brief.md).

Vì sao mở HTTP thay vì cho agent trỏ thẳng DB: role DB chỉ-đọc dùng chung là một
cửa quá rộng (thấy cả schema `public` chưa che PII), không giới hạn được hình dạng
truy vấn, và thu hồi phải đụng tới Postgres. Cửa này chỉ đọc hai bảng mart và thu
hồi bằng cách đổi env.

## Contract

Cả hai endpoint dùng chung cửa xác thực với
[API transcript cuộc gọi](hermes-call-transcript-api.md):

```
X-Api-Token: <HERMES_API_TOKEN>          # hoặc Authorization: Bearer <token>
```

### 1. Danh sách hội thoại

```
GET /api/ai/hermes/conversations
```

| Param | Mặc định | Ý nghĩa |
|---|---|---|
| `from` | `now - 7 ngày` | Mốc dưới theo **thời điểm tin cuối** (`ket_thuc`). `YYYY-MM-DD` hoặc ISO datetime, giờ VN. |
| `to` | `now` | Mốc trên. Dạng ngày **nở tới cuối ngày**. |
| `platform` | — | Nhãn nền tảng, không phân biệt hoa thường: `Facebook`, `Zalo OA`, `Zalo cá nhân`, `TikTok Shop`, `Shopee`, `YouTube`. |
| `channel_type` | — | `Tin nhắn` (inbox), `Bình luận`, `Đánh giá`. Inbox Fanpage = `platform=Facebook&channel_type=Tin nhắn`. |
| `page` | — | Tên page/OA/gian hàng, khớp chính xác. |
| `exclude_bot` | `1` | Bỏ hội thoại do chatbot xử lý (`do_bot_xu_ly`). Lưu lượng bot gấp ~3,8 lần tin khách — để mặc định khi thống kê nhu cầu. `0` để lấy cả. |
| `only_replied` | `0` | `1` chỉ lấy hội thoại khách có nhắn ít nhất 1 tin (bỏ page nhắn một chiều). |
| `min_customer_messages` | `0` | Ngưỡng số tin khách gửi. Đo 04/09/2026: chỉ ~20% hội thoại có từ 2 lượt khách nói. |
| `include_content` | `0` | `1` để trả thêm `content` = toàn bộ hội thoại gộp thành một chuỗi (`Khách: ...\nPage: ...`). Nặng, chỉ xin khi cần đọc. |
| `cursor` | — | `next_cursor` của trang trước. Keyset theo `id`. |
| `limit` | `50` | Trần cứng **200**; xin lớn hơn bị kẹp về 200, không báo lỗi. |

Tham số ngày/số sai cú pháp → `400` kèm `detail` nói rõ trường nào.

```json
{
  "items": [
    {
      "id": 48213,
      "pancake_id": "6f1c...",
      "page": "Delax Fanpage",
      "platform": "Facebook",
      "channel_type": "Tin nhắn",
      "started_at": "2026-09-10T09:12:03+07:00",
      "ended_at": "2026-09-10T09:40:51+07:00",
      "message_count": 9,
      "customer_message_count": 4,
      "page_message_count": 5,
      "customer_replied": true,
      "bot_handled": false,
      "tags": ["Đã tư vấn"],
      "customer_name": "Chị Lan",
      "customer_phone_masked": "0912***678",
      "ai": {
        "summary": "Khách hỏi giá ghế massage, cân nhắc trả góp",
        "needs": "Hỏi giá; Trả góp",
        "reasons_not_buying": null,
        "products_of_interest": ["MSC06"],
        "stage": "Đang cân nhắc",
        "interest_level": "Cao",
        "label_status": "Đã gán nhãn"
      },
      "customer_id": 5123,
      "order_id": null,
      "order_code": null,
      "synced_at": "2026-09-15T04:59:05+07:00",
      "cursor": 48213
    }
  ],
  "limit": 50,
  "has_more": true,
  "next_cursor": 48213
}
```

### 2. Tin nhắn của một hội thoại

```
GET /api/ai/hermes/conversations/{id}/messages
```

| Param | Mặc định | Ý nghĩa |
|---|---|---|
| `cursor` | — | `next_cursor` của trang trước. Phải là id tin thuộc đúng hội thoại này, sai → `400`. |
| `limit` | `200` | Trần cứng **500**. |

Hội thoại không tồn tại → `404`. Tin trả theo **thứ tự gửi** (`sent_at`, rồi `id`),
không theo `id` trần — id nguồn không đảm bảo tăng theo thời gian vì crawler
backfill lịch sử.

```json
{
  "conversation_id": 48213,
  "items": [
    {
      "id": 901234,
      "sent_at": "2026-09-10T09:12:03+07:00",
      "direction": "Khách gửi",
      "type": "Văn bản",
      "content": "ghế này bao nhiêu",
      "has_attachment": false,
      "cursor": 901234
    }
  ],
  "limit": 200,
  "has_more": false,
  "next_cursor": null
}
```

Vòng kéo đúng cho cả hai endpoint: gọi lần đầu không `cursor`, sau đó lặp lại với
`cursor = next_cursor` **cho tới khi `has_more = false`**. `next_cursor` là `null`
ở trang cuối.

## Những quyết định cố ý

**Chỉ đọc mart `ai`, không đọc bảng nguồn.** Mọi cột đã che SĐT dạng `0912***678`,
không có email hay địa chỉ. Hai cột **ngoại lệ chưa che** là `content` (hội thoại
gộp) và `content` của từng tin: văn bản thô khách gõ, có thể chứa SĐT/địa chỉ do
khách tự nhắn. Đây là cùng mức lộ với transcript cuộc gọi — quy tắc "không trích
SĐT/địa chỉ từ nội dung thô ra câu trả lời" nằm ở system prompt của Hermes
(brief mục 8), API không lọc thêm.

**Không có `include_phone`.** Schema `ai` không giữ SĐT thô nên không có gì để mở.
Đối chiếu ngược khách qua `customer_id` / `order_id`.

**`exclude_bot=1` mặc định.** Khác với transcript cuộc gọi (mặc định trả hết),
ở chat phần lớn lưu lượng là chatbot; trả hết mặc định sẽ làm mọi tỉ lệ lệch.

**Lọc theo tin cuối, không theo tin đầu.** Câu hỏi thường gặp là "tuần này khách
nhắn gì", nên cửa sổ đo theo hoạt động gần nhất. Hội thoại rỗng (`ended_at` null)
không bao giờ xuất hiện — đúng ý, chúng không có nội dung để đọc.

**Token tĩnh, fail-closed, keyset** — lý do y hệt API transcript cuộc gọi, xem
tài liệu đó.

## Cạm bẫy đã biết

- **`order_id` null KHÔNG có nghĩa khách không mua** — chỉ là không nối được. Nguồn
  không có khoá nối chat↔đơn, chỉ dựa vào SĐT khách tự gõ; liên kết làm mới ở
  rebuild đêm.
- **Nhãn AI (`ai.*`) đang backfill dở và luồng gán nhãn ĐÃ TẮT từ 07/09/2026.**
  Kiểm `label_status` trước khi dùng: `Chờ gán nhãn` nghĩa là chưa chạy, không phải
  khách không nói gì. Không thống kê trên các trường `ai.*` — mẫu đã gán lệch nặng
  về hội thoại dài.
- **`customer_replied=false`** là page nhắn một chiều — không có dữ liệu nhu cầu.
- **`page_message_count` gộp cả nhân viên lẫn bot**, nguồn không tách được và không
  lưu ai trả lời (không có admin_id).
- **Dữ liệu trễ tới 30 phút** so với Pancake (`synced_at`); liên kết đơn trễ tới
  1 ngày.
- **Staging không có dữ liệu chat** (cố ý không cấp `CHAT_CRAWLER_DB_PASSWORD`) —
  endpoint ở đó trả rỗng, không phải lỗi.

## Cấu hình

| Env | Mặc định | Ghi chú |
|---|---|---|
| `HERMES_API_TOKEN` | rỗng | Bắt buộc trên production. Dùng chung với API transcript cuộc gọi. |
| `HERMES_THROTTLE_RATE` | `600/hour` | Trần theo IP client, đếm chung cả hai kênh Hermes. Kéo 7 ngày inbox Facebook (~1-2k hội thoại) tốn ~10 request. |

Không cần env mới — token và compose đã có sẵn từ API transcript.

## Test

`backend/apps/ai_mart/tests/test_hermes_chat_api.py` — 21 test phủ cửa xác thực
(thiếu/sai/JWT CRM/chưa cấu hình), từng bộ lọc, cửa sổ mặc định, nở cuối ngày,
`include_content`, 404/400, và vòng phân trang keyset của cả hai endpoint (kéo hết
đúng một lần, tin nhắn đúng thứ tự gửi khi id lộn xộn).
