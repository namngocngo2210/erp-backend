# Hướng dẫn lấy credential Google Ads API (từ đầu)

> Đây là việc **ngoài code**, làm song song với Sprint 1. Mục tiêu: lấy đủ 5 giá trị để nhập vào
> form "Thêm TK Google Ads": `developer_token`, `client_id`, `client_secret`, `refresh_token`,
> `login_customer_id`. Khâu chậm nhất là **duyệt Basic access** (Google xét tay, vài ngày).

## Bước 0 — Chuẩn bị tài khoản MCC (Manager)
1. Vào https://ads.google.com/home/tools/manager-accounts/ → tạo **Manager Account (MCC)**.
2. Liên kết (link) tất cả tài khoản Google Ads đang chạy của công ty vào MCC này.
3. Ghi lại **Customer ID của MCC** (dạng `123-456-7890`) → khi nhập vào hệ thống bỏ dấu gạch:
   `1234567890`. Đây chính là **`login_customer_id`**.

## Bước 1 — Developer Token
1. Đăng nhập MCC → menu **Tools & Settings → Setup → API Center**.
2. Lấy **Developer token**. Mặc định ở mức **Test access** (chỉ gọi được tài khoản test).
3. Bấm **Apply for Basic access**, điền form mô tả use-case
   (vd: "Internal ERP pulling daily campaign cost for revenue/cost reporting").
4. ⏳ Chờ Google duyệt. **Chưa duyệt vẫn code + test với tài khoản test được**, nhưng không kéo
   được số liệu tài khoản thật → đây là blocker của Sprint 2.

## Bước 2 — Google Cloud Project + bật API
1. https://console.cloud.google.com → tạo project (vd `desi-order-ads`).
2. **APIs & Services → Library** → tìm **Google Ads API** → **Enable**.

## Bước 3 — OAuth Client (Web/Desktop) → client_id, client_secret
1. **APIs & Services → OAuth consent screen**: chọn **Internal** (nếu dùng Google Workspace công ty)
   hoặc External; thêm scope `https://www.googleapis.com/auth/adwords`.
2. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Desktop app** (đơn giản nhất để lấy refresh_token 1 lần).
   - Ghi lại **`client_id`** và **`client_secret`**.

## Bước 4 — Lấy refresh_token (chạy 1 lần)
Dùng script OAuth tạm trên máy (cần `pip install google-auth-oauthlib`):

```python
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": "<CLIENT_ID>",
            "client_secret": "<CLIENT_SECRET>",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    },
    scopes=["https://www.googleapis.com/auth/adwords"],
)
creds = flow.run_local_server(port=0)   # mở browser, đăng nhập tài khoản có quyền trên MCC
print("REFRESH TOKEN:", creds.refresh_token)
```

→ Lưu **`refresh_token`** in ra. Token này dùng vĩnh viễn (trừ khi bị thu hồi).

## Bước 5 — Nhập vào hệ thống
Vào trang **Google Ads** (admin) → **Thêm TK Google Ads** → điền:

| Field | Giá trị |
|---|---|
| Tên | Tên gợi nhớ (vd "MCC công ty") |
| Developer Token | từ Bước 1 |
| Client ID | từ Bước 3 |
| Client Secret | từ Bước 3 |
| Refresh Token | từ Bước 4 |
| Login Customer ID (MCC) | từ Bước 0 (10 chữ số, không gạch) |

→ Bấm **Đồng bộ TKQC** để hệ thống tự liệt kê các customer con, rồi map vào Channel.

## Checklist nhanh
- [ ] MCC tạo xong, đã link các tài khoản con
- [ ] Developer token — **đã apply Basic access** (chờ duyệt)
- [ ] Cloud project + Google Ads API enabled
- [ ] OAuth client → có client_id + client_secret
- [ ] Đã chạy script → có refresh_token
- [ ] Nhập đủ 5 field vào hệ thống + sync thành công
