# API transcript cuộc gọi cho agent Hermes

Kênh **máy-tới-máy** để agent phân tích ngoài (Hermes) kéo nội dung cuộc gọi
(transcript) từ CRM về tự chấm/phân tích. Nguồn dữ liệu là bảng
`callcenter_call_records` — pipeline sync + transcribe mô tả ở
[zenify-call-transcripts.md](zenify-call-transcripts.md).

## Contract

```
GET /api/callcenter/hermes/calls
X-Api-Token: <HERMES_API_TOKEN>          # hoặc Authorization: Bearer <token>
```

### Query params

| Param | Mặc định | Ý nghĩa |
|---|---|---|
| `from` | `now - 7 ngày` | Mốc dưới theo `start_time`. `YYYY-MM-DD` hoặc ISO datetime, giờ VN. |
| `to` | `now` | Mốc trên. Dạng ngày sẽ **nở tới cuối ngày** (23:59:59.999999). |
| `cursor` | — | `next_cursor` của trang trước. Keyset theo `id`. |
| `limit` | `50` | Trần cứng **200**; xin lớn hơn bị kẹp về 200, không báo lỗi. |
| `min_duration` | `0` | Lọc theo `acd_time` (giây). Ngưỡng chấm QA nội bộ đang dùng là `120`. |
| `only_transcribed` | `1` | Chỉ cuộc đã transcribe xong và transcript khác rỗng. `0` để lấy cả cuộc chưa có nội dung. |
| `include_phone` | `0` | `1` để lấy SĐT nguyên vẹn thay vì bản che. |

Tham số ngày/số sai cú pháp → `400` kèm `detail` nói rõ trường nào.

### Response

```json
{
  "items": [
    {
      "call_id": "HAPPYCALL-123456",
      "direction": "outbound",
      "agent_id": "6863",
      "agent_name": "",
      "customer_phone": "*******678",
      "start_time": "2026-08-26T09:12:03+07:00",
      "end_time": "2026-08-26T09:15:03+07:00",
      "duration_seconds": 180,
      "transcribe_status": 2,
      "transcript": "Nhân viên: alo ...",
      "order_id": 12345,
      "cursor": 98765
    }
  ],
  "limit": 50,
  "has_more": true,
  "next_cursor": 98765
}
```

Vòng kéo đúng: gọi lần đầu không `cursor`, sau đó lặp lại với
`cursor = next_cursor` **cho tới khi `has_more = false`**. `next_cursor` là
`null` ở trang cuối.

## Những quyết định cố ý

**Xác thực bằng token tĩnh, không phải JWT.** Bên gọi là agent chạy nền, không có
phiên đăng nhập để refresh, và quyền của nó không nên nằm chung hệ phân quyền
nhân sự. Thu hồi = đổi `HERMES_API_TOKEN` rồi restart, không phải đi sửa role.
Token của nhân viên CRM **không** mở được endpoint này.

**Fail-closed trên production.** Thiếu `HERMES_API_TOKEN` ⇒ endpoint trả `503`
chứ không bỏ qua bước xác thực — transcript là PII, mở nhầm nguy hiểm hơn là
chết dịch vụ. Ở môi trường dev không đặt token thì endpoint mở để tiện thử.

**Phân trang keyset, không dùng offset.** Agent kéo cả danh sách theo cụm; offset
sâu vừa chậm dần vừa nhảy sót/lặp bản ghi khi có cuộc gọi mới chèn vào giữa lúc
đang kéo.

**SĐT che mặc định.** Transcript đã là PII; không đẩy kèm cả danh bạ khách sang
hệ thống ngoài khi không cần. Agent đối chiếu ngược bằng `call_id` + `order_id`.
Cần SĐT thật thì phải gọi rõ `include_phone=1`.

**Không trả `recording_url`.** Link ghi âm là dữ liệu nhạy cảm nhất và cần auth
riêng của Zenify — chưa mở cho tới khi có yêu cầu cụ thể.

## Cạm bẫy đã biết

- **`agent_name` rỗng 100% trên production** và `agent_id` chỉ có ~4 giá trị dùng
  chung cho toàn bộ cuộc gọi (Zenify không trả định danh nhân viên thật). Đừng
  dựng báo cáo theo nhân sự từ hai trường này — đi qua `order_id` → `Order.staff`
  mới đúng người.
- **`call_id` chứa chuỗi `HAPPYCALL` không có nghĩa là happy call CSKH** — đó chỉ
  là tên hàng đợi outbound, vẫn là cuộc gọi bán hàng.
- **~39% cuộc gọi không có file ghi âm** nên không bao giờ có transcript. Với
  `only_transcribed=1` (mặc định) chúng không xuất hiện — đó là hành vi đúng.

## Cấu hình

| Env | Mặc định | Ghi chú |
|---|---|---|
| `HERMES_API_TOKEN` | rỗng | Bắt buộc trên production. |
| `HERMES_THROTTLE_RATE` | `600/hour` | Trần theo IP client. Mỗi trang tối đa 200 dòng nên kéo 30 ngày dữ liệu chỉ tốn ~13 request. |

Đã khai sẵn trong `docker-compose.local.yml`, `docker-compose.staging.yml`,
`docker-compose.prod.yml` và `.env.example`.

Sinh token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

## Test

`backend/apps/callcenter/tests/test_hermes_transcript_api.py` — 21 test phủ cửa
xác thực (thiếu/sai/JWT CRM/chưa cấu hình), bộ lọc, che SĐT và vòng phân trang
(kéo hết đúng một lần, không lặp không sót).
