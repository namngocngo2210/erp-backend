# Hệ số hiển thị chi phí Facebook Ads

Chi phí Facebook trên mọi báo cáo được nhân một hệ số phụ phí/VAT trước khi hiển
thị. Hệ số này **đổi theo thời gian**, nên nó là một hàm chứ không còn là hằng số.

**Lịch sử quyết định:** 1.1 (gốc) → 1.08 từ 01/08/2026 (chốt 26/08/2026) → **1.1
trở lại từ 01/09/2026** (chốt 11/09/2026).

## Luật

| Kỳ báo cáo bắt đầu | Hệ số |
|---|---|
| trước 01/08/2026 | **1.1** |
| 01/08/2026 – 31/08/2026 | **1.08** |
| từ 01/09/2026 trở đi | **1.1** |
| không lọc ngày | **1.1** (kỳ bao trùm cả quá khứ) |

Tháng 8/2026 là giai đoạn ×1.08 **duy nhất**, kẹp giữa hai giai đoạn ×1.1. Nhìn
biểu đồ chi phí FB theo tháng sẽ thấy tháng 8 thụt xuống ~1,85% rồi bật lại —
đúng như thiết kế, không phải mất dữ liệu.

Nguồn duy nhất: bảng mốc `FACEBOOK_ADS_COST_MULTIPLIER_SCHEDULE` + hàm
`facebook_ads_cost_multiplier(date_from)` trong
[`apps/orders/reporting/shared.py`](../backend/apps/orders/reporting/shared.py).
**Đổi hệ số = thêm một mốc vào đầu bảng**, không sửa đè giá trị cũ: báo cáo vẫn
xem được kỳ quá khứ, sửa đè sẽ viết lại lịch sử chi phí của những tháng đã chốt sổ.

Google Ads giữ hệ số cố định `1.08` (VAT 8% tại VN) ở hằng số **riêng** —
hai nền tảng đổi hệ số theo hai lịch khác nhau nên không gộp làm một, dù có lúc
hai con số bằng nhau.

## Chọn theo KỲ, không theo từng ngày chi tiêu

Hệ số lấy theo **ngày bắt đầu kỳ đang xem**, áp cho cả kỳ. Hệ quả đã được nêu
trước khi chốt và user chấp nhận:

- Xem "tháng 8/2026" → cả kỳ ×1.08.
- Xem "tháng 9/2026" → cả kỳ ×1.1.
- Xem một kỳ bắt đầu trong tháng 8 nhưng kéo sang tháng 9 → cả kỳ ×**1.08**, kể
  cả phần chi tiêu tháng 9.
- Các con số trên **không cộng lại khớp nhau**, chênh 1,85% ở phần bắc qua mốc.

Muốn hai kỳ luôn khớp thì phải đổi sang tính theo từng ngày chi tiêu (`CASE`
theo `spend.date`) — đây là thay đổi nghiệp vụ, không phải sửa lỗi.

Ngoại lệ có chủ đích: mart `ai.chi_phi_quang_cao` ở grain ngày nên áp hệ số
**theo từng ngày chi tiêu**, dựng `CASE` thẳng từ cùng bảng mốc. Vì vậy tổng của
mart có thể khác tổng trên màn hình báo cáo cho một kỳ bắc qua mốc — xem COMMENT
bảng (migration `0012`, cập nhật ở `0020`).

## Nơi áp dụng

| Màn hình / job | Điểm gọi |
|---|---|
| Báo cáo Ads — tab Theo page | `apps/facebook_ads/page_report.py` |
| Báo cáo Ads — tab Theo chiến dịch | `apps/facebook_ads/campaign_report.py` |
| Báo cáo Ads — tab Gán sản phẩm | `apps/facebook_ads/transport/views/facebook_ads_views_impl/campaign_product_views.py` |
| Báo cáo Ads — biểu đồ theo ngày/kênh, bung theo sản phẩm | `apps/orders/reporting/ads_reports.py` (`_ads_cost_channel_maps`) |
| Báo cáo tổng — ô "Chi phí Facebook" | `apps/orders/reporting/overview.py` |
| Báo cáo ngày (daily_report) | `apps/daily_report/application/ads_service.py` |
| Job đẩy bảng Lark (cửa sổ 7 ngày) | `apps/facebook_ads/infrastructure/lark_ads_report_sync.py` |
| Mart `ai.chi_phi_quang_cao` (theo NGÀY) | `apps/ai_mart/infrastructure/sql/chi_phi_quang_cao.py` |

Thêm một đường chi phí FB mới mà quên gọi helper thì màn đó lệch hệ số với các
màn còn lại — đúng lớp lỗi mà `test_applies_same_cost_multiplier_as_channel_report`
đang canh.

## Ảnh hưởng sau deploy (mốc 01/09/2026)

Chi phí Facebook của các kỳ **bắt đầu từ 01/09/2026** sẽ **tăng 1,85%**
(`1.1/1.08 − 1`), kéo theo ME/RE và CPL tăng tương ứng. Số của tháng 8/2026 và
các kỳ cũ hơn **không đổi**. Cần báo trước cho phòng Ads, nếu không con số nhích
lên sẽ bị đọc là lỗi dữ liệu.

Bảng Lark chạy cửa sổ 7 ngày gần nhất nên **đổi ngay** ở lần sync kế tiếp. Mart
`ai.chi_phi_quang_cao` rebuild toàn bộ mỗi đêm nên tự tính lại, không cần backfill.
