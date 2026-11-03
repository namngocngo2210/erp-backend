# Báo cáo Ads: sửa phạm vi TKQC cho NV ads + cột CTR

Ngày: 2026-08-10
Nhánh: `fix/ads-report-scope-duplicate-accounts`

## 1. Triệu chứng

Tab **Chiến dịch** của Báo cáo Ads: tài khoản admin thấy đầy đủ dòng, tài khoản nhân
viên ads (`loiads`) thấy bảng trắng — không lỗi, không cảnh báo, chỉ có dòng "Chưa có
dữ liệu chiến dịch trong khoảng đã chọn".

Kênh `facebook-loi` (id 6) đã map 10 TKQC Facebook và đã gán user `loiads`
(`UserChannel.is_active = true`). Nghĩa là dữ liệu cấu hình **không thiếu**.

## 2. Nguyên nhân gốc

### 2.1. Đường lọc hiện tại

```
user → UserChannel → Channel → ChannelAdAccount.account_id (PK) → lọc chi tiêu theo PK
```

- `OrderRepository.accessible_ads_ad_account_ids(user)` trả `None` cho user có scope
  `ads-report = ALL` (admin) → **không lọc gì cả**.
- Với user bị giới hạn, hàm trả **list PK** của `FacebookAdAccount`.
- `aggregate_campaign_cost` lọc `FacebookAdDailySpend.ad.ad_account_id IN (list PK)`.

### 2.2. TKQC bị nhân bản theo app

`FacebookAdAccount` có unique constraint `(app, account_id)` — **không** phải riêng
`account_id`. Đồng bộ danh mục chạy:

```python
FacebookAdAccount.objects.update_or_create(app=app, account_id=account_id, defaults=...)
```

Prod có 4 `FacebookApp`, cả 4 đều được cấp quyền vào cùng Business Manager nên cả 4
đều thấy đúng danh sách TKQC đó. Kết quả: **mỗi TKQC thật = 4 dòng DB, 4 PK khác nhau**.

Bản thân việc này không sai — mô hình cố ý cho phép một TKQC được nhiều app quản lý.
Cái sai là **tầng báo cáo lọc theo PK**, tức coi 4 danh tính đó là 4 tài khoản khác nhau.

### 2.3. Bằng chứng trên prod (2026-08-10)

Kênh 6 map PK thuộc **app 1**; toàn bộ chi tiêu ở grain ad nằm dưới PK thuộc **app 4**:

| TKQC | account_id (Facebook) | PK kênh 6 map | app | PK có chi tiêu | app | Chi tiêu 7 ngày |
|---|---|---|---|---|---|---|
| LỢI_TK7_HKD | 25322428374101900 | 21 | 1 | 172 | 4 | 17.254.277 |
| Lợi_TK6_HKD | 2007931173486188  | 2  | 1 | 138 | 4 | 17.133.241 |
| Lợi_TK1_HKD | 2090916511406591  | 6  | 1 | 147 | 4 | 22.383.787 |

Cả 20 dòng chi tiêu lớn nhất 7 ngày đều thuộc `app_id = 4`. Vậy list PK mà `loiads`
được cấp toàn là "vỏ rỗng": đúng tên, đúng id Facebook, nhưng không có ad nào treo dưới.
`WHERE ad_account_id IN (21, 2, 6, ...)` khớp 0 dòng → bảng trắng.

Admin không bị lọc nên không lộ ra vấn đề. Bug **âm thầm và chỉ ảnh hưởng NV ads**.

### 2.4. Phạm vi ảnh hưởng rộng hơn tab Chiến dịch

Luật phân quyền dùng chung cho cả **Tổng quan → Theo page**
(`page_report_view.py`). NV ads cũng đang mất số ở đó. Bản Google
(`accessible_google_ads_account_ids`) có cùng cấu trúc lỗi: `GoogleAdsAccount` unique
theo `(config, customer_id)` nên cũng nhân bản được theo config.

### 2.5. Cái bẫy sinh ra bug

Dropdown chọn TKQC ở màn Quản lý kênh liệt kê theo dòng DB → mỗi TKQC hiện **4 lần**,
cùng tên, cùng id trong ngoặc, không phân biệt được. Người gán kênh chọn trúng bản của
app 1 hoàn toàn ngẫu nhiên.

## 3. Thiết kế

### 3.1. Lọc theo TKQC vật lý thay vì PK (fix chính)

Sửa **tận gốc** trong `OrderRepository`, không bắt từng view tự opt-in. Luật phân quyền
nhận list PK từ mapping kênh, dịch sang id ngoài của nền tảng, rồi trả về **mọi PK cùng
id ngoài đó**.

Phần chạm model quảng cáo đi qua `AdsSpendPort` sẵn có — pattern trả nợ layering Lô 5 mà
repo đang dùng — vì `.importlinter` cấm `apps.orders` import `apps.facebook_ads` /
`apps.google_ads` (orders nằm dưới hai app đó):

```python
# apps/orders/contracts/ads_spend_port.py
class AdAccountScope(NamedTuple):
    ids: list[int] | None    # PK để lọc, đã trải; None = không lọc
    count: int | None        # số tài khoản THẬT (theo id ngoài), không đếm bản sao

class AdsSpendPort(Protocol):
    def account_scope(self, account_ids: list[int]) -> AdAccountScope: ...
```

Mỗi app quảng cáo tự hiện thực phần truy vấn model của mình trong `adapters.py`
(`FacebookAdAccount.account_id` / `GoogleAdsAccount.customer_id`), đăng ký ở
`AppConfig.ready()` như các method port sẵn có.

`OrderRepository.accessible_ads_ad_account_ids` và `accessible_google_ads_account_ids`
**giữ nguyên chữ ký** `(user) -> list[int] | None` nhưng nay trả list đã trải. Nhờ vậy cả
ba view báo cáo (chiến dịch FB, chiến dịch Google, **theo page**) tự đúng mà không sửa
dòng nào — và mọi caller tương lai cũng vậy. Không migration, không đổi model.

Hai classmethod mới `ads_ad_account_scope(user)` / `google_ads_account_scope(user)` trả
cả `ids` lẫn `count` cho endpoint nào cần số đếm (mục 3.2).

**Ngữ nghĩa sau khi sửa:** user được gán một TKQC là thấy chi tiêu của TKQC đó dưới mọi
app. Đây là hành vi đúng — với nghiệp vụ, đó là **một** tài khoản quảng cáo.

**Chi phí truy vấn:** thêm đúng 1 query nhẹ (`values_list` trên bảng vài trăm dòng,
đã có index `(app, account_id)`), chỉ chạy cho user bị giới hạn. Không N+1.

### 3.2. Bỏ trạng thái "rỗng câm"

Hai endpoint campaign (FB + Google) trả thêm field:

```
accessible_accounts: number | null   # null = xem toàn bộ; số = user bị giới hạn
```

Frontend: khi cả hai nguồn đều báo `accessible_accounts === 0` → hiện băng cảnh báo
*"Bạn chưa được gán tài khoản quảng cáo nào. Liên hệ quản trị để gán TKQC cho kênh bạn
phụ trách."* thay cho empty state hiện tại.

Lý do: bảng trắng hiện không phân biệt được "không tiêu đồng nào" với "bạn không được
gán TKQC nào". Chính vì thiếu tín hiệu này mà bug 2.2 phải truy DB mới tìm ra. Cùng
tinh thần với cờ `truncated` và `revenue: null` đã có sẵn trong codebase.

### 3.3. Cột CTR

`CTR = clicks / impressions`, hiển thị `%` 2 chữ số thập phân; `impressions = 0` → `—`
(cùng quy ước null của `computeCpm` / `computeCpc`).

- `computeCtr` đặt cạnh `computeCpm` / `computeCpc` trong `lib/api/ads_campaign_report.ts`.
- Cột chèn giữa **Click** và **CPM** trong `CampaignCostTable`.
- Tính ở frontend, không đổi payload backend — nhất quán với CPM/CPC hiện tại.

**Giới hạn nghiệp vụ đã biết:** `clicks` của Facebook là *tổng click* (gồm click vào
page/ảnh), nên CTR sẽ nhỉnh hơn cột "CTR (link)" trong Ads Manager. Muốn khớp tuyệt đối
phải lưu `inline_link_clicks` — thêm cột DB + backfill sync, **không** làm trong lần này.

### 3.4. Tìm kiếm theo tên chiến dịch

Ô tìm kiếm trong tab Chiến dịch, đặt cùng hàng với bộ lọc Nền tảng / Tài khoản quảng cáo.
Lọc phía client trong cùng `useMemo` với hai bộ lọc sẵn có — bảng đã tải tối đa 1000 dòng
nên không cần thêm request.

Dùng `matchesSearchTerm` từ `lib/text-search` (đã dùng ở màn Quản lý kênh): bỏ dấu tiếng
Việt, quy `đ → d`, AND theo token. Nhờ vậy `"msc06"` khớp `LDP_MSC06_080726_TK 7_BÀI CŨ`
và `"bai win"` khớp `LDP_MSC06_200526_TK 6_BÀI WIN`.

Khớp trên **tên chiến dịch và tên TKQC** — người dùng gõ tên tài khoản vào ô tìm kiếm là
phản xạ tự nhiên, và hai trường được nối bằng dấu phân cách nên một cụm từ không thể vắt
qua hai trường.

Ô rỗng = không lọc. Khi lọc ra 0 dòng, empty state phải nói rõ là **do bộ lọc**, không
được dùng lại câu "Chưa có dữ liệu chiến dịch trong khoảng đã chọn" — nếu không lại rơi
vào đúng lớp lỗi "rỗng câm" mà mục 3.2 đang sửa.

### 3.5. Gom trùng ở dropdown chọn TKQC

Màn Quản lý kênh: gom option theo `account_id` (Facebook) / `customer_id` (Google), mỗi
TKQC chỉ hiện **một** dòng thay vì 4. Chỉ sửa hiển thị, không đụng DB.

Value của option giữ nguyên là PK (một PK đại diện) để không đổi contract API. An toàn
vì mục 3.1 đã làm cho việc chọn bản sao nào cũng ra cùng kết quả.

## 4. Test

`test_campaign_report_api.py` hiện **không có test phân quyền nào** — toàn bộ dùng user
scope ALL, đúng lỗ hổng mà docstring của `PageReportScopeTests` đã cảnh báo.

Bổ sung:

1. **Test bắt đúng bug này** (FB + Google): TKQC nhân bản dưới 2 app, mapping kênh trỏ
   app A, chi tiêu nằm dưới app B → user scope CHANNEL vẫn phải thấy đủ số.
2. Test cách ly: TKQC của kênh khác vẫn không được lộ.
3. Test `accessible_accounts`: `null` cho scope ALL, đúng số cho user bị giới hạn.
4. Test page report với cùng kịch bản nhân bản (mục 2.4).
5. Frontend: `computeCtr` (gồm nhánh `impressions = 0`), cột CTR render đúng, băng cảnh
   báo hiện khi `accessible_accounts === 0` và **không** hiện khi bằng `null`.
6. Frontend: tìm kiếm bỏ dấu (`"bai win"` khớp `BÀI WIN`), khớp cả tên TKQC, ô rỗng
   không lọc, và empty state khi lọc hết dòng phải là câu "không khớp bộ lọc" chứ không
   phải câu "chưa có dữ liệu".
7. Frontend: `groupAdAccountOptions` gom trùng theo id ngoài, giữ chip đang chọn khi kênh
   trỏ vào bản sao không phải bản đại diện.

## 5. Cố ý không làm

- **Gộp 4 dòng trùng trong DB** (xoá bản thừa + unique trên `account_id` + trỏ lại FK từ
  `facebook_ads`, `facebook_ad_daily_spends`, `channel_ad_accounts`): rủi ro cao, chạm dữ
  liệu chi tiêu lịch sử, mất khả năng biết TKQC chạy dưới app nào. Tách MR riêng, cửa sổ
  thấp điểm. Mục 3.1 đã làm cho việc trùng trở nên vô hại với báo cáo.
- **Bảng gán trực tiếp TKQC ↔ user**: đã chốt giữ đường qua kênh.
- **Lưu link click cho CTR chuẩn Ads Manager**: cần cột DB + backfill (mục 3.3).
- Không đụng tab Báo cáo theo digital / chi tiết theo digital.
- **Chi phí theo kênh ở tab Tổng quan vẫn dùng PK thô, chưa trải theo mục 3.1.**
  `summary_ads_daily_breakdown` (tính `ad_account_to_channels` rồi gọi
  `spend_rows_by_account`) khớp thẳng `ChannelAdAccount.account_id` — PK thô lưu trong
  bảng nối — chứ không đi qua
  `account_scope`/`AdsSpendPort` như đường lọc theo user ở mục 3.1. Hệ quả: đúng với bằng
  chứng prod ở mục 2.3 (kênh trỏ PK app 1, chi tiêu nằm dưới PK app 4), cột **Chi phí**
  của các kênh đó ở tab Tổng quan đang là **0 với cả admin** — không phải vấn đề phân
  quyền, MR này không chữa. Sau khi deploy: tab **Chiến dịch** sẽ hiện đủ số (đã trải qua
  mục 3.1), còn tab **Tổng quan** vẫn 0 cho cùng khoảng thời gian — hai tab lệch nhau trên
  cùng một màn hình, dễ bị đọc nhầm thành bug mới của MR này. Cần MR riêng để trải PK ở
  đường này; đắt hơn mục 3.1 vì phải map ngược PK → kênh (ở đây map kênh → PK).
- **Google có thể cộng trùng chi phí chiến dịch sau khi trải PK.** `GoogleAdsCampaign`
  unique theo `(ad_account, campaign_id)` và job sync lặp **mọi** `GoogleAdsConfig`; nếu
  hai MCC cùng thấy một customer (đúng cấu trúc nhân bản ở mục 2.2, áp cho Google) thì
  cùng một chiến dịch có hai bản ghi, mỗi bản một chi tiêu riêng. `aggregate_campaign_cost`
  group theo `(campaign_id, name, ad_account__name)` — hai bản sao trùng tên nên rơi vào
  cùng khoá và bị **SUM**, chi phí/hiển thị/click của chiến dịch đó nhân đôi. Facebook
  KHÔNG dính lỗi này vì `FacebookAd.fb_ad_id` unique **toàn cục** (không kèm app), nên
  cùng một ad không thể có hai bản ghi chi tiêu song song. Rủi ro này vốn đã tồn tại sẵn
  với admin (không bị lọc scope), MR này chỉ mở rộng nó sang NV ads bị scope CHANNEL.
  **Phải kiểm tra trước khi deploy** bằng truy vấn:
  ```sql
  select customer_id, count(*) rows, array_agg(id) pks, array_agg(config_id) configs
  from google_ads_accounts group by customer_id having count(*) > 1;
  ```
  Nếu có kết quả thì phải xử lý (gộp/loại trùng) trước khi deploy — nếu không, tab
  Chiến dịch Google sẽ hiện chi phí nhân đôi cho NV ads ngay sau khi mục 3.1 lên prod.

## 6. Nợ kỹ thuật ghi nhận

- `SALE_FB_CHANNEL_IDS = (24,)` hard-code id kênh trong code
  (`_repositories_reports.py`). Không liên quan bug này nhưng cùng loại rủi ro với
  whitelist id cứng mà `sale_report_channel_ids()` đã bỏ đi.
- Kênh 4 (`facebook`) gán `loiads` với `is_active = false`. Không phải lỗi, chỉ ghi nhận
  để đối chiếu nếu sau này thiếu số.

## 7. Trạng thái

Đã hoàn thành trên nhánh `fix/ads-report-scope-duplicate-accounts`. Sau khi deploy cần
kiểm chứng bằng tài khoản `loiads` trên tab Chiến dịch và tab Tổng quan → Theo page.

Còn nợ (MR riêng): gộp các dòng `FacebookAdAccount` / `GoogleAdsAccount` trùng, thêm
unique trên id ngoài. Mục 3.1 đã làm cho việc trùng vô hại với báo cáo nên đây là dọn
dẹp, không phải sửa lỗi.
